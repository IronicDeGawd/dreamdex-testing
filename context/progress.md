# Progress — DreamDEX Contest Agent

**Live on mainnet, agent running in PROFIT mode with diversity rule at rank #2 of 11.** Wallet $44.50 USDso + ~16.5 SOMI native; 137 txns executed with 77 fills (~56% rate); $192+ volume traded; contest leaderboard PnL −$5.49. Brain now rotates trading pairs (SOMI/USDC.e/WETH) instead of spam-picking SOMI under PROFIT mode. Dashboard shows live liquidity breakdown (wallet USDso, vault, native SOMI, manual reserve), mode badge (GRIND/PROFIT), and activity log streaming decisions. All fixes deployed: vault funding (mainnet only, wallet-funded fills; vault-funded never fills), auto-drain post-sell, two-sided vault-delta fill detection, grind/profit modes with rank-based auto-flip (≤2 → PROFIT, >3 → GRIND), shared LeaderboardMonitor for accurate rank display.

Stack: Python 3.11 + Flask + web3.py agent backend (Docker `network_mode: host`); ESP32-C3 + SSD1306 OLED watch; OpenAI gpt-4o-mini trading brain; dreamDEX REST + SIWE. Server `user@<SERVER_HOST>` (Tailscale), Cloudflare tunnel `<TUNNEL_HOST>` → `localhost:5001`. Git history scrubbed: all instances of API key, Tailscale IP, public domain, SSH user prefix, and bare domain replaced with placeholders.

## Completed

### 2026-05-27 15:30 — Outcome-aware activity log + avoid-list for failing pairs (commit `f0da9b5`)
Trade result status now attached to each `last_decision` in the `/agent` endpoint. Activity log icons now reflect outcome, not intent: 🟢 BUY landed, 🔴 SELL landed, 🟡 sim-rejected (would_revert), 🟠 silent_reject / placed_unfilled / reverted, ⏸ HOLD, ⚪ unknown. Log waits one poll for `result_status` to populate so each entry shows final outcome. Brain's `_build_prompt` scans last 10 DB rows per pair and emits explicit `PAIRS TO AVOID THIS TICK` block when a pair's last 2+ attempts returned failure statuses (would_revert / silent_reject / placed_unfilled / reverted / unverified). GRIND prompt's hard rules reference this block so LLM rotates off failing pairs. First post-restart decision returned would_revert for SOMI — avoid-list will rotate to different pair next tick.

### 2026-05-27 14:24 — Hard-clamp min trade to $7, dashboard contrast invert (commit `f9ae085`)
`AGENT_MIN_TRADE` bumped 0.10 → 7.00 in `backend/config.py`. The agent's `max(MIN, min(MAX, amt))` clamp now guarantees every trade lands in [$7, $8], regardless of LLM output. Brain prompt updated to state $7 as hard floor (was picking $5 despite "$7–$8 sweet spot" guidance). Static palette inverted: outer body now white-ish `--c-bg #f4f4f4` with `--c-page-text #111`; panel interiors stay dark (`--c-surface #0d0d0d`, `--c-text #f5f5f5`). First decision post-restart: `BUY SOMI $8.00` (full cap). Verified: `docker exec` reports `min=$7.0 max=$8.0`.

### 2026-05-27 14:22 — Theme() CSS variable swap, modal style rebuild (commit `d1eb25a`)
Tailwind CDN's plain `<style>` blocks were not reliably resolving `theme()` calls, leaving modal headers and confirmation buttons with broken styles. Replaced every `theme()` with `:root` CSS variables (`--c-bg`, `--c-surface`, `--c-border`, `--c-text`, `--c-muted`, `--c-danger`, `--c-success`, `--c-warning`). New `.modal-card`, `.modal-overlay`, `.btn-primary`, `.btn-warning`, `.btn-danger` classes use CSS variables with inline fallbacks. Bumped panel surface #111 → #1a1a1a and text → #f5f5f5 for visible separation from page background.

### 2026-05-27 14:20 — Manual trades mirror to SQLite memory (commit `6b97aaf`)
`ManualTrader.execute` was bypassing the agent's `_log`, so manual trades from the dashboard or watch never reached sqlite. Added `db.record_trade(..., mode="manual")` inside the manual trade path. Verified: a manual $2 SOMI SELL now shows in `/agent/stats` last_trades with `mode=manual` and `status=success`.

### 2026-05-27 14:18 — Trade cap bump to $8, floor lowered to $30 (commit `54db550`)
Wallet steady at ~$44.50 provides headroom. Bumped `AGENT_MAX_TRADE` from 5.0 to 8.0 (+60% volume per round-trip), lowered `AGENT_STOP_BELOW` from 35.0 to 30.0. Brain prompt bias updated toward $7–$8 per trade. Mid-cycle exposure ~$36.50 with ~$6.50 buffer above floor. Rank #3 of 11, volume $330 (trader-6 at rank #2 with $586).

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

