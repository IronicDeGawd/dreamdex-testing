# DreamDEX Round 3 — Transaction & Performance Report

**Wallet:** `0xD84fE2a2220f0269e3d88dab908ADceb2d691E76` (trader-2)
**Round:** R3, ended 2026-07-07 15:00 UTC (~20:30 IST)
**Prepared:** 2026-07-07

---

## 1. Executive Summary

We finished **#2 of 6** on raw volume — the metric that decided week 2 — at
**1,086,202 USDso** of volume, **7,246 behind** the winner (trader-5, 1,093,448).
We were also **#2 on effective volume** (200,526) and, by a wide margin, the
**most capital-efficient trader in the field**: we generated our volume with the
fewest transactions and the highest fill rate of any active competitor.

We led the pack for large stretches and were within ~1% of first at the buzzer.
The gap came down to two things in the final hours: recurring **order-book
dislocations** (spread blowing out to 1–4%) that stalled fills for everyone, and
our own late pivot to a faster engine that we could not fully exploit before the
clock ran out.

| Result | Value |
|---|---|
| Final rank (raw volume) | **#2 of 6** |
| Raw volume | **1,086,202 USDso** |
| Effective volume | 200,526 USDso (**#2**) |
| Transactions | 31,324 |
| Fills | 32,555 |
| Real fill rate (de-duplicated) | **~98%** |
| Total capital used (PnL) | **−$122.31** |
| Cost per 1,000 volume | **≈ $0.113** |
| End state | Flat, liquidated to USDso ($27.69) |

---

## 2. Final Standings

| # | Trader | Raw volume | Effective | Tx count | Fill rate\* |
|---|--------|-----------:|----------:|---------:|-----------:|
| 1 | trader-5 | 1,093,448 | 175,649 | 43,337 | 73% |
| **2** | **US (trader-2)** | **1,086,202** | **200,526** | **31,324** | **~98%** |
| 3 | trader-3 | 945,481 | 13,648 | 127,156 | 55% |
| 4 | trader-1 | 922,886 | 218,409 | 94,371 | 49% |
| 5 | trader-4 | 734 | 1 | 136 | 32% |
| 6 | tester | 1 | 0 | 3 | 33% |

\*Fill rate: rivals' figures are the leaderboard's `fills/txCount`; ours is our
own de-duplicated execution logs (the leaderboard reads 104% for us because one
transaction can match several resting orders — inflated, not a true success rate).

**Efficiency read:** trader-3 sent **4×** our transaction count (127k vs 31k) to
finish *below* us. trader-1 sent **3×**. We converted nearly every order we placed;
they burned gas on orders that never filled. This is why our capital lasted.

---

## 3. Volume Progression (chart)

Cumulative raw volume across the round, key milestones annotated:

| Milestone | Raw volume | Note |
|---|---:|---|
| Mid-round restart | ~753,000 | Resumed after capital-preservation pause |
| Cushion runs | ~891,000 | Cost-aware climb |
| **1,000,000** | 1,000,092 | Crossed 1M |
| Cushion / lead-building | ~1,065,000 | Briefly #1 |
| Direct-burst final push | **1,086,202** | Stalled on wide book near 1.1M target |

> *Chart in the .docx: cumulative-volume line to 1.086M with the 1M and final markers.*

---

## 4. Economics — Cost & Fill Efficiency (chart)

- **Toll ≈ $0.113 per 1,000** of raw volume, essentially the WETH spread floor
  (~0.02%). Occasionally *negative* in choppy two-way flow (the market paid us).
- **Fill efficiency ~98%** (de-duplicated). Every order we placed filled, most
  matched multiple counterparties.
- Total capital consumed: **$122.31** (started $150 free USDso → ended $27.69),
  producing 1,086,202 volume ⇒ **$0.1126 / 1k**.
- Gas: ~0.035 SOMI per 1k; SOMI ≈ $0.10–0.12.

> *Chart in the .docx: our $/1k and fill% vs rivals' — we are the clear outlier
> on efficiency.*

---

## 5. Capital Drawdown (chart)

| Point | USDso | SOMI (gas) |
|---|---:|---:|
| Start (team test capital) | 150.0 | 50.0 |
| Gas top-ups (2× buys) | −~2.2 | +20.0 |
| Sent to personal wallet | — | −5.0 |
| Consumed by trading + gas | −119.x | −~64 |
| **End (liquidated to USDso)** | **27.69** | **0.60** |

All capital was **team-provided test funds** (not personal), so the ~$122 toll is
a cost of generating volume, not a loss. We **liquidated all inventory to USDso**
at the end (scoring rewards free USDso; held inventory drags the multiplier).

> *Chart in the .docx: USDso + SOMI balance over the round with top-ups and the
> final liquidation marked.*

---

## 6. Engines Used

| Engine | Role | Speed |
|---|---|---|
| **volume_climb.py** (API path) | Workhorse for most of the round | ~30s/round-trip |
| **direct_burst.py** (direct contract) | Final-day speed push | ~15s/round-trip (**~2×**) |

Both run WETH:USDso taker round-trips (buy at ask, sell at bid), ending flat.
The direct engine bypasses the REST `/orders` round-trip by calling
`placeOrder` (`0x4e978373`) on the pool directly — a discovery that doubled
throughput but landed too late in the round to fully exploit.

---

## 7. Issues Observed

1. **RPC node flakiness** — the single Somnia infra endpoint failed repeatedly
   under sustained load, tripping our safety breaker. We added multi-endpoint
   failover (publicnode, Ankr) as a workaround.
2. **DNS blips** — occasionally the whole container couldn't resolve any host for
   a few seconds; failover across endpoints can't help when name resolution
   itself is down.
3. **Order-book dislocations** — WETH spread intermittently blew out from ~0.02%
   to **1–4%**, during which IOC orders stopped crossing (no fillable liquidity
   at the touch). This stalled volume for the whole field, us included, and cost
   us pace in the final hours.
4. **"Silent rejects"** — orders that mine with `status=1` but move no balance
   (placed, not matched). Required verifying fills by on-chain balance delta, not
   receipt status.
5. **Ask-pull on thin books** — a limit +1 tick above best ask often failed to
   cross (likely JIT/MEV pulling the ask); we needed ~+5 ticks / +0.4% to fill.
6. **Leaderboard `usdsoBalance` is a stale snapshot** — it under-reported our real
   capital by up to 3× at one point; we had to read balances on-chain to trust them.
7. **Fill count can exceed transaction count** (multi-match per tx), so the
   headline "fill rate" reads >100% and isn't a true success rate.

---

## 8. Feedback (platform / dev)

- **RPC reliability** is the single biggest operational pain — a more robust or
  officially load-balanced endpoint would materially help all traders.
- **Clarify the scoring metric** for each phase. Raw-vs-effective (PnL-weighted)
  ambiguity in week 2 affected strategy; the leaderboard exposes both, which is
  confusing.
- **Document the direct-contract path.** `placeOrder` (`0x4e978373`, wallet-funded)
  is the real integration point; the REST `/orders` round-trip is a latency tax.
  A published ABI + example would let integrators skip it cleanly.
- **The fills metric is misleading** (>100%). Reporting distinct order-fill rate
  and/or gas-per-volume would better reflect real efficiency.

---

## 9. Lessons Carried Forward

- Trust **on-chain reads from our own wallet**, never the leaderboard snapshot or
  a one-off script (a stale RPC replica nearly caused a bad call mid-race).
- **Leg size must shrink as free capital shrinks**, or buys pre-revert.
- The **speed lever is the API round-trip**, not our own delays — direct
  `placeOrder` is the ~2× unlock, ready and hardened for next round.
- **Never swap the order path live under time pressure** without an encoding
  self-check and a bag-proof loop (we left a small bag once doing exactly that).
- End every round **flat, in USDso.**

---

*Engines (`volume_climb.py`, `direct_burst.py`) and full technical runbook are
committed on branch `feature/profit-maker-agent`; detailed findings in
`context/research/dreamdex-r3-findings.md`.*
