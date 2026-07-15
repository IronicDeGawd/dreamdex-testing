# Project Structure

> Authoritative map of the DreamDEX contest agent (Somnia, EVM/web3.py) +
> smartwatch UI. Round 3 state. Git history rewritten to redact secrets.

```
.
├── backend/                         # Python trading engine (Docker)
│   ├── volume_climb.py              # STEADY taker engine — WETH:USDso round-trips via
│   │                                #   DreamDEX REST; cost-aware (spread gate + $/1k
│   │                                #   ceiling), RPC-failover resilient, patient sell-retry
│   ├── direct_burst.py              # FAST taker engine — direct placeOrder(0x4e978373)
│   │                                #   calldata, ~2x faster; encoding self-check, spread
│   │                                #   gate, bag-proof (sell_all_weth each loop)
│   ├── maker_core.py                # Maker V2 decision core — desired_quotes, stop_loss_action,
│   │                                #   trend_mode, apply_fill, should_requote; 19 unit tests
│   ├── maker_v2.py                  # Maker V2 engine — two-sided PostOnly loop on shared layer;
│   │                                #   true capital tracking (wallet+reserved), bleed guard, fill
│   │                                #   detection; env-driven (MAKER2_*); live-validated on R3
│   ├── maker_feasibility.py         # Read-only feasibility probe for maker pairs; 160-sample
│   │                                #   run with book snapshots, EMA yield oracle, candle analysis
│   ├── tests/                       # Unit tests
│   │   ├── test_maker_core.py       # 19 tests for maker_core decision logic
│   │   └── test_legsize.py          # 8 tests for depth-aware leg sizing
│   ├── cheap.sh                     # Launcher (steady): target, bleed_cap, leg, cost_ceil
│   ├── direct_burst.sh              # Launcher (fast): target, leg, slip, spread_gate
│   ├── control/                     # Engine-control API + dashboard (host-run FastAPI)
│   │   ├── app.py                   # FastAPI: /status /balances /leaderboard /logs
│   │   │                            #   /launch /stop /gas/topup /flatten /trade /audit;
│   │   │                            #   X-API-Key auth; LiveBackend vs MockBackend
│   │   ├── engine_manager.py        # Launch/stop/status/logs, single-engine lock,
│   │   │                            #   log-tail volume parse, audit log, CONTROL_MOCK
│   │   ├── mock_engine.py           # Fake engine (prints tot=$… lines) for local dev
│   │   ├── keepalive.sh             # Cron-triggered idle-DQ guard: buys $1 SOMI after
│   │   │                            #   CONTROL_KEEPALIVE_AGE_S (default 20h) idle flat
│   │   ├── transition.sh            # Cron-triggered 600k handover: /stop taker, /launch maker
│   │   │                            #   at TRANSITION_TRIGGER volume; fires once via state marker
│   │   ├── run.sh                   # Host launcher: venv + uvicorn on $CONTROL_BIND:$PORT
│   │   ├── requirements.txt         # fastapi + uvicorn (on top of backend/requirements)
│   │   └── state/                   # Runtime: engine.json, audit.log, keepalive.json, runs.jsonl (per-run P&L records), logs/
│   ├── agent_v3/                    # Profit maker (Docker CMD = runner)
│   │   ├── runner.py                # Entrypoint: threaded supervisor, multi-pair cycling
│   │   ├── maker.py                 # Per-pair no-bleed PostOnly cycle + hold-mode trend gate
│   │   ├── market_data.py           # Book snapshots, mid/spread, 24h trend (candles)
│   │   ├── inventory.py             # Position tracking, PnL, reserve enforcement
│   │   ├── gas.py                   # SOMI refuel (buy native gas from working capital)
│   │   ├── strategist.py            # Gemini 2.5 Pro via Vertex ADC (trade context)
│   │   ├── context_store.py         # Rich trade-context SQLite store
│   │   └── monitor_bot.py           # 2nd Docker command: Telegram alerts + leaderboard poll
│   ├── trading/                     # Shared utilities (used by the live engines)
│   │   ├── wallet.py                # Web3 signing + FailoverHTTPProvider (multi-RPC rotation)
│   │   ├── dreamdex.py              # REST wrapper, placeOrder calldata, fill tracking
│   │   ├── legsize.py               # Depth-aware leg sizing: touch_fit_leg, touch_depth_usd
│   │   └── manual.py                # Manual-trade handler — reused by control /trade
│   ├── monitor/                     # Monitoring
│   │   ├── db.py                    # SQLite persistence (used by agent_v3/runner)
│   │   ├── leaderboard.py           # Live rank/volume poller — reused by control /leaderboard
│   │   ├── portfolio.py             # On-chain balance reader — reused by control /balances
│   │   └── prices.py                # Price/book poller (dashboard + strategist)
│   ├── static/index.html           # Engine-control dashboard (served by control/app.py)
│   ├── static/r1.html              # Original R1 dashboard (layout-only design reference)
│   ├── archive/                     # R1/R2 reference scripts incl. old server.py (DO NOT RUN)
│   ├── config.py                    # MARKETS, SOMNIA_RPCS failover pool, thresholds, CHAIN_ID
│   ├── docker-compose.yml           # Launches agent_v3.runner + agent_v3.monitor_bot
│   ├── Dockerfile / requirements.txt / .env.example
│   └── data/                        # SQLite volumes (gitkept)
│
├── docs/                            # Operational docs
│   ├── RUNBOOK.md                   # Ops guide (R1-era Flask; historical)
│   └── WEB-DASHBOARD-SPEC.md        # R1 dashboard spec (historical)
│
├── reports/                         # Findings + analysis deliverables
│   ├── FINDINGS.md                  # 7 protocol findings (R1)
│   ├── DreamDEX-Findings.docx / DreamDEX-Trade-Analysis.docx
│   ├── R3-report.docx / R3-transaction-report.md   # R3 performance report
│   ├── analysis/                    # 63,569-tx on-chain dataset + tooling
│   └── evidence/                    # Liquidity-blackout proof (block-pinned)
│
├── firmware/                        # ESP32-C3 watch (Arduino: watch.ino + diagnostics)
├── context/                         # Dev context (plans, research, progress) — tracked here
│   ├── progress.md / progress.archive.md / handover.md
│   ├── research/dreamdex.md, dreamdex-r3-findings.md, maker-feasibility-arena.md
│   └── plan/ (algo-arena, r4-improvements, run-pnl-snapshot, round2, round3-rules, dashboard, …)
└── README.md / .gitignore
```

