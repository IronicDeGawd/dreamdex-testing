#!/bin/bash
# Week-2 cost-aware volume: trades only while cheap, pauses when spread/cost spikes.
# Preserves budget for the final-2-day full burst. Usage: ./cheap.sh [target] [bleed_cap] [leg_usd]
cd ~/dreamdex-r3/backend
TARGET=${1:-400000}; BLEED=${2:-40}; LEG=${3:-50}
exec docker compose run --rm --no-deps -T \
  -e CLIMB_TARGET_VOLUME=$TARGET -e CLIMB_LEG_USD=$LEG -e CLIMB_SLIP_PCT=0.003 \
  -e CLIMB_MAX_GAS_SOMI=120 -e CLIMB_SOMI_FLOOR=4 -e CLIMB_MAX_USDSO_BLEED=$BLEED \
  -e CLIMB_MAX_ITERS=40000 -e CLIMB_PAUSE_S=0 -e CLIMB_PREAPPROVE=1 \
  -e CLIMB_SPREAD_GATE_PCT=0.05 -e CLIMB_COST_CEIL_PER_1K=0.15 -e CLIMB_PAUSE_EXP_S=45 -e CLIMB_COST_WINDOW=15 \
  agent python3 volume_climb.py
