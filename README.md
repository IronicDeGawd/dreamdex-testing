# DreamDEX Contest Agent + Smartwatch

Autonomous on-chain trading agent for the **DreamDEX** spot DEX on the **Somnia**
network, paired with an ESP32-C3 smartwatch UI. Run across multiple contest rounds,
each round adding an engine or a piece of control tooling.

**Round 4** (volume contest): reached **1,501,818 USDso** of volume. This round
added the **atomic (EIP-7702) taker** and **depth-aware dynamic leg sizing** — the
big-leg atomic engine amortizes the fixed per-tx gas over much larger round-trips,
pushing throughput to ~$130k/hr on a deep book while the toll stays at the spread
floor. Builder feedback for the team in
[`reports/DreamDEX-R4-Feedback.docx`](reports/DreamDEX-R4-Feedback.docx).

**Round 3** (volume contest): finished **#2 of 6** on raw volume at **~1,086,202
USDso**, and among the **most capital-efficient** traders in the field (~98% fill).
Write-up in [`reports/R3-report.docx`](reports/R3-report.docx).

**Round 1** (earlier): finished #4 of 11; the real deliverable there was a protocol
findings report + on-chain dataset, preserved under [`reports/`](reports/).

---

## Repo layout

```
backend/            Python trading engine (Docker)
  volume_climb.py     Steady taker — WETH/WBTC:USDso round-trips via REST; cost-aware
  direct_burst.py     Fast taker — direct placeOrder calldata, ~2x faster
  atomic_round.py     Atomic taker — buy+sell in ONE tx via an EIP-7702 delegate;
                      multi-pair rotation, depth-fitted legs, ~3x gas efficiency
  maker_v2.py         Two-sided PostOnly maker (maker_core.py = pure decision core)
  trading/
    dreamdex.py         REST + calldata + fill tracking + vault deposit/withdraw
    wallet.py           Signing + thread-safe multi-RPC round-robin failover
    legsize.py          Depth-aware leg sizing (touch_fit_leg / touch_depth_usd)
    delegate.py         EIP-7702 atomic buy+sell delegate (bytecode + trip topic)
    manual.py           One-shot manual trade helper
  control/            Host-run control API + web dashboard
    app.py              FastAPI: launch/stop/tune, balances, /gas/topup, /convert
    engine_manager.py   Builds each engine's env and shells out to docker compose
  monitor/            SQLite + prices/portfolio/leaderboard readers (dashboard data)
  static/index.html   Control dashboard UI
  config.py           Markets, RPC failover pool, thresholds
  tests/              Unit tests (maker_core, legsize)
  archive/            R1/R2 reference scripts (deprecated, do not run)

docs/               RUNBOOK.md, WEB-DASHBOARD-SPEC.md, round-4-feedback.md
reports/            Protocol findings + analysis deliverables (see below)
firmware/           ESP32-C3 + SSD1306 OLED watch (Arduino)
```

> Working notes (plans, research, progress logs) live in a git-ignored `context/`
> folder that is intentionally **not** published.

---

## The engines

All takers trade **WETH:USDso / WBTC:USDso** round-trips (buy at ask, sell at bid),
ending flat — the cost is the spread crossed (the "toll"), plus gas. Only one engine
runs at a time: they share a wallet, so concurrent runs collide on the nonce.

| Engine | Places orders by | Note |
|--------|------------------|------|
| `volume_climb.py` | DreamDEX REST `/orders` → sign → broadcast | Steady, cost-aware; holds a lead |
| `direct_burst.py` | Local `placeOrder` calldata, broadcast direct | ~2× faster; closes a gap |
| `atomic_round.py` | One tx buys **and** sells via an EIP-7702 delegate | Fewest txs → best gas/1k; big legs on deep books |
| `maker_v2.py`     | Two-sided resting PostOnly quotes | Earns the spread instead of paying it |

**Cost model.** Toll (spread) is ~fixed per $1k of volume and does not shrink with
leg size; gas is ~fixed *per transaction*, so it shrinks per $1k as the leg grows.
The atomic engine wins because it collapses a round-trip into one transaction and
then sizes each leg to the top-of-book depth (`legsize.py`), so the fixed gas is
spread over the largest trade the book can absorb without walking.

The takers are cost-aware: a spread gate skips wide books, a pre-trade `$/1k`
ceiling pauses when the toll climbs, and the wallet self-heals across RPC blips
(thread-safe multi-node failover in `wallet.py`).

---

## Running

