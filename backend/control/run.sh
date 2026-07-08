#!/bin/bash
# Host-run the engine-control API + dashboard.
#
# It runs on the HOST (not in a container) because it launches the engines the
# same way you do by hand — `docker compose run ... agent python3 <engine>.py`.
# A host process can shell out to docker directly; a container would need the
# docker socket + CLI baked in. Everything else (chain reads, manual trades) it
# does in-process via web3, so it reuses backend/requirements.txt plus fastapi.
#
# Secrets (CONTROL_API_KEY, wallet key, etc.) come from backend/.env — the app
# calls load_dotenv() on boot. On mainnet it refuses to start without
# CONTROL_API_KEY set (fail closed). For keyless local testing, run with
# CONTROL_MOCK=1 (stub data, no chain, no Docker).
#
# Usage:  ./control/run.sh [port]        (default 8787)
#         nohup ./control/run.sh 8787 > /tmp/control.log 2>&1 &   # detached
set -euo pipefail
cd "$(dirname "$0")/.."          # -> backend/
PORT="${1:-8787}"
VENV="control/.venv"

if [ ! -d "$VENV" ]; then
  echo "[control] creating venv…"
  python3 -m venv "$VENV"
fi
echo "[control] installing deps…"
"$VENV/bin/pip" -q install -r requirements.txt -r control/requirements.txt

echo "[control] serving on 0.0.0.0:$PORT  (mock=${CONTROL_MOCK:-0})"
exec "$VENV/bin/python3" -m uvicorn control.app:app --host 0.0.0.0 --port "$PORT"
