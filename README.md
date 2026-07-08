# DreamDEX Contest Agent + Smartwatch

Autonomous on-chain trading agent for the **DreamDEX** spot DEX on the **Somnia**
network, paired with an ESP32-C3 smartwatch UI. Run across multiple contest rounds.

**Round 3** (volume contest): finished **#2 of 6** on raw volume at **~1,086,202
USDso**, and the **most capital-efficient** trader in the field (~98% fill,
~$0.11 per 1k of volume). Full write-up in [`reports/R3-report.docx`](reports/R3-report.docx).

**Round 1** (earlier): finished #4 of 11; the real deliverable there was a protocol
findings report + on-chain dataset, preserved under [`reports/`](reports/).

---

## Repo layout

```
backend/            Python trading engine (Docker)
  volume_climb.py     Steady taker engine — WETH:USDso round-trips via REST; cost-aware
  direct_burst.py     Fast taker engine — direct placeOrder(0x4e978373), ~2x faster
  cheap.sh            Launcher: steady mode  (target, bleed_cap, leg, cost_ceil)
  direct_burst.sh     Launcher: fast mode    (target, leg, slip, spread_gate)
  agent_v3/           Profit maker: runner.py (entry), maker.py, market_data,
                      inventory, gas, strategist (Gemini via Vertex), context_store,
                      monitor_bot (Telegram alerts)
  trading/            dreamdex.py (REST + calldata + fill tracking),
                      wallet.py (signing + multi-RPC failover)
  monitor/            db.py (SQLite); prices/portfolio/leaderboard (dashboard data)
  static/             Web dashboard (index.html) — see context/plan/dashboard.md
  config.py           Markets, RPC failover pool, thresholds
  archive/            R1/R2 reference scripts (deprecated, do not run)

docs/               RUNBOOK.md, WEB-DASHBOARD-SPEC.md
reports/            Findings + analysis deliverables (R1) and R3 report
  FINDINGS.md, *.docx, R3-report.docx, R3-transaction-report.md
  analysis/           63,569-tx on-chain dataset + tooling
  evidence/           Liquidity-blackout proof (block-pinned)

firmware/           ESP32-C3 + SSD1306 OLED watch (Arduino)
context/            Dev context: plans, research, progress (working notes)
```

---

## The two R3 engines

Both trade **WETH:USDso** taker round-trips (buy at ask, sell at bid), ending flat.
Toll ≈ $0.11 per 1,000 of volume (the spread floor). Never run both at once — they
share one wallet, so concurrent runs collide on the nonce.

| Engine | Launcher | How it places orders | Speed | Use for |
|--------|----------|----------------------|-------|---------|
| `volume_climb.py` | `cheap.sh` | DreamDEX REST `/orders` → sign → broadcast | ~30s/round-trip | Steady, cost-aware volume; holding a lead |
| `direct_burst.py` | `direct_burst.sh` | Builds `placeOrder` calldata locally, broadcasts direct | ~15s (~2×) | Max throughput; closing a gap |

`volume_climb` is cost-aware: a spread gate skips wide books, a rolling `$/1k`
ceiling pauses when the toll climbs, and it self-heals across RPC blips (multi-node
failover). `direct_burst` verifies its calldata byte-for-byte against the REST path
at startup, gates on spread, and is bag-proof (sells any residual before every buy).

See [`context/research/dreamdex-r3-findings.md`](context/research/dreamdex-r3-findings.md)
for the full engine playbook, economics, and the `placeOrder` discovery.

---

## Running

### Engines (on the trading server)
```bash
cd backend
# steady mode: target, bleed_cap, leg, cost_ceil
nohup ./cheap.sh 100000 40 25 0.15 > /tmp/run.log 2>&1 &
# fast mode: target, leg, slip, spread_gate
nohup ./direct_burst.sh 100000 25 0.004 0.15 > /tmp/run.log 2>&1 &
```
Editing an engine requires rebuilding the image (`docker compose build agent`) —
the code is baked in. Launchers run detached and survive an SSH drop.

### Control dashboard (optional)
A small web panel to launch/stop/tune runs without typing the launchers by hand —
pick steady/fast mode, set leg/target/slip/etc., watch live volume/rank/logs, top
up gas, or flatten. It runs on the **host** (not in Docker) because it shells out
to the same `docker compose run` the launchers use.
```bash
cd backend
# set CONTROL_API_KEY in .env first (required on mainnet)
nohup ./control/run.sh 8787 > /tmp/control.log 2>&1 &   # → http://<server-ip>:8787
# local UI dev with stub data (no keys, no Docker):
CONTROL_MOCK=1 ./control/run.sh 8787
```
Every call needs the `X-API-Key` header (entered once in the UI, kept in
localStorage). One engine at a time (nonce safety); a leg bigger than 0.8× free
USDso is rejected before it can pre-revert. See
[`context/plan/dashboard.md`](context/plan/dashboard.md) for the design.

### Maker + Telegram monitor (Docker)
```bash
cd backend
cp .env.example .env    # wallet key, RPC, Vertex (Gemini), Telegram — never commit .env
docker compose up -d --build     # runs agent_v3.runner + monitor_bot
```

### Watch firmware
Copy `firmware/wifi_secrets.example.h` → `wifi_secrets.h`, fill SSID/password/API
key + backend URL, flash `firmware/watch.ino` to an ESP32-C3.

---

## Secrets

All secrets live in `backend/.env` (gitignored) — wallet private key, RPC URLs,
Vertex/Gemini creds, Telegram token. **This repo is public; never commit `.env`.**

---

## Historical: R1 findings & dataset

The R1 round doubled as a protocol stress test. Deliverables in [`reports/`](reports/):

- **[`FINDINGS.md`](reports/FINDINGS.md)** — 7 protocol findings for the DreamDEX team
  (e.g. `expireTimestampNs=0` silently rejected; native-SOMI vault sentinel address;
  `OrderPlaced` emitting `filled=0` on real fills; ~47% `eth_call` false-negatives;
  an 8.9-minute exchange-wide liquidity blackout).
- **[`reports/analysis/`](reports/analysis/)** — 63,569 on-chain transactions scraped +
  analyzed (fill rates, PnL, reverts).
- **[`reports/evidence/`](reports/evidence/)** — block-by-block proof of the blackout.

Hard-won lessons carry into every round: trade the tightest-spread pair (WETH),
wallet-funded orders only (vault IOC never fills), read the capital floor from live
RPC not a cache, and clamp trade size in code (LLM prompts are suggestions).

---

## Post-contest teardown

```bash
ssh <user>@<server>
cd ~/dreamdex-r3/backend && docker compose down
shred -u .env
```
Rotate the wallet private key and remove any tunnel/DNS entries afterward.
