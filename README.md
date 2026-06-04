# DreamDEX Contest Agent + Smartwatch

Autonomous on-chain trading agent built for the DreamDEX Spot DEX contest on Somnia mainnet, paired with an ESP32-C3 smartwatch UI. Finished **rank #4 of 11** with **~205k USDso volume** traded over the contest window.

The contest was explicitly a stress test — the real deliverable is the protocol findings report and on-chain dataset produced during the run.

---

## What's in this repo

```
backend/          Python + Flask agent backend (Docker)
  agent/          Main trading loop + GPT-4o-mini brain
  monitor/        SQLite persistence, portfolio, prices, leaderboard
  trading/        DreamDEX REST wrapper + ManualTrader
  static/         Single-file web dashboard (index.html)
  direct_burst.py Direct-RPC burst engine (~5× faster than REST)
  config.py       Markets, thresholds, constants

firmware/         ESP32-C3 + SSD1306 OLED watch (Arduino)
  watch.ino       All screens: prices, portfolio, agent, manual trade
  wifi_secrets.example.h  Copy → wifi_secrets.h, fill in credentials

analysis/         63,569 on-chain transactions scraped + analysed
  scrape_trades.py   Blockscout v2 scraper
  analyze_full.py    Fill-rate / PnL / revert stats
  onchain_trades.db  SQLite (git-LFS or local only)
  FULL-ANALYSIS.md   Key numbers

evidence/         Liquidity blackout proof (8.9-min exchange-wide outage)
  replay_book_state.py      Block-by-block book replay via archive node
  find_blackout_edges.py    Pin exact start/end blocks
  scan_blackout_history.py  24-h historical scan

FINDINGS.md             7 protocol findings (send to dreamDEX team)
DreamDEX-Findings.docx  Same, formatted
DreamDEX-Trade-Analysis.docx  Trade dataset report
RUNBOOK.md              Ops guide: start/stop/restart, common issues
```

---

## Architecture

```
ESP32 watch  ──HTTP──►  Flask server (port 5001)  ──►  DreamDEX REST API
Web dashboard ──────►  (Cloudflare tunnel)           ──►  Somnia RPC (web3.py)
                        │
                        ├─ TradingAgent (main, $7–15, 120s loop)
                        │    └─ Brain (GPT-4o-mini) → GRIND / PROFIT mode
                        ├─ MicroAgent (brainless, $2–5, 90s loop)
                        ├─ direct_burst.py (RPC-only, WETH:USDso, ~5× faster)
                        ├─ LeaderboardMonitor (shared, 60s poll)
                        └─ SQLite agent.db (trades, ticks, stats)
```

**Two execution paths:**

| Path | How | Speed | Used for |
|------|-----|-------|----------|
| REST `/manual` | Flask builds tx → RPC broadcasts | ~1 tx/4s | Main agent, micro agent, watch manual trades |
| `direct_burst.py` | Signs + broadcasts entirely in-process | ~5× faster | Volume burst; handles both native-SOMI and ERC20-WETH base |

---

## Key findings (see FINDINGS.md for full detail)

| ID | Finding |
|----|---------|
| A1 | `expireTimestampNs=0` silently rejected — always pass `(now + 3600) * 1e9` |
| A2 | Native SOMI vault uses sentinel `0x28f34De…`, NOT `address(0)` |
| A3 | `vault_withdraw` for native SOMI always reverts with `0x734b5f70` |
| A4 | `OrderPlaced` event emits `filled=0` even on real fills |
| A5 | `getOwnOpenOrders()` returns empty even with live resting orders |
| A6 | `eth_call` sim produces ~47% false-negatives against live book state |
| A7 | Exchange-wide liquidity blackout 2026-06-01 12:11–12:20 UTC (8.9 min, block-pinned) |

---

## Dataset

- **63,569 on-chain transactions** scraped from Blockscout v2 API
- Fill rates: USDC.e **99.5%** · SOMI **83.6%** · WETH **72.4%**
- WETH accounts for 84% of all reverts despite having the tightest spread (0.020% vs SOMI 0.132%)
- 136 SOMI consumed in gas (~$22 at contest prices)
- Net loss from $50 starting capital: ~$49 (all on spread + gas — no bugs, no losses)

---

## Running locally

### Prerequisites

- Docker + Docker Compose
- Python 3.11+ (for burst scripts run outside Docker)
- Arduino IDE with ESP32 board support (for watch firmware)

### Backend

```bash
cd backend
cp .env.example .env        # fill in FLASK_API_KEY, RPC_URL, OPENAI_KEY, MAINNET_PRIVATE_KEY
docker compose up -d --build
docker compose logs -f agent
```

The agent starts paused. Unpause via dashboard toggle or:
```bash
curl -X POST -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{}' http://localhost:5001/agent/toggle
```

### Dashboard

Open `http://localhost:5001` in a browser. Enter the API key when prompted — it persists in `localStorage`.

### Direct burst (optional, RPC-only)

```bash
cd backend
BURST_PAIR=WETH:USDso BURST_USDSO=3 python3 direct_burst.py
# or via wrapper:
./run_direct_burst.sh
```

### Watch firmware

1. Copy `firmware/wifi_secrets.example.h` → `firmware/wifi_secrets.h`
2. Fill in SSID, password, and `API_KEY`
3. Set `BACKEND` to your server URL (Cloudflare tunnel or LAN IP)
4. Flash `firmware/watch.ino` via Arduino IDE to an ESP32-C3

---

## Configuration

All runtime constants live in `backend/config.py`:

| Constant | Default | Description |
|----------|---------|-------------|
| `AGENT_MIN_TRADE` | 7.0 | Minimum trade size in USDso |
| `AGENT_MAX_TRADE` | 15.0 | Maximum trade size in USDso |
| `AGENT_STOP_BELOW` | 22.0 | Capital floor — agent pauses below this |
| `DREAMDEX_ENV` | `mainnet` | `mainnet` or `testnet` |
| `MARKETS` | — | Pool addresses, decimals, minQty per pair |

`AGENT_STOP_BELOW` is also hardcoded in the watch firmware as `AGENT_FLOOR_USDSO` — change both together.

---

## Hard-won lessons (short version)

- **Trade WETH:USDso.** Spread 0.020% vs SOMI 0.132% — 6.5× cheaper per $1k volume.
- **Vault-funded IOC buys never fill on mainnet.** Use `fundingSource: wallet` only.
- **`expireTimestampNs=0` is not "no expiry"** — it's silently rejected by the deployed contract.
- **The deep order book is a single MM bot** (`0xe3Ef9c0F…`), not contestants. Wash trading is impossible.
- **Capital floor check must read live RPC**, not the cached Portfolio — 60s staleness let the agent blow past the floor during a mid-cycle base-token accumulation.
- **LLM prompts are suggestions; hard rules need code-level clamps.** `max(MIN, min(MAX, amt))` on trade size, not natural-language guidance.

Full lessons log in `context/progress.md`.

---

## Post-contest teardown

```bash
ssh user@<SERVER_HOST>
cd ~/dreamdex-agent
docker compose down
docker rmi dreamdex-agent:latest
shred -u .env
cd ~ && rm -rf ~/dreamdex-agent
```

Also delete the Cloudflare tunnel published-app entry and rotate the private key.
