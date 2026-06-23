#!/bin/bash
# backend/burst_autotune.sh
# Host-side cron controller for the direct burst. Every run it asks the
# in-container decision helper what to do, then acts:
#   KEEP      - do nothing
#   RESTART   - relaunch run_direct_burst.sh at the optimal leg size
#   WAIT_GAS  - log and wait (SOMI below reserve; needs a top-up)
#   STOP      - kill the burst, drop a done-flag, stop touching it (200k reached)
#
# This REPLACES the plain keepalive cron — RESTART-on-stall covers keepalive.
set -u
DIR=/home/irony/dreamdex-agent
LOG=$DIR/logs/direct_burst.log
DONE_FLAG=/tmp/burst_done

# Once we've wrapped up at the target, stay quiet until the flag is cleared.
if [ -f "$DONE_FLAG" ]; then
  exit 0
fi

LINE=$(docker exec dreamdex-agent python3 /app/burst_decide.py 2>/dev/null)
if [ -z "$LINE" ]; then
  echo "$(date -u +%FT%TZ) [autotune] decide helper returned nothing" >> "$LOG"
  exit 0
fi
echo "$(date -u +%FT%TZ) [autotune] $LINE" >> "$LOG"

ACTION=$(echo "$LINE" | grep -oP 'action=\K[A-Z_]+')
OPTIMAL=$(echo "$LINE" | grep -oP 'optimal=\K[0-9]+')

case "$ACTION" in
  STOP)
    pkill -f "python3 /app/direct_burst" 2>/dev/null
    pkill -f run_direct_burst.sh 2>/dev/null
    touch "$DONE_FLAG"
    echo "$(date -u +%FT%TZ) [autotune] TARGET reached — burst stopped, done-flag set" >> "$LOG"
    ;;
  RESTART)
    pkill -f "python3 /app/direct_burst" 2>/dev/null
    pkill -f run_direct_burst.sh 2>/dev/null
    sleep 2
    BURST_USDSO="$OPTIMAL" nohup "$DIR/run_direct_burst.sh" >> "$LOG" 2>&1 &
    echo "$(date -u +%FT%TZ) [autotune] RESTART at leg \$$OPTIMAL" >> "$LOG"
    ;;
  WAIT_GAS)
    echo "$(date -u +%FT%TZ) [autotune] WAIT_GAS — SOMI below reserve, need top-up" >> "$LOG"
    ;;
  KEEP|*)
    : # healthy, nothing to do
    ;;
esac
