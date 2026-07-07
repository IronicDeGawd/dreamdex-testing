#!/bin/bash
# Direct-contract placeOrder burst — ~2x faster than the API path (no /orders
# round-trip per leg). Use when you need MAX raw-volume throughput and the WETH
# book is reasonably liquid. Falls back to pausing (spread gate) when the book
# goes wide, so it won't spin on no-fills.
#
# Requires direct_burst.py baked into the image: after editing it,
#   docker compose build agent
# then run. Detached-safe (survives SSH drop).
#
# Usage: ./direct_burst.sh [target] [leg_usd] [slip] [spread_gate_pct]
#   target          this-run volume to generate (default 100000)
#   leg_usd         $ per leg — keep BELOW free USDso or buys pre-revert (default 25)
#   slip            price cushion to cross the touch (default 0.004 = 0.4%)
#   spread_gate_pct pause trading when spread% exceeds this (default 0.15)
cd ~/dreamdex-r3/backend
TARGET=${1:-100000}; LEG=${2:-25}; SLIP=${3:-0.004}; GATE=${4:-0.15}
exec docker compose run --rm --no-deps -T \
  -e DP_TARGET=$TARGET -e DP_LEG_USD=$LEG -e DP_SLIP=$SLIP \
  -e DP_SPREAD_GATE_PCT=$GATE -e DP_SETTLE_S=1.5 -e DP_SOMI_FLOOR=3 \
  -e DP_PAUSE_S=8 -e DP_MAX_NOFILL=6 \
  agent python3 direct_burst.py