### Engines (on the trading server)
```bash
cd backend
# steady taker: target, bleed_cap, leg, cost_ceil
nohup ./cheap.sh 100000 40 25 0.15 > /tmp/run.log 2>&1 &
# fast taker: target, leg, slip, spread_gate
nohup ./direct_burst.sh 100000 25 0.004 0.15 > /tmp/run.log 2>&1 &
```
Editing an engine requires rebuilding the image (`docker compose build agent`) —
the code is baked in. Launchers run detached and survive an SSH drop. The atomic
and maker engines are launched through the control API below.

### Control dashboard
A web panel to launch/stop/tune runs without typing launchers by hand — pick the
mode, set leg/target/slip, watch live volume/rank/logs, top up gas, convert
USDC.e⇄USDso, or flatten. It runs on the **host** (not in Docker) because it shells
out to the same `docker compose run` the launchers use.
```bash
cd backend
# set CONTROL_API_KEY in .env first (required on mainnet)
nohup ./control/run.sh 8787 > /tmp/control.log 2>&1 &   # → http://<server-ip>:8787
# local UI dev with stub data (no keys, no Docker):
CONTROL_MOCK=1 ./control/run.sh 8787
```
Every call needs the `X-API-Key` header (entered once in the UI, kept in
localStorage). One engine at a time (nonce safety); a leg bigger than
`0.95 × free USDso` is rejected before it can pre-revert (the collateral is locked
at the limit price, above mid, so a leg needs slightly more than its notional).

Handy endpoints: `/launch` · `/stop` · `/balances` · `/gas/topup`
(USDso→SOMI) · `/convert` (USDC.e⇄USDso, IOC at ~1:1) · `/flatten` · `/trade`.

### Maker + Telegram monitor (Docker)
```bash
cd backend
cp .env.example .env    # wallet key, RPC, Telegram — never commit .env
docker compose up -d --build
```

### Watch firmware
Copy `firmware/wifi_secrets.example.h` → `wifi_secrets.h`, fill SSID/password/API
key + backend URL, flash `firmware/watch.ino` to an ESP32-C3.

---

## Secrets

All secrets live in `backend/.env` (gitignored) — wallet private key, RPC URLs,
Telegram token, control API key. **This repo may be public; never commit `.env`.**
`.env.example` documents every variable with placeholder values.

---

## Reports & deliverables, by round

Everything under [`reports/`](reports/).

**Round 1 — protocol stress test.** The round doubled as a mainnet stress test.
- **[`FINDINGS.md`](reports/FINDINGS.md)** / **[`DreamDEX-Findings.docx`](reports/DreamDEX-Findings.docx)**
  — protocol findings for the DreamDEX team, several confirmed by their dev
  (`expireTimestampNs=0` silently rejected; a non-standard native-SOMI vault
  sentinel; `OrderPlaced` emitting `filled=0` on real fills; ~47% `eth_call`
  pre-trade false-negatives; an ~8.9-minute exchange-wide liquidity blackout).
- **[`DreamDEX-Trade-Analysis.docx`](reports/DreamDEX-Trade-Analysis.docx)** +
  **[`reports/analysis/`](reports/analysis/)** — tens of thousands of on-chain
  transactions scraped and analyzed (fill rates, PnL, reverts), with the tooling.
- **[`reports/evidence/`](reports/evidence/)** — block-by-block proof of the blackout.

**Round 2.** No standalone report — the round's output fed directly into the R3
engine work (cost-aware gating, the tightest-spread-pair rule).

**Round 3 — volume contest, #2 of 6.**
- **[`R3-report.docx`](reports/R3-report.docx)** — transaction & performance report.
- **[`R3-transaction-report.md`](reports/R3-transaction-report.md)** — the same in Markdown.

**Round 4 — volume contest, 1.5M volume.**
- **[`DreamDEX-R4-Feedback.docx`](reports/DreamDEX-R4-Feedback.docx)** — builder
  feedback for the team (leaderboard PnL vs. stablecoin swaps, RPC reliability,
  collateral-at-limit-price, book-depth variance).

Lessons that carry across rounds: trade the tightest-spread pair, wallet-funded
orders only (vault IOC never fills), read the capital floor from live RPC not a
cache, clamp trade size in code, and — the R4 lesson — size each leg to the book so
fixed gas is amortized, not multiplied.

---

## Post-contest teardown

```bash
ssh <user>@<server>
cd ~/dreamdex/backend && docker compose down
shred -u .env
```
Rotate the wallet private key and remove any tunnel/DNS entries afterward.
