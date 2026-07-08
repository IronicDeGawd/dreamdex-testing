# DreamDEX Mainnet Stress Test — Findings, Methods & Bleed Analysis

**Participant wallet:** `0xF4c825F3C2970153d78B407CF190861dd4E2b905` (leaderboard handle `trader-9`)
**Network:** Somnia mainnet (chain 5031)
**Engagement:** 2026-05-27 → 2026-06-01
**Scale exercised:** 51,000+ on-chain transactions + 8,189 instrumented trades (local DB) + 552 order-book ticks

---

## Executive summary

We treated the contest as what it is — a **load and correctness test of the dreamDEX CLOB**, vault accounting, matching engine, and surrounding infra — and drove varied trading patterns at high volume to surface behavior the docs do not describe.

We found **7 findings**: **3 reported to the dev team** (A1 and A2 confirmed by `emrey.somi`; A7, the recurring liquidity blackout, reported with on-chain proof), and 4 contract/API issues documented here for the first time. We also quantified the **economic bleed** of round-trip trading under the contest's zero-fee regime, using our transaction corpus.

**Headline behavioral result:** because fees are zero (`makerBps = 0`, `takerBps = 0`), the *entire* cost of trading is the bid/ask spread plus chosen slippage. Pair choice therefore dominates everything: the SOMI/USDso book runs a **measured 1.11% realized spread**, while WETH, WBTC, and USDC.e all run **~0.02%** — a **~55× difference**. At the tight end, **$1 of USDso supports ~$9,600 of round-trip volume**; at the wide end it supports under $200.

---

## Part A — Findings

> Severity is our assessment of impact on an integrator. "Status" distinguishes issues already raised with the team from those first documented here.

### A1. `expireTimestampNs = 0` is silently rejected, not treated as "no expiry"  ✅ REPORTED (confirmed by dev)
**Severity: High (correctness, silent failure)**

- **Docs say:** `expireTimestampNs` — "Expiration in nanoseconds since Unix epoch (0 = no expiry)."
- **Actual:** Any order with `expireTimestampNs = 0` is silently rejected. The transaction succeeds (`status = 1`) and consumes ~300K gas, but `placeOrder` / `placeTakerOrderWithoutVault` return `(success = false, orderId = 0)` and emit **zero logs**. No revert reason — undiagnosable from the receipt alone.
- **Confirmed:** `eth_call`-simulated 6× across varying price/qty/side — all `(false, 0)` while `expireNs = 0`; flipping to `now + 3600s` returned `(true, <orderId>)` immediately and the broadcast tx emitted the expected logs.
  - Working sell tx: `0x343448e67781ef72792cf0a6f4e0f12322876d31c30e88a550cc0815f02b02d9`
  - Working bid tx: `0xf991386f358d9c7bd29acaa4692c2c243052fb4c4c22c0b30d9247f1d65ca01e`
- **Fix:** `expireNs = BigInt(floor(Date.now()/1000) + 3600) * 1_000_000_000n` — and update the docs, or make the contract treat `0` as documented.

### A2. Native SOMI uses a non-standard vault sentinel `0x28f34De…`, not `address(0)`  ✅ REPORTED (confirmed by dev)
**Severity: High (funds appear lost)**

- **Behavior:** For the SOMI/USDso pool, the vault tracks native SOMI under the marker `0x28f34DeFd2b4CB48d9eE6d89f2Be4Bc601694c00` — **not** `address(0)` and not the common `0xEeee…EEeE` convention.
- **Impact:** `getWithdrawableBalance(user, address(0))` returns `0` even when the vault holds native SOMI from filled BUY orders. Funds *appear* lost. ~$14 of native SOMI was untraceable for hours until `emrey.somi` pointed at the real sentinel; all of it was recoverable.
- **Discoverability:** The correct marker is exposed as the `base` field in `GET /v0/markets` for native pools — but this is not called out in the withdraw/vault docs.
- **Fix:** Always read `MARKETS[pair].base` for native pools; never hardcode `address(0)`. Documentation should state the sentinel explicitly in the withdraw section.

### A3. Native SOMI `vault_withdraw` reverts `0x734b5f70` for all amounts  🆕 NEW
**Severity: Medium (one-way deposits)**

- **Behavior:** Withdrawing native SOMI from the pool vault fails with selector `0x734b5f70` regardless of amount, while ERC20 (e.g. USDso) vault withdrawals succeed in small chunks.
- **Impact:** Native SOMI deposited to the vault is effectively **one-way** — strandable. Combined with A2, native-pool vault accounting is the sharpest edge for integrators.
- **Ask:** Document the constraint, or surface a decode for `0x734b5f70`.

