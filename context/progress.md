# Progress — DreamDEX Contest Agent

**Live on mainnet, agent running in PROFIT mode with diversity rule at rank #2 of 11.** Wallet $44.50 USDso + ~16.5 SOMI native; 137 txns executed with 77 fills (~56% rate); $192+ volume traded; contest leaderboard PnL −$5.49. Brain now rotates trading pairs (SOMI/USDC.e/WETH) instead of spam-picking SOMI under PROFIT mode. Dashboard shows live liquidity breakdown (wallet USDso, vault, native SOMI, manual reserve), mode badge (GRIND/PROFIT), and activity log streaming decisions. All fixes deployed: vault funding (mainnet only, wallet-funded fills; vault-funded never fills), auto-drain post-sell, two-sided vault-delta fill detection, grind/profit modes with rank-based auto-flip (≤2 → PROFIT, >3 → GRIND), shared LeaderboardMonitor for accurate rank display.

Stack: Python 3.11 + Flask + web3.py agent backend (Docker `network_mode: host`); ESP32-C3 + SSD1306 OLED watch; OpenAI gpt-4o-mini trading brain; dreamDEX REST + SIWE. Server `user@<SERVER_HOST>` (Tailscale), Cloudflare tunnel `<TUNNEL_HOST>` → `localhost:5001`. Git history scrubbed: all instances of API key, Tailscale IP, public domain, SSH user prefix, and bare domain replaced with placeholders.

## Completed

### 2026-05-27 — Three-state mode (GRIND / PROFIT / AUTO), sticky manual overrides (commit `04db1af`)
Mode system upgraded from 2-state to 3-state. GRIND and PROFIT are now manual sticky overrides that disable auto-flip (set `auto=false`); AUTO re-enables rank-based behavior. Previously rank-based flip was always on, causing oscillation when leaderboard rank flickered at the 2/3 boundary. Now `brain.set_mode("grind"|"profit")` locks the mode and prevents rank-based flips until `set_mode("auto")` is called. `/agent/mode` GET returns `{mode, auto, selected}` (selected = user pick, mode = effective). Dashboard adds AUTO button; when AUTO is selected, shows live effective mode in parens (e.g. "AUTO (GRI)"). Currently mode locked to GRIND (sticky, auto=false), fast 120s loop, agent holds grind regardless of rank wobble.

### 2026-05-27 — SQLite-backed agent memory + rank-flip threshold fix (commit `89c65c0`)
Added persistent agent state via sqlite at `/app/data/agent.db` (volume-mounted so it survives container rebuilds). New `backend/monitor/db.py` module exposes two tables (`trades`, `market_ticks`) and helpers: `record_trade`, `record_tick`, `last_trades`, `pnl_by_pair`, `recent_ticks`, `stats_summary`. Agent now records every tick's prices + 30-min momentum, and every trade attempt (success / skipped / placed_unfilled / reverted) is mirrored to sqlite from inside `_log`. Brain prompt's LAST N DECISIONS block now reads from the DB (deeper history, survives restarts) and includes a new PER-PAIR NET PnL section so the LLM can bias toward what's been working. New endpoint `GET /agent/stats` exposes totals + 24h per-pair PnL + last 20 trades. **Rank-flip threshold fix:** auto-flip profit→grind required `rank > 3`, which left rank #3 stuck in profit mode. Now uses `rank > 2`, so rank #3 immediately returns to grind. No hysteresis at the 2/3 boundary. Container rebuilt; currently at rank #3 of 11, grind mode active, reclaiming volume ($330 volume, trader-6 at rank #2 with $586).

### 2026-05-26 — Diversity rule in PROFIT mode: rotate pairs across SOMI/USDC.e/WETH (commit `e37fa71`)
Brain was repeatedly picking SOMI:USDso under PROFIT mode because SOMI's high volatility crosses the 0.3% momentum gate far more often than WETH (needs ~$6 absolute movement) or USDC.e (stable at $1.00, momentum always ~0%). Added DIVERSITY RULE to `PROFIT_PROMPT`: after closing a round-trip on pair X, next BUY avoids X. If only X has momentum, HOLD for a tick and let it cool down. `_build_prompt` now emits explicit "avoid X on next BUY" hint post-SELL so LLM doesn't drift back. GRIND prompt unchanged. Container redeployed; agent live at FST loop.

