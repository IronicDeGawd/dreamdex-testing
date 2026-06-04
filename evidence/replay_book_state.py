#!/usr/bin/env python3
"""
replay_book_state.py — Reconstruct dreamDEX order-book liquidity over time
from on-chain state (NOT the REST API).

Method: `getBookLevels(isBid, numLevels)` is a `view` function, so an archive
node can answer it at ANY past block via `block_identifier`. By sampling it
across a range of historical blocks we get the exact, chain-authoritative
bid/ask presence at each point in time — independent of any API caching.

Used to measure the 2026-06-01 liquidity blackout on USDC.e:USDso.

Run inside the container (has web3 + config):
    docker exec dreamdex-agent python3 /app/evidence/replay_book_state.py \
        --pair USDC.e:USDso --minutes 15 --step-blocks 300
"""
import argparse
import datetime
from web3 import Web3

RPC = "https://api.infra.mainnet.somnia.network/"

# getBookLevels is the only ABI fragment needed; price/quantity per level.
BOOK_ABI = [{
    "inputs": [{"name": "isBid", "type": "bool"},
               {"name": "numLevels", "type": "uint64"}],
    "name": "getBookLevels",
    "outputs": [{"components": [{"name": "price", "type": "uint256"},
                                {"name": "quantity", "type": "uint256"}],
                 "name": "", "type": "tuple[]"}],
    "stateMutability": "view", "type": "function",
}]


def hhmm(ts):
    return datetime.datetime.utcfromtimestamp(ts).strftime("%H:%M:%S")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pair", default="USDC.e:USDso")
    ap.add_argument("--pool", default=None,
                    help="pool address; if omitted, read from config.MARKETS")
    ap.add_argument("--minutes", type=float, default=15.0)
    ap.add_argument("--step-blocks", type=int, default=300)
    args = ap.parse_args()

    w3 = Web3(Web3.HTTPProvider(RPC, request_kwargs={"timeout": 15}))

    pool = args.pool
    if not pool:
        import sys
        sys.path.insert(0, "/app")  # config.py lives at /app in the container
        from config import MARKETS
        pool = MARKETS[args.pair]["contract"]
    c = w3.eth.contract(address=Web3.to_checksum_address(pool), abi=BOOK_ABI)

    latest = w3.eth.block_number
    # derive block time empirically
    t_now = w3.eth.get_block(latest)["timestamp"]
    t_old = w3.eth.get_block(latest - 500)["timestamp"]
    bt = (t_now - t_old) / 500.0
    print(f"pair={args.pair} pool={pool} block_time={bt:.3f}s latest={latest}")

    n = int(args.minutes * 60 / bt)
    start = latest - n
    for bn in range(start, latest + 1, args.step_blocks):
        a = c.functions.getBookLevels(False, 1).call(block_identifier=bn)
        b = c.functions.getBookLevels(True, 1).call(block_identifier=bn)
        ts = w3.eth.get_block(bn)["timestamp"]
        state = "EMPTY" if (len(a) == 0 and len(b) == 0) else "ok"
        print(f"{hhmm(ts)} blk{bn} bids={len(b)} asks={len(a)} {state}")


if __name__ == "__main__":
    main()
