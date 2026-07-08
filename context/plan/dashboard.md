# Plan — Agent Control Dashboard (revive R1 dashboard for R3 engines)

> Goal: a web control panel so you can launch/stop/tune runs yourself — pick
> **steady** (`cheap.sh` / volume_climb) or **fast** (`direct_burst.sh`) mode,
> set tunables (leg, target, slip, cost-ceil, spread-gate, bleed-cap), watch live
> status, and top up gas — without asking Claude each time.

## Background (from audit)
- R1/R2 had a Flask dashboard: `backend/archive/server.py` served
  `backend/static/index.html` (vanilla JS + Tailwind CDN, REST polling, X-API-Key
  auth) on `:5001` behind a Cloudflare tunnel. It drifted to a burst-era build
  with `/burst`, `/direct_burst`, `/agent/mode` endpoints.
- Its data producers still exist but are currently orphaned:
  `monitor/prices.py`, `monitor/portfolio.py`, `monitor/leaderboard.py`,
  `trading/manual.py`. **Reuse these** (don't delete).
- R3 reality: no server runs; engines are launched by hand via `cheap.sh` /
  `direct_burst.sh` (detached docker compose runs). The dashboard must wrap
  exactly those launchers so behavior matches what we do manually.

## Architecture
**Control API** — small **FastAPI** service (new `backend/control/app.py`), runs as
a new `control` service in `docker-compose.yml`, served behind the existing
**Caddy** (`dash-caddy`) with TLS + a subdomain (replaces the old Cloudflare
tunnel). Auth: `X-API-Key` header, key in `.env`.

**Endpoints:**
| Method | Path | Purpose |
|---|---|---|
| GET | `/status` | running engine? which mode + params + this-run volume, cost/1k, uptime |
| GET | `/balances` | on-chain USDso / SOMI / WETH (own wallet reads, not leaderboard) |
| GET | `/leaderboard` | live standings + our rank (reuse `monitor/leaderboard` or direct fetch) |
| GET | `/logs?n=` | tail of the current run's log |
| POST | `/launch` | body `{mode, target, leg, slip, bleed_cap, cost_ceil, spread_gate}` → start the right launcher detached |
| POST | `/stop` | stop the running engine (docker stop), then flatten-check |
| POST | `/gas/topup` | body `{somi}` → buy SOMI (native gas) via the tested path |
| POST | `/flatten` | sell any residual WETH to USDso (safety) |

**Launch mechanics:** the API shells out to `cheap.sh`/`direct_burst.sh` (or runs
`docker compose run --rm -T -e ... agent python3 <engine>.py` directly) detached,
records the container name + params + log path in a small state file/SQLite so
`/status` and `/stop` can find it. One engine at a time (nonce safety) — reject a
second `/launch` while one is running.

## Frontend (rebuild `static/index.html`)
Keep the stack (vanilla JS + Tailwind CDN, no build step, dark terminal look).
Rebuild against the new API. Panels:
1. **Engine control** — mode toggle (Steady / Fast); tunable inputs: leg $, target,
   slip %, and mode-specific (steady: cost-ceil, bleed-cap; fast: spread-gate);
   **Launch** + **Stop** buttons with confirm modals.
2. **Live status** — running/stopped, mode, this-run volume, rolling $/1k, uptime;
   auto-refresh via `/status` poll (5s).
3. **Balances + rank** — USDso / SOMI / WETH + leaderboard rank + gap to #1;
   `/balances` (15s), `/leaderboard` (60s).
4. **Log tail** — last N lines of the current run (`/logs`, 5s), scrollback.
5. **Gas** — SOMI balance + "Top up N SOMI" button (`/gas/topup`).
6. **Flatten** — emergency "sell all WETH → USDso" button.

## Guardrails (must-haves)
- **Single-engine lock** — never launch two (nonce collision). Enforce in the API.
- **Leg vs balance validation** — reject a launch where `leg > 0.8 × free USDso`
  (the pre-revert bug); surface a clear error in the UI.
- **Confirm modals** — Launch, Stop, Gas top-up, Flatten.
- **Auth** — X-API-Key on every call; key in `.env`, entered once in the UI
  (localStorage), never hardcoded.
- **Audit log** — every launch/stop/topup with params + timestamp + who (API key label).
- **Bag-proof stop** — `/stop` does docker stop then verifies flat; if a bag, runs
  the flatten loop (reuse engine `sell_all_weth` logic).

## Build phases
1. **Control API core** — `/status`, `/balances`, `/leaderboard`, `/launch`,
   `/stop`, `/logs`; wrap the existing launchers + reuse `monitor/*` producers.
   Single-engine lock + leg validation. Run locally first.
2. **Frontend rebuild** — the 6 panels against the API; confirm modals; polling.
3. **Deploy** — `control` service in compose + Caddy route + API key + HTTPS.
4. **Extras** — `/gas/topup`, `/flatten`, audit log, param presets (save favorite
   leg/target combos).

## Decisions to confirm before building
- Reuse the 4 orphaned data modules as-is, or rewrite lean against R3 engines?
  (Recommend: reuse `portfolio`/`leaderboard`/`prices`, drop `manual` unless we
  want manual single trades in the UI.)
- Serve behind Caddy subdomain (need a domain) or just bind to the server IP +
  API key for personal use? (Personal use → IP + key is fine, no domain needed.)
- Auth: single shared API key (simplest) vs per-user. Personal tool → single key.

## Prereqs
- API key (→ `.env`). Optional domain if we want a nice URL via Caddy.
- Faucet/agent wallet key already in server `.env` (the control API reuses it to
  launch engines + top up gas — same trust boundary as today).
