#!/usr/bin/env python3
"""
scan_blackout_history.py — Map ALL order-book liquidity blackouts over a wide
time window by replaying on-chain `getBookLevels` state at sampled historical
blocks, then collapsing consecutive empty samples into blackout windows.

Same on-chain method as replay_book_state.py (view fn @ historical blocks =
chain-authoritative, no API caching). Coarse sampling step trades edge precision
for coverage; pin exact edges afterward with find_blackout_edges.py.

Handles archive-horizon limits: if a historical state call fails (node pruned
that block), it records the earliest reachable point and stops going further back.

Run inside the container (pass --pool to skip the config import):
    docker exec dreamdex-agent python3 /app/evidence/scan_blackout_history.py \
        --pair USDC.e:USDso --pool 0x47fD2f18426f67106DBaC82F6d21D446c5F2120b \
        --hours 24 --step-blocks 1200
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


def iso(ts):
    return datetime.datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pair", default="USDC.e:USDso")
    ap.add_argument("--pool", default=None)
    ap.add_argument("--hours", type=float, default=24.0)
    ap.add_argument("--step-blocks", type=int, default=1200)
    args = ap.parse_args()

    w3 = Web3(Web3.HTTPProvider(RPC, request_kwargs={"timeout": 20}))
    pool = args.pool
    if not pool:
        import sys
        sys.path.insert(0, "/app")
        from config import MARKETS
        pool = MARKETS[args.pair]["contract"]
    c = w3.eth.contract(address=Web3.to_checksum_address(pool), abi=BOOK_ABI)

    latest = w3.eth.block_number
    t_now = w3.eth.get_block(latest)["timestamp"]
    t_old = w3.eth.get_block(latest - 500)["timestamp"]
    bt = (t_now - t_old) / 500.0
    n = int(args.hours * 3600 / bt)
    start = latest - n
    step = args.step_blocks
    print(f"pair={args.pair} pool={pool} block_time={bt:.3f}s")
    print(f"scanning blk {start}..{latest} step={step} (~{args.hours}h, "
          f"~{(latest-start)//step} samples)")

    def empty(bn):
        a = c.functions.getBookLevels(False, 1).call(block_identifier=bn)
        b = c.functions.getBookLevels(True, 1).call(block_identifier=bn)
        return len(a) == 0 and len(b) == 0

    samples = []          # (block, ts, is_empty)
    archive_floor = None
    for bn in range(start, latest + 1, step):
        try:
            e = empty(bn)
            ts = w3.eth.get_block(bn)["timestamp"]
            samples.append((bn, ts, e))
        except Exception as ex:
            if archive_floor is None:
                archive_floor = bn
                print(f"  [archive horizon ~blk {bn}: {str(ex)[:60]}]")
            continue
    if not samples:
        print("no reachable samples")
        return

    # Collapse consecutive empties into windows
    windows = []  # (start_blk, start_ts, end_blk, end_ts)
    cur = None
    for bn, ts, e in samples:
        if e and cur is None:
            cur = [bn, ts, bn, ts]
        elif e and cur is not None:
            cur[2], cur[3] = bn, ts
        elif not e and cur is not None:
            windows.append(tuple(cur)); cur = None
    if cur is not None:
        windows.append(tuple(cur) + ("ONGOING",))

    total_empty = 0.0
    print(f"\n=== blackout windows ({args.pair}) ===")
    if not windows:
        print("none in window")
    for w in windows:
        sb, st, eb, et = w[0], w[1], w[2], w[3]
        dur = (et - st) / 60.0
        total_empty += (et - st)
        ongoing = " (ONGOING)" if len(w) > 4 else ""
        print(f"{iso(st)} -> {iso(et)} UTC  ~{dur:.1f} min  "
              f"blk {sb}..{eb}{ongoing}")

    span = samples[-1][1] - samples[0][1]
    print(f"\nobserved span: {span/3600:.1f}h | blackout windows: {len(windows)} | "
          f"total empty: {total_empty/60:.1f} min "
          f"({100*total_empty/span:.1f}% of span)")
    print(f"resolution: +/- ~{step*bt:.0f}s (one sample step). "
          f"Pin exact edges with find_blackout_edges.py.")


if __name__ == "__main__":
    main()
