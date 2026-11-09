"""Deterministic crash injection for the recovery test (scripts/crash_test.py). Inert unless CTRL_CRASH_PLAN is set.

CTRL_CRASH_PLAN="intent:1,sent:2,apply:1,ingest_mid:40" -> hard-kill (os._exit(137), no cleanup, like SIGKILL/OOM) the
first time `intent` is reached, then (after run.sh restarts us) the 2nd time `sent` is reached, and so on.
Progress is kept in a sidecar file, NOT the database: a point like `ingest_mid` fires inside an open transaction, and
anything written there would be rolled back by the very crash being simulated.
"""
import json
import os
import sys


def maybe_crash(store, point: str):
    plan = os.environ.get("CTRL_CRASH_PLAN")
    if not plan:
        return
    path = store.path + ".chaos"
    steps = [s.split(":") for s in plan.split(",") if s]
    try:
        st = json.load(open(path))
    except Exception:
        st = {"i": 0, "hits": 0}
    if st["i"] >= len(steps) or steps[st["i"]][0] != point:
        return
    st["hits"] += 1
    fire = st["hits"] >= int(steps[st["i"]][1])
    if fire:
        st = {"i": st["i"] + 1, "hits": 0}
    json.dump(st, open(path, "w"))
    if fire:
        print(f"[chaos] simulated kill -9 at '{point}' (sim minute {store.now})", file=sys.stderr, flush=True)
        os._exit(137)
