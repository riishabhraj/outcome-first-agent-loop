#!/usr/bin/env python
"""Crash-recovery demonstration (C13).

Runs public_02 end to end while the controller is killed repeatedly, with no manual steps:
  * 4 injected hard-kills (os._exit(137), identical to SIGKILL/OOM) at the dangerous instants:
      intent    - approval/auto-apply intent row written, HTTP call not yet made
      sent      - simulator already accepted the request, the answer not yet recorded
      apply     - simulator already applied the approved change, not yet recorded
      ingest_mid- inside the chunk transaction (must roll back and be re-read)
  * 2 external SIGKILLs at fixed simulated times (D2 11:00, D6 12:00)
./run.sh restarts the process; the simulator keeps its clock. Afterwards we check invariants against the simulator's own
log (ground truth) and the controller's store.

    python scripts/crash_test.py [scenario] [port]       ->  reports/crash_recovery.md
"""
import json
import os
import signal
import sqlite3
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
SCEN = sys.argv[1] if len(sys.argv) > 1 else "public_02_incidents"
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 8702
PY = str(ROOT / ".venv/bin/python") if (ROOT / ".venv/bin/python").exists() else sys.executable
URL = f"http://127.0.0.1:{PORT}"
RUN = ROOT / "runs" / f"crash_{SCEN}"
DB = ROOT / "state" / f"crash_{SCEN}.db"
EXTERNAL_KILLS = [2 * 1440 + 11 * 60, 6 * 1440 + 12 * 60]
PLAN = "intent:2,sent:2,apply:2,ingest_mid:120"


def sh(cmd, **kw):
    return subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, **kw)


