# DreamDEX Event Contracts — Test Report

A hands-on test of **DreamDEX Event Contracts** (binary Up/Down prediction
markets on Somnia): docs, SDK, app UI, and the full on-chain trading path. Every
tested feature is listed below as **working / not working / issue**. Everything
was executed live and recorded — nothing is inferred.

- **Tested:** 2026-08-11 · Somnia **Shannon testnet** (chain 50312)
- **App:** `https://app.dreamdex.io/event-contracts`
- **SDK:** `@somnia-chain/markets-sdk@0.25.0`
- **Bot wallet:** `0x996aa4F40ea5BC951Ff9dd4f9fF1C7cE4C1a9420` (disposable; key never committed)

> **Context.** At the first look the app's **backend was down**, so the app
> couldn't load event markets — the empty selector, "no liquidity", the wrong
> price on the chart and the odd balance were all symptoms of that outage, not UI
> defects. This report retests every feature with the backend back up. Separately,
> the SDK's *documented* testnet indexer (`187.124.114.32.nip.io`) was unreachable
> during this test, so indexer-backed SDK calls fail here and market discovery was
> done from chain logs — that is a distinct availability issue (see "Not working").

---

## Test matrix — every feature we tested

| # | Area | Feature tested | Status | Evidence |
|---|------|----------------|--------|----------|
| 1 | App UI | Event market loads and is selectable | ✅ Working | working/RETEST-01 |
| 2 | App UI | Price chart with strike line + Up/Down zones (correct asset) | ✅ Working | working/RETEST-02 |
| 3 | App UI | Order book renders (bids 70–72¢ / asks 75–76¢, spread 3¢, last 58¢) | ✅ Working | working/RETEST-02 |
| 4 | App UI | Max-stake computes; question + asset labelled; balances correct | ✅ Working | working/RETEST-01 |
| 5 | App UI | "Recently published" results feed (settled UP/DOWN outcomes) | ✅ Working | working/RETEST-01 |
| 6 | SDK read | Market discovery from chain logs (`MarketCreated`, no indexer) | ✅ Working | 12 events; BTC+ETH; 15m/1h/4h |
| 7 | SDK read | Market status on-chain (`getMarketOnchain`) | ✅ Working | status/finalized/winner |
| 8 | SDK read | Order book on-chain (`getAllOpenOrdersOnchain`) | ✅ Working | 3 bids + 3 asks |
| 9 | SDK read | Outcome balances on-chain (`getOutcomeBalance`) | ✅ Working | ERC-6909 Up/Down |
| 10 | SDK read | Own resting orders on-chain | ✅ Working | 1 resting |
| 11 | SDK helper | `probabilityToPrice` / `priceToProbability` | ✅ Working | 0.62 → 620000 → 0.62 |
| 12 | Trade write | `faucet` TestUSDC | ✅ Working | `0xeab04b…54b1` |
| 13 | Trade write | `mintSet` — 1 collateral → 1 Up + 1 Down | ✅ Working | ΔUp +6, ΔDown +6 |
| 14 | Trade write | `burnSet` — merge 1 Up + 1 Down → 1 collateral | ✅ Working | ΔUp −2, ΔDown −2 |
| 15 | Trade write | Maker order rests (PostOnly SELL Up) | ✅ Working | orderId returned |
| 16 | Trade write | Taker order fills (IOC BUY Up crosses book) | ✅ Working | filled 1 @ 0.368 |
| 17 | Trade write | `cancelOrder` (cancel resting maker) | ✅ Working | `0xf0e282…3afe` |
| 18 | Trade write | Settle → redeem winning side after resolution | ✅ Working | Down won; 10 → 10 USDC · `0xbfd0c9…8c24` |
| 19 | SDK | PostOnly crossing order rejected **silently** (success, no orderId) | ⚠️ Issue | returns `success`, `orderId: undefined` |
| 20 | SDK | `import` under native Node (`node bot.mjs`) | ❌ Not working | `ERR_MODULE_NOT_FOUND` (extensionless ESM) |
| 21 | SDK | `loadMarkets` / unified `createOrder` (needs indexer) | ❌ Not working | documented indexer `HTTP 000` |
| 22 | SDK | `listBinaryMarkets` (indexer discovery) | ❌ Not working | documented indexer `HTTP 000` |

Legend: ✅ working · ⚠️ works but has an issue · ❌ not working during this test.
The programmatic subset (SDK reads/writes + the PostOnly issue) ran in one
automated pass — `methodology/matrix.mjs` → `methodology/matrix-result.json`.

