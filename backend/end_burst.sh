#!/bin/bash
# end_burst.sh — timed contest-finish hand-off (one-shot, fired by cron near the
# deadline). Transitions maker -> taker burst, recovering capital from the vault:
#   1) remove maker_keepalive + end_burst cron lines (one-shot)
#   2) stop the maker
#   3) cancel its open order  (frees vault-reserved funds)
#   4) withdraw vault -> wallet (auto_withdraw: base + USDso, all pools)
#   5) liquidate base -> USDso (wallet)
#   6) start the taker burst + re-enable burst_keepalive
set -u
DIR=/home/irony/dreamdex-agent
LOG="$DIR/logs/end_burst.log"
H=0xF4c825F3C2970153d78B407CF190861dd4E2b905
echo "=== $(date -u +%FT%TZ) END_BURST start ===" >>"$LOG"

# 1) one-shot: strip our own + maker keepalive cron lines
crontab -l 2>/dev/null | grep -v 'maker_keepalive\|end_burst' | crontab -

# 2) stop maker
docker exec dreamdex-agent sh -c 'for p in /proc/[0-9]*; do read c < "$p/comm" 2>/dev/null||continue; case "$c" in python*) grep -qa profit_maker "$p/cmdline" 2>/dev/null && kill "${p##*/}";; esac; done' 2>>"$LOG"
sleep 5

# 3) cancel any resting maker order -> frees vault funds
docker exec dreamdex-agent sh -c 'PROFIT_PRIVATE_KEY="$MAINNET_PRIVATE_KEY" PROFIT_ADDRESS='"$H"' PROFIT_PAIR=WETH:USDso python3 /app/profit_maker.py --shutdown' >>"$LOG" 2>&1
sleep 3

# 4) vault -> wallet (base + USDso across all pools)
docker exec -e DREAMDEX_ENV=mainnet dreamdex-agent python3 /app/auto_withdraw.py >>"$LOG" 2>&1
sleep 3

# 5) base -> USDso (wallet) so the burst starts with clean cash
docker exec -e DREAMDEX_ENV=mainnet dreamdex-agent python3 /app/liquidate_to_usdso.py >>"$LOG" 2>&1

# 5b) gas safety — the burst needs ~12 SOMI to spend the capital; if low, buy ~$2
SOMI=$(docker exec dreamdex-agent python3 -c "from web3 import Web3; from config import SOMNIA_RPC; print('%.2f'%(Web3(Web3.HTTPProvider(SOMNIA_RPC,request_kwargs={'timeout':10})).eth.get_balance(Web3.to_checksum_address('$H'))/1e18))" 2>>"$LOG")
echo "$(date -u +%FT%TZ) pre-burst SOMI=$SOMI" >>"$LOG"
if [ -n "$SOMI" ] && awk "BEGIN{exit !($SOMI < 15)}"; then
  echo "$(date -u +%FT%TZ) SOMI low -> buying \$2 gas" >>"$LOG"
  docker exec -e GAS_BUY_USD=2 dreamdex-agent python3 /app/buy_gas.py >>"$LOG" 2>&1
fi

# 6) start taker burst + keepalive
nohup "$DIR/run_direct_burst.sh" >> "$DIR/logs/direct_burst.log" 2>&1 &
sleep 3
( crontab -l 2>/dev/null; echo "*/2 * * * * $DIR/burst_keepalive.sh" ) | crontab -
echo "=== $(date -u +%FT%TZ) END_BURST done ===" >>"$LOG"