def main():
    subprocess.run("pkill -f 'sim serve.*--port %d'" % PORT, shell=True)
    for p in (DB, Path(str(DB) + "-wal"), Path(str(DB) + "-shm"), Path(str(DB) + ".chaos")):
        p.unlink(missing_ok=True)
    if RUN.exists():
        sh(["rm", "-rf", str(RUN)])
    base = ROOT / "runs" / f"base_{SCEN}"
    if not base.exists():
        sh([PY, "-m", "sim", "baseline", "--scenario", f"scenarios/{SCEN}.yaml", "--run-dir", str(base)])
    sim = subprocess.Popen([PY, "-m", "sim", "serve", "--scenario", f"scenarios/{SCEN}.yaml", "--run-dir", str(RUN),
                            "--port", str(PORT), "--force"], cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(240):
        try:
            if httpx.get(URL + "/health", timeout=1).status_code == 200:
                break
        except Exception:
            time.sleep(1)
    env = dict(os.environ, CTRL_DB=str(DB), CTRL_CRASH_PLAN=PLAN, CTRL_VIEW_PORT="0")
    log = open(ROOT / "runs" / f"crash_{SCEN}.controller.log", "w")
    runner = subprocess.Popen(["./run.sh", URL], cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
    external_done = []
    t0 = time.time()
    while runner.poll() is None:
        time.sleep(0.3)
        try:
            st = httpx.get(URL + "/sim/state", timeout=5).json()
        except Exception:
            continue
        for k in EXTERNAL_KILLS:
            if st["sim_minute"] >= k and k not in external_done:
                pids = sh(["pgrep", "-f", f"controller --url {URL}"]).stdout.split()
                for pid in pids:
                    try:
                        os.kill(int(pid), signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                external_done.append(k)
                log.write(f"[crash_test] external SIGKILL at sim minute {st['sim_minute']} (pids {pids})\n")
                log.flush()
        if time.time() - t0 > 1500:
            runner.kill()
            raise SystemExit("timeout")
    rc = runner.returncode
    final = httpx.get(URL + "/sim/state", timeout=5).json()
    # every proposal our store still considers open must be exactly what the simulator says it is (nothing forgotten)
    open_rows = sqlite3.connect(DB).execute(
        "SELECT proposal_id,approval_id,state FROM proposals WHERE state IN ('pending','approved')").fetchall()
    open_bad = []
    for pid, aid, state in open_rows:
        sim_status = httpx.get(f"{URL}/approvals/{aid}", timeout=5).json().get("status") if aid else None
        if sim_status != state:
            open_bad.append((pid, aid, state, sim_status))
    sim.terminate()
    time.sleep(1)
    grade = sh([PY, "-m", "sim", "grade", "--run", str(RUN), "--baseline", str(base), "--json"])
    g = json.loads(grade.stdout)
    # ---- invariants from the simulator's own log (ground truth) and the store
    simlog = [json.loads(l) for l in (RUN / "sim_log.jsonl").read_text().splitlines() if l.strip()]
    kinds = Counter(r["kind"] for r in simlog)
    req_ids = [r["proposal_id"] for r in simlog if r["kind"] == "approval_requested"]
    applied = [r["approval_id"] for r in simlog if r["kind"] == "approval_applied"]
    db = sqlite3.connect(DB)
    states = Counter(r[0] for r in db.execute("SELECT state FROM proposals"))
    submitting_left = states.get("submitting", 0)
    rows = {r[0]: r[1] for r in db.execute("SELECT approval_id,state FROM proposals WHERE approval_id IS NOT NULL")}
    mismatch = [a for a in applied if rows.get(a) != "applied"]
    reconciled = db.execute("SELECT count(*) FROM audit WHERE kind IN ('proposal_reconciled','auto_reconciled')").fetchone()[0]
    restarts = sum(1 for l in open(ROOT / "runs" / f"crash_{SCEN}.controller.log") if l.startswith("[run.sh] controller exited"))
    chaos_hits = sum(1 for l in open(ROOT / "runs" / f"crash_{SCEN}.controller.log") if "[chaos]" in l)
    ext = sum(1 for l in open(ROOT / "runs" / f"crash_{SCEN}.controller.log") if "external SIGKILL" in l)
    checks = {
        "run finished with exit 0 and the scenario reached its end": rc == 0 and final["finished"],
        "no approval request sent twice (unique proposal ids == requests)": len(req_ids) == len(set(req_ids)),
        "no change applied twice (applied approvals unique)": len(applied) == len(set(applied)),
        "every applied approval is recorded as applied in our store": not mismatch,
        "no proposal left in 'submitting' (intent never resolved)": submitting_left == 0,
        "no approval lapsed because of a restart": kinds.get("approval_lapsed", 0) == 0,
        "no auto-apply refused (no duplicate/blind re-send)": kinds.get("auto_apply_rejected", 0) == 0,
        f"open proposals at the end ({len(open_rows)}) match the simulator's view exactly (none forgotten)": not open_bad,
    }
    ok = all(checks.values())
    kp = g["kpis"]["ALL"]
    out = [f"# Crash-recovery test: {SCEN}", "",
           f"Injected hard-kills (plan `{PLAN}`): **{chaos_hits}** of 4 fired; external SIGKILLs: **{ext}**; "
           f"supervisor restarts by run.sh: **{restarts}**.", "",
           "| check | result |", "|---|---|"]
    out += [f"| {k} | {'PASS' if v else '**FAIL**'} |" for k, v in checks.items()]
    out += ["", f"Simulator ground truth: {kinds.get('approval_requested', 0)} approval requests, "
                f"{kinds.get('approval_applied', 0)} applied via approval, {kinds.get('auto_applied', 0)} auto-applied, "
                f"{kinds.get('apply_conflict', 0)} stale-base conflicts, {kinds.get('approval_lapsed', 0)} lapsed.",
            f"Controller store: proposal states {dict(states)}; {reconciled} intents reconciled after restart.", "",
            f"Outcome despite {restarts} restarts: conclusion rate {kp['conclusion_rate']:.3f}, pickup {kp['pickup_rate']:.3f}, "
            f"engagement {kp['engagement_rate']:.3f}; incidents: {json.dumps([i for i in g['incidents'] if 'mttr_minutes' in i])}.",
            "", "State lost on a crash: nothing durable. In-flight HTTP responses are lost and recovered by reconciling "
            "intent rows against the simulator (GET /approvals, config history). The only thing recomputed is the "
            "current tick's decision, which is idempotent.", "", f"**Overall: {'PASS' if ok else 'FAIL'}**"]
    (ROOT / "reports").mkdir(exist_ok=True)
    (ROOT / "reports" / "crash_recovery.md").write_text("\n".join(out) + "\n")
    print("\n".join(out))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
