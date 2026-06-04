#!/usr/bin/env python3
"""
analyze_full.py — Complete analysis of the scraped on-chain trade dataset
(/app/data/onchain_trades.db, ~63.5k txs). Read-only; reports:

  1. Overview (totals, time span, sessions)
  2. By pair (volume, fill rate, reverts, gas)
  3. By action / side
  4. Fill classification (gas heuristic)
  5. Gas economics (cost per outcome, total SOMI)
  6. Traded-price ranges per pair
  7. Activity over time (per-day, per-hour-of-day)
  8. Reverts over time
  9. Bot-tuning takeaways

Run in the container (needs config.MARKETS for decimals):
    docker exec dreamdex-agent python3 /app/analysis/analyze_full.py
"""
import datetime
import sqlite3
import sys

DB = "/app/data/onchain_trades.db"
FILL_GAS = 250000  # gas_used >= this AND status ok => real fill (bimodal split)


def decimals():
    sys.path.insert(0, "/app")
    from config import MARKETS
    d = {}
    for pair, mk in MARKETS.items():
        d[pair] = (int(mk.get("baseDecimals", 18)), int(mk.get("quoteDecimals", 18)))
    return d


def hr(ts):
    return datetime.datetime.utcfromtimestamp(ts)


def main():
    con = sqlite3.connect(DB)
    con.create_function("isfill", 3,
                        lambda st, act, g: 1 if (st == "ok" and act in ("buy", "sell")
                                                 and (g or 0) >= FILL_GAS) else 0)
    cur = con.cursor()
    dec = decimals()
    bar = "=" * 64

    # ---- 1. OVERVIEW ----
    total, tmin, tmax = cur.execute(
        "SELECT COUNT(*), MIN(ts), MAX(ts) FROM onchain_tx").fetchone()
    span_h = (tmax - tmin) / 3600.0
    print(bar)
    print(f"1. OVERVIEW")
    print(bar)
    print(f"  transactions : {total:,}")
    print(f"  first tx     : {hr(tmin)} UTC")
    print(f"  last tx      : {hr(tmax)} UTC")
    print(f"  span         : {span_h:.1f} h ({span_h/24:.1f} days)")
    orders = cur.execute(
        "SELECT COUNT(*) FROM onchain_tx WHERE action IN ('buy','sell')").fetchone()[0]
    print(f"  order txs    : {orders:,}  (rest = approvals/other)")

    # sessions (gap > 10 min in order-tx stream)
    ts = [r[0] for r in cur.execute(
        "SELECT ts FROM onchain_tx WHERE action IN ('buy','sell') ORDER BY ts")]
    sessions, s0, prev = [], ts[0], ts[0]
    for t in ts[1:]:
        if t - prev > 600:
            sessions.append((s0, prev)); s0 = t
        prev = t
    sessions.append((s0, prev))
    active = sum(b - a for a, b in sessions) / 3600.0
    print(f"  trading sessions: {len(sessions)} (gap>10min splits) | "
          f"active trading time ~{active:.1f}h")

    # ---- 2. BY PAIR ----
    print("\n" + bar); print("2. BY PAIR"); print(bar)
    print(f"  {'pair':14s} {'txs':>7s} {'fills':>7s} {'fill%':>6s} "
          f"{'revert':>7s} {'volume(USDso)':>14s} {'gas(SOMI)':>10s}")
    grand_vol = 0.0
    for pair in [r[0] for r in cur.execute(
            "SELECT DISTINCT pair FROM onchain_tx WHERE pair IS NOT NULL")]:
        bdec, qdec = dec.get(pair, (18, 18))
        rows = cur.execute(
            "SELECT status, gas_used, gas_price, price_raw, qty_raw, action "
            "FROM onchain_tx WHERE pair=? AND action IN ('buy','sell')", (pair,)).fetchall()
        n = len(rows); fills = rev = 0; vol = 0.0; gas = 0.0
        for st, gu, gp, pr, qt, act in rows:
            gas += (gu or 0) * (gp or 0) / 1e18
            if st != "ok":
                rev += 1; continue
            if (gu or 0) >= FILL_GAS:
                fills += 1
                if pr and qt:
                    vol += (int(qt) / 10**bdec) * (int(pr) / 10**qdec)
        grand_vol += vol
        print(f"  {pair:14s} {n:7d} {fills:7d} {100*fills/n if n else 0:5.1f}% "
              f"{rev:7d} {vol:14,.0f} {gas:10.2f}")
    print(f"  {'TOTAL filled notional':>40s}: ~{grand_vol:,.0f} USDso "
          f"(cf. leaderboard ~205k; counts our filled legs)")

    # ---- 3. BY ACTION ----
    print("\n" + bar); print("3. BY SIDE"); print(bar)
    for act in ("buy", "sell"):
        n, f = cur.execute(
            "SELECT COUNT(*), SUM(isfill(status,action,gas_used)) "
            "FROM onchain_tx WHERE action=?", (act,)).fetchone()
        print(f"  {act:5s}: {n:7,d} txs, {f or 0:7,d} filled ({100*(f or 0)/n if n else 0:.1f}%)")

    # ---- 4/5. STATUS + GAS ECONOMICS ----
    print("\n" + bar); print("4. STATUS & GAS ECONOMICS"); print(bar)
    for st, n in cur.execute("SELECT status, COUNT(*) FROM onchain_tx GROUP BY status"):
        print(f"  status {st:10s}: {n:,}")
    tot_gas = cur.execute(
        "SELECT SUM(CAST(gas_used AS REAL)*CAST(gas_price AS REAL)) FROM onchain_tx"
    ).fetchone()[0] or 0
    print(f"  TOTAL gas spent : {tot_gas/1e18:.2f} SOMI")
    for label, cond in [("filled order", "status='ok' AND action IN ('buy','sell') AND gas_used>=%d" % FILL_GAS),
                        ("no-fill order", "status='ok' AND action IN ('buy','sell') AND gas_used<%d" % FILL_GAS),
                        ("reverted", "status='reverted'")]:
        r = cur.execute(f"SELECT COUNT(*), AVG(gas_used) FROM onchain_tx WHERE {cond}").fetchone()
        if r[0]:
            print(f"  {label:14s}: n={r[0]:,} avg_gas={r[1]:,.0f}")

    # ---- 6. PRICE RANGES ----
    print("\n" + bar); print("6. TRADED PRICE RANGE (filled, per pair)"); print(bar)
    for pair in [r[0] for r in cur.execute(
            "SELECT DISTINCT pair FROM onchain_tx WHERE pair IS NOT NULL")]:
        bdec, qdec = dec.get(pair, (18, 18))
        ps = [int(r[0]) / 10**qdec for r in cur.execute(
            "SELECT price_raw FROM onchain_tx WHERE pair=? AND price_raw IS NOT NULL "
            "AND status='ok' AND gas_used>=?", (pair, FILL_GAS))]
        if ps:
            print(f"  {pair:14s} min={min(ps):.6g} max={max(ps):.6g} "
                  f"avg={sum(ps)/len(ps):.6g}")

    # ---- 7. ACTIVITY OVER TIME ----
    print("\n" + bar); print("7. ACTIVITY BY DAY (order txs)"); print(bar)
    day = {}
    for t, isf in cur.execute(
            "SELECT ts, isfill(status,action,gas_used) FROM onchain_tx "
            "WHERE action IN ('buy','sell')"):
        d = hr(t).strftime("%Y-%m-%d")
        day.setdefault(d, [0, 0]); day[d][0] += 1; day[d][1] += isf
    for d in sorted(day):
        n, f = day[d]
        print(f"  {d}: {n:7,d} txs  {f:7,d} fills ({100*f/n if n else 0:.0f}%)")

    print("\n" + bar); print("8. HOUR-OF-DAY DISTRIBUTION (UTC, order txs)"); print(bar)
    hod = {}
    for (t,) in cur.execute("SELECT ts FROM onchain_tx WHERE action IN ('buy','sell')"):
        h = hr(t).hour; hod[h] = hod.get(h, 0) + 1
    mx = max(hod.values()) if hod else 1
    for h in range(24):
        n = hod.get(h, 0)
        print(f"  {h:02d}:00  {n:6,d} {'#' * int(40*n/mx)}")

    con.close()
    print("\n" + bar)
    print("9. BOT-TUNING TAKEAWAYS")
    print(bar)
    print("  - USDC.e slip-0 had the best fills (~99%); use it as the volume engine.")
    print("  - Reverts cluster where USDso/gas ran dry or book was empty (see")
    print("    evidence/scan_blackout_history.py for liquidity outages).")
    print("  - Fill = gas>=250k heuristic; tune FILL_GAS if the split shifts.")


if __name__ == "__main__":
    main()
