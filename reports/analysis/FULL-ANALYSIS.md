# DreamDEX Campaign — Complete On-Chain Trade Analysis

**Wallet:** `0x0000000000000000000000000000000000000000` (handle `trader-9`)
**Network:** Somnia mainnet (chain 5031)
**Dataset:** 63,569 transactions, 2026-05-26 → 2026-06-01, scraped from chain (Blockscout v2)
**Source:** `analysis/onchain_trades.db`; regenerate via `analysis/scrape_trades.py`, analyse via `analysis/analyze_full.py`

> The direct-burst engine fires raw RPC and never logged to the backend DB, so the
> chain is the only complete record. Computed filled notional (~204,475 USDso)
> matches the leaderboard (~205k) — confirming the dataset is complete.

---

## 1. Overview
| Metric | Value |
|---|---|
| Transactions | 63,569 |
| Order txs (buy/sell) | 62,118 |
| Other (approvals/setup) | 1,451 |
| First tx | 2026-05-26 10:02:42 UTC |
| Last tx | 2026-06-01 15:16:39 UTC |
| Calendar span | 149.2 h (6.2 days) |
| Trading sessions (gap >10 min) | 48 |
| Active trading time | ~69.4 h |

---

## 2. By pair — where volume and reverts came from
| Pair | Txs | Fills | Fill % | Reverts | Volume (USDso) | Gas (SOMI) |
|------|-----|-------|--------|---------|----------------|------------|
| WETH:USDso | 39,703 | 28,751 | 72.4% | 4,635 | 124,789 | 82.93 |
| SOMI:USDso | 8,419 | 7,041 | 83.6% | 815 | 64,406 | 18.55 |
| USDC.e:USDso | 13,836 | 13,761 | 99.5% | 19 | 14,543 | 33.91 |
| WBTC:USDso | 160 | 91 | 56.9% | 25 | 736 | 0.38 |
| **TOTAL** | **62,118** | **49,644** | **79.9%** | **5,494** | **~204,475** | **136.34** |

**Key reads:**
- **WETH was the workhorse and the troublemaker** — 61% of all volume, but **84% of all reverts** (4,635) and 61% of gas.
- **USDC.e was the cleanest engine** — 99.5% fills, only 19 reverts (the slip-0 + skip-sim era).
- **SOMI carried 64k volume off just 7k fills** — early legs were large (~$9 notional vs ~$1 for USDC.e).

---

## 3. Buy/sell balance
| Side | Txs | Fills | Fill % |
|---|---|---|---|
| Buy | 31,244 | 24,815 | 79.4% |
| Sell | 30,874 | 24,829 | 80.4% |

Near-perfectly balanced — disciplined round-tripping, no lopsided inventory drift.

---

## 4. Status & gas economics
| Outcome | Count | Avg gas |
|---|---|---|
| Filled order | 49,644 | 419,222 |
| No-fill order | 6,980 | 185,238 |
| Reverted | 5,507 | 95,404 |
| **status ok / reverted** | **58,062 / 5,507** | revert rate **8.7%** |

**Total gas burned: 136.34 SOMI** — the true cost of the campaign. The clean
gas separation (fill ≈419k, no-fill ≈185k, revert ≈95k) is what makes the
gas-based fill classifier reliable.

---

## 5. Traded price ranges (filled)
| Pair | Min | Max | Avg | Note |
|---|---|---|---|---|
| USDC.e:USDso | 0.9996 | 1.0006 | 1.000 | rock-stable peg → safe pair |
| SOMI:USDso | 0.1457 | 0.171 | 0.153 | ~17% range → volatile, wide spread |
| WETH:USDso | 1,975 | 2,089 | 2,020 | |
| WBTC:USDso | 73,347 | 73,618 | 73,505 | |

---

## 6. Activity over time
| Day | Order txs | Fill % |
|---|---|---|
| 2026-05-26 | 107 | 88% |
| 2026-05-27 | 1,469 | 89% |
| 2026-05-28 | 3,103 | 86% |
| 2026-05-29 | 23,775 | 68% |
| 2026-05-30 | 13,109 | 82% |
| 2026-05-31 | 14,746 | 89% |
| 2026-06-01 | 5,809 | 99% |

Fill rate **climbed over the campaign** — from ~68% on the heaviest day to 99% at
the end — as skip-sim + slip-0 + USDC.e were dialled in.

**Hour-of-day (UTC):** activity peaked **12:00–16:00** (afternoon), bottomed
overnight (22:00–00:00). Peak hour 15:00 (~5,200 txs); quietest 23:00 (~0).

---

## 7. Bot-tuning takeaways (for the post-contest profit bot)
1. **USDC.e slip-0 is the engine** — 99.5% fills, ~zero reverts, stable peg. Start here.
2. **WETH is a revert trap** — 84% of all reverts; only use with skip-sim + a strict
   balance guard, or avoid for reliability.
3. **SOMI's wide spread** = high capital bleed; only for big-notional bursts, never
   for profit-making.
4. **Fill classifier is solid** — 419k/185k/95k gas tiers cleanly separate
   fill/no-fill/revert, so the dataset's `filled` flag is trustworthy for modeling.
5. **8.7% reverts** concentrate in empty-book windows + WETH — a smarter bot should
   gate on live book depth before firing (see `evidence/LIQUIDITY-BLACKOUT.md`).
