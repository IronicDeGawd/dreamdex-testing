# DreamDEX Event Contracts — Test Report

A hands-on test of **DreamDEX Event Contracts** (binary Up/Down prediction
markets on Somnia): docs, SDK, app UI, and the full on-chain trading path. Every
row in the matrix below was executed live and recorded — nothing is inferred.

- **Retested:** 2026-08-11 · Somnia **Shannon testnet** (chain 50312)
- **App:** `https://app.dreamdex.io/event-contracts`
- **SDK:** `@somnia-chain/markets-sdk@0.25.0`
- **Bot wallet:** `0x996aa4F40ea5BC951Ff9dd4f9fF1C7cE4C1a9420` (disposable; key never committed)

> **Note on the indexer.** During this test the SDK's documented testnet indexer
> (`https://187.124.114.32.nip.io/v1/graphql`) was **unreachable** (`HTTP 000`),
> while the app and the RPC were both up. So indexer-backed reads fail here, and
> market discovery had to be done from **chain logs** instead. This is a real
> availability finding (see "Open issues"), not a venue failure — the venue
> itself trades fine, as the matrix shows.

---

## Test matrix — what we tested and whether it works

| # | Area | What we tested | Works? | Evidence |
|---|------|----------------|--------|----------|
| 1 | SDK / discovery | Market discovery from chain logs (`MarketCreated`), no indexer | ✅ Yes | 12 events, BTC + ETH, 15m/1h/4h |
| 2 | SDK / read | Read market status on-chain (`getMarketOnchain`) | ✅ Yes | status, finalized, winningOutcome |
| 3 | SDK / read | Read order book on-chain (`getAllOpenOrdersOnchain`) | ✅ Yes | 3 bids + 3 asks from a maker |
| 4 | SDK / read | On-chain outcome balances (`getOutcomeBalance`) | ✅ Yes | Up/Down ERC-6909 balances |
| 5 | SDK / helper | `probabilityToPrice` / `priceToProbability` | ✅ Yes | 0.62 → 620000 → 0.62 |
| 6 | Trade / write | `faucet` TestUSDC | ✅ Yes | `0xeab04b…54b1` |
| 7 | Trade / write | `mintSet` — 1 collateral → 1 Up + 1 Down | ✅ Yes | ΔUp +6, ΔDown +6 · `0x0b3494…3fdf` |
| 8 | Trade / write | `burnSet` — merge 1 Up + 1 Down → 1 collateral | ✅ Yes | ΔUp −2, ΔDown −2 · `0x078b24…e315` |
| 9 | Trade / write | Maker order rests (PostOnly SELL Up) | ✅ Yes | orderId returned, fills 0 |
| 10 | SDK / read | Read own resting orders on-chain | ✅ Yes | 1 resting |
| 11 | Trade / write | Taker order fills (IOC BUY Up crosses book) | ✅ Yes | filled 1 @ 0.368 |
| 12 | Trade / write | `cancelOrder` (cancel resting maker) | ✅ Yes | `0xf0e282…3afe` |
| 13 | Trade / write | Settle → redeem winning side after resolution | ✅ Yes | Finalized, Down won; 10 Down → 10 USDC · `0xbfd0c9…8c24` |
| 14 | SDK / bug | PostOnly crossing order rejected **silently** (success + no orderId) | ⚠️ Repro | returns `success`, `orderId: undefined` |
| 15 | SDK / packaging | `import` under native Node (`node bot.mjs`) | ❌ No | `ERR_MODULE_NOT_FOUND` (extensionless ESM) |
| 16 | SDK / indexer | `loadMarkets` / unified `createOrder` (needs indexer snapshot) | ❌ Down | indexer `HTTP 000` |
| 17 | SDK / indexer | `listBinaryMarkets` (indexer discovery) | ❌ Down | indexer `HTTP 000` |

Legend: ✅ works · ⏳ pending settlement · ⚠️ works-but-a-bug · ❌ broken/unavailable.
The programmatic matrix (rows 1–12, 14) passed **13/15** in one automated run
(`methodology/matrix.mjs` → `methodology/matrix-result.json`); the two non-passes
are both the indexer outage (rows 16–17).

---

## UI issues from the first review — now FIXED

The app was updated since the first review; every UI issue reported is resolved.
Verified live with the screenshots in `evidence/` (before → after).

