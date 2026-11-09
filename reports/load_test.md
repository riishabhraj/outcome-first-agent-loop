# Load test: public_03_load

12 workflows, ~650k dials/day (~850k events/day), 5 simulated days, on a laptop-class core (Apple silicon, one
controller process, SQLite). Numbers are the controller's own `loop_metrics`.

| metric | value |
|---|---|
| wall-clock for the whole run (simulator + controller, run.sh start to finish) | 207 s  (**~41 s per simulated day**, SRE target: < 600 s) |
| bootstrap: history events ingested before D0 | 5,537,531 in 38 s = **144,295 events/s** |
| live events ingested | 4,200,213 in 31 s = **134,669 events/s** (single process) |
| decision lag: tick wall time, avg / p95 / p99 / max | 237 / 1115 / 1824 / 2349 ms (a tick = 15 simulated minutes: ingest + all 12 workflows' decisions) |
| decision-only time per tick, avg / max | 158 / 2348 ms (max is the 00:00 tick: daily schedule + partition refresh for all workflows) |
| max chunk backlog at the start of any tick | 1 |
| peak RSS of the controller | 676 MB |
| store size | 81 MB |
| proposals | {'approval:applied': 58, 'approval:pending': 3, 'approval:rejected': 8, 'approval:stale': 6, 'auto:applied': 23} |

Where the time goes: the simulator itself needs ~40 s of every simulated day to generate events; the controller's ingest
is ~7 s/day and its decisions ~2 s/day. The controller is not the bottleneck at 1x; see SUBMISSION.md section 8 for 10x.
