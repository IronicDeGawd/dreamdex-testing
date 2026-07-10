#!/bin/bash
# Engine watchdog — run from cron every 15 min:
#   */15 * * * * /home/irony/dreamdex-r3/backend/control/watchdog.sh
#
# Hits POST /autorestart, which relaunches the last run ONLY when it died
# unexpectedly (crash, host reboot, docker/RPC kill — i.e. no "=== STOP:" line in
# its log). It deliberately does NOT restart when:
#   • you stopped it from the dashboard (/stop clears the autorestart flag)
#   • the engine self-stopped for a real reason (target reached, bleed/gas cap,
#     trade-failure breaker, startup abort)
# Network/RPC blips no longer kill the engine at all — it backs off and retries —
# so this is the second line of defence, not the first.
cd "$(dirname "$0")/.." || exit 1
PORT="${CONTROL_PORT:-8787}"
K=$(grep "^CONTROL_API_KEY=" .env 2>/dev/null | cut -d= -f2-)
[ -z "$K" ] && { echo "$(date -Is) no CONTROL_API_KEY" >> /tmp/watchdog.log; exit 0; }
R=$(curl -s -m 25 -X POST -H "X-API-Key: $K" "http://127.0.0.1:$PORT/autorestart" 2>&1)
echo "$(date -Is) $R" >> /tmp/watchdog.log
