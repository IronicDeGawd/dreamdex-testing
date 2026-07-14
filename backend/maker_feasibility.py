#!/usr/bin/env python3
"""Maker/yield feasibility probe — READ-ONLY (no keys, no orders, no capital).

Answers, with live mainnet data, whether a no-bleed maker can actually earn here:
  1. Is the spread CAPTURABLE?   median spread in bps AND ticks, % of samples
     where spread > 2 ticks (must join bid+ask a tick inside to earn anything).
  2. Is there FLOW to fill us?   hourly candle volume per pair (every trade has
     a maker side — that volume is the pool resting orders feed on).
  3. Does the market OSCILLATE?  chop ratio = sum|hourly moves| / |net move|.
     A maker earns on two-way traffic; a one-way trend just fills our bid and
     leaves us holding the bag (the R3 failure mode).
  4. Is maker YIELD still live?  getMidpointEmaState (0x2d1590a0) per pool, and
     maker/taker fee bps from /v0/markets (fees were 0 in R2).

Usage:
  python3 maker_feasibility.py                 # sample ~15 min, then report
  FEAS_MINUTES=45 python3 maker_feasibility.py # longer window
Writes samples to FEAS_OUT (default data/feasibility-<ts>.jsonl) and prints a
summary table; run it for a full day with nohup for the real Phase-1 decision.
"""
import json
import os
import statistics
import time

import requests

HTTP = os.environ.get("DREAMDEX_HTTP", "https://api.dreamdex.io")
RPC = os.environ.get("SOMNIA_RPC", "https://api.infra.mainnet.somnia.network/")
PAIRS = [p.strip() for p in os.environ.get(
    "FEAS_PAIRS", "WBTC:USDso,WETH:USDso,SOMI:USDso").split(",") if p.strip()]
MINUTES = float(os.environ.get("FEAS_MINUTES", "15"))
POLL_S = float(os.environ.get("FEAS_POLL_S", "5"))
OUT = os.environ.get("FEAS_OUT", f"data/feasibility-{int(time.time())}.jsonl")

POOLS = {  # pair -> pool contract (config.py mainnet MARKETS)
    "WETH:USDso": "0xa936da11B57b50A344e1293AAaE5232885ea2bDE",
    "WBTC:USDso": "0x25bfF6B7B5E2243424F38E75de7ab03C0522a5EA",
    "SOMI:USDso": "0x035De7403eac6872787779CCA7CCF1b4CDb61379",
}
EMA_SELECTOR = "0x2d1590a0"   # getMidpointEmaState() -> (emaPrice 1e18, ts_ns)


def get_json(url, **kw):
    r = requests.get(url, timeout=10, **kw)
    r.raise_for_status()
    return r.json()


def market_meta():
    """tick/lot/min + fee bps per pair from /v0/markets."""
    out = {}
    for m in get_json(f"{HTTP}/v0/markets").get("markets", []):
        sym = m.get("symbol")
        if sym in PAIRS:
            out[sym] = {
                "tick": float(m.get("tickSize", 0) or 0),
                "lot": float(m.get("lotSize", 0) or 0),
                "minq": float(m.get("minQuantity", 0) or 0),
                # fee field names have shifted between API versions — grab any
                "maker_fee": m.get("makerFeeBps", m.get("makerBps", m.get("makerBpsTimes1k"))),
                "taker_fee": m.get("takerFeeBps", m.get("takerBps", m.get("takerBpsTimes1k"))),
            }
    return out


def book(pair):
    d = get_json(f"{HTTP}/v0/orderbooks", params={"symbols": pair})
    b = (d.get("orderbooks") or [d])[0] if isinstance(d, dict) else d[0]
    def lvl(side, i=0):
        L = (b.get(side) or [])
        if len(L) <= i:
            return None, 0.0
        e = L[i]
        if isinstance(e, dict):
            return float(e["price"]), float(e.get("quantity", e.get("size", 0)))
        return float(e[0]), float(e[1])
    bid, bq = lvl("bids"); ask, aq = lvl("asks")
    return bid, bq, ask, aq


def candles(pair, hours=72):
    try:
        d = get_json(f"{HTTP}/v0/markets/{pair}/candles",
                     params={"interval": "1h", "limit": hours})
    except Exception:
        d = get_json(f"{HTTP}/v0/candles", params={"symbol": pair, "interval": "1h",
                                                   "limit": hours})
    rows = d.get("candles", d) if isinstance(d, dict) else d
    out = []
    for c in rows or []:
        try:
            out.append({"close": float(c["close"]),
                        "volume": float(c.get("volume", c.get("volumeUsd", 0) or 0))})
        except (KeyError, TypeError, ValueError):
            continue
    return out


