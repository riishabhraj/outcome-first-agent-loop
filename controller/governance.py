"""Governance client (C9). The only module that mutates a workflow's config.

Proposal lifecycle (route = approval):
  submitting -> pending -> approved -> applied
                        \\-> rejected | expired            (re-queued later with FRESH data, never re-sent as is)
                approved -> lapsed (apply window missed)  | stale (config moved under us: re-proposed from scratch)
  pending/approved -> stale when base_version != live version (supervisor edit or our own auto-apply)
Route = auto: submitting -> applied | refused (403 policy) | stale

Every mutation writes an intent row first with a deterministic id, so a crash between "decided to send" and
"recorded the answer" is reconciled against the simulator on restart instead of double-sending.
"""
from __future__ import annotations

import json
import math
import random

from . import cfgutil as U
from . import config as C
from .context import Ctx
from .chaos import maybe_crash
from .simclient import SimError

NONTERMINAL = ("submitting", "pending", "approved")


class Governance:
    def __init__(self, ctx: Ctx):
        self.ctx = ctx
        self.st = ctx.store
        if self.st.kv_get("nonce") is None:
            self.st.kv_set("nonce", "%04x" % random.randrange(16 ** 4))

    # ------------------------------------------------------------------ policy helpers
    def pol(self, wf):
        return self.ctx.meta[wf].governance

    def prefs(self, wf):
        return C.TENANT_PREFS.get(self.ctx.meta[wf].tenant, C.DEFAULT_TENANT_PREFS)

    def inflight(self, wf):
        r = self.st.q("SELECT proposal_id,route,state,approval_id,base_version,created_minute,dims,kind "
                      "FROM proposals WHERE wf=? AND state IN ('submitting','pending','approved') "
                      "ORDER BY created_minute DESC", (wf,))
        return r[0] if r else None

    def recent_requests(self, wf, now):
        return self.st.q("SELECT count(*) FROM proposals WHERE wf=? AND route='approval' AND created_minute>? "
                         "AND state NOT IN ('invalid')", (wf, now - 1440))[0][0]

    def last_negative(self, wf):
        r = self.st.q("SELECT updated_minute,reasons,state FROM proposals WHERE wf=? AND state IN "
                      "('rejected','expired','lapsed') ORDER BY updated_minute DESC LIMIT 1", (wf,))
        return r[0] if r else None

    def can_submit(self, wf, now, urgent=False) -> tuple[bool, str]:
        # One routine request in flight at a time (a second would go stale the moment the first applies). An urgent
        # fix does NOT wait behind a routine request: the routine one goes stale and is re-proposed from fresh data.
        if self.inflight(wf) and not urgent:
            return False, "a request is already in flight for this workflow"
        pol, pref = self.pol(wf), self.prefs(wf)
        hard = min(pol["max_requests_per_24h"], pref.get("hard_max_req_per_day", 10**6))
        # routine work never takes the last slot(s): an incident fix must not queue behind a routine experiment
        limit = hard if urgent else min(max(hard - C.URGENT_RESERVE, 1), pref["max_req_per_day"])
        if self.recent_requests(wf, now) >= limit:
            return False, f"request budget ({limit}/24h) used"
        neg = self.last_negative(wf)
        if neg and not urgent:
            reasons = json.loads(neg[1] or "[]")
            cool = C.FATIGUE_COOLDOWN_H if any("fatigue" in r for r in reasons) else C.REJECT_COOLDOWN_H
            if now < neg[0] + cool * 60:
                return False, "cooling down after a negative decision"
        if urgent:
            return True, "urgent"
        last = self.st.q("SELECT max(created_minute) FROM proposals WHERE wf=? AND route='approval'", (wf,))[0][0]
        if last is not None and now - last < C.MIN_SPACING_H * 60:
            return False, f"spacing: last request {now - last} min ago (min {C.MIN_SPACING_H}h)"
        hod = (now % 1440) // 60
        lo, hi = pol["approver_hours"]
        s0 = max(lo, pref.get("no_requests_before_hour", 0))
        lat_h = math.ceil(pol["approver_latency_minutes"][1] / 60)
        s1 = max(s0 + 1, hi - lat_h)
        if not (s0 <= hod < s1):
            return False, f"outside submission window {s0:02d}:00-{s1:02d}:00 (approver hours {lo}-{hi})"
        return True, "ok"

    def auto_ok(self, wf, live, new, *, emergency=False) -> tuple[bool, list[str]]:
        pol = self.pol(wf)["auto_apply"]
        dims = U.changed_dims(live, new)
        problems = []
        if not pol.get("enabled"):
            problems.append("auto-apply disabled by policy")
        bad = [d for d in dims if d not in pol.get("allowed_dims", [])]
        if bad:
            problems.append(f"dims not permitted: {bad}")
        if U.max_shift(live, new) > pol.get("max_shift", 0) + 1e-9:
            problems.append("shift exceeds max_shift")
        cf = [d for d in dims if d in U.CUSTOMER_FACING]
        if cf and not (C.AUTO_APPLY_CUSTOMER_FACING or (emergency and C.EMERGENCY_AUTO_ROLLBACK)):
            problems.append(f"voluntary: customer-facing dims {cf} go through a named approver")
        if not dims:
            problems.append("no change")
        return (not problems), problems

    def _new_id(self, wf, tag):
        with self.st.tx():
            n = int(self.st.kv_get("prop_seq", 0)) + 1
            self.st.kv_set("prop_seq", n)
        return f"{self.st.kv_get('nonce')}-{wf}-{tag}{n:04d}"

    # ------------------------------------------------------------------ submit (approval route)
    def submit_approval(self, wf, base_version, cfg, *, summary, diff, affected, dry_run, impact, rationale,
                        evidence=None, kind="batch", urgent=False, parent=None, note=None) -> str | None:
        now = self.st.now
        ok, why = self.can_submit(wf, now, urgent)
        if not ok:
            return None
        pid = self._new_id(wf, "a")
        dims = (note or {}).get("dims", [])
        body = {"proposal_id": pid, "workflow_id": wf, "base_version": base_version, "proposed_config": cfg,
                "change_summary": summary, "diff": diff, "affected_leads": int(affected), "dry_run": dry_run,
                "impact_score": float(impact), "rationale": rationale}
        if evidence:
            body["evidence"] = evidence
        with self.st.tx():
            self.st.db.execute(
                "INSERT INTO proposals(proposal_id,wf,route,kind,state,created_minute,base_version,dims,change_summary,"
                "impact_score,affected_leads,request,config,parent_id,updated_minute,note) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (pid, wf, "approval", kind, "submitting", now, base_version, json.dumps(dims), summary, impact,
                 int(affected), json.dumps({k: v for k, v in body.items() if k != "proposed_config"}, default=str),
                 json.dumps(cfg), parent, now, json.dumps(note or {})))
            self.st.audit("proposal_intent", wf, f"submitting: {summary}", {"kind": kind, "dims": dims,
                          "impact": impact, "affected_leads": int(affected), "rationale": rationale,
                          "evidence": evidence, "dry_run": dry_run, "diff": diff, "urgent": urgent},
                          proposal_id=pid, version=base_version)
        maybe_crash(self.st, "intent")
        self._send(wf, pid, body)
        return pid

    def _send(self, wf, pid, body):
        try:
            r = self.ctx.sim.post("/approvals", body)
            maybe_crash(self.st, "sent")
        except SimError as e:
            if e.code == "duplicate_proposal_id":
                r = next((a for a in self.ctx.sim.get("/approvals", params={"workflow_id": wf})
                          if a["proposal_id"] == pid), None)
                if r is None:
                    self._set(pid, "invalid", note_add={"error": "duplicate id but not found"})
                    return
            elif e.code == "stale_base_version":
                self._set(pid, "stale", note_add={"detail": e.detail})
                self.st.audit("proposal_stale", wf, "base version moved before submission", {"detail": e.detail},
                              proposal_id=pid)
                return
            elif e.status in (409, 422):
                self._set(pid, "invalid", note_add={"error": e.code, "detail": e.detail})
                self.st.audit("proposal_invalid", wf, f"{e.code}: {e.detail}", {}, proposal_id=pid)
                if e.code != "no_change":
                    self.st.alert(wf, "ticket", "invalid_proposal", f"{e.code}: {str(e.detail)[:160]}")
                return
            else:
                raise
        self.st.db.execute("UPDATE proposals SET state='pending', approval_id=?, updated_minute=? WHERE proposal_id=?",
                           (r["approval_id"], self.st.now, pid))
        self.st.audit("proposal_submitted", wf, f"approval {r['approval_id']} pending (expires {r['expires_at']})",
                      {"approval": r}, proposal_id=pid, approval_id=r["approval_id"])

    # ------------------------------------------------------------------ auto route
    def auto_apply(self, wf, base_version, cfg, *, rationale, evidence=None, kind="ops", note=None,
                   emergency=False) -> dict | None:
        now = self.st.now
        pid = self._new_id(wf, "u")
        body = {"proposal_id": pid, "base_version": base_version, "proposed_config": cfg, "rationale": rationale}
        if evidence:
            body["evidence"] = evidence
        dims = (note or {}).get("dims", [])
        with self.st.tx():
            self.st.db.execute(
                "INSERT INTO proposals(proposal_id,wf,route,kind,state,created_minute,base_version,dims,change_summary,"
                "request,config,updated_minute,note) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (pid, wf, "auto", kind, "submitting", now, base_version, json.dumps(dims), rationale[:200],
                 json.dumps({k: v for k, v in body.items() if k != "proposed_config"}, default=str), json.dumps(cfg),
                 now, json.dumps(note or {})))
            self.st.audit("auto_apply_intent", wf, f"auto-apply: {rationale[:160]}",
                          {"dims": dims, "evidence": evidence, "emergency": emergency, "kind": kind,
                           "detail": (note or {}).get("detail")}, proposal_id=pid, version=base_version)
        maybe_crash(self.st, "intent")
        try:
            r = self.ctx.sim.call("PUT", f"/workflows/{wf}/config", json=body)
            maybe_crash(self.st, "sent")
        except SimError as e:
            if e.code == "duplicate_proposal_id":
                return self._reconcile_auto(wf, pid)
            state = "stale" if e.code == "stale_base_version" else "refused" if e.status == 403 else "invalid"
            self._set(pid, state, note_add={"error": e.code, "detail": e.detail})
            self.st.audit("auto_apply_" + state, wf, f"{e.code}: {e.detail}", {}, proposal_id=pid)
            if state == "refused":
                self.st.alert(wf, "ticket", "auto_apply_refused", str(e.detail)[:200])
            return None
        self._set(pid, "applied", applied_version=r["version"])
        self.st.audit("auto_applied", wf, f"v{r['version']} applied (auto) dims={r['changed_dims']}",
                      {"rationale": rationale, "evidence": evidence, "dims": r["changed_dims"]},
                      proposal_id=pid, version=r["version"], actor="controller_auto")
        return {"proposal_id": pid, "version": r["version"], "dims": r["changed_dims"]}

    def _reconcile_auto(self, wf, pid):
        for h in self.ctx.sim.get(f"/workflows/{wf}/config/history"):
            if h["proposal_id"] == pid:
                self._set(pid, "applied", applied_version=h["version"])
                return {"proposal_id": pid, "version": h["version"], "dims": h["changed_dims"]}
        self._set(pid, "invalid", note_add={"error": "duplicate id but no version"})
        return None

    # ------------------------------------------------------------------ reconcile
    def reconcile_startup(self):
        """Auto-apply intents that never got an answer: either they reached the simulator (a config version carries
        the proposal id) or they did not. Never re-send blindly."""
        for pid, wf in self.st.q("SELECT proposal_id,wf FROM proposals WHERE route='auto' AND state='submitting'"):
            res = self._reconcile_auto_quiet(wf, pid)
            self.st.audit("auto_reconciled", wf, "applied before the restart" if res else "never reached the simulator",
                          {}, proposal_id=pid)

    def _reconcile_auto_quiet(self, wf, pid):
        for h in self.ctx.sim.get(f"/workflows/{wf}/config/history"):
            if h["proposal_id"] == pid:
                self._set(pid, "applied", applied_version=h["version"])
                return True
        self._set(pid, "abandoned")
        return False

    def _set(self, pid, state, applied_version=None, note_add=None, **cols):
        row = self.st.q("SELECT note FROM proposals WHERE proposal_id=?", (pid,))
        note = json.loads(row[0][0] or "{}") if row else {}
        note.update(note_add or {})
        self.st.db.execute("UPDATE proposals SET state=?, applied_version=COALESCE(?,applied_version), "
                           "updated_minute=?, note=? WHERE proposal_id=?",
                           (state, applied_version, self.st.now, json.dumps(note, default=str), pid))

    def poll(self, now: int, live_versions: dict) -> list[dict]:
        """Advance every non-terminal proposal. Returns events for the main loop (applied/rejected/...)."""
        events: list[dict] = []
        for pid, wf, route, state, aid, base, kind in self.st.q(
                "SELECT proposal_id,wf,route,state,approval_id,base_version,kind FROM proposals "
                "WHERE state IN ('submitting','pending','approved') AND route='approval'"):
            if state == "submitting":
                # crashed between intent and answer: find it at the simulator, else re-send the identical body
                found = next((a for a in self.ctx.sim.get("/approvals", params={"workflow_id": wf})
                              if a["proposal_id"] == pid), None)
                if found:
                    self.st.db.execute("UPDATE proposals SET state='pending', approval_id=? WHERE proposal_id=?",
                                       (found["approval_id"], pid))
                    self.st.audit("proposal_reconciled", wf, f"found {found['approval_id']} after restart", {},
                                  proposal_id=pid, approval_id=found["approval_id"])
                else:
                    req = json.loads(self.st.q("SELECT request FROM proposals WHERE proposal_id=?", (pid,))[0][0])
                    cfg = json.loads(self.st.q("SELECT config FROM proposals WHERE proposal_id=?", (pid,))[0][0])
                    req["proposed_config"] = cfg
                    self._send(wf, pid, req)
                continue
            a = self.ctx.sim.get(f"/approvals/{aid}")
            status = a["status"]
            cur = live_versions.get(wf)
            if status == "pending":
                if cur is not None and cur != base:
                    self._set(pid, "pending", note_add={"stale_pending": True})
                continue
            if status in ("rejected", "expired"):
                self.st.db.execute("UPDATE proposals SET state=?, reasons=?, decided_minute=?, updated_minute=? "
                                   "WHERE proposal_id=?",
                                   (status, json.dumps(a.get("reasons", [])), now, now, pid))
                self.st.audit(f"proposal_{status}", wf, f"{aid} {status}: {a.get('reasons') or 'no reply before expiry'}",
                              {"approval": a}, proposal_id=pid, approval_id=aid, actor="approver")
                events.append({"type": status, "wf": wf, "proposal_id": pid, "reasons": a.get("reasons", []),
                               "kind": kind})
            elif status == "approved":
                self.st.db.execute("UPDATE proposals SET state='approved', decided_minute=?, apply_by=?, "
                                   "updated_minute=? WHERE proposal_id=?", (now, a.get("apply_by"), now, pid))
                if state == "pending":
                    self.st.audit("proposal_approved", wf, f"{aid} approved at {a.get('decided_at')}; "
                                  f"apply by {a.get('apply_by')}", {"approval": a}, proposal_id=pid, approval_id=aid,
                                  actor="approver")
                events.extend(self._apply(wf, pid, aid, kind))
            elif status == "applied":
                events.extend(self._record_applied(wf, pid, aid, kind))
            elif status == "lapsed":
                self._set(pid, "lapsed")
                self.st.audit("proposal_lapsed", wf, f"{aid} lapsed: not applied inside the apply window", {},
                              proposal_id=pid, approval_id=aid)
                events.append({"type": "lapsed", "wf": wf, "proposal_id": pid, "kind": kind})
        return events

    def _apply(self, wf, pid, aid, kind):
        try:
            r = self.ctx.sim.post(f"/workflows/{wf}/apply", {"approval_id": aid})
            maybe_crash(self.st, "apply")
        except SimError as e:
            if e.code == "stale_base_version":
                self._set(pid, "stale", note_add={"detail": e.detail})
                self.st.audit("proposal_stale", wf, "approved but config moved before apply: will re-propose",
                              {"detail": e.detail}, proposal_id=pid, approval_id=aid)
                return [{"type": "stale", "wf": wf, "proposal_id": pid, "kind": kind}]
            if e.code == "approval_lapsed":
                self._set(pid, "lapsed")
                self.st.audit("proposal_lapsed", wf, "apply window missed", {}, proposal_id=pid, approval_id=aid)
                return [{"type": "lapsed", "wf": wf, "proposal_id": pid, "kind": kind}]
            if e.code == "not_approved" and isinstance(e.detail, dict) and e.detail.get("status") == "applied":
                return self._record_applied(wf, pid, aid, kind)
            raise
        self._set(pid, "applied", applied_version=r["version"])
        self.st.audit("approval_applied", wf, f"v{r['version']} applied dims={r['changed_dims']}",
                      {"changed_dims": r["changed_dims"]}, proposal_id=pid, approval_id=aid, version=r["version"],
                      actor="controller_approved")
        return [{"type": "applied", "wf": wf, "proposal_id": pid, "version": r["version"], "kind": kind,
                 "dims": r["changed_dims"], "route": "approval"}]

    def _record_applied(self, wf, pid, aid, kind):
        for h in self.ctx.sim.get(f"/workflows/{wf}/config/history"):
            if h["approval_id"] == aid:
                self._set(pid, "applied", applied_version=h["version"])
                self.st.audit("approval_applied", wf, f"v{h['version']} applied (reconciled after restart)", {},
                              proposal_id=pid, approval_id=aid, version=h["version"])
                return [{"type": "applied", "wf": wf, "proposal_id": pid, "version": h["version"], "kind": kind,
                         "dims": h["changed_dims"], "route": "approval"}]
        return []

    def mark_stale_if_moved(self, wf, live_version) -> int:
        """A pending/approved request whose base is not the live version can never be applied. Record it."""
        n = 0
        for pid, base, state in self.st.q("SELECT proposal_id,base_version,state FROM proposals WHERE wf=? AND "
                                          "route='approval' AND state IN ('pending','approved')", (wf,)):
            if base != live_version and state == "approved":
                self._set(pid, "stale")
                self.st.audit("proposal_stale", wf, "approved request outdated by a config change", {},
                              proposal_id=pid)
                n += 1
        return n
