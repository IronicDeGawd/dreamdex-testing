# DreamDEX Event Contracts — Builder Feedback

Feedback on **DreamDEX Event Contracts** (binary prediction markets on Somnia)
from actually building and running a trading bot against the venue — not just
reading the docs. Covers **docs, the SDK, the app UI, and the end-to-end trading
path**.

- App reviewed: `https://app.dreamdex.io/event-contracts`
- Docs reviewed: `https://docs.dreamdex.io/developers/event-contracts`
- SDK exercised: `@somnia-chain/markets-sdk@0.25.0`
- Network: Somnia **Shannon testnet** (chain 50312)

---

## How I tested

I ran a disposable bot wallet (`0x996aa4F40ea5BC951Ff9dd4f9fF1C7cE4C1a9420`,
funded with 10 STT gas) through the full lifecycle using the SDK:
faucet TestUSDC → mint a complete set → rest a maker order → cross as a taker
for a real fill. Every step is a real on-chain transaction (hashes below). I
also cross-checked the app UI against the on-chain order book at the same
instant, and read the SDK source and the docs in full.

---

## 1. The protocol works — proven with live transactions

The core Event Contracts mechanics behave correctly, one round-trip per write,
receipts returned:

| Step | Result | Tx hash |
|------|--------|---------|
| `faucet()` TestUSDC | success | `0x230f0dbb1f3192cb74ed882cb7b6f90f18b106d86bdf73cae349cbd8f787baba` |
| `mintSet` 10 → 10 Up + 10 Down | success | `0xf16cad949ae5729e8d9b34c3ac2298a351ee0b308df3ff4890f925e24ab3fe05` |
| Maker: rest `SELL Up @0.55 ×5` (PostOnly) | rested, orderId `129127…019` | `0x0b4fc3c4bddb5dc0680c2609e92e3322425738c57e964be40b5e8924f93a44bf` |
| **Taker: `BUY Up` IOC → matched a real maker** | **filled 3 Up @ 0.045**, taker remaining 0 | `0xbef57a15051c7c3283d555eacbed1c8dd9397a27635d53fb1be5c439cb066bd9` |
| **Settle → redeem the winning side after resolution** | market **Finalized**, Down won; **redeemed 10 Down → 10 USDC** | `0x4a6774ced9c6bcc74c7141efdf401e6b8f5626f3e0f1cc97dcf68f0ac724db0a` |

Traded market: `0x…3e04` — BTC "closes at or above its opening price", 4h window,
pool `0x0e7d5043787a567282f48c7d9d3627c917495a2d`, 6-decimal collateral. The
taker order matched maker `0x7970…A460`'s resting ask at 0.045.

At expiry the oracle resolved the question **false** (BTC closed *below* its
opening price), so the market finalized with `winningOutcome: 1` (Down/NO). The
winning **Down** tokens redeemed 1:1 for collateral; the losing **Up** tokens
were correctly worth nothing. **The full lifecycle — faucet → mint → maker →
taker → settle → redeem — works end to end. The on-chain venue is healthy.**

---

## 2. The app UI is the broken layer (highest priority)

A liquidity census across all six live markets (read straight from the pool
contracts) shows **every rolling market has a two-sided book from a
market-maker (`0x7970…A460`) — 3 bids + 3 asks each**:

```
BTC 15min   bids=3 asks=3 makers=1        ETH 15min   bids=3 asks=3 makers=1
BTC 60min   bids=3 asks=3 makers=1        ETH 60min   bids=3 asks=3 makers=1
BTC 240min  bids=3 asks=4 makers=2 (←me)  ETH 240min  bids=3 asks=3 makers=1
```

Yet at that same moment the app shows **"No event markets," both Up and Down
"no liquidity," and "Max: -- USDso"** — see `evidence/`. So this is **not** a
cold-start with no liquidity; it is a **frontend / data-plumbing bug**: the app
is not rendering an on-chain order book that demonstrably exists (I placed and
filled orders against it). The likely trigger is that these rolling markets
carry `symbol: undefined`, so the market picker finds nothing while the trade
panel — which keys off the live window — keeps ticking. **This is the #1 fix.**

Two further confirmed UI issues:

- **The chart shows the wrong asset.** On an ETH/BTC event page the chart plots
  **SOMI/USDso** (~$0.097) and the header reads "SOMI/USDso $0.09". The
  underlying (ETH vs BTC) is **never labelled** anywhere in the trade UI — the
  only place the asset appears is the on-chain `question` field. See
  `evidence/02-chart-shows-somi-on-eth-market.jpg`.
- **Liquidity is real but thin and wide.** A single maker quotes Up bid 0.024 /
  ask 0.045 on BTC with 120–280 tokens of depth (a ~2× spread). Even once the UI
  renders it, one maker at that spread will not feel tradeable — worth seeding a
  second, tighter maker.

### Screenshots (in `evidence/`)

