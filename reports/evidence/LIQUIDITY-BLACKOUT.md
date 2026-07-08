# Evidence — Exchange-wide liquidity blackout (USDC.e:USDso), 2026-06-01

> **Status: REPORTED to the dev team** (recurring liquidity blackout, with on-chain proof).

## Finding
On 2026-06-01 the dreamDEX order book went **completely empty** — 0 bids, 0 asks —
for approximately **8 minutes**, during which no taker order could execute. First
observed across **all pairs simultaneously** (USDC.e, SOMI, WETH, WBTC all returned
empty), which points to a single liquidity source (the third-party market-maker bot)
withdrawing, rather than a per-pool fault.

## Measured window (USDC.e:USDso) — exact, block-pinned
Pinned via binary search over historical `getBookLevels` state
(`find_blackout_edges.py`). Pool `0x47fD2f18426f67106DBaC82F6d21D446c5F2120b`.

| Edge | Block | Time (UTC) | Book state |
|---|---|---|---|
| Last liquidity before gap | 321994922 | 12:11:50 | bids=1, asks=1 (ok) |
| **Gap start** | **321994923** | **12:11:50** | bids=0, asks=0 (EMPTY) |
| Last empty block | 322000206 | 12:20:42 | bids=0, asks=0 |
| **Liquidity returns** | **322000207** | **12:20:42** | bids=1, asks=1 (ok) |

**Duration: 8.9 minutes** — 12:11:50 → 12:20:42 UTC on 2026-06-01 (5,284 blocks at
~0.10 s/block). The book emptied at one block (321994923) and refilled at one block
(322000207): an abrupt all-or-nothing transition, consistent with a single liquidity
provider toggling off then on.

## Recurrence — 24h scan (this is a repeated pattern, not a one-off)
Replayed the USDC.e:USDso book every ~2 min over the prior 24.2h
(`scan_blackout_history.py`, ±2 min resolution, full archive history reached).

| # | Date | Start (UTC) | End (UTC) | Duration | Block range | Precision |
|---|------|------|------|------|------|------|
| 1 | 2026-05-31 | 14:19:37 | 14:27:40 | ~8.1 min | 321212560 → 321217360 | ±2 min (scan) |
| 2 | 2026-06-01 | 12:11:50 | 12:20:42 | **8.9 min** | 321994923 → 322000207 | exact (block-pinned) |
| 3 | 2026-06-01 | 12:40:04 | ~12:40 | ≤2 min | 322011760 | single sample |
| 4 | 2026-06-01 | 12:51:29 | unmeasured | <2 min | ~322018568 | live point read |

**Summary:** 3 windows detected at scan resolution + ≥1 sub-resolution flicker
(window 4, caught live). Total measured downtime ~16.1 min over 24.2h → **~98.9%
uptime (UPPER BOUND** — the ±2 min scan misses short flickers, so true downtime is
higher; window 4 proves sub-2-min outages occur between samples). Two severe ~8-min
blackouts ~22h apart; a **cluster of 3 events in 40 min** around 2026-06-01 noon UTC
(liquidity notably shakier then). All consistent with a single MM toggling off/on.

## Why this is on-chain proof (not API noise)
`getBookLevels(isBid, numLevels)` is a `view` function on the pool contract. An
archive node answers it at **any historical block** via `block_identifier`. We replayed
the book state block-by-block across the window — this is the chain's own recorded
state at each block, independent of any REST/indexer caching. Both RPC (`getBookLevels`)
and the REST `/v0/orderbooks` endpoint agreed it was empty in real time, and the
historical replay confirms the exact span.

## Root-cause assessment
- The blackout was **clean and continuous**, then liquidity returned **all at once** —
  the signature of a single provider toggling off/on, not a flaky matching engine.
- `getBookLevels` returned **correct, consistent** state at every historical block —
  the view function is reliable; it faithfully reported a genuinely empty book.
- Contest traders predominantly send IOC **takers** (consume liquidity); the resting
  liquidity is supplied by the market-maker bot. When it withdrew, the book emptied.

**Conclusion:** Not a contract/view bug. A real ~8-minute exchange-wide liquidity
outage — a **single-point-of-failure in liquidity provision**. Worth surfacing to the
team as a resilience observation: the venue has no fallback liquidity when the sole MM
steps away.

## Reproduce
```
# 1) Map the empty window at ~30s resolution over the last N minutes:
docker exec dreamdex-agent python3 /app/evidence/replay_book_state.py \
    --pair USDC.e:USDso --minutes 60 --step-blocks 300

# 2) Pin the exact transition blocks once you have rough brackets:
docker exec dreamdex-agent python3 /app/evidence/find_blackout_edges.py \
    --pair USDC.e:USDso \
    --ok-block 321994893 --empty-block 321995493 \
    --empty-block2 322000053 --ok-block2 322000353
```
Scripts: `evidence/replay_book_state.py`, `evidence/find_blackout_edges.py`.
