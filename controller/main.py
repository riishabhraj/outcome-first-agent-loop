"""The always-on loop. One tick = 15 simulated minutes:

   ingest new chunks -> handle sim events (supervisor edits...) -> per workflow: incidents, caller-ID, experiments,
   schedule, compose desired config, route the diff through governance -> advance the clock.

Crash safety: all state is in SQLite; the loop re-derives what to do from (store, simulator) on every iteration, and
every mutation is an intent row + deterministic id (governance.py). Killing the process at any line is safe.
"""
from __future__ import annotations

import json
import os
import signal
import sys
import time
import traceback

from . import analytics as A
from . import cfgutil as U
from . import config as C
from . import did as DID
from . import experiments as X
from . import explain as E
from . import incidents as I
from . import planner as P
from .context import Ctx
from .governance import Governance
from .ingest import ingest_chunk, ts_minute
from .simclient import SimClient, SimError
from .store import Store
from .stats import tv


class Controller:
    def __init__(self, url: str, db: str, view_port: int | None = None):
        self.sim = SimClient(url)
        self.sim.wait_ready()
        self.store = Store(db)
        self.ctx = Ctx(self.sim, self.store)
        self.ctx.load_workflows()
        self.ctx.rebuild_memory()
        self.gov = Governance(self.ctx)
        self.live: dict[str, dict] = {}
        self.view_port = view_port
        self.decisions = 0
        self._stop = False
        signal.signal(signal.SIGTERM, lambda *_: setattr(self, "_stop", True))

    # ------------------------------------------------------------------ bootstrap (C3)
    def world_check(self):
        """Never reuse state from a different simulator world: clock behind our store, other scenario, or fewer events."""
        st = self.store
        s = self.sim.get("/sim/state")
        meta = st.kv_get("world")
        fresh = os.environ.get("CTRL_FRESH") == "1"
        if meta and (fresh or meta["scenario"] != s["scenario"] or s["sim_minute"] < st.now
                     or s["last_event_id"] < meta["last_event_id"]):
            st.close()
            arch = f"{st.path}.stale-{int(time.time())}"
            os.rename(st.path, arch)
            for ext in ("-wal", "-shm"):
                if os.path.exists(st.path + ext):
                    os.rename(st.path + ext, arch + ext)
            print(f"controller: state belongs to a different world; archived to {arch}", file=sys.stderr, flush=True)
            self.store = Store(st.path)
            self.ctx = Ctx(self.sim, self.store)
            self.ctx.load_workflows()
            self.gov = Governance(self.ctx)
            self.live = {}
        self.store.kv_set("world", {"scenario": s["scenario"], "last_event_id": s["last_event_id"]})

    def bootstrap(self):
        self.world_check()
        st = self.store
        t0 = time.time()
        n = 0
        for ch in self.sim.get("/events/chunks", params={"kind": "history"}):
            r = ingest_chunk(self.ctx, ch)
            n += r["events"]
        if n:
            st.audit("bootstrap", None, f"ingested {n} history events from pre-D0 world", {"seconds": round(time.time() - t0, 1)})
        total_dials = sum(m.dph * (m.window[1] - m.window[0]) for m in self.ctx.meta.values())
        self.ctx.ledger = {"on": True, "off": False}.get(os.environ.get("CTRL_LEDGER", ""),
                                                          total_dials <= C.LEDGER_MAX_DIALS_PER_DAY)
        self.gov.reconcile_startup()
        st.kv_set("options", {wf: m.options for wf, m in self.ctx.meta.items()})   # lets audit queries print names, not indexes

    def ingest_pending(self) -> tuple[int, float, int]:
        st = self.store
        last = st.q("SELECT coalesce(max(chunk),-1) FROM chunks WHERE kind='events'")[0][0]
        n, ms = 0, 0.0
        pend = self.sim.get("/events/chunks", params={"kind": "events", "after": last})
        for ch in pend:
            r = ingest_chunk(self.ctx, ch)
            n += r["events"]
            ms += r["ms"]
        return n, ms, len(pend)

    # ------------------------------------------------------------------ config mirror
    def fetch_live(self, wf: str) -> dict:
        r = self.sim.get(f"/workflows/{wf}/config")
        cur = self.live.get(wf)
        if not cur or cur["version"] != r["version"]:
            hist = {h["version"]: h for h in self.sim.get(f"/workflows/{wf}/config/history")}
            for v in sorted(v for v in hist if not self.store.q("SELECT 1 FROM config_versions WHERE wf=? AND version=?", (wf, v))):
                h = hist[v]
                self.store.db.execute("INSERT OR IGNORE INTO config_versions VALUES(?,?,?,?,?,?,?,?)",
                                      (wf, v, h["actor"], json.dumps(h["changed_dims"]),
                                       ts_minute(h["applied_at"]) if h["applied_at"] else None, h["proposal_id"],
                                       h["approval_id"], json.dumps(r["config"]) if v == r["version"] else None))
        self.live[wf] = r
        return r

    # ------------------------------------------------------------------ sim events
    def handle_events(self, now: int):
        st = self.store
        rows = st.q("SELECT event_id,type,wf,minute,body FROM sim_events WHERE handled=0 ORDER BY event_id")
        for eid, typ, wf, minute, body in rows:
            e = json.loads(body)
            if typ == "config_changed":
                self.on_config_changed(wf, e, minute)
            st.db.execute("UPDATE sim_events SET handled=1 WHERE event_id=?", (eid,))

    def on_config_changed(self, wf: str, e: dict, minute: int):
        st = self.store
        ws = st.wf_get(wf)
        dims = e["changed_dims"]
        if "prompt_version" in dims:
            ws["prompt_since"] = minute
            if e["actor"] == "supervisor":
                ws["validate_prompt"] = {"minute": minute, "version": e["version"]}
        if e["actor"] == "supervisor":
            prev = st.q("SELECT config FROM config_versions WHERE wf=? AND version<? AND config IS NOT NULL "
                        "ORDER BY version DESC LIMIT 1", (wf, e["version"]))
            live = self.fetch_live(wf)
            diff = {}
            if prev and live["version"] == e["version"]:
                diff = E.build_diff(json.loads(prev[0][0]), live["config"], dims)
            st.audit("supervisor_edit", wf, f"supervisor changed {dims} at v{e['version']}; we will not overwrite it "
                     f"for {C.SUPERVISOR_LOCK_H}h and will re-plan on top of it", {"diff": diff}, actor="supervisor",
                     version=e["version"])
            locks = ws.setdefault("locks", {})
            for d in dims:
                if d != "prompt_version":
                    locks[d] = minute + C.SUPERVISOR_LOCK_H * 60
            if "cohorts" in dims:
                locks["cohorts"] = minute + C.SUPERVISOR_LOCK_H * 60
            exp = ws.get("exp")
            if exp:
                for dim in X.DIMS:
                    if dim in dims and dim in exp:
                        dist = live["config"]["allocations"]["default"][dim]
                        X.reset_epoch(exp, dim, max(dist, key=dist.get), e["version"], minute,
                                      f"supervisor edit v{e['version']}")
                        st.audit("experiment_reset", wf, f"{dim}: evidence before v{e['version']} no longer applies; "
                                 f"control is now {exp[dim]['control']}", {}, version=e["version"])
            ws.setdefault("did", {})["initialised"] = ws.get("did", {}).get("initialised", False) if "caller_id" not in dims else False
            self.gov.mark_stale_if_moved(wf, e["version"])
        st.wf_set(wf, ws)

    # ------------------------------------------------------------------ one workflow
    def decide(self, wf: str, now: int):
        meta = self.ctx.meta[wf]
        ws = self.store.wf_get(wf)
        live = self.fetch_live(wf)
        cfg, ver = live["config"], live["version"]
        ws["live_version"] = ver
        if X.ensure_state(self.ctx, wf, cfg, now, ws.setdefault("exp", {}), ver):
            self.store.audit("experiment_init", wf, "experiment state created: control = " + ", ".join(
                f"{d}:{ws['exp'][d]['control']}" for d in X.DIMS), {"enabled": X.enabled(self.ctx, wf, ws)})
        # ---- incidents
        self.incidents(wf, now, ws, cfg, ver)
        live = self.live[wf]
        cfg, ver = live["config"], live["version"]
        # ---- caller id
        self.caller_id(wf, now, ws, cfg)
        if now % 1440 == 0:
            self.guardrail_defense(wf, now, ws)
        # ---- experiments (every 3h) + schedule (daily and when the cohort structure changes)
        blocks_changed = False
        if X.enabled(self.ctx, wf, ws) and now % 180 == 0:
            for ev in X.update(self.ctx, wf, now, ws, ver):
                self.log_exp_event(wf, ev)
                self.decisions += 1
                blocks_changed = True
                if ev.get("urgent"):
                    ws["urgent_rollback"] = now
        if "sched" not in ws or now % 1440 == 0 or blocks_changed:
            blocks = P.build_blocks(self.ctx, wf, ws, now)
            if P.refresh_schedule(self.ctx, wf, ws, now, blocks, force=("sched" not in ws)):
                self.store.audit("schedule_target", wf, "hour-weight tables updated for cohorts "
                                 + ", ".join(sorted(ws["sched"]["committed"]["tables"])), {
                                     "partition": ws["sched"].get("partition"),
                                     "tables": ws["sched"]["committed"]["tables"]})
                self.decisions += 1
        # ---- compose + route
        self.route(wf, now, ws, live)
        self.store.wf_set(wf, ws)

    def guardrail_defense(self, wf: str, now: int, ws: dict):
        """Pickup-primary workflows: our pickup gains bring in repeat callers who already declined to engage, which can
        erode the engagement guardrail. Once engagement is half-way to its allowed drop, start protecting it with
        welcome/voice experiments (same machinery, same governance)."""
        meta = self.ctx.meta[wf]
        if meta.primary != "pickup_rate" or ws.get("guardrail_defense"):
            return
        base = A.kpi_window(self.ctx, wf, -10**8, 0)
        rec = A.kpi_window(self.ctx, wf, now - 3 * 1440, now)
        if rec["conn"] < 600 or base["conn"] < 1000:
            return
        from .stats import two_prop_z
        rel = rec["engagement"] / base["engagement"] - 1
        z = two_prop_z(rec["eng"], rec["conn"], base["eng"], base["conn"])
        if rel <= -C.GUARDRAIL_DEFENSE_FRACTION * meta.guardrail_drop and z <= -1.65:
            ws["guardrail_defense"] = True
            self.store.audit("guardrail_defense", wf, f"engagement {rel:+.1%} vs pre-D0 (guardrail {meta.guardrail_drop:.0%}): "
                             "starting welcome/voice experiments to protect it", {"relative": rel, "z": z, "n": rec["conn"]})
            self.store.alert(wf, "ticket", "guardrail_erosion", f"{wf}: engagement {rel:+.1%} vs baseline; defence experiments started")

    def log_exp_event(self, wf: str, ev: dict):
        k = ev["kind"]
        txt = {"candidate_flagged": "candidate flagged (stage 1, needs confirmation on fresh data)",
               "winner_promoted": "winner promoted", "candidate_rejected": "candidate did not replicate",
               "arm_eliminated": "arm eliminated (worse than control)", "winner_decayed": "winner decayed: roll back",
               "arm_guardrail_block": "arm blocked by guardrail"}[k]
        self.store.audit(k, wf, f"{ev['dim']}={ev['opt']} in {ev['slice']}: {txt}", ev)

    # ---- caller id
    def caller_id(self, wf: str, now: int, ws: dict, cfg: dict):
        d = ws.setdefault("did", {})
        ev = DID.detect(self.ctx, wf, now, d)
        for did in ev["new"]:
            f = d["flagged"][did]
            self.store.audit("did_flagged", wf, f"caller ID {did} pickup {f['ratio']:.0%} of its peers (z={f['z']}, n={f['n']})",
                             {"did": did, **f})
            self.store.alert(wf, "ticket", f"did_flagged_{did}", f"{wf}: caller ID {did} degraded; rotating away")
        for did in ev["recovered"]:
            self.store.audit("did_recovered", wf, f"caller ID {did} back to peer level", {"did": did})
            self.store.resolve_alert(wf, f"did_flagged_{did}")
        locked = ws.get("locks", {}).get("caller_id", 0) > now
        if locked:
            return
        due = (now % (C.DID_REBALANCE_EVERY_H * 60) == 0) or ev["new"] or ev["recovered"] or not d.get("initialised")
        if not due:
            return
        live_dist = cfg["allocations"]["default"]["caller_id"]
        tgt, evid = DID.target(self.ctx, wf, now, live_dist, d)
        full = (not d.get("initialised")) or bool(ev["new"])
        auto_ok = self.ctx.meta[wf].governance["auto_apply"]
        by_auto = auto_ok.get("enabled") and "caller_id" in auto_ok.get("allowed_dims", [])
        committed = tgt if (full or not by_auto) else DID.step_toward(live_dist, tgt, 1.0, C.DID_DAMPING)
        min_tv = C.DID_MIN_SHARE_CHANGE if by_auto else C.DID_MIN_SHARE_CHANGE_APPROVAL
        gain = self.did_gain(wf, now, live_dist, committed)
        gate = C.DID_MIN_GAIN if by_auto else C.DID_MIN_GAIN_APPROVAL
        if (tv(committed, live_dist) >= min_tv and (gain is None or gain >= gate)) or ev["new"]:
            d["target"], d["evidence"], d["urgent"] = committed, evid, bool(ev["new"])
            d["initialised"] = True
            self.store.audit("did_target", wf, "caller-ID target " + ", ".join(f"{k}:{v:.2f}" for k, v in committed.items()),
                             {"target": committed, "live": live_dist, **evid, "urgent": bool(ev["new"])})
            self.decisions += 1
        else:
            d["target"], d["urgent"] = None, False
            d["initialised"] = True

    def did_gain(self, wf: str, now: int, old: dict, new: dict) -> float | None:
        """Predicted relative change in current pickup health from a share move, from the freshest per-DID pickups.
        None when the move sends traffic to DIDs we have not measured (exploration: no prediction possible)."""
        fac = DID.factors(self.ctx, wf, now, C.DID_GATE_WINDOW_H)
        if any(fac[d]["n"] < C.DID_MEASURED_N and new.get(d, 0) > 0.001 for d in new):
            return None
        eo = sum(w * fac[d]["f"] for d, w in old.items())
        en = sum(w * fac[d]["f"] for d, w in new.items())
        return en / eo - 1 if eo > 0 else None

    # ---- incidents
    def incidents(self, wf: str, now: int, ws: dict, cfg: dict, ver: int):
        inc = I.load_open(self.ctx, wf)
        prompt = cfg["prompt_version"]
        pv = ws.get("validate_prompt")
        if pv and inc is None:
            # A client supervisor just changed the prompt. We cannot know whether it is safe, and waiting for bad calls
            # to accumulate is the expensive way to find out: spend a validation run (population, not anecdote).
            ws.pop("validate_prompt")
            tmp = {"id": f"edit-{wf}-{now}", "credits_spent": 0, "validations": []}
            r = I.run_validation(self.ctx, wf, prompt, tmp)
            if r is not None:
                thr = self.ctx.meta[wf].governance["min_validation_pass_rate"]
                self.store.audit("prompt_edit_validated", wf, f"supervisor prompt {prompt}: validation pass_rate {r['pass_rate']} "
                                 f"(threshold {thr})", {"validation": r, "credits": tmp["credits_spent"]})
                if r["pass_rate"] < thr:
                    inc = I.open_incident(self.ctx, wf, now, "unsafe_prompt_edit", prompt,
                                          {"prompt": prompt, "validation_pass_rate": r["pass_rate"], "threshold": thr,
                                           "validation_run_id": r["validation_run_id"], "flag_rate": 0.0, "baseline": 0.0})
                    inc["validations"].append({"prompt": prompt, "run": r["validation_run_id"], "pass_rate": r["pass_rate"], "n": r["n"]})
                    inc["credits_spent"] += tmp["credits_spent"]
                    I.save(self.ctx, inc, wf)
                    self.decisions += 1
        if inc is None:
            ev = I.detect(self.ctx, wf, now, ws, prompt)
            kind = "hallucination_spike"
            if ev is None and ws.get("prompt_since") is not None and not ws.get("regression_checked_" + prompt):
                ev = I.detect_engagement_regression(self.ctx, wf, now, ws, prompt, ws["prompt_since"])
                kind = "engagement_regression"
                if ev is not None:
                    ws["regression_checked_" + prompt] = True
            if ev is None:
                return
            inc = I.open_incident(self.ctx, wf, now, kind, prompt, ev)
            ws["halluc"]["consec"] = 0
            self.decisions += 1
        self.advance_incident(wf, now, ws, cfg, ver, inc)

    def advance_incident(self, wf, now, ws, cfg, ver, inc):
        st = inc["state"]
        if st == "detected":
            chosen = inc.get("chosen") or I.choose_fix(self.ctx, wf, now, inc, inc["bad"])
            if not chosen:
                inc["state"] = "failed"
                inc["closed"] = now
                self.store.audit("incident_no_fix", wf, "no candidate passed validation; escalate to a human",
                                 {"incident": inc["id"], "validations": inc["validations"]})
                self.store.alert(wf, "page", "incident_no_fix", f"{wf}: no safe prompt found for incident {inc['id']}")
                I.save(self.ctx, inc, wf)
                return
            inc["chosen"] = chosen
            val = next(v for v in inc["validations"] if v["prompt"] == chosen)
            fix = U.clone(cfg)
            fix["prompt_version"] = chosen
            dims = ["prompt_version"]
            ev = {"validation_run_id": val["run"]}
            why = (f"{inc['kind']} on {inc['bad']}: {I._fmt(inc['evidence'])}. Proposed {chosen} "
                   f"(validation pass_rate {val['pass_rate']} on n={val['n']}).")
            ok, _ = self.gov.auto_ok(wf, cfg, fix, emergency=True)
            if ok:
                res = self.gov.auto_apply(wf, ver, fix, rationale=why, evidence=ev, kind="incident_fix", emergency=True,
                                          note={"dims": dims, "incident": inc["id"]})
                if res:
                    inc.update(state="applied", applied_minute=now, version=res["version"])
                    self.store.audit("incident_fix_applied", wf, f"{chosen} applied via policy-permitted auto-apply", {"incident": inc["id"]},
                                     version=res["version"])
            else:
                aff, _ = E.affected_leads(self.ctx, wf, cfg, fix, now)
                pid = self.gov.submit_approval(
                    wf, ver, fix, summary=f"Replace prompt {inc['bad']} with {chosen} (hallucination incident)",
                    diff=E.build_diff(cfg, fix, dims),
                    affected=aff, dry_run={"method": "validation run + observed QA flags",
                                           "validation": val, "observed": inc["evidence"]},
                    impact=min(1.0, max(0.3, round(max(inc["evidence"].get("flag_rate", 0.0), 1 - inc["evidence"].get("validation_pass_rate", 1.0)) * 10, 3))),
                    rationale=why, evidence=ev, kind="incident_fix", urgent=True,
                    note={"dims": dims, "incident": inc["id"]})
                if pid:
                    inc.update(state="proposed", proposal_id=pid)
                    self.store.audit("incident_fix_proposed", wf, f"proposal {pid} for {chosen}", {"incident": inc["id"]}, proposal_id=pid)
            I.save(self.ctx, inc, wf)
        elif st == "proposed":
            row = self.store.q("SELECT state,applied_version FROM proposals WHERE proposal_id=?", (inc["proposal_id"],))[0]
            if row[0] == "applied":
                inc.update(state="applied", applied_minute=now, version=row[1])
            elif row[0] in ("rejected", "expired", "lapsed", "stale", "invalid"):
                inc["history"].append({"proposal": inc["proposal_id"], "state": row[0], "at": now})
                inc["state"] = "detected"          # re-propose from the live config; keep the validated choice
                if row[0] == "rejected":
                    inc.pop("chosen", None)        # rejected: pick again (likely evidence/threshold)
            I.save(self.ctx, inc, wf)
        elif st == "applied":
            res = I.verify(self.ctx, wf, now, inc, cfg["prompt_version"])
            if res == "resolved":
                inc.update(state="resolved", closed=now)
                self.store.audit("incident_resolved", wf, f"verified: {inc['verify']}", {"incident": inc["id"], **inc["verify"],
                                 "minutes_from_detection_to_fix": inc["applied_minute"] - inc["opened"],
                                 "credits_spent": inc["credits_spent"]}, version=inc.get("version"))
                self.store.resolve_alert(wf, f"incident_{inc['kind']}")
            elif res == "failed":
                inc["attempts"] += 1
                self.store.audit("incident_fix_failed", wf, f"fix did not clear the problem: {inc['verify']}", {"incident": inc["id"]})
                inc.update(state="detected" if inc["attempts"] < 3 else "failed", bad=cfg["prompt_version"])
                inc.pop("chosen", None)
            I.save(self.ctx, inc, wf)

    # ---- route the diff
    def route(self, wf: str, now: int, ws: dict, live: dict):
        cfg, ver = live["config"], live["version"]
        d = ws.get("did", {})
        desired, blocks = P.compose(self.ctx, wf, cfg, ws, now, did_target=d.get("target"))
        dims = U.changed_dims(cfg, desired)
        if not dims:
            ws.pop("waiting", None)
            return
        gov = self.gov
        inflight = gov.inflight(wf)
        urgent_did = bool(d.get("urgent"))
        urgent_rb = ws.get("urgent_rollback") is not None
        pol_auto = self.ctx.meta[wf].governance["auto_apply"]
        # ---- operational part via auto-apply where the policy allows it
        ops = U.clone(cfg)
        if "caller_id" in dims:
            tgt = desired["allocations"]["default"]["caller_id"]
            lim = 0.9 * pol_auto.get("max_shift", 0) if pol_auto.get("enabled") else 1.0
            ops["allocations"]["default"]["caller_id"] = DID.step_toward(cfg["allocations"]["default"]["caller_id"], tgt, lim, 1.0)
        if "schedule" in dims and "cohorts" not in dims:
            ops["schedule"] = desired["schedule"]
        ops_dims = U.changed_dims(cfg, ops)
        if ops_dims and ws.get("auto_blocked_until", 0) <= now:
            ok, problems = gov.auto_ok(wf, cfg, ops)
            if ok:
                if inflight and not urgent_did:
                    ws["waiting"] = "operational change deferred: a request is in flight (an auto-apply would make it stale)"
                    return
                res = gov.auto_apply(wf, ver, ops, rationale=self.ops_rationale(wf, ws, ops_dims),
                                     evidence={"caller_id": d.get("evidence"), "dims": ops_dims},
                                     note={"dims": ops_dims, "detail": E.build_diff(cfg, ops, ops_dims)})
                if res:
                    self.decisions += 1
                    return
                ws["auto_blocked_until"] = now + 60
        # ---- emergency rollback of a decayed winner: auto only where policy permits, else urgent approval
        if urgent_rb:
            ok, _ = gov.auto_ok(wf, cfg, self.shift_limited(cfg, desired, pol_auto), emergency=True)
            if ok and not inflight:
                lim = self.shift_limited(cfg, desired, pol_auto)
                res = gov.auto_apply(wf, ver, lim, rationale="rollback of a decayed winner: " + self.summary_text(wf, ws, dims),
                                     evidence={"dims": dims}, emergency=True, kind="rollback",
                                     note={"dims": U.changed_dims(cfg, lim)})
                if res:
                    if not U.changed_dims(lim, desired):
                        ws.pop("urgent_rollback", None)
                    return
        # ---- everything else: one batched approval request
        urgent = (urgent_rb or urgent_did) and bool({"welcome_message", "voice", "caller_id"} & set(dims))
        ok, why = gov.can_submit(wf, now, urgent=urgent)
        if not ok:
            ws["waiting"] = why
            return
        ws.pop("waiting", None)
        aff, aff_info = E.affected_leads(self.ctx, wf, cfg, desired, now)
        dr = E.dry_run(self.ctx, wf, ws, cfg, desired, dims, blocks, now)
        initial = not any(c for c in cfg["cohorts"]) and "cohorts" in dims
        stages_moved = self.stage_sig(ws) != ws.get("last_sig")
        if not (urgent or urgent_rb or initial or stages_moved) and U.max_shift(cfg, desired) < C.MIN_ALLOC_SHIFT \
                and dr["expected_gain"]["pickup"] < C.MIN_PICKUP_GAIN:
            ws["waiting"] = "change below materiality threshold (not worth an approver's attention yet)"
            return
        impact = E.impact_score(dr["expected_gain"]["pickup"], dr["expected_gain"]["engagement"])
        if urgent_rb and not dr["expected_gain"]["engagement"]:
            impact = max(impact, 0.3)
        diff = E.build_diff(cfg, desired, dims)
        diff["affected_detail"] = aff_info
        snap = [k for dim in X.DIMS if dim in ws.get("exp", {}) for k, a in ws["exp"][dim]["arms"].items()
                if a["stage"] == "candidate"]
        prior = self.store.q("SELECT proposal_id,state FROM proposals WHERE wf=? AND state IN ('stale','lapsed','expired','rejected') "
                             "ORDER BY updated_minute DESC LIMIT 1", (wf,))
        rationale = self.rationale(wf, ws, dims, dr, prior)
        pid = gov.submit_approval(wf, ver, desired, summary=self.summary_text(wf, ws, dims), diff=diff, affected=aff,
                                  dry_run=dr, impact=impact, rationale=rationale, urgent=urgent,
                                  kind="rollback" if urgent_rb else "batch", parent=prior[0][0] if prior else None,
                                  note={"dims": dims, "candidates": snap, "expected_gain": dr["expected_gain"]})
        if pid:
            self.decisions += 1
            ws["last_sig"] = self.stage_sig(ws)

    @staticmethod
    def stage_sig(ws: dict) -> str:
        """Which hypotheses are in which stage - a change here is worth a request even if the shift is small."""
        return json.dumps(sorted((d, k, a["stage"]) for d in X.DIMS for k, a in ws.get("exp", {}).get(d, {}).get("arms", {}).items()
                                 if a["stage"] in ("candidate", "winner", "eliminated", "decayed")))

    def shift_limited(self, live: dict, desired: dict, pol_auto: dict) -> dict:
        """Interpolate allocations so a single auto-apply stays inside max_shift (multi-step convergence)."""
        if live["cohorts"] != desired["cohorts"] or not pol_auto.get("enabled"):
            return desired
        lim = 0.9 * pol_auto.get("max_shift", 0)
        sh = U.max_shift(live, desired)
        if sh <= lim or sh == 0:
            return desired
        a = lim / sh
        out = U.clone(desired)
        for k in set(live["allocations"]) | set(desired["allocations"]):
            for dim in U.AGENT:
                lo, hi = U.dist(live, k, dim), U.dist(desired, k, dim)
                new = {o: lo.get(o, 0) + a * (hi.get(o, 0) - lo.get(o, 0)) for o in set(lo) | set(hi)}
                new = {o: round(v, 4) for o, v in new.items() if v > 1e-4}
                s = sum(new.values())
                out["allocations"].setdefault(k, {})[dim] = {o: v / s for o, v in new.items()}
        return out

    # ---- text
    def summary_text(self, wf: str, ws: dict, dims: list[str]) -> str:
        parts = []
        if "cohorts" in dims:
            parts.append("define cohorts")
        if "schedule" in dims:
            parts.append("call each cohort at its best hours")
        if "caller_id" in dims:
            parts.append("spread/rotate caller IDs")
        if "welcome_message" in dims or "voice" in dims:
            arms = [a for dim in X.DIMS for a in ws.get("exp", {}).get(dim, {}).get("arms", {}).values()
                    if a["stage"] in ("winner", "candidate", "eliminated", "decayed")]
            if any(a["stage"] == "decayed" for a in arms):
                parts.append("roll back a variant that stopped working")
            elif any(a["stage"] == "winner" for a in arms):
                parts.append("promote proven welcome/voice variants for their cohorts")
            else:
                parts.append("run a controlled welcome/voice test")
        return "; ".join(parts).capitalize() + f" ({wf})"

    def ops_rationale(self, wf, ws, dims):
        return (f"Operational change within auto-apply policy ({'+'.join(dims)}): "
                f"{json.dumps(ws.get('did', {}).get('evidence', {}).get('factors', {}), default=str)[:300]}")

    def rationale(self, wf, ws, dims, dr, prior) -> str:
        bits = []
        if "schedule" in dims or "cohorts" in dims:
            part = ws.get("sched", {}).get("partition", {}).get("features", {})
            bits.append("Cohorts: callers whose hour-of-day pickup shape differs significantly are split (" +
                        (", ".join(f"{f}: {g}" for f, g in part.items()) or "none") + "); hour weights favour each cohort's best hours "
                        f"(replay: pickup {dr.get('pickup_replay', {}).get('relative_change', 0):+.1%}).")
        if "caller_id" in dims:
            bits.append("Caller IDs rebalanced by measured pickup per DID (see dry_run.caller_id_replay).")
        if {"welcome_message", "voice"} & set(dims):
            arms = E.X.summary(ws)
            bits.append("Variant plan from the experiment engine: " + (", ".join(
                f"{a['dim']}={a['opt']}@{a['slice']}:{a['stage']}({a['lift']:+.0%})" for a in arms
                if a["stage"] in ("winner", "candidate", "eliminated", "decayed")) or
                "initial controlled test, control keeps 40% of traffic"))
        if prior:
            bits.append(f"Re-proposed from fresh data after {prior[0][1]} of {prior[0][0]}.")
        locks = {k: v for k, v in ws.get("locks", {}).items() if v > self.store.now}
        if locks:
            bits.append(f"Dimensions {sorted(locks)} recently edited by the client supervisor are left untouched.")
        return " ".join(bits)

    # ------------------------------------------------------------------ the loop
    def tick(self, st: dict):
        t0 = time.perf_counter()
        now = st["sim_minute"]
        self.store.set_now(now)
        n, ims, backlog = self.ingest_pending()
        self.handle_events(now)
        t1 = time.perf_counter()
        # governance first so freshly applied/decided proposals inform this tick's decisions
        versions = {wf: self.fetch_live(wf)["version"] for wf in self.ctx.meta}
        for ev in self.gov.poll(now, versions):
            self.after_governance(ev)
        for wf in self.ctx.meta:
            self.decide(wf, now)
        t2 = time.perf_counter()
        import resource
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 * 1024 if sys.platform == "darwin" else 1024)
        self.store.db.execute("INSERT OR REPLACE INTO loop_metrics VALUES(?,?,?,?,?,?,?,?,?,?)",
                              (now, time.time(), n, ims, (t2 - t1) * 1000, (t2 - t0) * 1000,
                               float(st["credits_remaining"]), self.decisions, backlog, round(rss, 1)))
        self.health_alerts(now, st, backlog, (t2 - t0) * 1000)
        self.verify_changes(now)
        return n

    def health_alerts(self, now: int, st: dict, backlog: int, tick_ms: float):
        """Loop-health alerts. PAGE only when a human must act before morning; everything else is a ticket/info."""
        s = self.store
        total = s.kv_get("credits_total")
        if total is None:
            s.kv_set("credits_total", float(st["credits_remaining"]))
            total = float(st["credits_remaining"])
        open_inc = s.q("SELECT count(*) FROM incidents WHERE state NOT IN ('resolved','failed')")[0][0]
        if float(st["credits_remaining"]) < 0.1 * total and open_inc:
            s.alert(None, "page", "credits_exhausted_during_incident",
                    f"{st['credits_remaining']} credits left with {open_inc} open incident(s): validation/LLM calls will fail (402). "
                    "Top up credits or fix manually")
        elif float(st["credits_remaining"]) >= 0.1 * total:
            s.resolve_alert(None, "credits_exhausted_during_incident")
        if backlog > 20:
            s.alert(None, "ticket", "ingest_backlog", f"{backlog} event chunks waiting; decisions are running on stale data. "
                    "Check tick_ms in `controller status`")
        elif backlog <= 2:
            s.resolve_alert(None, "ingest_backlog")
        pend_old = s.q("SELECT count(*) FROM proposals WHERE route='approval' AND state='approved' AND ?-updated_minute>90", (now,))[0][0]
        if pend_old:
            s.alert(None, "page", "approved_not_applied", "an approved change has not been applied for >90 min; it lapses after the "
                    "apply window. Check controller logs/governance.poll")
        else:
            s.resolve_alert(None, "approved_not_applied")

    def after_governance(self, ev: dict):
        wf = ev["wf"]
        ws = self.store.wf_get(wf)
        if ev["type"] == "applied":
            if ev.get("kind") == "rollback":
                ws.pop("urgent_rollback", None)
            row = self.store.q("SELECT note FROM proposals WHERE proposal_id=?", (ev["proposal_id"],))
            note = json.loads(row[0][0] or "{}") if row else {}
            for k in note.get("candidates", []):
                for dim in X.DIMS:
                    a = ws.get("exp", {}).get(dim, {}).get("arms", {}).get(k)
                    if a and a["stage"] == "candidate" and a.get("fresh_version") is None:
                        a["fresh_version"] = ev["version"]
            ws.setdefault("watch", []).append({"version": ev["version"], "minute": self.store.now, "dims": ev["dims"],
                                               "expected": note.get("expected_gain"), "proposal": ev["proposal_id"]})
            self.live.pop(wf, None)
        elif ev["type"] in ("rejected", "expired", "lapsed", "stale"):
            self.store.audit("replan", wf, f"{ev['type']}: next batch is recomputed from fresh data", ev)
            self.live.pop(wf, None)
        self.store.wf_set(wf, ws)

    def verify_changes(self, now: int):
        """+24h after every applied change: compare KPIs before/after (descriptive, with the model's prediction)."""
        for wf in self.ctx.meta:
            ws = self.store.wf_get(wf)
            keep, dirty = [], False
            for w in ws.get("watch", []):
                if now - w["minute"] < 1440:
                    keep.append(w)
                    continue
                pre = A.kpi_window(self.ctx, wf, w["minute"] - 1440, w["minute"])
                post = A.kpi_window(self.ctx, wf, w["minute"], w["minute"] + 1440)
                rel = lambda a, b: None if not a or not b else round(b / a - 1, 4)
                res = {"version": w["version"], "dims": w["dims"], "predicted": w.get("expected"),
                       "pickup_before": pre["pickup"], "pickup_after": post["pickup"],
                       "engagement_before": pre["engagement"], "engagement_after": post["engagement"],
                       "pickup_change": rel(pre["pickup"], post["pickup"]),
                       "engagement_change": rel(pre["engagement"], post["engagement"]),
                       "note": "before/after on the same workflow; descriptive, not causal"}
                self.store.audit("verification", wf, f"v{w['version']} +24h: pickup {res['pickup_change']}, "
                                 f"engagement {res['engagement_change']}", res, proposal_id=w.get("proposal"), version=w["version"])
                g = self.ctx.meta[wf].guardrail_drop
                pk = self.ctx.meta[wf].primary == "pickup_rate"
                gk = "engagement_change" if pk else "pickup_change"
                from .stats import two_prop_z
                zz = (two_prop_z(post["eng"], post["conn"], pre["eng"], pre["conn"]) if pk
                      else two_prop_z(post["conn"], post["dials"], pre["conn"], pre["dials"]))
                res["guardrail_z"] = round(zz, 2)
                if res[gk] is not None and res[gk] < -g and zz <= -3.0:
                    self.store.alert(wf, "ticket", f"guardrail_{w['version']}", f"{wf}: guardrail KPI moved {res[gk]:+.1%} (z={zz:.1f}) after v{w['version']}")
                dirty = True
            if dirty or len(keep) != len(ws.get("watch", [])):
                ws["watch"] = keep
                self.store.wf_set(wf, ws)

    def run(self):
        self.bootstrap()
        if self.view_port:
            from . import view
            view.serve(self, self.view_port)
        while not self._stop:
            st = self.sim.get("/sim/state")
            if st["finished"]:
                self.tick(st)  # final ingest + verification
                break
            self.tick(st)
            try:
                self.sim.post("/sim/advance", {"minutes": C.TICK_MIN}, retry_5xx=False)
            except SimError as e:
                if e.code == "finished":
                    continue
                if e.status >= 500:
                    time.sleep(1)
                    continue
                raise
        if self._stop:                      # deliberate SIGTERM (deploy/drain): not finished, and run.sh must not restart us
            print("controller: stopped by SIGTERM before the scenario ended", flush=True)
            sys.exit(143)
        self.store.audit("run_finished", None, "scenario finished; final state written", {"decisions": self.decisions})
        print("controller: scenario finished", flush=True)


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser("controller")
    ap.add_argument("--url", required=True)
    ap.add_argument("--db", default=os.environ.get("CTRL_DB", "state/controller.db"))
    ap.add_argument("--view-port", type=int, default=int(os.environ.get("CTRL_VIEW_PORT", "8701")))
    a = ap.parse_args(argv)
    os.makedirs(os.path.dirname(os.path.abspath(a.db)), exist_ok=True)
    Controller(a.url, a.db, a.view_port).run()


if __name__ == "__main__":
    main()
