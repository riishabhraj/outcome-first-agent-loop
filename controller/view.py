"""Supervisor view (C11), audit queries (C12), per-lead treatment ledger, and loop health (C14).

Everything here reads only the controller's own SQLite store (never the simulator), so the answers survive a
simulator restart and are what we would show a client's auditor.

    python -m controller why    --db state/x.db --wf nw_renewal --at "D3 14:00"
    python -m controller lead   --db state/x.db --wf nw_renewal --lead nw_renewal:l00001234
    python -m controller view   --db state/x.db [--tenant kestrel_finance]
    python -m controller status --db state/x.db
    python -m controller audit  --db state/x.db --wf ks_cancel [--kind incident] [--limit 50]

and, while the controller runs:   GET http://127.0.0.1:8701/   (HTML)   /api/view /api/why /api/status /api/alerts
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


def fmt(m) -> str:
    if m is None:
        return "-"
    d, r = divmod(int(m), 1440)
    return f"D{d} {r // 60:02d}:{r % 60:02d}"


def parse_when(s: str) -> int:
    m = re.match(r"^\s*D?(-?\d+)\s+(\d{1,2}):(\d{2})\s*$", s)
    if not m:
        return int(s)
    return int(m.group(1)) * 1440 + int(m.group(2)) * 60 + int(m.group(3))


def connect(path: str) -> sqlite3.Connection:
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
    db.row_factory = sqlite3.Row
    return db


# ---------------------------------------------------------------------------------------------- why
def why(db, wf: str, minute: int) -> dict:
    """Why did workflow `wf` look the way it did at `minute`; who approved it; what happened next."""
    cv = db.execute("SELECT * FROM config_versions WHERE wf=? AND (applied_minute IS NULL OR applied_minute<=?) "
                    "ORDER BY version DESC LIMIT 1", (wf, minute)).fetchone()
    if not cv:
        return {"error": "no config version known at that time"}
    out = {"workflow": wf, "at": fmt(minute), "version": cv["version"], "set_at": fmt(cv["applied_minute"]),
           "actor": cv["actor"], "changed_dims": json.loads(cv["changed_dims"] or "[]")}
    if cv["actor"] == "supervisor":
        out["explanation"] = "client supervisor edit (bypasses governance; recorded, not overwritten)"
    if cv["actor"] == "business_default":
        out["explanation"] = "the business's original configuration"
    pid = cv["proposal_id"]
    if pid:
        p = db.execute("SELECT * FROM proposals WHERE proposal_id=?", (pid,)).fetchone()
        if p:
            req = json.loads(p["request"] or "{}")
            out["proposal"] = {"id": pid, "route": p["route"], "kind": p["kind"], "summary": p["change_summary"],
                               "impact_score": p["impact_score"], "affected_leads": p["affected_leads"],
                               "rationale": req.get("rationale"), "diff": req.get("diff"), "dry_run": req.get("dry_run"),
                               "evidence": req.get("evidence"), "approval_id": p["approval_id"],
                               "parent_proposal": p["parent_id"]}
        trail = db.execute("SELECT sim_minute,kind,actor,summary FROM audit WHERE proposal_id=? ORDER BY id", (pid,)).fetchall()
        out["lifecycle"] = [f"{fmt(r['sim_minute'])}  {r['kind']:<20} [{r['actor']}] {r['summary'][:150]}" for r in trail]
        t0 = trail[0]["sim_minute"] if trail else minute
        ev = db.execute("SELECT sim_minute,kind,summary FROM audit WHERE wf=? AND sim_minute BETWEEN ? AND ? AND kind IN "
                        "('candidate_flagged','winner_promoted','candidate_rejected','arm_eliminated','winner_decayed',"
                        "'did_target','did_flagged','schedule_target','incident_opened','prompt_edit_validated',"
                        "'supervisor_edit','guardrail_defense') ORDER BY id", (wf, t0 - 4320, t0 + 30)).fetchall()
        out["evidence_trail"] = [f"{fmt(r['sim_minute'])}  {r['kind']:<18} {r['summary'][:140]}" for r in ev][-14:]
    nxt = db.execute("SELECT sim_minute,kind,summary FROM audit WHERE wf=? AND sim_minute>? AND sim_minute<=? AND kind IN "
                     "('verification','approval_applied','auto_applied','winner_decayed','incident_resolved','supervisor_edit',"
                     "'proposal_rejected','proposal_expired','proposal_stale') ORDER BY id LIMIT 8",
                     (wf, cv["applied_minute"] or minute, (cv["applied_minute"] or minute) + 2880)).fetchall()
    out["what_happened_next"] = [f"{fmt(r['sim_minute'])}  {r['kind']:<18} {r['summary'][:140]}" for r in nxt]
    return out


def render_why(w: dict) -> str:
    if "error" in w:
        return w["error"]
    L = [f"WHY  {w['workflow']} at {w['at']}",
         f"  config in effect: v{w['version']}  set {w['set_at']} by {w['actor']}  dims={w['changed_dims']}"]
    if "explanation" in w:
        L.append(f"  {w['explanation']}")
    p = w.get("proposal")
    if p:
        L += [f"  proposal {p['id']}  route={p['route']} kind={p['kind']}  approval={p['approval_id']}",
              f"    summary   : {p['summary']}", f"    impact    : {p['impact_score']}   affected leads: {p['affected_leads']}",
              f"    rationale : {p['rationale']}", f"    dry run   : {json.dumps(p['dry_run'], default=str)[:420]}"]
        if p.get("evidence"):
            L.append(f"    evidence  : {json.dumps(p['evidence'], default=str)[:300]}")
        L.append("  lifecycle (who did what, when):")
        L += [f"    {x}" for x in w.get("lifecycle", [])]
        L.append("  what the controller had seen before proposing:")
        L += [f"    {x}" for x in w.get("evidence_trail", [])]
    L.append("  what happened next:")
    L += [f"    {x}" for x in w.get("what_happened_next", [])] or ["    (nothing recorded yet)"]
    return "\n".join(L)


def lead_treatment(db, wf: str, lead: str, day: int | None = None) -> dict:
    """Compliance question 3: why did this customer hear this variant? (ledger: variant, cohort, config version)."""
    num = int(lead.rsplit(":l", 1)[1])
    q = "SELECT * FROM ledger WHERE wf=? AND num=?" + (" AND minute>=? AND minute<?" if day is not None else "") + " ORDER BY minute"
    args = (wf, num) + ((day * 1440, day * 1440 + 1440) if day is not None else ())
    rows = db.execute(q, args).fetchall()
    cohorts = {0: "default", **{r["code"]: r["name"] for r in db.execute("SELECT code,name FROM cohort_codes WHERE wf=?", (wf,))}}
    row = db.execute("SELECT value FROM kv WHERE key='options'").fetchone()
    opts = json.loads(row[0]).get(wf, {}) if row else {}

    def nm(dim, i):
        o = opts.get(dim, [])
        return o[i] if i < len(o) else i
    out = []
    vers = [dict(r) for r in db.execute("SELECT version,actor,changed_dims,proposal_id,approval_id,applied_minute FROM config_versions "
                                        "WHERE wf=? ORDER BY version", (wf,))]

    def origin(version, dim):
        """The config version that last changed `dim` at or before `version` (that is the decision that put it there)."""
        for v in reversed(vers):
            if v["version"] <= version and dim in json.loads(v["changed_dims"] or "[]"):
                return {"version": v["version"], "set_by": v["actor"], "at": fmt(v["applied_minute"]),
                        "proposal": v["proposal_id"], "approval": v["approval_id"]}
        return {"version": 1, "set_by": "business_default", "at": None, "proposal": None, "approval": None}
    for r in rows:
        cv = db.execute("SELECT actor,proposal_id,approval_id FROM config_versions WHERE wf=? AND version=?", (wf, r["version"])).fetchone()
        out.append({"at": fmt(r["minute"]), "config_version": r["version"], "cohort": cohorts.get(r["cohort"], r["cohort"]),
                    "variant": {"welcome_message": nm("welcome_message", r["w"]), "voice": nm("voice", r["v"]),
                                "caller_id": nm("caller_id", r["d"]), "prompt_version": nm("prompt_version", r["p"])},
                    "connected": bool(r["conn"]), "engaged": bool(r["eng"]),
                    "why_this_variant": {d: origin(r["version"], d) for d in ("cohorts", "welcome_message", "voice", "caller_id", "prompt_version")}})
    return {"workflow": wf, "lead": lead, "calls": out,
            "note": "config_set_by/proposal/approval say why that config was live; `controller why --wf --at` expands the proposal"}


# ------------------------------------------------------------------------------------------ supervisor view
def supervisor_view(db, tenant: str | None = None) -> dict:
    now = int(json.loads(db.execute("SELECT value FROM kv WHERE key='now_minute'").fetchone()[0]))
    wfs = [r["wf"] for r in db.execute("SELECT wf FROM wf_state ORDER BY wf")]
    tenants = {}
    for wf in wfs:
        ws = json.loads(db.execute("SELECT body FROM wf_state WHERE wf=?", (wf,)).fetchone()[0])
        meta_t = db.execute("SELECT details FROM audit WHERE wf=? AND kind='experiment_init' LIMIT 1", (wf,)).fetchone()
        tnt = wf.split("_")[0]
        v = {"workflow": wf, "version": ws.get("live_version"), "now": fmt(now)}
        testing, winning = [], []
        for dim, s in (ws.get("exp") or {}).items():
            for k, a in s["arms"].items():
                row = {"dim": dim, "option": a["opt"], "where": a["slice"], "vs_control": s["control"],
                       "lift": round(a.get("lift_win", a.get("lift1", a.get("lift", 0.0))), 3), "stage": a["stage"]}
                if a["stage"] == "winner":
                    winning.append(row)
                elif a["stage"] == "candidate":
                    testing.append(row)
        v["being_tested"] = testing
        v["winning"] = winning
        v["decayed_or_rolled_back"] = [{"dim": d, "option": a["opt"], "where": a["slice"]} for d, s in (ws.get("exp") or {}).items()
                                       for a in s["arms"].values() if a["stage"] == "decayed"]
        ask = db.execute("SELECT proposal_id,change_summary,impact_score,affected_leads,state,created_minute,kind FROM proposals "
                         "WHERE wf=? AND route='approval' AND state IN ('submitting','pending','approved') "
                         "ORDER BY impact_score DESC", (wf,)).fetchall()
        v["we_are_asking_you"] = [{"id": r["proposal_id"], "summary": r["change_summary"], "impact": r["impact_score"],
                                   "affected_leads": r["affected_leads"], "state": r["state"], "sent": fmt(r["created_minute"]),
                                   "kind": r["kind"]} for r in ask]
        v["waiting"] = ws.get("waiting")
        today = now // 1440
        ch = db.execute("SELECT sim_minute,kind,actor,summary FROM audit WHERE wf=? AND sim_minute>=? AND kind IN "
                        "('approval_applied','auto_applied','incident_resolved','incident_opened','supervisor_edit','proposal_rejected',"
                        "'proposal_expired','did_flagged') ORDER BY id", (wf, today * 1440)).fetchall()
        v["changed_today"] = [f"{fmt(r['sim_minute'])} {r['summary'][:140]}" for r in ch]
        v["guardrail_defense"] = bool(ws.get("guardrail_defense"))
        v["alerts_open"] = [r["message"] for r in db.execute("SELECT message FROM alerts WHERE wf=? AND resolved_minute IS NULL", (wf,))]
        tenants.setdefault(tnt, []).append(v)
    if tenant:
        tenants = {k: v for k, v in tenants.items() if tenant.startswith(k) or k in tenant}
    return {"as_of": fmt(now), "tenants": tenants}


# ----------------------------------------------------------------------------------------------- loop health
def status(db) -> dict:
    now = int(json.loads(db.execute("SELECT value FROM kv WHERE key='now_minute'").fetchone()[0]))
    m = db.execute("SELECT count(*) n, sum(events) ev, sum(ingest_ms) ims, avg(tick_ms) tick, max(tick_ms) tmax, "
                   "avg(decide_ms) dec, max(wall)-min(wall) span, max(decisions) dcs FROM loop_metrics").fetchone()
    last = db.execute("SELECT credits,wall,backlog,rss_mb FROM loop_metrics ORDER BY sim_minute DESC LIMIT 1").fetchone()
    first = db.execute("SELECT credits FROM loop_metrics ORDER BY sim_minute LIMIT 1").fetchone()
    days = max((now) / 1440, 1e-9)
    ticks = db.execute("SELECT tick_ms FROM loop_metrics ORDER BY tick_ms").fetchall()
    p95 = ticks[int(len(ticks) * 0.95) - 1]["tick_ms"] if ticks else None
    alerts = db.execute("SELECT severity,code,wf,message,sim_minute,resolved_minute FROM alerts ORDER BY id DESC LIMIT 20").fetchall()
    props = {f"{r['route']}:{r['state']}": r["n"] for r in db.execute("SELECT route,state,count(*) n FROM proposals GROUP BY 1,2")}
    return {"sim_time": fmt(now), "ticks": m["n"], "events_ingested": m["ev"],
            "ingest_events_per_sec": round(m["ev"] / (m["ims"] / 1000)) if m["ims"] else None,
            "tick_ms_avg": round(m["tick"] or 0), "tick_ms_p95": round(p95 or 0), "tick_ms_max": round(m["tmax"] or 0),
            "decision_ms_avg": round(m["dec"] or 0),
            "decisions_total": m["dcs"], "decisions_per_sim_hour": round((m["dcs"] or 0) / max(now / 60, 1), 2),
            "wall_seconds_per_sim_day": round(m["span"] / days, 1) if m["span"] else None,
            "chunk_backlog_at_last_tick": last["backlog"] if last else None,
            "peak_rss_mb": last["rss_mb"] if last else None,
            "credits_remaining": last["credits"] if last else None,
            "credits_spent": round(first["credits"] - last["credits"], 1) if first and last else None,
            "proposals": props, "recent_alerts": [dict(r) for r in alerts]}


def audit(db, wf: str | None, kind: str | None, limit: int) -> list[str]:
    q, a = "SELECT sim_minute,wf,kind,actor,summary,proposal_id FROM audit WHERE 1=1", []
    if wf:
        q += " AND wf=?"; a.append(wf)
    if kind:
        q += " AND kind LIKE ?"; a.append(f"%{kind}%")
    q += " ORDER BY id DESC LIMIT ?"; a.append(limit)
    return [f"{fmt(r['sim_minute'])} {r['wf'] or '-':<14} {r['kind']:<22} [{r['actor']}] {r['summary'][:160]}"
            for r in reversed(db.execute(q, a).fetchall())]


# --------------------------------------------------------------------------------------------------- HTTP
HTML = """<!doctype html><meta charset=utf-8><title>Controller view</title>
<style>body{font:14px system-ui;margin:20px;max-width:1100px}h2{margin:22px 0 4px}table{border-collapse:collapse;width:100%%}
td,th{border:1px solid #ddd;padding:4px 8px;text-align:left;vertical-align:top}th{background:#f4f4f4}.ask{background:#fff8e1}
.pill{padding:1px 8px;border-radius:9px;background:#eee}.page{background:#fdd}.small{color:#666;font-size:12px}</style>
<h1>What the agent is doing <span class=small>as of %(as_of)s</span></h1>%(body)s"""


def render_html(v: dict) -> str:
    out = []
    for tnt, wfs in v["tenants"].items():
        out.append(f"<h2>{html.escape(tnt)}</h2>")
        for w in wfs:
            out.append(f"<h3>{html.escape(w['workflow'])} <span class=small>config v{w['version']}</span></h3>")
            if w["we_are_asking_you"]:
                out.append("<table class=ask><tr><th>We need your decision (ranked by impact)</th><th>impact</th><th>leads</th><th>state</th></tr>" +
                           "".join(f"<tr><td>{html.escape(a['summary'])}<br><span class=small>{a['id']} sent {a['sent']}</span></td>"
                                   f"<td>{a['impact']}</td><td>{a['affected_leads']}</td><td>{a['state']}</td></tr>" for a in w["we_are_asking_you"]) + "</table>")
            else:
                out.append(f"<p>Nothing needs your decision. <span class=small>{html.escape(w['waiting'] or '')}</span></p>")
            rows = [("winning", w["winning"]), ("being tested", w["being_tested"])]
            for title, items in rows:
                if items:
                    out.append(f"<b>{title}</b><ul>" + "".join(
                        f"<li>{html.escape(i['dim'])}: <b>{html.escape(i['option'])}</b> for {html.escape(i['where'])} "
                        f"({i['lift']:+.0%} vs {html.escape(i['vs_control'])})</li>" for i in items) + "</ul>")
            if w["decayed_or_rolled_back"]:
                out.append("<b>rolled back (stopped working)</b><ul>" + "".join(
                    f"<li>{html.escape(i['dim'])} {html.escape(i['option'])} for {html.escape(i['where'])}</li>" for i in w["decayed_or_rolled_back"]) + "</ul>")
            if w["changed_today"]:
                out.append("<b>changed today</b><ul>" + "".join(f"<li>{html.escape(c)}</li>" for c in w["changed_today"]) + "</ul>")
            for a in w["alerts_open"]:
                out.append(f"<p class=page>{html.escape(a)}</p>")
    return HTML % {"as_of": v["as_of"], "body": "".join(out)}


def serve(ctl, port: int):
    if not port:
        return
    path = ctl.store.path

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            u = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            db = connect(path)
            try:
                if u.path == "/":
                    body, ct = render_html(supervisor_view(db, q.get("tenant"))), "text/html"
                elif u.path == "/api/view":
                    body, ct = json.dumps(supervisor_view(db, q.get("tenant"))), "application/json"
                elif u.path == "/api/why":
                    body, ct = json.dumps(why(db, q["wf"], parse_when(q["at"])), default=str), "application/json"
                elif u.path == "/api/status":
                    body, ct = json.dumps(status(db), default=str), "application/json"
                elif u.path == "/api/alerts":
                    body, ct = json.dumps([dict(r) for r in db.execute("SELECT * FROM alerts WHERE resolved_minute IS NULL")]), "application/json"
                else:
                    self.send_error(404)
                    return
            except Exception as e:  # never let a view request affect the control loop
                self.send_error(500, str(e)[:100])
                return
            finally:
                db.close()
            self.send_response(200)
            self.send_header("Content-Type", ct)
            self.end_headers()
            self.wfile.write(body.encode())

    try:
        srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    except OSError:
        print(f"controller: view port {port} busy; continuing without the HTTP view", flush=True)
        return
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print(f"controller: supervisor view on http://127.0.0.1:{port}/", flush=True)


# ------------------------------------------------------------------------------------------------------ CLI
def cli(argv):
    ap = argparse.ArgumentParser("controller")
    ap.add_argument("cmd", choices=["why", "view", "status", "audit", "lead"])
    ap.add_argument("--db", required=True)
    ap.add_argument("--wf")
    ap.add_argument("--at")
    ap.add_argument("--lead")
    ap.add_argument("--day", type=int)
    ap.add_argument("--tenant")
    ap.add_argument("--kind")
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    db = connect(a.db)
    if a.cmd == "why":
        w = why(db, a.wf, parse_when(a.at))
        print(json.dumps(w, indent=1, default=str) if a.json else render_why(w))
    elif a.cmd == "lead":
        print(json.dumps(lead_treatment(db, a.wf, a.lead, a.day), indent=1, default=str))
    elif a.cmd == "view":
        print(json.dumps(supervisor_view(db, a.tenant), indent=1, default=str))
    elif a.cmd == "status":
        print(json.dumps(status(db), indent=1, default=str))
    elif a.cmd == "audit":
        print("\n".join(audit(db, a.wf, a.kind, a.limit)))
