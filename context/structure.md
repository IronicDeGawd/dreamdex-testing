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
│   ├── cheap.sh                     # Launcher (steady): target, bleed_cap, leg, cost_ceil
│   ├── direct_burst.sh              # Launcher (fast): target, leg, slip, spread_gate
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
│   │   └── manual.py                # ORPHANED — old dashboard manual-trade handler (revival kit)
│   ├── monitor/                     # Monitoring
│   │   ├── db.py                    # SQLite persistence (used by agent_v3/runner)
│   │   ├── leaderboard.py           # ORPHANED — dashboard data producer (revival kit)
│   │   ├── portfolio.py             # ORPHANED — dashboard data producer (revival kit)
│   │   └── prices.py                # ORPHANED — dashboard data producer (revival kit)
│   ├── static/index.html           # Old web dashboard (to revive — context/plan/dashboard.md)
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
│   ├── research/dreamdex.md, dreamdex-r3-findings.md
│   └── plan/ (round2, round3-rules, dashboard, somnia-faucet-bot, …)
└── README.md / .gitignore
```

## Entry points
- **Steady engine:** `backend/volume_climb.py` via `cheap.sh` (detached).
- **Fast engine:** `backend/direct_burst.py` via `direct_burst.sh` (detached).
- **Maker + Telegram:** `backend/agent_v3/runner.py` + `monitor_bot.py` (docker compose).

## Critical facts (R3)
- **Somnia network, EVM, web3.py** — NOT Solana. Mainnet chain 5031, testnet 50312.
- **No Flask server runs in R3.** The old dashboard server is in `archive/server.py`;
  its data producers (`monitor/{prices,portfolio,leaderboard}.py`, `trading/manual.py`)
  are currently orphaned — kept as the dashboard revival kit (`context/plan/dashboard.md`).
- **Order placement:** REST `/orders` (volume_climb) OR direct `placeOrder`
  (0x4e978373, direct_burst). Native-SOMI pool ops need gas ≥5M; ERC20 fine at 3M.
- **Secrets** in `backend/.env` only (gitignored). Repo is PUBLIC.
- **Editing an engine** requires `docker compose build agent` (code baked into image).
