#!/usr/bin/env python3
"""
scrape_trades.py — Pull our COMPLETE on-chain trade history (incl. the ~43k
direct-burst txs that never hit the backend DB) into a local SQLite store, then
analyse it for (a) per-pair / fill stats and (b) burst-stall gaps that mark when
liquidity vanished — i.e. likely market-maker outages.

Why this exists: the burst engine (direct_burst.py) fires straight at the chain
over raw RPC and does NOT log to agent.db, so agent.db only holds ~8k REST-routed
trades. The truth lives on-chain. We pull it via the Somnia mainnet Blockscout
txlist API (fast, paginated) instead of scanning millions of 0.1s blocks.

Key idea for outage detection: when the order book is empty the burst SKIPS
(sends no tx at all), so a market-maker outage appears as a TIME GAP in our tx
stream, not as failed txs. The analyser finds those gaps and you can cross-check
them against evidence/LIQUIDITY-BLACKOUT.md.

Usage (inside the container so config.MARKETS is importable):
    docker exec dreamdex-agent python3 /app/analysis/scrape_trades.py --scrape
    docker exec dreamdex-agent python3 /app/analysis/scrape_trades.py --analyze
    docker exec dreamdex-agent python3 /app/analysis/scrape_trades.py --scrape --analyze
"""
import argparse
import calendar
import datetime
import sqlite3
import time

import requests

WALLET = "0x0000000000000000000000000000000000000000"
EXPLORER = "https://mainnet.somnia.w3us.site"
DB = "/app/data/onchain_trades.db"

# Function selectors we care about (first 4 bytes of tx input).
SELECTORS = {
    "0x1c792779": "placeTakerOrderWithoutVault",  # burst IOC, wallet-funded
    "0x4e978373": "placeOrder",                    # vault-funded
    "0x095ea7b3": "approve",
}


def pool_pair_map():
    """pool address (lower) -> pair symbol, from config.MARKETS."""
    import sys
    sys.path.insert(0, "/app")
    from config import MARKETS
    m = {}
    for pair, mk in MARKETS.items():
        c = mk.get("contract")
        if c:
            m[c.lower()] = {"pair": pair,
                            "baseDec": int(mk.get("baseDecimals", 18)),
                            "quoteDec": int(mk.get("quoteDecimals", 18))}
    return m


def init_db():
    con = sqlite3.connect(DB)
    con.execute("""
        CREATE TABLE IF NOT EXISTS onchain_tx (
            hash TEXT PRIMARY KEY,
            block INTEGER,
            ts INTEGER,
            to_addr TEXT,
            pair TEXT,
            selector TEXT,
            action TEXT,        -- buy | sell | approve | other
            side_is_bid INTEGER,
            price_raw TEXT,
            qty_raw TEXT,
            gas_used INTEGER,
            gas_price INTEGER,
            status TEXT,        -- ok | reverted
            filled INTEGER,     -- 1 if token transfers present (a real fill), else 0
            value TEXT
        )""")
    con.execute("CREATE INDEX IF NOT EXISTS idx_ts ON onchain_tx(ts)")
    con.commit()
    return con


def decode_taker_input(inp):
    """Decode placeTakerOrderWithoutVault calldata -> (is_bid, price_raw, qty_raw).
    Args: (bool isBid, uint64 userData, uint256 price, uint256 quantity, ...).
    ABI-encoded args are 32-byte words after the 4-byte selector."""
    try:
        data = inp[10:]  # strip 0x + selector
        words = [data[i:i + 64] for i in range(0, len(data), 64)]
        is_bid = int(words[0], 16) == 1
        price_raw = int(words[2], 16)
        qty_raw = int(words[3], 16)
        return is_bid, price_raw, qty_raw
    except Exception:
        return None, None, None


def _iso_to_epoch(s):
    # "2026-06-01T15:15:18.000000Z" -> UTC epoch seconds
    return calendar.timegm(time.strptime(s[:19], "%Y-%m-%dT%H:%M:%S"))


def _addr(v):
    if isinstance(v, dict):
        return (v.get("hash") or "").lower()
    return (v or "").lower()


def scrape(con):
    """Page through the Blockscout v2 address-transactions endpoint using its
    next_page_params cursor (handles unlimited history; the v1 txlist with
    startblock=0 returns empty on this instance)."""
    pools = pool_pair_map()
    cur = con.cursor()
    base = f"{EXPLORER}/api/v2/addresses/{WALLET}/transactions"
    params = {}
    seen = inserted = 0
    while True:
        for _ in range(6):
            r = requests.get(base, params=params, timeout=30)
            if r.status_code == 429:
                time.sleep(4); continue
            break
        js = r.json()
        items = js.get("items") or []
        if not items:
            break
        for tx in items:
            seen += 1
            h = (tx.get("hash") or "").lower()
            blk = tx.get("block_number")
            ts = _iso_to_epoch(tx.get("timestamp"))
            to = _addr(tx.get("to"))
            inp = tx.get("raw_input") or "0x"
            sel = (tx.get("method") or (inp[:10] if len(inp) >= 10 else "0x")).lower()
            name = SELECTORS.get(sel, "")
            action = "other"
            is_bid = price_raw = qty_raw = None
            if name in ("placeTakerOrderWithoutVault", "placeOrder"):
                is_bid, price_raw, qty_raw = decode_taker_input(inp)
                action = "buy" if is_bid else ("sell" if is_bid is not None else "other")
            elif name == "approve":
                action = "approve"
            pinfo = pools.get(to)
            pair = pinfo["pair"] if pinfo else None
            status = "ok" if (tx.get("status") == "ok") else "reverted"
            # token_transfers present => the order actually filled (settled tokens)
            tts = tx.get("token_transfers") or []
            filled = 1 if (status == "ok" and len(tts) > 0) else 0
            cur.execute(
                "INSERT OR IGNORE INTO onchain_tx VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (h, blk, ts, to, pair, sel, action,
                 1 if is_bid else (0 if is_bid is not None else None),
                 str(price_raw) if price_raw is not None else None,
                 str(qty_raw) if qty_raw is not None else None,
                 int(tx.get("gas_used") or 0), int(tx.get("gas_price") or 0),
                 status, filled, tx.get("value", "0")))
            inserted += cur.rowcount
        con.commit()
        npp = js.get("next_page_params")
        print(f"  ...scraped {seen} txs, stored +{inserted} "
              f"(cursor block {params.get('block_number','start')})")
        if not npp:
            break
        params = npp
        time.sleep(0.2)  # be polite to the public explorer
    print(f"scrape done: {seen} txs seen, {inserted} stored in {DB}")


