# Project Structure

> Authoritative map of DreamDEX contest agent + smartwatch UI. Git history rewritten to redact secrets.

```
.
├── backend/                         # Agent trading engine (Python + Flask, Round 3 restructure)
│   ├── agent_v3/                    # R3 profit maker (active)
│   │   ├── __init__.py
│   │   ├── runner.py                # Entrypoint: threaded supervisor, multi-pair cycling
│   │   ├── maker.py                 # Per-pair no-bleed PostOnly cycle logic
│   │   ├── market_data.py           # Book snapshots, mid-prices, spreads, volatility
│   │   ├── inventory.py             # Position tracking, PnL, reserve enforcement
│   │   ├── gas.py                   # SOMI refuel automation
│   │   ├── strategist.py            # Gemini 2.5 Pro via Vertex ADC (trade context)
│   │   └── context_store.py         # Rich trade-context SQLite store
│   ├── archive/                     # R2 reference code (deprecated, do not run)
│   │   ├── agent_r2/                # Old R2 agent/ package
│   │   ├── ARCHIVE.md               # R2 runbook index + legacy script reference
│   │   └── [R2 scripts moved here]  # aware_burst*.py/.sh, burst_*, *keepalive.sh, probe*.py, sweep_*.py, etc.
│   ├── trading/                     # Shared utilities (R3 live)
│   │   ├── __init__.py
│   │   ├── wallet.py                # Web3 account + signing
│   │   ├── dreamdex.py              # Pool calldata + fill tracking
│   │   └── manual.py                # Manual order + mock
│   ├── monitor/                     # Monitoring (R3 live)
│   │   ├── __init__.py
│   │   ├── db.py                    # SQLite persistence: trades, context, stats
│   │   ├── leaderboard.py           # LeaderboardMonitor: shared rank + balance polling
│   │   ├── portfolio.py             # Vault + wallet snapshot (quote, base, native)
│   │   └── prices.py                # Pool mid-prices + sparklines
│   ├── config.py                    # Constants: MARKETS, pool addrs, R3 thresholds
│   ├── data/                        # SQLite volumes (agent.db, context.db, logs)
│   │   └── .gitkeep
│   ├── Dockerfile                   # Docker image (Python 3.11, web3.py, vertex SDK)
│   ├── docker-compose.yml           # Compose config (network_mode: host, .env secrets)
│   ├── requirements.txt              # pip dependencies (vertex-ai, openai via vertex)
│   ├── .env                         # Secrets: VERTEX_PROJECT, VERTEX_REGION, RPC_URL, etc. (not in git)
│   ├── README.md                    # R3 architecture + quick-start
│   ├── logs/                        # Agent logs (local only)
│   ├── pyrightconfig.json           # Type checking config
│   └── .venv/                       # Virtual environment (git-ignored)
│
├── firmware/                        # ESP32-C3 watch firmware (Arduino)
│   ├── watch.ino                    # Main sketch: WiFi, OLED UI, button logic
│   ├── INTERACTION-GUIDE.md         # Watch UI walkthrough (screens, buttons, states)
│   ├── wifi_secrets.h               # WiFi SSID/pwd, #define API_KEY (git-ignored)
│   ├── button_check/
│   │   └── button_check.ino         # Diagnostic sketch for button testing
│   └── screen_check/
│       └── screen_check.ino         # Diagnostic sketch for OLED testing
│
├── context/                         # Development context (git-ignored)
│   ├── progress.md                  # Session tracking + lessons
│   ├── handover.md                  # Compact essentials for context switches
│   └── structure.md                 # This file
│
├── RUNBOOK.md                       # Operational guide (start, stop, mainnet flip, teardown)
├── WEB-DASHBOARD-SPEC.md            # Dashboard UI spec (liquidity table, mode toggle, logs)
├── .gitignore                       # Excludes .venv, .env, logs, context/
├── .vscode/
│   └── settings.json                # VSCode workspace settings
└── docker-compose.yml               # (optional) Root-level compose if needed

```

## Entry Points