| File | Shows |
|------|-------|
| `01-no-markets-no-liquidity.jpg` | Selector "No event markets", both sides "no liquidity", Max "--", a live window ticking |
| `02-chart-shows-somi-on-eth-market.jpg` | Chart plotting SOMI/USDso (0.0973–0.0976) on an ETH event page |
| `03-no-liquidity-while-onchain-has-book.jpg` | "no liquidity" shown at a moment the ETH 15-min market has 3 bids + 3 asks on-chain |

---

## 3. SDK — well designed, but the first-run path fights you

The SDK is genuinely good: ccxt-familiar surface, a properly typed
discriminated-union `Market` type, a WebSocket live feed with no polling, React
hooks that auto-watch, one-round-trip writes via `realtime_sendRawTransaction`,
and a debug sink that maps 1:1 onto OpenTelemetry. The problems are in
packaging and a couple of sharp edges:

- **Cannot `import` under native Node.** `package.json` sets `"type":"module"`
  but the published `dist/*.js` uses **extensionless relative imports**
  (`export { SomniaMarkets } from "./unified/exchange"`). Node's native ESM
  resolver rejects these — a plain `node bot.mjs` fails on the first import with
  `ERR_MODULE_NOT_FOUND`.
- **Naive bundling also breaks.** esbuild-ing the app inlines `viem` and hits
  `Dynamic require of "events" is not supported`, which then makes *every* read
  fail as a misleading `"An unknown RPC error occurred."` I had to run via
  **`tsx`** to get anything working. Since the README advertises a "Node bot"
  workflow, this out-of-the-box failure is worth fixing (emit `.js` extensions
  or ship a CJS build; document `tsx`-or-bundler-with-externals).
- **PostOnly rejection is silent.** A `SELL Down @0.55` (= buy Up @0.45, which
  crosses the 0.045 asks) was correctly refused by PostOnly — but it returned
  `status: success`, `orderId: undefined`, `fills: 0`, and **no error**. A caller
  cannot distinguish "rested" from "rejected-for-crossing" except by
  null-checking `orderId`. An explicit signal would help.
- **`marketId` vs `poolAddress` inconsistency.** The docs say "key by `marketId`,
  never pool address," but `getBinaryOrderBook(marketId)` throws
  `Address … is invalid` — it requires `poolAddress`. Pick one convention or
  accept both.
- **Reads that need the outcome-token address.** `getOutcomeBalance` needs the
  ERC-6909 outcome-token address, but the market row from `listBinaryMarkets`
  doesn't expose it directly, so a balance read needs an extra lookup.
- **Correction / credit:** the raw `trader.placeOrder` **does throw on revert**
  (it even replays the call to recover the reason). The "a reverted write does
  not throw" gotcha applies only to the higher-level unified verbs — good design
  at the raw tier.

---

## 4. Docs

The **gotchas page is excellent** — real field notes (reverts, float-price
precision, manual lot sizing, gate on on-chain status, order-expiry as a
dead-man's switch). Gaps found:

- **The tick/lot grid it tells you to respect is never published** for binary
  markets. The gotchas say "size to the venue's lot grid yourself" and "only
  0.25/0.5/0.75 survive binary floats," but no page gives the actual
  `tickSize` / `lotSize` / `minQuantity` (spot documents all three).
- **Stale window list.** Docs say "15-minute and 1-hour"; on-chain there are
  **15m / 1h / 4h** markets for both BTC and ETH, and the UI also offers
  1m/5m/Daily.
- **README example doesn't match the live venue.** It uses a fixed-strike symbol
  (`BTC-95000-31DEC26/USDC#YES`); the markets that actually exist are rolling
  reference markets (`asset: ETH`, `strike: 0`, no human symbol), so a copy-paste
  quickstart finds nothing.
- **Raw-IP indexer URL.** The testnet indexer is hardcoded in the README as
  `https://187.124.114.32.nip.io/v1/graphql` — no stable DNS; it will rot and
  looks alarming in a quickstart.
- **Naming drift.** "Prophecy Oracle" (UI) vs "Oracle Hub" (docs) vs `oracleHub`
  (SDK) — three names for one thing.
- One linked market-structure page 404s at the path linked from the overview.

---

## Bottom line

The primitive and the SDK design are strong, and the on-chain venue works — I
confirmed it by trading on it. The distance between "looks dead" and "works" is
almost entirely **the frontend not rendering a live on-chain order book**, plus
the SDK's import/packaging friction. Fix those two and Event Contracts is
genuinely usable today.

---

## Methodology

The scripts under `methodology/` are the exact ones used (run with `tsx`; a
disposable key was written to a git-ignored file and never committed):

- `bot.mjs` — faucet → mint set → rest maker orders → read back on-chain
- `trade`/`check.mjs` — outcome balances + full order-book readback
- `census.mjs` — liquidity census across all trading markets
- `probe2.mjs` — market-structure / field inspection
- `settle.mjs` — polls the traded market until the oracle resolves it, then
  cancels resting orders and redeems the winning side (`redeem-result.json`)

Settlement resolved on the 4h BTC market: **Finalized, Down won**, redeem tx
`0x4a6774…db0a` — see `redeem-result.json`.
