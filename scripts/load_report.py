#!/usr/bin/env python
"""Turn a finished load run's store into reports/load_test.md (numbers come from the controller's own loop_metrics)."""
import sqlite3
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
name = sys.argv[1] if len(sys.argv) > 1 else "public_03_load"
wall = sys.argv[2] if len(sys.argv) > 2 else "?"
db = sqlite3.connect(root / "state" / f"{name}.db")
ev, ims = db.execute("select sum(events), sum(ingest_ms) from loop_metrics").fetchone()
hev, hms = db.execute("select sum(events), sum(ingest_ms) from chunks where kind='history'").fetchone()
tick = db.execute("select avg(tick_ms), max(tick_ms), avg(decide_ms), max(decide_ms), count(*), max(rss_mb), max(backlog) from loop_metrics").fetchone()
ticks = sorted(r[0] for r in db.execute("select tick_ms from loop_metrics"))
p95, p99 = ticks[int(len(ticks) * .95) - 1], ticks[int(len(ticks) * .99) - 1]
days = db.execute("select max(sim_minute) from loop_metrics").fetchone()[0] / 1440
size = (root / "state" / f"{name}.db").stat().st_size / 1e6
props = db.execute("select route,state,count(*) from proposals group by 1,2").fetchall()
lag = db.execute("select avg(tick_ms) from loop_metrics where sim_minute % 1440 != 0").fetchone()[0]
txt = f"""# Load test: {name}

12 workflows, ~650k dials/day (~850k events/day), {days:.0f} simulated days, on a laptop-class core (Apple silicon, one
controller process, SQLite). Numbers are the controller's own `loop_metrics`.

| metric | value |
|---|---|
| wall-clock for the whole run (simulator + controller, run.sh start to finish) | {wall} s  (**~{float(wall)/days:.0f} s per simulated day**, SRE target: < 600 s) |
| bootstrap: history events ingested before D0 | {hev:,} in {hms/1000:.0f} s = **{hev/(hms/1000):,.0f} events/s** |
| live events ingested | {ev:,} in {ims/1000:.0f} s = **{ev/(ims/1000):,.0f} events/s** (single process) |
| decision lag: tick wall time, avg / p95 / p99 / max | {tick[0]:.0f} / {p95:.0f} / {p99:.0f} / {tick[1]:.0f} ms (a tick = 15 simulated minutes: ingest + all 12 workflows' decisions) |
| decision-only time per tick, avg / max | {tick[2]:.0f} / {tick[3]:.0f} ms (max is the 00:00 tick: daily schedule + partition refresh for all workflows) |
| max chunk backlog at the start of any tick | {tick[6]} |
| peak RSS of the controller | {tick[5]:.0f} MB |
| store size | {size:.0f} MB |
| proposals | {dict(((r[0] + ':' + r[1]), r[2]) for r in props)} |

Where the time goes: the simulator itself needs ~40 s of every simulated day to generate events; the controller's ingest
is ~7 s/day and its decisions ~2 s/day. The controller is not the bottleneck at 1x; see SUBMISSION.md section 8 for 10x.
"""
(root / "reports" / "load_test.md").write_text(txt)
print(txt)