def ema_state(pool):
    """Raw eth_call of getMidpointEmaState. Returns (price, age_s) or None."""
    body = {"jsonrpc": "2.0", "id": 1, "method": "eth_call",
            "params": [{"to": pool, "data": EMA_SELECTOR}, "latest"]}
    r = requests.post(RPC, json=body, timeout=10)
    r.raise_for_status()
    res = r.json().get("result")
    if not res or res == "0x":
        return None
    raw = res[2:]
    if len(raw) < 128:
        return None
    price = int(raw[0:64], 16) / 1e18
    ts_ns = int(raw[64:128], 16)
    return price, time.time() - ts_ns / 1e9


def main():
    meta = market_meta()
    print(f"pairs={PAIRS} window={MINUTES}min poll={POLL_S}s out={OUT}")
    print(f"market meta: {json.dumps(meta, indent=1)}")

    # one-shot: yield/EMA liveness
    for pair in PAIRS:
        try:
            st = ema_state(POOLS[pair])
            print(f"EMA {pair}: " + (f"price={st[0]:.6g} age={st[1]:.0f}s" if st else "NO DATA"))
        except Exception as e:
            print(f"EMA {pair}: call failed — {str(e)[:80]}")

    os.makedirs(os.path.dirname(OUT) or ".", exist_ok=True)
    t_end = time.time() + MINUTES * 60
    n = 0
    with open(OUT, "a") as fh:
        while time.time() < t_end:
            row = {"ts": time.time()}
            for pair in PAIRS:
                try:
                    bid, bq, ask, aq = book(pair)
                    if not bid or not ask:
                        continue
                    mid = (bid + ask) / 2
                    row[pair] = {
                        "bid": bid, "ask": ask,
                        "spread_bps": (ask - bid) / mid * 1e4,
                        "spread_ticks": (ask - bid) / meta[pair]["tick"] if meta.get(pair, {}).get("tick") else None,
                        "bid_depth_usd": bq * bid, "ask_depth_usd": aq * ask,
                    }
                except Exception as e:
                    row[pair] = {"err": str(e)[:60]}
            fh.write(json.dumps(row) + "\n"); fh.flush()
            n += 1
            time.sleep(POLL_S)
    print(f"collected {n} samples -> {OUT}")

    # ── report ────────────────────────────────────────────────────────────
    samples = [json.loads(l) for l in open(OUT)]
    print(f"\n=== FEASIBILITY REPORT ({len(samples)} samples) ===")
    for pair in PAIRS:
        rows = [s[pair] for s in samples if pair in s and "spread_bps" in s.get(pair, {})]
        if not rows:
            print(f"\n{pair}: no usable samples"); continue
        sb = sorted(r["spread_bps"] for r in rows)
        st_ = [r["spread_ticks"] for r in rows if r.get("spread_ticks") is not None]
        med_bps = sb[len(sb) // 2]
        capt = 100.0 * sum(1 for t in st_ if t > 2) / len(st_) if st_ else 0.0
        depth = statistics.median(r["bid_depth_usd"] for r in rows)
        cs = candles(pair)
        vol24 = sum(c["volume"] for c in cs[-24:]) if cs else 0.0
        chop = None
        if cs and len(cs) >= 25:
            closes = [c["close"] for c in cs]
            moves = [abs(closes[i] - closes[i - 1]) for i in range(1, len(closes))]
            net = abs(closes[-1] - closes[0])
            chop = sum(moves) / net if net > 0 else float("inf")
        print(f"\n{pair}:")
        print(f"  spread: median {med_bps:.2f} bps ({statistics.median(st_):.1f} ticks); "
              f"p10 {sb[len(sb)//10]:.2f} / p90 {sb[-max(1, len(sb)//10)]:.2f} bps")
        print(f"  capturable (>2 ticks): {capt:.0f}% of samples")
        print(f"  touch depth: ~${depth:,.0f} median (bid side)")
        print(f"  candle volume 24h: ~${vol24:,.0f}  ({vol24/24:,.0f}/h — the flow makers feed on)")
        if chop is not None:
            print(f"  chop ratio 72h: {chop:.1f}x (sum|moves|/|net| — higher = two-way, maker-friendly)")


if __name__ == "__main__":
    main()