## Entry points
- **Steady engine:** `backend/volume_climb.py` via `cheap.sh` (detached).
- **Fast engine:** `backend/direct_burst.py` via `direct_burst.sh` (detached).
- **Maker + Telegram:** `backend/agent_v3/runner.py` + `monitor_bot.py` (docker compose).
- **Control dashboard:** `backend/control/run.sh [port]` (host-run FastAPI; `CONTROL_MOCK=1`
  for keyless local dev). Serves `static/index.html`; wraps the two launchers.

## Critical facts (R3)
- **Somnia network, EVM, web3.py** — NOT Solana. Mainnet chain 5031, testnet 50312.
- **Control API is host-run, not containerized** (`archive/server.py` is the retired R1
  Flask). It reuses `monitor/{prices,portfolio,leaderboard}.py` + `trading/manual.py` and
  shells out to `cheap.sh`/`direct_burst.sh` — see `context/plan/dashboard.md`.
- **Order placement:** REST `/orders` (volume_climb) OR direct `placeOrder`
  (0x4e978373, direct_burst). Native-SOMI pool ops need gas ≥5M; ERC20 fine at 3M.
- **Secrets** in `backend/.env` only (gitignored). Repo is PRIVATE (made private this session).
- **Editing an engine** requires `docker compose build agent` (code baked into image).
