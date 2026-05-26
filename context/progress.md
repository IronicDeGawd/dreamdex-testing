# Progress — DreamDEX Contest Agent

## Current Status

**Deployed + smoke-tested on testnet 2026-05-26.** Agent is currently **paused** at 3 successful trades on testnet. Wallet has ~857 STT + ~49 USDso. All code reviewed for mainnet safety; all 15 audit findings (6 critical + 5 high + 4 medium) patched and committed. Server runs as a Docker Compose service on `user@<SERVER_HOST>` (Tailscale home server), fronted by Cloudflare tunnel `<TUNNEL_HOST>` → host `localhost:5001`.

Stack: Python 3.11 + Flask + web3.py for the agent backend (Docker, `network_mode: host`); ESP32-C3 + SSD1306 OLED + 3-button UI for the watch; OpenAI gpt-4o-mini for the trading brain; dreamDEX REST API + SIWE auth for orders. Contest budget split: 30 USDso to agent, 20 USDso for manual trades.

## Completed

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

- **Cloudflare tunnel + Flask: bind to `0.0.0.0` OR use `network_mode: host`.** Bridged port-publish via Docker (`127.0.0.1:5001:5001`) refused to forward on this Ubuntu 24.04 box even though Docker said the container was healthy. Host network mode side-steps the issue.
- **`docker compose config` prints `env_file` contents in plaintext** (incl. `OPENAI_KEY` + `TESTNET_PRIVATE_KEY`). Never run with shared screen / public logs.
- **ESP32 HTTPS via WiFiClientSecure**: a shared file-scope client (`_tlsClient`) with `setInsecure()` is much faster than per-request handshake on the C3 — saves ~25 KB heap pressure and several hundred ms of TLS negotiation per call.
- **CF tunnel adds real RTT** vs LAN. p50 100-300 ms, p99 ~2 s. HTTP timeouts must be set with this in mind (we use 4 s).
- **dreamDEX trade success ≠ tx.status=1 + any pool log.** A vault-deposit log in the same block can fool the heuristic. Use **vault-balance delta** (pre vs post `getWithdrawableBalance`) as the authoritative signal. C4 fix.
- **WBTC is 8 decimals on dreamDEX mainnet, not 18.** USDC.e is 6. Get this wrong and order quantities are off by orders of magnitude. The agent's MARKETS dict + `refresh_market_params` already encode this; verified vs research/dreamdex-contracts.md.

## Resume From Here

**State at end of 2026-05-26 PM session:** agent paused at 3 testnet trades; container running; tunnel live; firmware up to date on `main` (head `85f9f70`).

### Next steps (user-driven)

1. **(Optional) Re-flash the watch** so the latest UX pass (BAL LOW warning, sparklines, staggered fetches) is on the device. Arduino IDE → `firmware/watch.ino` → Upload. Re-flash needed only if the device is still running an older firmware.
2. **(Optional) Resume testnet rehearsal** — press SELECT on watch Agent screen to unpause, or `curl -X POST -H "X-API-Key: ..." -d '{}' https://<TUNNEL_HOST>/agent/toggle`. Agent will resume on next 120s tick.
3. **Mainnet day**:
   - Move the 50 USDso contest seed to wallet `0xF4c825F3C2970153d78B407CF190861dd4E2b905`.
   - SSH to server: `sed -i 's/^DREAMDEX_ENV=.*/DREAMDEX_ENV=mainnet/' ~/dreamdex-agent/.env && cd ~/dreamdex-agent && docker compose restart agent`.
   - Confirm boot logs say `MAINNET mode`, wallet `0xF4c8…2b905`.
   - Unpause agent from watch or via curl.
4. **Post-contest teardown** — follow `RUNBOOK.md` § Post-contest teardown.

### Critical state pointers

| Thing | Where |
|---|---|
| Agent container | `user@<SERVER_HOST>:~/dreamdex-agent/` (Docker Compose) |
| Public URL | `https://<TUNNEL_HOST>` |
| Testnet wallet | `0xe21c64a04562D53EA6AfFeB1c1561e49397B42dd` (857 STT / 49 USDso) |
| Mainnet wallet | `0xF4c825F3C2970153d78B407CF190861dd4E2b905` (empty until contest seed lands) |
| API key | `FLASK_API_KEY` in server `.env`, `#define API_KEY` in `firmware/wifi_secrets.h` — never commit |
| Watch firmware HEAD | `85f9f70` |
| Runbook | `RUNBOOK.md` at project root |
