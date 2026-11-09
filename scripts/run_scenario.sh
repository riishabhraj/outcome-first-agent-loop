#!/usr/bin/env bash
# Serve a scenario, run the controller against it, grade it. Usage: scripts/run_scenario.sh public_01_dev [port]
# Produces runs/mine_<name>, state/<name>.db, reports/<name>.md (needs runs/base_<name>; created if missing).
set -euo pipefail
cd "$(dirname "$0")/.."
NAME="${1:?scenario name, e.g. public_01_dev}"
PORT="${2:-8700}"
PY=python; [ -x .venv/bin/python ] && PY=.venv/bin/python
[ -d "runs/base_$NAME" ] || "$PY" -m sim baseline --scenario "scenarios/$NAME.yaml" --run-dir "runs/base_$NAME"
pkill -f "sim serve.*--port $PORT" 2>/dev/null || true
rm -rf "runs/mine_$NAME" "state/$NAME.db" "state/$NAME.db-wal" "state/$NAME.db-shm"
mkdir -p state reports
"$PY" -m sim serve --scenario "scenarios/$NAME.yaml" --run-dir "runs/mine_$NAME" --port "$PORT" --force > "runs/serve_$NAME.log" 2>&1 &
SERVE=$!
trap 'kill $SERVE 2>/dev/null || true' EXIT
for i in $(seq 1 240); do curl -sf "http://127.0.0.1:$PORT/health" >/dev/null && break; sleep 1; done
START=$(date +%s)
CTRL_DB="state/$NAME.db" CTRL_VIEW_PORT=0 ./run.sh "http://127.0.0.1:$PORT"
echo "wall seconds: $(( $(date +%s) - START ))"
"$PY" -m sim grade --run "runs/mine_$NAME" --baseline "runs/base_$NAME" --out "reports/$NAME.md" > /dev/null
echo "report: reports/$NAME.md"
