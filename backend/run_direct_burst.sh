#!/bin/bash
# Launch wrapper for direct_burst.py. The container already holds
# MAINNET_PRIVATE_KEY + DREAMDEX_ENV via compose env_file, so we do NOT pass the
# key on the command line — that keeps it out of the host process list (ps/pgrep).
# Round 2 defaults: USDC.e:USDso (tightest spread + deepest book), $40 legs
# (big legs = few txs = low gas), skip-sim, 3-tick slippage.
set -u
exec docker exec \
    -e BURST_PAIR="${BURST_PAIR:-USDC.e:USDso}" \
    -e BURST_USDSO="${BURST_USDSO:-45}" \
    -e BURST_CYCLES="${BURST_CYCLES:-99999}" \
    -e BURST_DELAY_MS="${BURST_DELAY_MS:-0}" \
    -e BURST_SLIPPAGE_TICKS="${BURST_SLIPPAGE_TICKS:-3}" \
    -e BURST_SOMI_GAS_RESERVE="${BURST_SOMI_GAS_RESERVE:-1.0}" \
    -e BURST_BAL_REFRESH_EVERY="${BURST_BAL_REFRESH_EVERY:-3}" \
    -e BURST_SKIP_SIM=1 \
    dreamdex-agent python3 /app/direct_burst.py
