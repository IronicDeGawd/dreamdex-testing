# Project Structure

> Authoritative map of DreamDEX contest agent + smartwatch UI. Git history rewritten to redact secrets.

```
.
├── backend/                         # Agent trading engine (Python + Flask)
│   ├── agent/
│   │   ├── __init__.py
│   │   ├── agent.py                 # TradingAgent main loop + state machine
│   │   ├── brain.py                 # GPT-4o-mini trading logic (GRIND/PROFIT modes)
│   │   ├── state.py                 # Trade state enum (placed/filled/unfilled)
│   │   └── strategy.py              # Trading strategy (momentum, thresholds)
│   ├── monitor/
│   │   ├── __init__.py
│   │   ├── db.py                    # SQLite persistence: trades, market_ticks, stats helpers
│   │   ├── leaderboard.py           # LeaderboardMonitor (shared across server + agent)
│   │   ├── portfolio.py             # Portfolio (reads pool balances via web3.py)
│   │   └── prices.py                # Price fetcher (dreamDEX pools)
│   ├── main.py                      # Entry point: spawn agent loop + leaderboard monitor
│   ├── server.py                    # Flask API (SIWE auth, /api/order, /api/status)
│   ├── config.py                    # Constants: MARKETS, pool addrs, decimals, thresholds
│   ├── data/                        # SQLite DB volume (agent.db, persists across restarts)
│   │   └── .gitkeep
│   ├── Dockerfile                   # Docker image (Python 3.11, web3.py, openai)
│   ├── docker-compose.yml           # Compose config (network_mode: host, .env secrets)
│   ├── requirements.txt              # pip dependencies
│   ├── .env                         # Secrets: FLASK_API_KEY, RPC_URL, OPENAI_KEY (not in git)
│   ├── buy_gas.py                   # Swap USDso→SOMI on SOMI pool to refuel gas agent
│   ├── check_balance.py             # Utility: read wallet balance snapshot
│   ├── gas_topup.sh                 # Drop-safe orchestration: disable keepalive → kill burst → buy gas → restart
│   ├── smoke_testnet.py             # End-to-end test on testnet
│   ├── smoke_live_order.py          # Test live order (mainnet)
│   ├── test_connectivity.py         # Network diagnostics
│   ├── burst_keepalive.sh           # Host cron (every 2 min): detect stalls via pgrep, restart if dead
│   ├── run_direct_burst.sh          # Direct-RPC burst engine: defaults USDC.e:USDso, reads key from container env
│   ├── logs/                        # Agent + server logs (local only)
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

- **Backend agent:** `backend/main.py` — spawns TradingAgent loop + LeaderboardMonitor
- **Flask server:** `backend/server.py` — SIWE auth + order API
- **Watch:** `firmware/watch.ino` — ESP32-C3 main sketch

## Config Files

- **`backend/config.py`** — Constants: MARKETS (pool decimals, minQty, lot, tick), thresholds (floor $22, max trade $5), mode settings (GRIND/PROFIT), momentum threshold ±0.3%
- **`backend/.env`** — Runtime secrets: FLASK_API_KEY, RPC_URL (Somnia chain), OPENAI_KEY, DREAMDEX_ENV (testnet/mainnet)
- **`firmware/wifi_secrets.h`** — WiFi credentials + API_KEY (mirrors backend FLASK_API_KEY)
- **`backend/docker-compose.yml`** — Container config: network_mode=host, env-file=.env

## Key Modules

| File | Purpose |
|------|---------|
| `agent/agent.py` | TradingAgent main loop, trade placement, vault-delta fill detection |
| `agent/brain.py` | GPT-4o-mini strategy (momentum, allocation, mode logic) |
| `agent/strategy.py` | Market analysis, momentum calc, buy/sell signals |
| `monitor/leaderboard.py` | Shared LeaderboardMonitor: fetches rank, auto-polls every 60s |
| `monitor/portfolio.py` | Vault + wallet balance snapshot (quote, base, native) |
| `monitor/prices.py` | dreamDEX pool mid-prices + sparklines |
| `server.py` | Flask routes: /api/order, /api/status, /api/portfolio, /auth/siwe |

## Critical Hidden Details

- **`.env` not in git.** Contains FLASK_API_KEY, RPC_URL, OPENAI_KEY. Local dev must populate.
- **`wifi_secrets.h` not in git.** WiFi SSID/password + #define API_KEY (copy of FLASK_API_KEY).
- **Placeholder values in code.** After git-filter-repo redaction: `<FLASK_API_KEY>`, `<SERVER_HOST>`, `<TUNNEL_HOST>`, `<EXAMPLE_DOMAIN>` need real values for local dev.
- **LeaderboardMonitor is shared.** Instantiated in `main.py`, passed to TradingAgent. If agent creates its own, rank checks fail silently.
- **Auto-drain post-sell.** After profitable sells, `agent.py` withdraws vault quote to wallet to prevent runway bleed (vault PnL is counted separately on leaderboard).

## Docker / Deployment

- **Build & run:** `cd backend && docker-compose up --build`
- **Network mode:** `host` (required on Ubuntu 24.04 for port-forwarding to work)
- **Logs:** Streamed to stdout + written to `backend/logs/`
- **Environment:** Read from `.env` at container start

## Testing & Diagnostics

- **Smoke tests:** `smoke_testnet.py` (full cycle on testnet), `smoke_live_order.py` (real mainnet order)
- **Connectivity:** `test_connectivity.py` (RPC + API health)
- **Firmware diagnostics:** `button_check.ino`, `screen_check.ino` in subdirectories