### 2026-05-26 — Dashboard auto-derives BASE_URL from page origin; API key persists via localStorage (commit `a5318a2`)
Live dashboard now auto-detects its URL from `window.location.origin` so it works wherever the backend serves it — only falls back to `<TUNNEL_HOST>` for `file://` testing. API key now reads from localStorage and persists after first entry via the modal. `CAPITAL_FLOOR` synced in firmware to match backend (22 → 35). Deployed to live server; no rebuild needed.

### 2026-05-26 23:59 — History scrub: git-filter-repo redacted secrets (6 rewrites)
Rewrote full commit history via `git-filter-repo --replace-text` to redact sensitive identifiers from git objects: API key `ce0e9ce0c18c6dd0fd6555d115364e40` → `<FLASK_API_KEY>`, Tailscale IP `100.80.130.21` → `<SERVER_HOST>`, public domain `dreamdex.ironyaditya.xyz` → `<TUNNEL_HOST>`, SSH prefix `irony@` → `user@`, bare domain `ironyaditya.xyz` → `<EXAMPLE_DOMAIN>`. Six top commits re-authored for clarity: fill detection, config defaults, mode toggle, shared monitor + auto-drain, dashboard UX, and handover notes. **Important:** Original values are still in user's terminal history and must be rotated/regenerated before sharing repo with broader team. Placeholders need real values plugged in for any local dev work.

### 2026-05-26 18:00 — Live mainnet trading + dashboard rework (6 commits)
- **Vault funding root cause:** vault-funded IOC buys on dreamDEX mainnet never fill. USDso gets reserved then refunded 5-10 min later; wallet-funded buys DO fill. Switched default to `funding=wallet`.
- **Leaderboard PnL bleed:** formula counts ONLY wallet USDso (not vault, not native SOMI). Sells credit USDso to pool vault, silently bleeding wallet runway. Added auto-drain that withdraws quote vault back to wallet post-sell.
- **Vault-delta rewrite:** now reads 5 balances (vault quote, vault base, wallet USDso, wallet base ERC20, wallet native SOMI), accounts for gas, requires TWO-sided movement to confirm fill. Single-side → new `placed_unfilled` status prevents phantom round-trip sells.
- **Mode toggle + auto-flip:** GRIND maximizes volume (always trades), PROFIT only fires when 30-min momentum exceeds ±0.3%. Agent auto-flips grind→profit at rank ≤2, back to grind when rank >3.
- **Brain bug fix:** TradingAgent created its own LeaderboardMonitor that was never `.start()`-ed, so rank check always saw "?". Now shares running monitor from `main.py`.
- **Dashboard UX:** explicit liquidity breakdown table with per-row explanations (USDso wallet, vault, native SOMI, manual reserve); mode badge in agent controls; activity log streaming live decisions + fills + auto-flip events. Fixed two JS/CSS bugs that blanked the dashboard.
- Commits: `36600c1` (fill detection), `680464e` (config defaults), `a21ad60` (mode toggle), `a8a42c7` (shared LB + auto-drain), `1d6b4c4` (dashboard), `127ca9f` (handover notes).

### 2026-05-26 — Live testnet rehearsal
- Deployed Docker container, CF tunnel up, watch firmware re-flashed for HTTPS.
- Devrel funded testnet wallet with +1000 STT.
- Swapped 300 STT → 49.63 USDso (rate ~0.164) so balance cleared the $22 agent floor.
- Agent fired **3 BUY trades on SOMI:USDso** at ~0.16 USDso each, all confirmed via vault-delta proof (the C4 audit fix). LLM picked SOMI for the 60% volume-generator allocation per the system prompt.
- Final testnet state: 857 STT, ~49 USDso, 3 orders done, agent paused for next session.

### 2026-05-26 — Mainnet-safety hardening (commit `42fdf44`)
- 6 critical fixes: Flask `X-API-Key` auth (C1), on-chain Portfolio as capital-floor source (C2), MAX_CONCURRENT_POS code gate (C3), vault-delta success proof replacing log-heuristic (C4), book-aware vault sufficiency (C5), state demoted to history-only (C6).
- 5 high fixes: ManualTrader reads lot/min from MARKETS (H1), approve cap (H2), thread-safe local nonce manager (H3), per-pool quoteDecimals in Portfolio (H4), fill price from limit instead of mid (H5).
- 4 medium fixes: mainnet OPENAI_KEY guard (M1), tick-decimal price format (M2), minQty-vs-AGENT_MAX_TRADE skip (M3), config-priority over API for base/decimals (M5).
- EIP-1559 gas fields when node supports it; legacy gasPrice fallback.