- **R3 profit maker:** `backend/agent_v3/runner.py` — multi-pair cycling supervisor (no Flask)
- **R2 reference:** `backend/archive/ARCHIVE.md` — legacy script index (do not run directly)
- **Watch:** `firmware/watch.ino` — ESP32-C3 main sketch (unchanged)

## Config Files

- **`backend/config.py`** — Constants: MARKETS, pool addrs, pair rotation, reserve thresholds, no-bleed PostOnly cycle params
- **`backend/.env`** — Runtime secrets: VERTEX_PROJECT, VERTEX_REGION, RPC_URL (Somnia), DREAMDEX_ENV (testnet/mainnet), wallet keys (not in git)
- **`firmware/wifi_secrets.h`** — WiFi credentials + API_KEY (removed: no backend server in R3)
- **`backend/docker-compose.yml`** — Container config: network_mode=host, .env secrets, agent_v3/runner.py as entrypoint
- **`context/plan/round3-rules.md`** — R3 operational rules: capital limits, pair order, gas auto-refuel, context logging

## Key Modules (R3)

| File | Purpose |
|------|---------|
| `agent_v3/runner.py` | Entrypoint: multi-pair supervisor, threaded cycle manager, context store writer |
| `agent_v3/maker.py` | Per-pair no-bleed PostOnly cycle: book snap → reserve check → limit order logic |
| `agent_v3/market_data.py` | Book fetcher, mid-price calc, spread calc, volatility signals |
| `agent_v3/inventory.py` | Position tracking, realized/unrealized PnL, reserve enforcement, sweep logic |
| `agent_v3/strategist.py` | Gemini 2.5 Pro via Vertex ADC: trade context summarizer + decision helper |
| `agent_v3/context_store.py` | SQLite schema + helpers: rich trade log with book state, mid, reserve, decision |
| `monitor/leaderboard.py` | LeaderboardMonitor: shared across all agents, rank + balance polling |
| `monitor/portfolio.py` | Vault + wallet snapshot (quote, base, native, collateral) |
| `monitor/prices.py` | dreamDEX pool mid-prices + sparklines (shared across agents) |
| `trading/dreamdex.py` | Pool calldata encoding, tx submission, fill tracking (Solana devnet/mainnet) |
| `trading/wallet.py` | Web3.py account + signing, balance reads, native SOL reserves |

## Critical Hidden Details (R3)

- **`.env` not in git.** Contains VERTEX_PROJECT, VERTEX_REGION, RPC_URL (Somnia), DREAMDEX_ENV, wallet keys (from vault / KMS).
- **No Flask / server.py in R3.** Agent runs headless in container; watch UI removed from scope.
- **agent_v3/runner.py is the only entrypoint.** Spawns pair-cycling threads; do not import/call agent_v3 modules directly.
- **LeaderboardMonitor is shared singleton.** Instantiated once at runner start, passed to all market_data instances. Polling every 60s.
- **Context store (context_store.py) is append-only SQLite.** Every cycle logs full book state, mid, inventory, reserve, decision. Used for post-trade audits + strategist context.
- **No vault auto-drain.** R3 focuses on no-bleed PostOnly: keeps capital locked in vault per pair, cycles between pairs, never withdraws mid-round unless user manual intervention.
- **Gas auto-refuel in gas.py.** If native SOL reserve drops below threshold, gas.py detects and triggers SOMI→USDso→native swap via strategist guidance.

## Docker / Deployment (R3)

- **Build & run:** `cd backend && docker-compose up --build` → starts agent_v3/runner.py as entrypoint
- **Network mode:** `host` (required for Somnia RPC + DreamDEX interaction)
- **Logs:** Streamed to stdout + written to `backend/logs/`, context store to `backend/data/context.db`
- **Environment:** Read from `.env` at container start (no Flask port needed; only RPC + Vertex ADC)

## Testing & Diagnostics (R3)

- **R2 reference scripts:** `backend/archive/` contains old smoke tests, diagnostics (do not run against mainnet capital)
- **R3 validation:** See `context/plan/round3-rules.md` for pre-launch checklist (book snapshot, gas reserve, pair cycling, context logging)
- **Manual testing:** Use testnet USDC.e:USDso pair with small amounts before mainnet flip

