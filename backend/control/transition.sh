#!/bin/bash
# One-shot R4 handover — run from cron every 10 min:
#   */10 * * * * /home/irony/dreamdex-r3/backend/control/transition.sh
#
# When the LEADERBOARD total volume crosses TRIGGER, stop the taker run
# (flattens) and launch MAKER mode. Fires exactly once (marker file), refuses
# to touch an already-running maker, and retries the launch on the next cron
# if it failed. Log: /tmp/transition.log
cd "$(dirname "$0")/.." || exit 1
MARKER=control/state/transition.done
[ -f "$MARKER" ] && exit 0
PORT="${CONTROL_PORT:-8787}"
TRIGGER="${TRANSITION_TRIGGER:-600000}"
K=$(grep "^CONTROL_API_KEY=" .env 2>/dev/null | cut -d= -f2-)
[ -z "$K" ] && { echo "$(date -Is) no CONTROL_API_KEY" >> /tmp/transition.log; exit 0; }

MODE=$(curl -s -m 20 -H "X-API-Key: $K" "http://127.0.0.1:$PORT/status" | grep -oP '"mode":"\K[^"]+')
if [ "$MODE" = "maker" ]; then
    echo "$(date -Is) maker already running — marking done" >> /tmp/transition.log
    touch "$MARKER"; exit 0
fi

VOL=$(curl -s -m 20 -H "X-API-Key: $K" "http://127.0.0.1:$PORT/leaderboard" | grep -oP '"my_volume":\K[0-9.]+')
[ -z "$VOL" ] && { echo "$(date -Is) no volume read" >> /tmp/transition.log; exit 0; }
awk "BEGIN{exit !($VOL >= $TRIGGER)}" || exit 0   # below trigger: stay quiet

echo "$(date -Is) TRIGGER: vol=$VOL >= $TRIGGER (mode=$MODE) — stop taker, launch maker" >> /tmp/transition.log
R=$(curl -s -m 150 -X POST -H "X-API-Key: $K" "http://127.0.0.1:$PORT/stop")
echo "$(date -Is) stop: $R" >> /tmp/transition.log
sleep 20
# R4 maker params: WETH (the capturable book), $25 legs (capture > gas), $40
# inventory cap, $3 true-capital bleed guard, inv_floor 0 (R4 scores free
# USDso — unwind fully), target 0 = run until stopped.
L=$(curl -s -m 90 -X POST -H "X-API-Key: $K" -H "Content-Type: application/json" \
  -d '{"mode":"maker","target":0,"leg":25,"pair":"WETH:USDso","bleed_cap":3,"cap":40,"inv_floor":0}' \
  "http://127.0.0.1:$PORT/launch")
echo "$(date -Is) launch: $L" >> /tmp/transition.log
if echo "$L" | grep -q '"ok":true'; then
    touch "$MARKER"
    echo "$(date -Is) HANDOVER COMPLETE — maker mode live" >> /tmp/transition.log
else
    echo "$(date -Is) LAUNCH FAILED — will retry next cron" >> /tmp/transition.log
fi