- **CSS `theme()` calls don't resolve in plain `<style>` blocks loaded via Tailwind CDN.** When the Tailwind build doesn't happen at server startup, browsers can't find theme references. Use `:root` CSS variables with hard-coded hex values instead. This guarantees specificity wins even with `!important` overrides for `:active`-style states.
- **Every code path that writes to the chain must also write to the analysis database.** If `ManualTrader.execute` skips `db.record_trade()` while `TradingAgent._log` always calls it, manual trades vanish from stats. Grep for all trade-execution paths (manual, automated, signal-based) and ensure every one records to the same DB table. This is load-bearing for accuracy metrics and LLM context.
- **Hysteresis flips need an explicit opt-in flag when manual overrides exist.** Rank-based auto-flips work for fully automatic systems, but when you add sticky manual mode-locks (e.g., lock to GRIND, disable auto-flip), the auto flip becomes a distraction — users expect their manual pick to stick until explicitly overridden. Solution: add an `auto` flag (default true); set_mode("grind"/"profit") sets `auto=false`, re-enabling requires explicit set_mode("auto"). This prevents rank wobble from fighting user intent.
- **Off-by-one hysteresis at tier boundaries.** If a threshold like `rank > 3` keeps a tier (rank #3) stuck at the boundary with no recovery path, flip it to `rank > 2`. A tier that needs action to recover shouldn't be locked out of taking that action. No hysteresis gap needed — immediate flip is correct.
- **Wallet-funded only.** Vault-funded IOC buys on dreamDEX mainnet never fill — USDso reserved then refunded 5–10 min later. Wallet-funded fills normally. This is the ONLY path that works on mainnet; never retry vault-funded as a fallback.
- **Leaderboard PnL bleeds wallet, not vault.** The PnL formula reads ONLY wallet USDso (not vault quote, not vault base, not native SOMI). Sells credit USDso to vault; vault PnL looks good but wallet runway silently drains. Always auto-drain vault quote back to wallet post-sell on mainnet to preserve runway.
- **Two-sided vault-delta is the real fill signal.** Single-side movement (quote only, or base only) can happen from gas refunds or unrelated vault ops. Require BOTH quote AND base (or quote AND native) to move in expected directions to confirm a fill. Single-side → `placed_unfilled` status prevents phantom round-trip sells.
- **Shared LeaderboardMonitor, not per-agent.** If TradingAgent creates its own monitor without calling `.start()`, rank checks always see "?". Always pass the main loop's running monitor to agent init, or create + start the monitor as a pre-condition before any agent spawns.
- **CSS `theme()` doesn't resolve in plain `<style>` blocks via CDN.** Tailwind `theme()` calls inside Tailwind CDN-loaded pages sometimes fail. Hard-code hex colours with `!important` when `:active`-style overrides need guaranteed specificity wins.
- **LLM prompts are suggestions, not rules.** A prompt like "$7–$8 sweet spot" guided behavior in earlier test, but the LLM still picked $5 when reasoning diverged. Hard rules (trade size, capital floors, mode locks) must be enforced at the code level with mathematical clamps, not natural-language guidance. Prompts guide behavior within code boundaries; code sets the boundary.
- **OrderPlaced events emit `filled=0` even on real fills.** Don't trust `filled` field from on-chain logs. Use vault-balance delta (pre vs post `getWithdrawableBalance`) as the authoritative signal.
- **Decimal field renames require grep cleanup.** When refactoring (e.g., `dec` → `decLog`), easy to miss a reference and silently pass wrong args to `Decimal()`. Always grep the old name to verify full migration.
- **WBTC is 8 decimals on dreamDEX, not 18.** USDC.e is 6. The agent's MARKETS dict encodes this; if you hardcode elsewhere, order quantities will be orders of magnitude off.
- **Outcome-aware log icons matter when the same action can have wildly different outcomes.** When "BUY SOMI" can mean a real on-chain buy, a sim rejection, or a placement that never fills, the user sees three identical entries and assumes three different things happened. Surface the final status to the user, not the intent. Use distinct icons (🟢/🔴/🟡/🟠) per outcome, not side. Log should wait for result_status to populate so users read accurate history, not speculative narrative.

## Resume From Here

**Four fixes deployed: CSS variable swap, manual trade DB mirroring, $8 cap bump, and hard min-trade clamp to $7.** New $7–$8 sizing band is now code-enforced with `AGENT_MIN_TRADE=7.0` clamp, not just prompt guidance. Agent running GRIND (sticky, auto=false), loop 120s, rank #3 of 11 (~$330 volume). Wallet $44.50 USDso + ~47 SOMI; mid-cycle exposure ~$36.50 with ~$6.50 buffer. Target: rank #2 (~$256 more volume).

### Next steps
1. **Monitor volume + LLM decisioning.** With hard $7 floor + $8 cap, verify LLM stays within bounds and volume accelerates toward rank #2.
2. **Watch for buy frequency.** Hard $7 floor removes the $5 wiggle room; if LLM avoids small moves, cycle time may increase. Adjust brain prompt's momentum threshold if needed.
3. **CSS/modal verification.** Reload dashboard; confirm theme colors render correctly (no broken theme() calls in practice).

### Critical pointers
| Item | Value |
|---|---|
| **Rank** | #3 of 11 (target #2: +$256 volume) |
| **Mode** | GRIND (sticky, auto=false) |
| **Trade min** | $7.00 (code-enforced clamp) |
| **Trade max** | $8.00 |
| **Floor** | $30.00 USDso |
| **DB path** | `/app/data/agent.db` (docker volume) |
| **Loop speed** | 120s (FST, locked in watch) |
