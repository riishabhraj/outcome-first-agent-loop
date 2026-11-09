"""Proposal evidence (C10): diff, affected leads, dry run, impact score. Each number says how it was computed.

Impact score = expected relative gain in *leads concluded per dial* (pickup x engagement), clipped to [0,1] with
IMPACT_FULL_LIFT (10%) mapped to 1.0. It is a model-based expectation; exploration is ~0 by construction. We
back-test it: after each applied change the verification job logs realised-vs-predicted into the audit store.
"""
from __future__ import annotations

import numpy as np

from . import analytics as A
from . import cfgutil as U
from . import config as C
from . import experiments as X
from . import schedule as S
from .context import Ctx
from .feat import Slice


def _masks(cfg):
    return [Slice.make(c["where"]).mask() for c in cfg["cohorts"]]


def affected_leads(ctx: Ctx, wf: str, old: dict, new: dict, now: int) -> tuple[int, dict]:
    """Leads inside their cadence window, not yet concluded, whose treatment differs under `new`."""
    feats = A.active_leads_mask(ctx, wf, now // 1440)
    if len(feats) == 0:
        return 0, {"active_leads": 0}
    hours = list(range(*ctx.meta[wf].window))
    mo, mn = _masks(old), _masks(new)
    changed = np.zeros(512, dtype=bool)
    for cell in range(512):
        if U.cell_treatment(old, mo, cell, hours) != U.cell_treatment(new, mn, cell, hours):
            changed[cell] = True
    n = int(changed[feats].sum())
    return n, {"active_leads": int(len(feats)), "changed_cells": int(changed.sum()),
               "future_leads_per_day": ctx.meta[wf].daily_batch.get("size")}


def expected_engagement_gain(ctx: Ctx, wf: str, state: dict, old: dict, new: dict, blocks) -> float:
    """Relative engagement change implied by moving allocation weights, using measured arm lifts (0 for unmeasured)."""
    if not blocks or "exp" not in state:
        return 0.0
    live_ids = {c["id"] for c in old["cohorts"]}
    tot = 0.0
    for b in blocks:
        bid = "default" if b.default else b.id
        g = 0.0
        for dim in X.DIMS:
            if dim not in state["exp"]:
                continue
            st = X.option_states(state, dim, b.slice_ids)
            wn = U.dist(new, bid, dim)
            wo = U.dist(old, bid if bid in live_ids or bid == "default" else "default", dim)
            for o, (stage, lift) in st.items():
                if stage in ("winner", "candidate", "eliminated"):
                    shrink = 1.0 if stage == "winner" else 0.5
                    g += (wn.get(o, 0.0) - wo.get(o, 0.0)) * lift * shrink
        tot += b.share * g
    return tot


def impact_score(gain_pickup: float, gain_eng: float) -> float:
    g = (1 + gain_pickup) * (1 + gain_eng) - 1
    return round(float(min(max(g / C.IMPACT_FULL_LIFT, 0.0), 1.0)), 3)


def build_diff(old: dict, new: dict, dims: list[str]) -> dict:
    d: dict = {"changed_dims": dims}
    if "cohorts" in dims:
        d["cohorts"] = {"before": [c["id"] for c in old["cohorts"]], "after": [c["id"] for c in new["cohorts"]]}
    for dim in U.AGENT:
        if dim in dims:
            ch = {}
            for k in set(old["allocations"]) | set(new["allocations"]):
                a, b = U.dist(old, k, dim), U.dist(new, k, dim)
                if U._tv(a, b) > U.TOL:
                    ch[k] = {"from": {o: round(w, 3) for o, w in a.items()}, "to": {o: round(w, 3) for o, w in b.items()}}
            d[dim] = ch
    if "prompt_version" in dims:
        d["prompt_version"] = {"from": old["prompt_version"], "to": new["prompt_version"]}
    if "schedule" in dims:
        d["schedule"] = {"hour_weight_tables": sorted(new["schedule"].get("hour_weights", {})),
                         "peak_hours": {k: sorted(t, key=lambda h: -t[h])[:3]
                                        for k, t in new["schedule"].get("hour_weights", {}).items()}}
    return d


def dry_run(ctx: Ctx, wf: str, state: dict, old: dict, new: dict, dims: list[str], blocks, now: int) -> dict:
    out: dict = {"method": "model-based replay on historical cohorts (pre-D0 history + observed calls); "
                           "not a counterfactual measurement", "caveats": []}
    gain_pick = 0.0
    model = S.build_model(ctx, wf)
    if model and ({"schedule", "cohorts"} & set(dims)):
        a, b = S.replay_pickup(model, old), S.replay_pickup(model, new)
        gain_pick = b / a - 1 if a > 0 else 0.0
        out["pickup_replay"] = {"history_days": 14, "expected_pickup_per_dial_before": round(a, 4),
                                "expected_pickup_per_dial_after": round(b, 4), "relative_change": round(gain_pick, 4)}
    if "caller_id" in dims:
        fac = state.get("did", {}).get("evidence", {}).get("factors", {})
        d_old, d_new = U.dist(old, "default", "caller_id"), U.dist(new, "default", "caller_id")
        if fac:
            eo = sum(w * fac.get(o, {}).get("f", 1.0) for o, w in d_old.items())
            en = sum(w * fac.get(o, {}).get("f", 1.0) for o, w in d_new.items())
            g = en / eo - 1 if eo > 0 else 0.0
            gain_pick += g
            out["caller_id_replay"] = {"relative_health_factor_before": round(eo, 3),
                                       "after": round(en, 3), "relative_change": round(g, 4),
                                       "basis": "last 12h observed pickup per DID vs pooled"}
        out["caveats"].append("caller-ID health depends on load; gain is for the current load state")
    gain_eng = expected_engagement_gain(ctx, wf, state, old, new, blocks) if {"welcome_message", "voice", "cohorts"} & set(dims) else 0.0
    if gain_eng or {"welcome_message", "voice"} & set(dims):
        out["engagement_replay"] = {"expected_relative_change": round(gain_eng, 4),
                                    "basis": "measured arm-vs-control lifts x traffic moved; unmeasured arms = 0"}
        arms = X.summary(state)
        out["arms_considered"] = [a for a in arms if a["stage"] in ("winner", "candidate", "eliminated")][:12]
        if gain_eng == 0.0:
            out["caveats"].append("exploration step: expected KPI change ~0 by design; value is information")
    out["expected_gain"] = {"pickup": round(gain_pick, 4), "engagement": round(gain_eng, 4)}
    return out