### A4. `OrderPlaced` emits `filled = 0` even on real fills  🆕 NEW
**Severity: Medium (observability)**

- **Behavior:** The `filled` field on emitted events reads `0` even when the order demonstrably filled (vault balance moved).
- **Impact:** Integrators trusting the event field will miscount fills. We had to use **vault-balance delta** (pre vs post `getWithdrawableBalance`) as the authoritative fill signal.
- **Ask:** Populate `filled` correctly, or document that vault-delta is the source of truth.

### A5. `getOwnOpenOrders()` returns empty despite resting orders  🆕 NEW
**Severity: Medium (view correctness)**

- **Behavior:** With orders visibly resting in the book (confirmed via the order-book endpoint), `getOwnOpenOrders()` via `eth_call({from: owner})` returns `[]`.
- **Workaround:** Simulate `cancelOrder(orderId)` — the revert payload encodes the owner, letting you reconstruct ownership.
- **Ask:** Fix the accessor; it is the natural integration point for order management.

### A6. `eth_call` pre-trade simulation produces ~47% false negatives  🆕 NEW
**Severity: Medium (throughput / matching)**

- **Behavior:** Gating each order on an `eth_call` simulation (a common safety pattern) rejected a large fraction of orders that **would have filled** if broadcast. Bypassing the simulation gate raised our effective fill rate from ~53% to 95%+ on the same book.
- **Data:** Of 8,189 instrumented trades, **527 were silent rejects** and ~46% were non-fills overall; much of that gap is simulation false-negatives plus A1.
- **Ask:** Investigate divergence between simulated and broadcast execution for marketable orders (likely state/timing sensitivity in the matching path).

### A7. Exchange-wide liquidity blackout — order book empty for ~8.9 min  ✅ REPORTED (availability incident)
**Severity: High (availability) — root cause is liquidity structure, not the contract**

- **What happened:** The order book repeatedly went **completely empty** (0 bids, 0 asks) on USDC.e:USDso — and, observed at the same time, on SOMI / WETH / WBTC — blocking all taker execution venue-wide. This is a **recurring pattern**, not a one-off: a 24h on-chain replay (±2 min) found 3 windows plus ≥1 sub-resolution flicker caught live.

| # | Date | Start (UTC) | End (UTC) | Duration | Block range | Precision |
|---|------|------|------|------|------|------|
| 1 | 2026-05-31 | 14:19:37 | 14:27:40 | ~8.1 min | 321212560→321217360 | ±2 min |
| 2 | 2026-06-01 | 12:11:50 | 12:20:42 | **8.9 min** | 321994923→322000207 | exact (block-pinned) |
| 3 | 2026-06-01 | 12:40:04 | ~12:40 | ≤2 min | 322011760 | single sample |
| 4 | 2026-06-01 | 12:51:29 | unmeasured | <2 min | ~322018568 | live point read |

- **Magnitude:** ~16.1 min measured downtime over 24.2h → **~98.9% uptime (upper bound** — the ±2 min scan misses short flickers; window 4 proves sub-2-min outages occur between samples, so true downtime is higher). Two severe ~8-min blackouts ~22h apart; a **cluster of 3 events in 40 min** around 2026-06-01 noon UTC.
- **Proof method:** `getBookLevels(isBid, numLevels)` is a `view` function, so an archive node answers it at any historical block via `block_identifier`. We replayed the book state block-by-block — chain-authoritative, independent of any API caching — and binary-searched the exact transition blocks. Both RPC (`getBookLevels`) and the REST `/v0/orderbooks` endpoint agreed in real time.
- **Root-cause assessment:** The book emptied at a single block and refilled at a single block — an abrupt all-or-nothing flip, the signature of **one liquidity provider toggling off then on**, not gradual multi-actor cancellation. `getBookLevels` returned correct, consistent state throughout (the view function is not at fault). Resting liquidity is supplied by a single third-party market-maker bot; when it withdrew, the venue had no fallback.
- **Ask:** This is a **resilience / single-point-of-failure** finding. Consider redundant/independent market makers or a liquidity-floor mechanism so takers aren't fully blocked when one MM steps away.
- **Evidence + reproduce:** `evidence/LIQUIDITY-BLACKOUT.md`, `evidence/replay_book_state.py`, `evidence/find_blackout_edges.py`.

---

## Part B — Methods / test vectors exercised

A summary of the load patterns we drove, so the team can map findings to traffic:

