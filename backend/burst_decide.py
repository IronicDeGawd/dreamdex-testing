#!/usr/bin/env python3
# backend/burst_decide.py
"""
Decision helper for the burst autotuner. Runs INSIDE the container (has web3
+ requests). Reads on-chain balances, the live burst stats file, and the
leaderboard, then prints ONE parseable line the host cron acts on:

  usdso=.. somi=.. weth_usd=.. vol=.. curleg=.. optimal=.. lastage=.. action=KEEP|RESTART|WAIT_GAS|STOP

Actions:
  KEEP      - burst healthy, leg fine, nothing to do
  RESTART   - burst stalled (no action >60s) OR leg should change ≥$2; relaunch at `optimal`
  WAIT_GAS  - SOMI below reserve; can't trade, wait for a top-up
  STOP      - leaderboard volume ≥ TARGET; wrap up, kill burst, do not restart
"""
import json
import time
import os
import requests
from web3 import Web3

RPC = "https://api.infra.mainnet.somnia.network/"
A = Web3.to_checksum_address("0xF4c825F3C2970153d78B407CF190861dd4E2b905")
USDSO = Web3.to_checksum_address("0x00000022dA000002656c64D9eA6011ea952D008A")
WETH = Web3.to_checksum_address("0x936Ab8C674bcb567CD5dEB85D8A216494704E9D8")
STATS_PATH = os.environ.get("BURST_STATS_PATH", "/tmp/direct_burst_stats.json")
LEADERBOARD = "https://dreamdex-leaderboard-super-cool.vercel.app/api/leaderboard"

TARGET_VOLUME = float(os.environ.get("BURST_TARGET_VOLUME", "200000"))
GAS_RESERVE = float(os.environ.get("BURST_SOMI_GAS_RESERVE", "0.3"))
LEG_MIN = float(os.environ.get("BURST_LEG_MIN", "3"))
LEG_MAX = float(os.environ.get("BURST_LEG_MAX", "10"))
STALL_SECS = float(os.environ.get("BURST_STALL_SECS", "60"))
LEG_DRIFT = float(os.environ.get("BURST_LEG_DRIFT", "2"))

ERC = [{"name": "balanceOf", "type": "function", "stateMutability": "view",
        "inputs": [{"name": "a", "type": "address"}],
        "outputs": [{"name": "", "type": "uint256"}]}]


def main():
    w3 = Web3(Web3.HTTPProvider(RPC, request_kwargs={"timeout": 8}))
    usdso = w3.eth.contract(address=USDSO, abi=ERC).functions.balanceOf(A).call() / 1e18
    somi = w3.eth.get_balance(A) / 1e18

    # WETH USD value (best-effort price)
    weth_qty = w3.eth.contract(address=WETH, abi=ERC).functions.balanceOf(A).call() / 1e18
    wpx = 2028.0
    try:
        b = requests.get("https://api.dreamdex.io/v0/orderbooks?symbols=WETH:USDso", timeout=6).json()["orderbooks"][0]
        wpx = (float(b["bids"][0]["price"]) + float(b["asks"][0]["price"])) / 2
    except Exception:
        pass
    weth_usd = weth_qty * wpx

    # Current burst leg + freshness from stats file
    curleg = 0.0
    last_age = 9e9
    try:
        with open(STATS_PATH) as fh:
            st = json.load(fh)
        curleg = float(st.get("leg_size", 0))
        last_age = time.time() - float(st.get("last_action_ts", 0))
    except Exception:
        pass

    # Our leaderboard volume
    vol = 0.0
    try:
        d = requests.get(LEADERBOARD, timeout=6).json()
        for t in d.get("traders", []):
            if t.get("address", "").lower() == A.lower():
                vol = float(t.get("volumeUsdso", 0))
                break
    except Exception:
        pass

    # Optimal leg: base on TOTAL trading capital (USDso + WETH), not the
    # instantaneous USDso — the two cycle rapidly so a wallet snapshot is
    # noisy and would thrash the leg size.
    #
    # Size to ~85% of total capital (the biggest leg that won't deadlock —
    # after a full swing one side holds ~all the capital, so it can always
    # fire). Bigger legs are BETTER here: gas is the binding constraint and a
    # skipped leg costs ZERO gas, so maximizing volume-per-SENT-tx (big leg)
    # beats minimizing skips (small leg). Clamped to [LEG_MIN, LEG_MAX].
    total_capital = usdso + weth_usd
    optimal = max(LEG_MIN, min(LEG_MAX, round(total_capital * 0.85)))

    # Decide
    if vol >= TARGET_VOLUME:
        action = "STOP"
    elif somi < GAS_RESERVE:
        action = "WAIT_GAS"
    elif last_age > STALL_SECS:
        action = "RESTART"
    elif abs(optimal - curleg) >= LEG_DRIFT:
        action = "RESTART"
    else:
        action = "KEEP"

    print(f"usdso={usdso:.2f} somi={somi:.2f} weth_usd={weth_usd:.2f} "
          f"vol={vol:.0f} curleg={curleg:.0f} optimal={optimal:.0f} "
          f"lastage={last_age:.0f} action={action}")


if __name__ == "__main__":
    main()