---

## Working

- **All app UI features.** With the backend up, the market loads, the chart shows
  the correct asset with the strike line and Up/Down zones, the order book renders
  with a live spread and last-traded price, max-stake and balances compute, the
  question/asset are labelled, and the results feed populates. These were simply
  **unavailable during the backend outage** at the first look — compare
  `evidence/outage/` (backend down) with `evidence/working/` (backend up).
- **The full trading path, on-chain.** faucet → mintSet → burnSet/merge → maker
  rest → taker fill → cancel → settle → redeem, all verified with real
  transactions (see below). A maker (`0x789f…`) quoted a two-sided book around
  50/50 with a ~0.03 spread.
- **Indexer-free SDK reads.** Discovery from chain logs and `getMarketOnchain` /
  `getAllOpenOrdersOnchain` / `getOutcomeBalance` all read directly from the RPC.

## Not working (during this test)

- **SDK won't `import` under native Node.** `package.json` sets `"type":"module"`
  but the published `dist/*.js` uses **extensionless relative imports**, so
  `node bot.mjs` fails on the first import with `ERR_MODULE_NOT_FOUND`. It runs
  only via a bundler or `tsx`.
- **The SDK's documented testnet indexer was down** — `HTTP 000` over many
  minutes while the app and RPC were up. It is a raw-IP `nip.io` URL in the README
  with no stable DNS. This breaks the documented SDK path (`loadMarkets`,
  `listBinaryMarkets`, unified `createOrder`, `getOutcomeBalances`). Discovery via
  chain logs is a working fallback but isn't the documented path.

## Issues (works, but rough edges)

- **PostOnly rejection is silent.** A PostOnly order that would cross returns
  `status: success` with `orderId: undefined`, `fills: 0`, and no error — a caller
  can only tell it was rejected by null-checking `orderId`.
- **Docs mismatch on `getOutcomeBalance`.** README shows positional
  `getOutcomeBalance(token, address, id)`; the real signature is an object
  `getOutcomeBalance({ outcomeToken, account, id })`. The plural
  `getOutcomeBalances(account, market)` reads the (down) indexer; the singular
  reads on-chain.
- **`getBinaryOrderBook` wants a pool address**, not the `marketId` the docs say
  to key on; the README's binary example uses a fixed-strike symbol that finds
  nothing against the live rolling markets; the tick/lot grid is never published;
  the window list says "15m/1h" but 4h markets also exist.

**Credit:** the raw `trader.placeOrder` correctly **throws** on revert (it even
replays to recover the reason) — the "reverts don't throw" caveat applies only to
the higher-level unified verbs.

---

## Transactions from this test

Full trade re-run on BTC-60min (`marketId 0x…3f04`), RPC-only (indexer bypassed):

| Step | Tx |
|------|----|
| faucet | `0xcfaddaa5de777bda5887a4089be15e6665c5940c02944108fef4b18d01f9a062` |
| mintSet | `0xa80c0949cc4498613aa5a69c553371dc8a5c90c6376c432b01448ff5f8fdb6ab` |
| maker rest (SELL Up) | `0xf211d5aad05af43a189210a62149de841fd5a594f1677eaa256940ef7d95153c` |
| taker fill (BUY Up, 2 filled) | `0x9e5f9c286247af357ae8928e63bf8ca8f980285555ecf4cdba12f0472d1120b9` |
| settle → redeem (Down won, 10 → 10 USDC) | `0xbfd0c93c44bef14b36657e7194ada9225befc2b58d9e307561f257fa3d0a8c24` |

---

## Methodology

Scripts under `methodology/` (run with `tsx`; disposable key in a git-ignored file):

- `disco2.mjs` — discover live markets from `MarketCreated` chain logs (no indexer)
- `redo.mjs` — full trade re-run (faucet → mint → maker → taker) on one market
- `matrix.mjs` — the automated feature checks → `matrix-result.json`
- `osettle.mjs` — indexer-free settler: polls `getMarketOnchain` until finalized, then redeems
- `bot.mjs` / `census.mjs` / `check.mjs` — earlier scripts (mint, book census, readback)

The full lifecycle **faucet → mint → maker → taker → settle → redeem** is verified
this test; the traded market finalized (Down won) and the winning side redeemed
1:1 (`methodology/redeem-result-retest.json`).
