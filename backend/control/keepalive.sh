#!/bin/bash
# Idle-DQ keepalive — run from cron hourly:
#   0 * * * * /home/irony/dreamdex-r3/backend/control/keepalive.sh
#
# Hits POST /keepalive, which places a tiny SOMI buy ONLY when the wallet's
# lifetime volume hasn't moved for ~20h (contest rule: >24h idle = DQ). While
# an engine is running it does nothing. The buy doubles as a gas top-up, so a
# fired keepalive costs effectively nothing. The danger window this covers is
# right after a self-stop (target reached / breaker) when nobody is watching.
cd "$(dirname "$0")/.." || exit 1
PORT="${CONTROL_PORT:-8787}"
K=$(grep "^CONTROL_API_KEY=" .env 2>/dev/null | cut -d= -f2-)
[ -z "$K" ] && { echo "$(date -Is) no CONTROL_API_KEY" >> /tmp/keepalive.log; exit 0; }
# Generous timeout: a fired keepalive waits for a real tx receipt (up to ~2 min).
R=$(curl -s -m 180 -X POST -H "X-API-Key: $K" "http://127.0.0.1:$PORT/keepalive" 2>&1)
echo "$(date -Is) $R" >> /tmp/keepalive.log