### 2026-05-26 — Containerization + Cloudflare wiring (commit `4e40357`)
- Dockerfile + docker-compose with `network_mode: host` (bridged port-publish was blocked by something host-level on Ubuntu 24.04 — host-mode sidestepped it; root cause undiagnosed but not blocking).
- `RUNBOOK.md` at project root: start/stop/restart, testnet↔mainnet flip, manual curl recipes, common-issue table, full post-contest teardown sequence.
- Cloudflare tunnel `<TUNNEL_HOST>` published via dashboard → `http://localhost:5001`. SIWE → JWT → orders flow proven end-to-end.

### 2026-05-26 — Firmware UX pass (commits `02d24f1` → `85f9f70`)
- `02d24f1` — BACKEND switched from LAN IP to `https://<TUNNEL_HOST>`; WiFiClientSecure with shared `_tlsClient` for keepalive.
- `335c409` — WiFi menu trap fix: UP/DOWN now navigates menus when already connected.
- `b143c17` — Hide WiFi screen from menu cycle when connected; auto-return on disconnect; navDots count updates.
- `77501e3` — Sparklines on Prices (24-sample ring buffer × 3 pairs), richer Portfolio (big total, PnL%, wallet vs vaults), clearer escape hints (`HOLD:home`), Manual SEND screen UP/DOWN cancels back to field 0.
- `e4d1c20` — Performance: per-endpoint fetch timers (agent 5s / prices 10s / portfolio 30s / leaderboard 60s) so at most one HTTP call per loop iteration; HTTP timeout 8s → 4s; `handleButtons()` called after every fetch to keep button latency bounded.
- `85f9f70` — BAL LOW warning on Agent screen when capital floor hit; shows current USDso, dollars short of floor, "Top up to start" call to action.

## Known Issues

- **`Portfolio` reports `base +0` for SOMI native pool vault-delta.** `getWithdrawableBalance(0x0, ...)` doesn't work for native tokens, so we can't read the native base side. Trade success is still proven by quote-delta (USDso left the vault), but the local state's SOMI count drifts. Acceptable for v1; would need a separate `eth_getBalance` query for the pool contract's payable balance to fix.
- **Leaderboard endpoint 404** — `https://dreamdex-leaderboard.vercel.app/api/leaderboard` returns DEPLOYMENT_NOT_FOUND. Likely the contest leaderboard hasn't been deployed yet. Agent gracefully treats `lb_data` as empty and continues.
- **`docker compose port` doesn't list any host bindings** even though `host` network mode means Flask IS bound on the host. Cosmetic — `ss -lntp` confirms the port is up. CF tunnel reaches it normally.
- **`AGENT_STOP_BELOW = 22.0`** is hardcoded both in `config.py` (backend) and `AGENT_FLOOR_USDSO` (firmware) — if you change one, change the other.

## TODO / Backlog

- **Mainnet flip when contest starts.** Set `DREAMDEX_ENV=mainnet` in `~/dreamdex-agent/.env` on the server, `docker compose restart agent`, transfer the 50 USDso contest seed to wallet `0xF4c8…2b905`. Watch firmware needs no change (already points at the tunnel).
- **Eventual mainnet teardown.** Full sequence in `RUNBOOK.md` → "Post-contest teardown".
- **(Optional) Get the SOMI native-pool base balance into Portfolio.** Read pool contract's payable balance via `eth_getBalance(pool, ...)` instead of `getWithdrawableBalance(arena, 0x0)`. Low priority — the floor check uses USDso anyway.

## Lessons

