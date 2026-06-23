#!/bin/bash
# Keepalive for the direct-RPC burst. Cron runs this every few minutes; if the
# burst isn't alive, relaunch it. Detection is HOST-side: the
# `docker exec ... python3 /app/direct_burst.py` process is visible here
# (pgrep isn't installed inside the slim container).
set -u
DIR=/home/irony/dreamdex-agent
LOG="$DIR/logs/direct_burst.log"
mkdir -p "$DIR/logs"

if pgrep -f "/app/direct_burst.py" >/dev/null 2>&1; then
  exit 0
fi

echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) [keepalive] burst not running — relaunching" >> "$LOG"
nohup "$DIR/run_direct_burst.sh" >> "$LOG" 2>&1 &
