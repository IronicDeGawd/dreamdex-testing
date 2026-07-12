# Maker/Yield Feasibility — Algo Arena (measured 2026-07-12/13)

> Phase-1 gate from `context/plan/algo-arena.md`: measure BEFORE building on hope.
> Sources: 160 book samples over 18 min (backend/maker_feasibility.py, read-only),
> 72×1h candles per pair, raw eth_call EMA probes, and a LIVE bounded smoke test
> of maker_v2 on the R3 wallet (WETH, leg $8, bleed cap $0.75).

## Verdict: FEASIBLE — with one hard constraint

A no-bleed maker works on DreamDEX mainnet today: quotes rest, real fills arrive,
the sell floor (avg_cost + margin) holds, fees are zero, and shutdown leaves no
bag. The profit driver is OTHER VOLUME FARMERS' taker churn (they cross the
spread both ways all day — perfect maker counterparty flow), concentrated on
whatever pairs the Arena boosts each week.

**The hard constraint: never quote a pair OUR OWN taker trades.** Our maker
resting at the touch tightens that book, which *attracts* our own taker's
cheapest-book rotation, and a cross-wallet self-fill is textbook wash trading
(DQ). This is why the WETH smoke test was cut short — the live R4 engine
rotates WBTC+WETH and could have crossed us. Pair-disjointness between our
maker and taker is not a nice-to-have; it must be ENFORCED in config.

## Book measurements (160 samples, 18 min, ~00:00 UTC Jul 13)

| pair | spread median | in ticks | >2 ticks | touch depth | 24h flow* | 72h chop |
|---|---|---|---|---|---|---|
| WBTC:USDso | 1.05 bps (p10 0.55 / p90 1.55) | 67 | 100% | ~$3.9k/side | ~4 WBTC ≈ $256k | 8.9× |
| WETH:USDso | 4.01 bps (p10 2.36 / p90 4.08) | 73 | 100% | ~$70–90/side | ~60 WETH ≈ $109k | 5.1× |
| SOMI:USDso | 19.4 bps (p10 9.7 / p90 19.4) | 2 | 53% | ~$700–900/side | ~116k SOMI ≈ $12k | 187× |

\* candle `volume` is in BASE units (verified: WBTC 4/day × $64k ≈ our own R4
engine's ~$250k/day — most WBTC/WETH "flow" today IS contest bots, incl. ours).

Reading the table:
- **WBTC**: spread is 67 ticks but only ~1 bps — capture per round-trip is tiny,
  the $3.9k touch queue buries a small order, and the flow is mostly our own
  taker. NOT a maker pair while any of our takers run there.
- **WETH**: 4 bps, 100% capturable, thin touch (~$80) so our quote goes straight
  to the front — fills proven within minutes. The pair to quote once our own
  taker isn't rotating into it.
- **SOMI**: the only genuinely ORGANIC pair (~$12k/day retail, chop 187× = pure
  two-way traffic — a maker's dream tape) BUT the tick is huge (9.7 bps), so
  capture = spread − 2 ticks ≈ 0 about half the time, and the base is native
  (maker v2 excludes native pairs — the R3 tracker-desync lesson). Supporting
  SOMI = Phase 1.5: needs tracker inventory with a strict gas-reserve split.

## Live smoke test (maker_v2 on the R3 wallet, WETH, ~10 min active)

- SIWE auth with a second wallet initially FAILED (401 invalid_signature) —
  found + fixed a signer-vs-owner bug in `trading/dreamdex.py`: the SIWE message
  embedded config MY_ADDRESS while the override wallet signed. Same bug class
  as bot-kit issue #7.
- After fix: quotes rested two-sided; **maker BUY filled 0.0044 WETH @ 1818.02
  ($8.00 volume at zero spread cost)**; engine re-quoted the cap-limited buy and
  a sell at 1818.26–1818.51 — always ≥ avg + margin (no-bleed floor held live);
  drift re-quotes followed the ask upward (capturing more when offered).
- Cut short deliberately on the self-cross realization (above). Final capital
  accounting: see the run log `/tmp/maker_v2_smoke.log` on the server — bleed
  bounded by the $0.75 hard guard either way.
- Clean SIGTERM shutdown (cancel-all + IOC flatten) verified twice.

## Fees & yield

- `/v0/markets` exposes no fee fields; R2 on-chain `getPoolParams` read maker=
  taker=0 bps. The smoke fill's realized math is consistent with ZERO maker fee.
- `getMidpointEmaState` (0x2d1590a0) is LIVE on all three pools and decodes to
  each pair's current mid (WETH 1819, WBTC ~64.0k, SOMI 0.1033) — the yield
  oracle machinery runs. Whether a PAID yield program exists for the Arena is
  unverified (nothing on the algo-arena page); our two-sided at-touch quoting
  sits on the EMA yield peak by construction (R3 finding), so any yield is
  free upside. Treat as bonus, not core economics.

## Economics sketch (WETH, post-R4, assuming Arena farmer flow ≥ today's)

- Capture per round-trip ≈ spread ≈ 4 bps of leg → $15 leg ≈ $0.006/trip + the
  volume itself (2 × leg = $30/round-trip toward score).
- Gas ≈ 2–4 tx per round-trip; gasUsed ~200–400k at Somnia prices ≈ $0.002–0.004
  → roughly break-even to slightly positive per trip BEFORE adverse selection;
  the trend gate + inventory cap + slip-protected stop bound the adverse tail.
- The real prize is Arena scoring: maker fills = volume × boost with ~zero
  bleed, vs the taker's ~$0.06–0.15/1k toll.

## What this means for the build (decisions)

1. **maker_v2.py + maker_core.py are the Phase-1 deliverable** — core logic is
   pure + unit-tested (19 tests), engine live-validated. Ready for a longer
   soft-launch once R4 ends.
2. **Enforce pair-disjointness** maker-vs-taker across ALL our wallets at the
   control layer (engine_manager named slots — Arena plan Phase 2).
3. **Soft-launch plan (post-R4 stop / Arena wallet):** WETH first (thin touch,
   4 bps), leg $15, cap $20/pair, bleed guard $2, 24–48h measured run; judge
   fills/day, realized PnL, gas burn. Quote boosted pairs when disjoint from
   our taker.
4. **SOMI native support** is the biggest upside (only organic book, 187× chop)
   — separate task, needs tracker inventory + gas-reserve isolation + the 5M
   native-pool gas floor (already in the shared layer).
5. Re-run `backend/maker_feasibility.py` for 24h before each strategy change —
   today's flow is contest-distorted and will shift when R4 ends / Arena boosts
   land.
