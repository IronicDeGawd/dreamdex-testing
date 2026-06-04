#!/bin/bash
# Keepalive wrapper for direct_burst.py. Cron checks if this is running,
# restarts if not. Reads the private key from the agent's .env so secrets
# stay out of the cron entry itself.
set -u
ENV_FILE="${ENV_FILE:-/home/irony/dreamdex-agent/.env}"
KEY=$(grep '^MAINNET_PRIVATE_KEY=' "$ENV_FILE" | cut -d= -f2-)
if [ -z "$KEY" ]; then
  echo "[run_direct_burst] no MAINNET_PRIVATE_KEY in $ENV_FILE" >&2
  exit 1
fi

# 99999 cycles ≈ days of runtime at 0.5 tx/s
exec docker exec \
    -e MAINNET_PRIVATE_KEY="$KEY" \
    -e BURST_PAIR="${BURST_PAIR:-WETH:USDso}" \
    -e BURST_USDSO="${BURST_USDSO:-6}" \
    -e BURST_CYCLES="${BURST_CYCLES:-99999}" \
    -e BURST_DELAY_MS="${BURST_DELAY_MS:-0}" \
    -e BURST_SLIPPAGE_TICKS="${BURST_SLIPPAGE_TICKS:-3}" \
    -e BURST_SOMI_GAS_RESERVE="${BURST_SOMI_GAS_RESERVE:-0.3}" \
    -e BURST_SKIP_SIM=1 \
    dreamdex-agent python3 /app/direct_burst.py
