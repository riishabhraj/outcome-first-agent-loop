"""Runtime context: simulator client, store, static workflow metadata, and in-memory lead feature arrays
(rebuilt from the store on restart)."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .simclient import SimClient
from .store import Store

AGENT_DIMS = ["welcome_message", "voice", "caller_id"]


@dataclass
class WfMeta:
    id: str
    tenant: str
    options: dict
    window: tuple
    dph: int
    cadence: list
    objective: dict
    governance: dict
    mid_flight: str
    languages: list
    daily_batch: dict
    prompt_catalog: dict
    opt_idx: dict = field(default_factory=dict)

    @property
    def primary(self) -> str:
        return self.objective["primary"]

    @property
    def guardrail_drop(self) -> float:
        g = self.objective.get("guardrail", {})
        return float(next(iter(g.values()), 0.02)) if g else 0.02

    @staticmethod
    def from_api(w: dict) -> "WfMeta":
        m = WfMeta(id=w["workflow_id"], tenant=w["tenant_id"], options=w["options"],
                   window=tuple(w["dialling_window"]), dph=w["dials_per_hour"], cadence=w["cadence"],
                   objective=w["objective"], governance=w["governance"], mid_flight=w["mid_flight"],
                   languages=w["languages"], daily_batch=w["daily_batch"], prompt_catalog=w["prompt_catalog"])
        m.opt_idx = {d: {o: i for i, o in enumerate(opts)} for d, opts in w["options"].items()}
        return m

    def refresh(self, w: dict):
        """Policies are re-read from the API; they are the contract, not our memory of it."""
        self.governance = w["governance"]
        self.objective = w["objective"]


class LeadArrays:
    def __init__(self):
        self.feat = np.zeros(1024, dtype=np.uint16)
        self.day = np.full(1024, -32768, dtype=np.int16)
        self.concl = np.zeros(1024, dtype=np.uint8)
        self.n = 0  # max lead number + 1

    def ensure(self, k: int):
        if k <= len(self.feat):
            return
        cap = len(self.feat)
        while cap < k:
            cap *= 2
        for name, fill in (("feat", 0), ("day", -32768), ("concl", 0)):
            old = getattr(self, name)
            new = np.full(cap, fill, dtype=old.dtype)
            new[: len(old)] = old
            setattr(self, name, new)


class Ctx:
    def __init__(self, sim: SimClient, store: Store):
        self.sim, self.store = sim, store
        self.meta: dict[str, WfMeta] = {}
        self.leads: dict[str, LeadArrays] = {}
        self.cohort_codes: dict[str, dict[str, int]] = {}
        self.cohort_names: dict[str, dict[int, str]] = {}
        self.ledger = False  # per-call treatment ledger (compliance question 3); off at load scale

    def load_workflows(self):
        for w in self.sim.get("/workflows"):
            if w["workflow_id"] in self.meta:
                self.meta[w["workflow_id"]].refresh(w)
            else:
                self.meta[w["workflow_id"]] = WfMeta.from_api(w)
                self.leads[w["workflow_id"]] = LeadArrays()
                self.cohort_codes[w["workflow_id"]] = {"default": 0}
                self.cohort_names[w["workflow_id"]] = {0: "default"}

    def rebuild_memory(self):
        """After a restart: reload lead features/conclusions and cohort codes from the store."""
        st = self.store
        for wf, name, code in st.q("SELECT wf,name,code FROM cohort_codes"):
            self.cohort_codes.setdefault(wf, {"default": 0})[name] = code
            self.cohort_names.setdefault(wf, {0: "default"})[code] = name
        for wf, la in self.leads.items():
            rows = st.q("SELECT num,day,feat,concl FROM leads WHERE wf=?", (wf,))
            if not rows:
                continue
            a = np.array(rows, dtype=np.int64)
            la.ensure(int(a[:, 0].max()) + 1)
            la.feat[a[:, 0]] = a[:, 2]
            la.day[a[:, 0]] = a[:, 1]
            la.concl[a[:, 0]] = a[:, 3]
            la.n = int(a[:, 0].max()) + 1

    def cohort_code(self, wf: str, name: str) -> int:
        d = self.cohort_codes[wf]
        c = d.get(name)
        if c is None:
            c = len(d)
            d[name] = c
            self.cohort_names[wf][c] = name
            self.store.db.execute("INSERT OR IGNORE INTO cohort_codes VALUES(?,?,?)", (wf, name, c))
        return c