def fmt(ts):
    return datetime.datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def analyze(con, gap_threshold_s=90, fill_gas=250000):
    cur = con.cursor()
    cur.execute("SELECT COUNT(*) FROM onchain_tx")
    total = cur.fetchone()[0]
    print(f"\n=== dataset: {total} on-chain txs ===")

    # The v2 list endpoint omits token_transfers, so derive `filled` from gas:
    # a real fill settles tokens (~380-460k gas); a no-fill IOC / revert uses far
    # less (<250k). Backfill the column so the stored dataset is correct.
    cur.execute("""UPDATE onchain_tx SET filled =
        CASE WHEN status='ok' AND action IN ('buy','sell') AND gas_used >= ?
             THEN 1 ELSE 0 END""", (fill_gas,))
    con.commit()
    print(f"(fill classifier: status=ok AND gas_used >= {fill_gas})")

    print("\n-- order txs by pair (filled = settled, via gas heuristic) --")
    for pair, n, fills in cur.execute("""
        SELECT pair, COUNT(*), SUM(filled)
        FROM onchain_tx WHERE action IN ('buy','sell')
        GROUP BY pair ORDER BY COUNT(*) DESC"""):
        fills = fills or 0
        rate = 100.0 * fills / n if n else 0
        print(f"  {pair or '(none)':14s} txs={n:6d} filled={fills:6d} ({rate:.1f}%)")
    print("\n-- by action --")
    for action, n in cur.execute(
            "SELECT action, COUNT(*) FROM onchain_tx GROUP BY action ORDER BY COUNT(*) DESC"):
        print(f"  {action:8s} {n}")
    print("\n-- by status --")
    for st, n in cur.execute(
            "SELECT status, COUNT(*) FROM onchain_tx GROUP BY status"):
        print(f"  {st:10s} {n}")
    # gas spent (CAST to REAL to avoid 64-bit integer overflow on the product)
    g = cur.execute(
        "SELECT SUM(CAST(gas_used AS REAL)*CAST(gas_price AS REAL)) FROM onchain_tx"
    ).fetchone()[0] or 0
    print(f"\ntotal gas spent: {g/1e18:.4f} SOMI")

    # ---- burst-stall gap detection = candidate MM outages ----
    # Walk order txs in time order; a gap > threshold between consecutive txs,
    # inside an otherwise-active burst run, marks a period where we sent nothing
    # (book empty -> burst skipped). Those gaps are candidate MM outages.
    ts = [r[0] for r in cur.execute(
        "SELECT ts FROM onchain_tx WHERE action IN ('buy','sell') ORDER BY ts")]
    print(f"\n=== burst-stall gaps (>{gap_threshold_s}s between order txs) "
          f"= candidate MM outages ===")
    if len(ts) < 2:
        print("not enough order txs"); return
    gaps = []
    for i in range(1, len(ts)):
        d = ts[i] - ts[i - 1]
        if d > gap_threshold_s:
            gaps.append((ts[i - 1], ts[i], d))
    if not gaps:
        print("none — no stalls above threshold")
    for a, b, d in gaps:
        print(f"  {fmt(a)} -> {fmt(b)} UTC   gap {d/60:.1f} min "
              f"(last tx before, first tx after)")
    print(f"\n{len(gaps)} stall-gaps found. Cross-check vs "
          f"evidence/LIQUIDITY-BLACKOUT.md (empty-book windows should line up). "
          f"NOTE: only gaps WITHIN active burst runs are MM-outage signals; long "
          f"gaps between separate sessions are just us not trading.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scrape", action="store_true")
    ap.add_argument("--analyze", action="store_true")
    ap.add_argument("--gap-seconds", type=int, default=90,
                    help="min gap between order txs to flag as a stall")
    ap.add_argument("--fill-gas", type=int, default=250000,
                    help="gas_used >= this (and status ok) counts as a real fill")
    args = ap.parse_args()
    con = init_db()
    if args.scrape:
        scrape(con)
    if args.analyze or not args.scrape:
        analyze(con, gap_threshold_s=args.gap_seconds, fill_gas=args.fill_gas)


if __name__ == "__main__":
    main()