| Issue (first review) | Status now | Evidence |
|----------------------|-----------|----------|
| Selector showed "No event markets" | ✅ Fixed — a BTC market loads and is selectable | before/01 → after/RETEST-01 |
| Chart plotted **SOMI** on an ETH/BTC page | ✅ Fixed — BTC price with the strike line + Up/Down zones | before/02 → after/RETEST-02 |
| Both sides "no liquidity" | ✅ Fixed — full order book (bids 70–72¢ / asks 75–76¢, spread 3¢, last traded 58¢) | before/03 → after/RETEST-02 |
| "Max: -- USDso" wouldn't compute | ✅ Fixed — "Max: 1.01 USDso" | after/RETEST-01 |
| Asset never labelled | ✅ Fixed — "BTC" + "Will BTC settle above $63,554.50 at 16:30 UTC?" | after/RETEST-01 |
| Balance showed **1.02 trillion** USDso | ✅ Fixed — now 1.02 (a ÷10⁶-vs-÷10¹⁸ decimals bug) | after/RETEST-01 |

New, good additions since the first review: an order-book panel, a "Recently
published" results feed (BTC 15m/1h with UP/DOWN outcomes), and a live last-traded price.

---

## Open issues (still worth fixing)

1. **SDK won't `import` under native Node.** `package.json` sets
   `"type":"module"` but the published `dist/*.js` uses **extensionless relative
   imports**, so `node bot.mjs` fails on the first import with
   `ERR_MODULE_NOT_FOUND`. It only runs via a bundler or `tsx`. (Row 15.)
2. **The documented testnet indexer is unreliable** — it was fully down during
   this test (`HTTP 000` over many minutes) while the app and RPC were up. It's a
   raw-IP `nip.io` URL in the README with no stable DNS. This breaks the SDK's
   documented quickstart (`loadMarkets`, `listBinaryMarkets`, unified
   `createOrder`, `getOutcomeBalances`). Discovery via chain logs is a viable
   fallback but isn't the documented path. (Rows 16–17.)
3. **PostOnly rejection is silent.** A PostOnly order that would cross returns
   `status: success` with `orderId: undefined`, `fills: 0`, and no error — a
   caller can only tell it was rejected by null-checking `orderId`. (Row 14.)
4. **Docs mismatch on `getOutcomeBalance`.** The README shows a positional call
   `getOutcomeBalance(token, address, outcomeId)`; the actual signature is an
   object `getOutcomeBalance({ outcomeToken, account, id })`. The plural
   `getOutcomeBalances(account, market)` reads the (down) indexer; the singular
   reads on-chain.
5. Carried over from the first review: `getBinaryOrderBook` wants a pool address
   (not the `marketId` the docs say to key on); the README's binary example uses
   a fixed-strike symbol that finds nothing against the live rolling markets; the
   tick/lot grid is never published; window list says "15m/1h" but 4h exists.

**Credit:** the raw `trader.placeOrder` correctly **throws** on revert (it even
replays to recover the reason) — the "reverts don't throw" caveat applies only to
the higher-level unified verbs.

---

## Fresh transactions from this retest

Full trade re-run on BTC-60min (`marketId 0x…3f04`), RPC-only (indexer bypassed):

| Step | Tx |
|------|----|
| faucet | `0xcfaddaa5de777bda5887a4089be15e6665c5940c02944108fef4b18d01f9a062` |
| mintSet | `0xa80c0949cc4498613aa5a69c553371dc8a5c90c6376c432b01448ff5f8fdb6ab` |
| maker rest (SELL Up) | `0xf211d5aad05af43a189210a62149de841fd5a594f1677eaa256940ef7d95153c` |
| taker fill (BUY Up, 2 filled) | `0x9e5f9c286247af357ae8928e63bf8ca8f980285555ecf4cdba12f0472d1120b9` |
| settle → redeem (Down won, 10 → 10 USDC) | `0xbfd0c93c44bef14b36657e7194ada9225befc2b58d9e307561f257fa3d0a8c24` |

The maker on the book (`0x789f…`) quoted a two-sided market around 50/50 with a
~0.03 spread — tighter than the maker seen in the first review.

---

## Methodology

Scripts under `methodology/` (run with `tsx`; disposable key in a git-ignored file):

- `disco2.mjs` — discover live markets from `MarketCreated` chain logs (no indexer)
- `redo.mjs` — full trade re-run (faucet → mint → maker → taker) on one market
- `matrix.mjs` — the automated test matrix (rows 1–12, 14) → `matrix-result.json`
- `osettle.mjs` — indexer-free settler: polls `getMarketOnchain` until finalized, then redeems
- `bot.mjs` / `census.mjs` / `check.mjs` — first-review scripts (mint, book census, readback)

Row 13 confirmed: the traded market finalized (Down won) and the winning side
redeemed 1:1 — full lifecycle **faucet → mint → maker → taker → settle → redeem**
verified this retest. See `methodology/redeem-result-retest.json`.