1. **Paired-EOA cross-fill attempts** — two wallets quoting/taking against each other (early phase).
2. **High-rate IOC taker bursts** — `placeTakerOrderWithoutVault` (wallet-funded, wallet-settled) in tight round-trip loops.
3. **Multi-pool rotation** — SOMI / WETH / WBTC / USDC.e to compare matching and fill behavior across books.
4. **Simulation-gate bypass (`skip_sim`)** — see A6.
5. **Direct RPC burst engine** — bypassing the REST layer, local nonce management, 2M gas limit (Somnia 63/64 rule), pipelined legs (~5× the REST throughput).
6. **Slippage sweep** — slip-0 / 1 / 3 ticks to map fill rate vs bleed.
7. **Vault vs wallet settlement paths** — `placeOrder` (vault) vs `placeTakerOrderWithoutVault` (wallet), confirming the latter never leaves funds in the vault (no sentinel exposure).

**Settlement note (useful for integrators):** `placeTakerOrderWithoutVault` is "wallet in, wallet out" — it never touches the vault, so it sidesteps A2/A3 entirely. The REST `/manual` SELL path *does* route through the vault and must auto-drain.

---

## Part C — Empirical bleed analysis

**Corpus:** 8,189 instrumented trades (2026-05-27 → 05-29, REST/manual phase) + 552 order-book ticks; on-chain anchors from the 51K-tx RPC burst phase (05-31 → 06-01).

### C1. Zero fees → spread is the entire cost
With `makerBps = 0` and `takerBps = 0`, no fee is charged on either side. The only value lost per round-trip is **(spread crossed) + (slippage ticks × tick size × 2)**. This makes pair selection and slippage the sole levers on capital efficiency.

### C2. Realized spread by pair (measured from 552 ticks)
| Pair | Realized spread | Relative |
|---|---|---|
| USDC.e:USDso | **0.0200%** | 1.0× (tightest) |
| WBTC:USDso | 0.0201% | 1.0× |
| WETH:USDso | 0.0205% | 1.0× |
| SOMI:USDso | **1.1102%** | **~55× wider** |

The native pair is the outlier by a wide margin — and it is also the only native-sentinel pool (A2/A3). Volume-maximizing strategy is unambiguous: trade the stable/blue-chip books.

### C3. Fill rate by pair (8,189 trades)
| Pair | Trades | Fill rate |
|---|---|---|
| USDC.e:USDso | 156 | **82.7%** |
| SOMI:USDso | 7,540 | 54.6% |
| WETH:USDso | 203 | 35.5% |
| WBTC:USDso | 290 | 30.3% |

### C4. Outcome breakdown (8,189 trades)
| Outcome | Count | Share |
|---|---|---|
| Filled | 4,409 | 53.8% |
| Error | 2,172 | 26.5% |
| Failed | 1,068 | 13.0% |
| Silent reject | 527 | 6.4% |
| Unverified | 13 | 0.2% |

Silent rejects + a portion of errors trace to A1 (expire=0) and A6 (sim false-negatives).

### C5. Capital efficiency (on-chain anchor, slip-0, USDC.e)
Measured live during the burst phase:
- Volume window: $192,333 → $198,691 (**+$6,358**)
- USDso consumed: $2.24 → $1.58 (**−$0.66**)
- **Efficiency: ≈ $9,633 of volume per $1 of USDso bled**

Slippage cost per round-trip cycle (tick = 0.0001):
| Slippage | Cost / cycle | Reachable volume per $1 USDso |
|---|---|---|
| slip-0 | $0.0002 | ~$9,600 |
| slip-1 | $0.0004 | ~$4,800 |
| slip-3 | $0.0008 | ~$2,400 |

### C6. Gas characteristics
- Every complex order needs a **2,000,000 gas limit** (Somnia 63/64 forwarding rule) or it under-provisions sub-calls.
- Measured burn: **~0.00236 SOMI/tx** (~0.0047 SOMI per BUY+SELL cycle).
- Practical consequence: volume is jointly capped by USDso (spread fuel) and SOMI (gas) — whichever runs out first.

---

## Appendix — environment

- Pools (mainnet 5031): SOMI/WETH/WBTC/USDC.e vs USDso. WBTC base = 8 decimals; USDC.e base = 6 decimals (decimal-mismatch trap for sizing).
- Native sentinel (SOMI pool): `0x28f34DeFd2b4CB48d9eE6d89f2Be4Bc601694c00`.
- Third-party market maker seeds deep two-sided liquidity (Gnosis Safe wallets), which is why same-account "wash" patterns yield no spread advantage — any marketable order hits the MM's inside quote first.
