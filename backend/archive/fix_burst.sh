#!/bin/bash
# fix_burst.sh — recover the orphaned WETH sell order that end_burst missed, then
# relaunch the taker burst on FULL capital. No gas buy (we already have ~30 SOMI).
# Detached so a flaky SSH/Tailscale drop can't half-finish it.
set -u
DIR=/home/irony/dreamdex-agent
LOG="$DIR/logs/fix_burst.log"
ORPHAN=387381625547911232268
echo "=== $(date -u +%FT%TZ) fix_burst start ===" >>"$LOG"

# 1) pause burst keepalive so it can't relaunch mid-fix
crontab -l 2>/dev/null | grep -v 'burst_keepalive' | crontab -

# 2) stop the running burst (no nonce races while we cancel/liquidate)
pkill -9 -f direct_burst.py 2>>"$LOG"
sleep 4

# 3) cancel the orphaned WETH sell order -> frees ~$18 of WETH to the wallet
docker exec dreamdex-agent sh -c 'PROFIT_PRIVATE_KEY="$MAINNET_PRIVATE_KEY" python3 /app/cancel_order.py WETH:USDso '"$ORPHAN" >>"$LOG" 2>&1
sleep 3

# 4) liquidate freed WETH -> USDso (retry to beat the silent-reject)
for i in 1 2 3 4; do
  docker exec -e DREAMDEX_ENV=mainnet dreamdex-agent python3 /app/liquidate_to_usdso.py >>"$LOG" 2>&1
  sleep 3
done

# 4b) REVERT the $2 gas buy: sell the extra SOMI back to USDso, keep ~12 for gas
for i in 1 2 3 4; do
  docker exec -e KEEP_SOMI=12 dreamdex-agent python3 /app/sell_somi.py >>"$LOG" 2>&1 && break
  sleep 4
done

# 5) relaunch burst on full USDso ($45 legs). run_direct_burst.sh does NOT buy gas.
nohup "$DIR/run_direct_burst.sh" >> "$DIR/logs/direct_burst.log" 2>&1 &
sleep 3

# 6) re-enable burst keepalive
( crontab -l 2>/dev/null; echo "*/2 * * * * $DIR/burst_keepalive.sh" ) | crontab -
echo "=== $(date -u +%FT%TZ) fix_burst done ===" >>"$LOG"
