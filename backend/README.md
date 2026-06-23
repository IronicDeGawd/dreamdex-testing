# DreamDEX Round 3 — Profit Maker Agent

> **Goal change from R1/R2.** R1/R2 were scored on **raw volume**, so we burned capital for
> turnover. **R3 ranks on `Effective Volume = Raw Volume × (1 + PnL%)`** — profit now multiplies
> your score and a wiped balance counts as **zero**. So this is a *profit-first* market maker,
> not a volume sprinter. Full rules + verified mechanics: `../context/plan/round3-rules.md`.

## The edge (where profit comes from)
1. **Spread capture** — two-sided PostOnly quotes; every SELL is forced ≥ BUY + margin ticks, so
   each round-trip is profitable (the proven R2 `profit_maker.py` no-bleed invariant).
2. **Maker yield** — DreamDEX redistributes ~3.3% APY USDso yield to makers each period, weighted
   by a Gaussian on distance-to-mid (`W = exp(-(P_order−P_mid)²/2σ²)`), auto-settled, **both sides
   must rest**. ⇒ quote tight and on both sides to maximize the yield slice.
3. **Volume** — the activity above still earns milestone rewards ($25 / 500k volume) and feeds the
   `× (1 + PnL%)` ranking, but only as a by-product of *profitable* trading.

## Hard constraints (from the rules)
- **$150 start, no top-ups ever.** Working capital must be defended — PnL% is relative to 150.
- **$20 USDso reserve** held untouched (gas safety + PnL cushion). Working capital = balance − 20.
- **50 SOMI gas, no refills.** More gas only by converting our own USDso → SOMI (and that spend
  hurts PnL), so gas is spent deliberately and refueled from working capital, never the reserve.
- **Eligible pairs only:** `BTC/USDso`, `ETH/USDso`, `SOMI/USDso` (no stablecoin pairs).
- **>24h with no on-chain trade = auto-DQ** → a liveness guard forces a tiny tick if ever idle.

## Architecture
Deterministic execution in the hot loop; an LLM only as a slow, periodic strategist.

```
backend/
  config.py            # R3 constants: pools, tokens, reserve, pairs, Gemini, RPC (UPDATED)
  trading/             # REUSED — wallet.py (sign/broadcast, 5gwei tip), dreamdex.py (place_order,
                       #          SIWE, vault deposit/withdraw, getWithdrawableBalance), manual.py
  monitor/             # REUSED — db.py (+ context table), leaderboard.py, portfolio.py, prices.py
  agent_v3/            # NEW profit market-maker
    runner.py          #   entrypoint: supervises pairs, drives the control loop, liveness guard
    market_data.py     #   per-pair live book + EMA mid (getMidpointEmaState), spread, short-vol, depth
    maker.py           #   two-sided PostOnly quoter per pair (ports profit_maker.py no-bleed logic)
    inventory.py       #   position + realized/unrealized PnL, reserve & working-capital math
    gas.py             #   SOMI watch + USDso→SOMI refuel (gas≥5M), reserve-aware, drip-sell
    strategist.py      #   Gemini 2.5 Pro via Vertex ADC — periodic pair/spread/size decisions
    context_store.py   #   rich per-quote/per-fill context → SQLite (the LLM validation dataset)
  data/                # sqlite (agent.db)
  Dockerfile, docker-compose.yml, requirements.txt   # deploy scaffold (UPDATED for R3 entrypoint)
  archive/             # all R2 code + ARCHIVE.md runbook (reference only, nothing runs)
```

### Control loop (deterministic, every few seconds per pair)
1. Pull live book + EMA mid + short-window volatility for each active pair.
2. Desired spread = `max(protocol_tick_floor, k × volatility)`; size = leg_usd bounded by working
   capital and **inventory skew** (skew quotes to mean-revert inventory toward ~0, capping risk).
3. Maintain a resting BUY at/near best-bid and SELL at `max(best_ask, buy_px + margin_ticks)` —
   PostOnly so we never pay taker and never cross. Detect fills by balance delta (events lie).
4. On fill: log full context, update inventory + PnL, re-quote. On drift past threshold: re-quote.
5. Gas manager refuels SOMI from working capital if below floor. Liveness guard prevents 24h idle.

### Strategist (Gemini 2.5 Pro, Vertex ADC, every ~5–10 min — NOT in the hot loop)
Reads recent context-store stats (per-pair realized PnL, fill rate, captured spread, volatility,
quote competition) + live book, returns JSON parameters the deterministic loop consumes:
`{active_pairs, spread_mult, leg_usd, max_inventory, pause}`. **Guardrails are hard-coded** — the
LLM cannot disable the reserve, set loss-making spreads, or exceed capital. Auth via Application
Default Credentials on the server (`gcloud auth application-default login`) — **no key in the repo.**

### Trade-context store (for LLM validation)
Every quote + fill writes: `ts, pair, side, our_px, mid_ema, best_bid, best_ask, spread_abs,
spread_bps, bid_depth, ask_depth, short_vol, inv_base, inv_usdso, working_capital, gas_somi,
order_type, status, tx_hash, realized_pnl_delta, cum_pnl`. This is both the audit trail and the
dataset the strategist reasons over.

## Status
Scaffolding in progress on branch `feature/profit-maker-agent`. Not deployed. Do not start the
engine until built, funded ($150 USDso to `0xD84fE2a2220f0269e3d88dab908ADceb2d691E76`), and the
user says go. Deploy/runbook steps land here as the build completes.
