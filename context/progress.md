# Progress — DreamDEX Contest Agent

## Current Status

**Live on mainnet, agent running in PROFIT mode at rank #2 of 11.** Wallet $44.50 USDso + 16.5 SOMI native; 137 txns executed with 77 fills (~56% rate); $192+ volume; leaderboard PnL -$5.49 (real session cost ~$5 across fills). Dashboard now shows live liquidity breakdown, mode badge, activity log. Vault-funded buys never filled on dreamDEX mainnet (USDso reserved then refunded 5-10 min later) — switched to wallet funding. Auto-drain post-sell to prevent wallet runway bleed. Agent auto-flips grind→profit at rank ≤2 and back when rank >3. Two-sided vault-delta fill detection + nonce-drift recovery deployed.

Stack: Python 3.11 + Flask + web3.py for the agent backend (Docker, `network_mode: host`); ESP32-C3 + SSD1306 OLED + 3-button UI for the watch; OpenAI gpt-4o-mini for the trading brain; dreamDEX REST API + SIWE auth for orders. Server on `user@<SERVER_HOST>` (Tailscale home server), fronted by Cloudflare tunnel `<TUNNEL_HOST>` → `localhost:5001`.

## Completed

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

- **Decimal field renames require careful testing.** The vault-delta refactor renamed `dec` → `decLog` in several places (balance reading, logging, gas accounting). Easy to miss a reference and silently pass wrong args to `Decimal()`. Always grep the old name to ensure full migration.
- **CSS `.btn-active` state: `!important` may be needed.** If a global stylesheet has a stronger specificity on `:hover` or `:active`, literal hex color `#fff !important` can be necessary to force the active state to stay visible. Document non-obvious overrides inline.
- **Vault-funded orders on dreamDEX mainnet never fill.** USDso gets reserved for ~5-10 min then refunded. Wallet-funded orders fill normally. The leaderboard PnL formula counts ONLY wallet USDso (not vault balance), so every profitable round-trip silently drains wallet runway. Always auto-drain vault to wallet post-sell on mainnet.
- **Two-sided vault-delta is the fill confirmation signal, not single-side.** Vault quote can move from gas refunds or vault operations. Require BOTH quote AND base (or quote AND native) to move in the expected direction to call a trade "filled". Single-side movement → `placed_unfilled` status, preventing phantom sells.
- **Shared monitor instead of per-agent LeaderboardMonitor.** If TradingAgent creates its own monitor without `.start()`, rank checks always see "?". Always share the main loop's running monitor via a passed-in reference, or make monitor instantiation + startup a pre-condition of agent init.
- **Cloudflare tunnel + Flask: bind to `0.0.0.0` OR use `network_mode: host`.** Bridged port-publish via Docker refused to forward on Ubuntu 24.04 even though Docker said healthy. Host mode side-steps the issue.
- **dreamDEX trade success ≠ tx.status=1 + any pool log.** A vault-deposit log in the same block can fool the heuristic. Use **vault-balance delta** (pre vs post `getWithdrawableBalance`) as the authoritative signal.
- **WBTC is 8 decimals on dreamDEX mainnet, not 18.** USDC.e is 6. Order quantities are off by orders of magnitude if wrong. The agent's MARKETS dict + `refresh_market_params` already encode this; verified vs research/dreamdex-contracts.md.

## Resume From Here

**State at end of 2026-05-26 evening session:** agent **live on mainnet**, mode PROFIT (auto-flipped from grind), loop NORMAL (300s), unpaused. Rank #2 of 11 contestants. 137 txns, 77 fills (~56% fill rate), $192+ volume, wallet $44.50 USDso + 16.5 SOMI native, leaderboard PnL -$5.49. Container running on server `user@<SERVER_HOST>:~/dreamdex-agent/`, tunnel `<TUNNEL_HOST>` live. Firmware on `main` branch.

### Open follow-ups

1. **Container loop speed:** Currently set to NORMAL (300s = 5 min per trade cycle). User may want FST (120s) for higher volume attempt. Flip in watch Agent screen or via backend config if performance allows.
2. **Profit mode test:** Just fired its first momentum trade (`BUY 80% • momentum down, buy the dip`). Observe next 1-2 hours whether it nets actual PnL vs GRIND baseline. If yes, keep PROFIT mode active; if no or unstable, revert to GRIND.
3. **Watch firmware:** Dashboard UX and activity log are on latest `main` (head `127ca9f`). If user is still on older build, re-flash: Arduino IDE → `firmware/watch.ino` → Upload (optional, only if old firmware in use).

### Post-session decision points

- **Keep PROFIT mode or revert to GRIND?** Observe 1-2 hours of P&L and fill rates. If momentum trades consistently beat hold-out cost, keep it. Otherwise flip back.
- **Increase loop speed to FST (120s)?** Would double trade frequency but may degrade fill rate and burn through capital faster. Watch rank trend first.
- **Tweak momentum threshold (currently ±0.3%)?** If fills are too frequent or too rare, adjust `MOMENTUM_THRESHOLD` in backend config and watch the activity log.

### Critical state pointers

| Thing | Where |
|---|---|
| Agent container | `user@<SERVER_HOST>:~/dreamdex-agent/` (Docker Compose, mainnet) |
| Public URL | `https://<TUNNEL_HOST>` |
| Mainnet wallet | `0xF4c825F3C2970153d78B407CF190861dd4E2b905` ($44.50 USDso + 16.5 SOMI) |
| Loop speed | NORMAL (300s) — flip in watch or backend config |
| Mode | PROFIT (auto-flips grind↔profit by rank) |
| Rank | #2 of 11 |
| API key | `FLASK_API_KEY` in server `.env`, `#define API_KEY` in `firmware/wifi_secrets.h` |
| Watch firmware HEAD | `127ca9f` (post-dashboard commit) |
| Runbook | `RUNBOOK.md` at project root (includes teardown) |
