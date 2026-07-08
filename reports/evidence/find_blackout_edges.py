#!/usr/bin/env python3
"""
find_blackout_edges.py — Pin the EXACT block where an order book transitioned
empty<->non-empty, by binary-searching `getBookLevels` over historical state.

Given a known-OK block and a known-EMPTY block bracketing a transition, this
narrows to the single block where the state flips — giving second-level
(actually sub-second, ~0.1s/block) precision on when liquidity vanished or
returned.

Used to pin the 2026-06-01 USDC.e:USDso liquidity blackout edges.

Run inside the container:
    docker exec dreamdex-agent python3 /app/evidence/find_blackout_edges.py \
        --pair USDC.e:USDso \
        --ok-block 321994893 --empty-block 321995493 \
        --empty-block2 322000053 --ok-block2 322000353
(ok-block/empty-block bracket the START of the gap; empty-block2/ok-block2
bracket the RETURN. Get rough brackets from replay_book_state.py first.)
"""
import argparse
import datetime
from web3 import Web3

RPC = "https://api.infra.mainnet.somnia.network/"
BOOK_ABI = [{
    "inputs": [{"name": "isBid", "type": "bool"},
               {"name": "numLevels", "type": "uint64"}],
    "name": "getBookLevels",
    "outputs": [{"components": [{"name": "price", "type": "uint256"},
                                {"name": "quantity", "type": "uint256"}],
                 "name": "", "type": "tuple[]"}],
    "stateMutability": "view", "type": "function",
}]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pair", default="USDC.e:USDso")
    ap.add_argument("--pool", default=None)
    ap.add_argument("--ok-block", type=int, required=True)
    ap.add_argument("--empty-block", type=int, required=True)
    ap.add_argument("--empty-block2", type=int, required=True)
    ap.add_argument("--ok-block2", type=int, required=True)
    args = ap.parse_args()

    w3 = Web3(Web3.HTTPProvider(RPC, request_kwargs={"timeout": 15}))
    pool = args.pool
    if not pool:
        import sys
        sys.path.insert(0, "/app")  # config.py lives at /app in the container
        from config import MARKETS
        pool = MARKETS[args.pair]["contract"]
    c = w3.eth.contract(address=Web3.to_checksum_address(pool), abi=BOOK_ABI)

    def empty(bn):
        a = c.functions.getBookLevels(False, 1).call(block_identifier=bn)
        b = c.functions.getBookLevels(True, 1).call(block_identifier=bn)
        return len(a) == 0 and len(b) == 0

    def ts(bn):
        return w3.eth.get_block(bn)["timestamp"]

    def hhmm(t):
        return datetime.datetime.utcfromtimestamp(t).strftime("%H:%M:%S UTC")

    # Edge 1: first EMPTY block (gap start)
    lo, hi = args.ok_block, args.empty_block
    while lo < hi:
        mid = (lo + hi) // 2
        if empty(mid):
            hi = mid
        else:
            lo = mid + 1
    e_start = lo

    # Edge 2: first OK block (liquidity return)
    lo2, hi2 = args.empty_block2, args.ok_block2
    while lo2 < hi2:
        mid = (lo2 + hi2) // 2
        if not empty(mid):
            hi2 = mid
        else:
            lo2 = mid + 1
    e_end = lo2

    t0, t1 = ts(e_start), ts(e_end)
    print(f"last OK before gap: blk {e_start-1} {hhmm(ts(e_start-1))}")
    print(f"FIRST EMPTY block:  blk {e_start} {hhmm(t0)}")
    print(f"LAST EMPTY block:   blk {e_end-1} {hhmm(ts(e_end-1))}")
    print(f"FIRST OK (return):  blk {e_end} {hhmm(t1)}")
    print(f"DURATION: {(t1-t0)/60.0:.1f} min ({e_end-e_start} blocks)")


if __name__ == "__main__":
    main()