- **Hysteresis flips need an explicit opt-in flag when manual overrides exist.** Rank-based auto-flips work for fully automatic systems, but when you add sticky manual mode-locks (e.g., lock to GRIND, disable auto-flip), the auto flip becomes a distraction — users expect their manual pick to stick until explicitly overridden. Solution: add an `auto` flag (default true); set_mode("grind"/"profit") sets `auto=false`, re-enabling requires explicit set_mode("auto"). This prevents rank wobble from fighting user intent.
- **Off-by-one hysteresis at tier boundaries.** If a threshold like `rank > 3` keeps a tier (rank #3) stuck at the boundary with no recovery path, flip it to `rank > 2`. A tier that needs action to recover shouldn't be locked out of taking that action. No hysteresis gap needed — immediate flip is correct.
- **Wallet-funded only.** Vault-funded IOC buys on dreamDEX mainnet never fill — USDso reserved then refunded 5–10 min later. Wallet-funded fills normally. This is the ONLY path that works on mainnet; never retry vault-funded as a fallback.
- **Leaderboard PnL bleeds wallet, not vault.** The PnL formula reads ONLY wallet USDso (not vault quote, not vault base, not native SOMI). Sells credit USDso to vault; vault PnL looks good but wallet runway silently drains. Always auto-drain vault quote back to wallet post-sell on mainnet to preserve runway.
- **Two-sided vault-delta is the real fill signal.** Single-side movement (quote only, or base only) can happen from gas refunds or unrelated vault ops. Require BOTH quote AND base (or quote AND native) to move in expected directions to confirm a fill. Single-side → `placed_unfilled` status prevents phantom round-trip sells.
- **Shared LeaderboardMonitor, not per-agent.** If TradingAgent creates its own monitor without calling `.start()`, rank checks always see "?". Always pass the main loop's running monitor to agent init, or create + start the monitor as a pre-condition before any agent spawns.
- **CSS `theme()` doesn't resolve in plain `<style>` blocks via CDN.** Tailwind `theme()` calls inside Tailwind CDN-loaded pages sometimes fail. Hard-code hex colours with `!important` when `:active`-style overrides need guaranteed specificity wins.
- **OrderPlaced events emit `filled=0` even on real fills.** Don't trust `filled` field from on-chain logs. Use vault-balance delta (pre vs post `getWithdrawableBalance`) as the authoritative signal.
- **Decimal field renames require grep cleanup.** When refactoring (e.g., `dec` → `decLog`), easy to miss a reference and silently pass wrong args to `Decimal()`. Always grep the old name to verify full migration.
- **WBTC is 8 decimals on dreamDEX, not 18.** USDC.e is 6. The agent's MARKETS dict encodes this; if you hardcode elsewhere, order quantities will be orders of magnitude off.

## Resume From Here

**Mode now sticky-GRIND (auto=false, rank-flips disabled) — agent holds trading strategy regardless of leaderboard rank wobble.** Three-state mode system live: manual overrides (GRIND/PROFIT) lock the mode and disable auto-flip; AUTO re-enables rank-based behavior. SQLite memory is live; agent records every tick + trade to `/app/data/agent.db` (survives rebuilds). Prompt reads LAST N DECISIONS from DB + PER-PAIR NET PnL. New `/agent/stats` endpoint shows totals, 24h per-pair PnL, and last 20 trades. Dashboard has AUTO button; when selected, shows live effective mode in parens. Current wallet $44.49 USDso + 16.4 SOMI, volume $330 (rank #3 of 11).

### Next steps
1. **Monitor sticky grind mode.** With auto=false, agent stays in grind regardless of rank swings at 2/3 boundary. Verify volume grows without oscillation.
2. **Watch dashboard mode display.** When AUTO is selected, verify the mode badge shows effective state (e.g. "AUTO (GRI)" or "AUTO (PRO)").
3. **Test manual mode toggles.** Switch GRIND → PROFIT → AUTO via dashboard API and confirm `/agent/mode` returns correct `{mode, auto, selected}` tuple.

### Critical pointers
| Item | Value |
|---|---|
| **Rank** | #3 of 11 (was #2, slipped with momentum trades) |
| **Mode** | GRIND (auto-corrected from PROFIT when rank > 2) |
| **DB path** | `/app/data/agent.db` (docker volume, survives restarts) |
| **Stats endpoint** | `GET /agent/stats` (totals, 24h per-pair PnL, last 20 trades) |
| **Threshold fix** | `rank > 2` now (was `rank > 3` — kept rank #3 stuck) |

### Critical pointers

| Item | Value |
|---|---|
| **Container** | `user@<SERVER_HOST>:~/dreamdex-agent/` (Docker, mainnet live) |
| **Public URL** | `https://<TUNNEL_HOST>` |
| **Wallet** | `0xF4c825F3C2970153d78B407CF190861dd4E2b905` ($44.50 USDso + ~16.5 SOMI) |
| **Loop speed** | NORMAL (300s) — reset on Docker restart; set FST (120s) in watch if needed |
| **Mode** | PROFIT (auto-flips ≤2 rank → PROFIT, >3 rank → GRIND) |
| **Rank** | #2 of 11 |
| **API key location** | `FLASK_API_KEY` env var (server `.env`), `#define API_KEY` (firmware `wifi_secrets.h`) |
| **Runbook** | `RUNBOOK.md` (includes full teardown sequence) |
