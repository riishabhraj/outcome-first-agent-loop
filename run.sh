#!/usr/bin/env bash
# Controller entrypoint. Contract: ./run.sh <SIM_URL> runs until the scenario finishes, no manual steps.
#
# Runtime: plain Python 3.11+ with numpy + httpx (requirements.txt). No Docker, no extra services: all state is one
# SQLite file (CTRL_DB, default state/controller.db). If the process dies for any reason (OOM, node recycle, deploy)
# this wrapper restarts it and it resumes from the store; the simulator keeps its own clock.
# A state file left over from a different simulator run is detected (clock went backwards / different scenario) and
# archived, never reused.
set -uo pipefail
SIM_URL="${1:?usage: ./run.sh <SIM_URL>}"
cd "$(dirname "$0")"
PY="${PYTHON:-python3}"
[ -x .venv/bin/python ] && PY=.venv/bin/python
export CTRL_DB="${CTRL_DB:-state/controller.db}"
mkdir -p "$(dirname "$CTRL_DB")"
fails=0
while true; do
  "$PY" -m controller --url "$SIM_URL" --db "$CTRL_DB"
  rc=$?
  if [ $rc -eq 0 ]; then exit 0; fi
  if [ $rc -eq 143 ]; then echo "[run.sh] controller stopped by SIGTERM; not restarting" >&2; exit 143; fi
  fails=$((fails + 1))
  echo "[run.sh] controller exited rc=$rc (restart #$fails); resuming from $CTRL_DB" >&2
  [ $fails -ge 50 ] && { echo "[run.sh] giving up after 50 restarts" >&2; exit 1; }
  sleep 2
done
