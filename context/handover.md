# Handover — DreamDEX Contest Agent

> Quick-resume essentials. For the full operational story see `context/progress.md` and `RUNBOOK.md`.

## Branch + commits

```
main (head)
  85f9f70 feat(firmware): BAL LOW warning on Agent screen when capital floor hit
  e4d1c20 perf(firmware): stagger fetches + tighter HTTP timeout
  77501e3 feat(firmware): sparklines on prices + richer Portfolio + escape hints
  b143c17 feat(firmware): hide WiFi screen from cycle when connected
  335c409 fix(firmware): unblock menu navigation on WiFi screen
  02d24f1 feat(firmware): point watch at Cloudflare tunnel + HTTPS
  4e40357 feat(deploy): dockerize backend + RUNBOOK
  42fdf44 feat(backend,firmware): mainnet-safety hardening (15 fixes)
```

No uncommitted edits at session end.

## Session Notes

**Status (2026-05-26 evening):** agent **running**, mode **PROFIT** (auto-flipped at rank ≤ 2), loop 300s (NORMAL — reset on container restart; flip to fast via /agent/speed if desired).

**Live leaderboard:** rank **#2/11**, txCount 137, fills 77 (~56% rate), volume **$192+**, wallet $44.50, native 16.5 SOMI, contest PnL −$5.49.

**Hot path commands (auth = `X-API-Key: $FLASK_API_KEY` — see server `.env`):**
```bash
# Mode flip (auto-flips at rank ≤ 2)
curl -X POST -H "X-API-Key: $K" -H 'content-type: application/json' \
  -d '{"mode":"profit"}' https://<TUNNEL_HOST>/agent/mode
# Drain vault USDso back to wallet (recover stuck funds)
ssh user@<SERVER_HOST> 'docker exec dreamdex-agent python3 /app/drain_now.py'
# Force-recreate container (must after env_file edits)
ssh user@<SERVER_HOST> 'cd ~/dreamdex-agent && docker compose up -d --force-recreate --build'
```

**Big code changes shipped this session (all on `main` working tree; not yet committed):**
- `trading/dreamdex.py` — vault_delta rewritten: 5-balance read (vault quote/base + wallet USDso/base/native), 3s settlement delay, gas-aware, two-sided movement required → fill_proven, single-side → new `placed_unfilled` status. Default buy buffer +1→+5 ticks (empirical: +1 doesn't cross on mainnet).
- `trading/wallet.py` — auto-retry once on `nonce too low` (handles docker-exec races).
- `config.py` — `AGENT_FUNDING_SOURCE=wallet` (vault-funded buys don't fill), `AGENT_STOP_BELOW=35.0`, `AGENT_MAX_TRADE=5.0`.
- `agent/brain.py` — split prompts: `GRIND_PROMPT` (volume) + `PROFIT_PROMPT` (momentum, hold-by-default). Runtime `set_mode/get_mode`.
- `agent/agent.py` — auto-flip rank≤2 runs FIRST in `_tick` (before capital-floor return). Affordability check before vault deposits. Auto-drain quote vault after every successful SELL.
- `main.py` — `TradingAgent(portfolio=…, lb=…)` shares the *running* LB monitor (prev bug: agent had its own that was never `.start()`-ed, so `my_rank` was always "?").
- `monitor/leaderboard.py` — `get_my_stats` now exposes `my_volume`, `my_fills`, `my_pnl`, `my_balance`.
- `server.py` — new `/agent/mode` GET/POST.
- `static/index.html` — explicit Liquidity Breakdown table (Wallet/Vault/Native/Manual reserve, each with one-line explainer), Mode buttons (GRIND/PROFIT), activity log surfaces brain decisions + tx_count deltas + mode flips. Fixed `theme()` not resolving in plain `<style>` → hardcoded `#e5e5e5`/`#0a0a0a` with `!important` on `.btn-active`. Renamed `const dec` → `decLog` to avoid clash with existing block.

**Hard-won gotchas (don't re-discover):**
- **Leaderboard PnL = wallet USDso − $50.** Vault USDso + native SOMI DON'T count. Auto-drain after sells is mandatory or wallet bleeds.
- **Vault-funded IOC buys evaporate $ for 5–10 min then refund.** Wallet-funded fills cleanly. Default funding must be `wallet`.
- **dreamDEX OrderPlaced log shows `filled=0` even on actual fills** — they emit the event before the in-block match settles. Trust vault/wallet deltas, not the event.
- **Container rebuild resets agent state** (speed/mode env defaults, in-memory history). Re-set speed after every restart.
- **Auto-flip needs SHARED LB monitor** (R6 fix); standalone `LeaderboardMonitor()` never starts polling without `.start()`.

**Open follow-ups:**
- Commit the session's edits (`git add -A && git commit` — files in flight cover all of `trading/`, `agent/`, `config.py`, `main.py`, `monitor/leaderboard.py`, `server.py`, `static/index.html`).
- Loop is at NORMAL (300s); user may want FST (120s) for higher volume gen.
- Profit-mode brain made its first real momentum trade (`BUY 80% • momentum down, buy the dip`) — let it run, observe whether PnL recovers.

## Hot gotchas (already encoded into the code — don't re-discover)

- **Mainnet refuses to start without `FLASK_API_KEY` AND `OPENAI_KEY`.** Either set both real values OR set `OPENAI_KEY=disable` (which forces hold-only operation, no blind real-money trades).
- **Capital floor is `AGENT_STOP_BELOW = $22 USDso`.** Below this the agent holds and the watch shows BAL LOW. Top up USDso to unblock.
- **WBTC = 8 decimals, USDC.e = 6, others = 18.** Hardcoded in `config.py` MARKETS. Don't trust an API refresh to override these silently — `refresh_market_params` keeps config when the API disagrees (M5 fix).
- **Vault-delta proves a fill, NOT log presence.** SOMI native pool returns base+0 because we can't read native vault — that's expected; quote-delta still proves it.
- **Cloudflare tunnel needs Flask on the host's network namespace** (we use `network_mode: host`). Bridged port-publish (`127.0.0.1:5001:5001`) was blocked by something on this Ubuntu 24.04 box.

## Mainnet flip sequence (when contest starts)

1. Move 50 USDso to `0xF4c825F3C2970153d78B407CF190861dd4E2b905` (mainnet)
2. `ssh user@<SERVER_HOST>`
3. `sed -i 's/^DREAMDEX_ENV=.*/DREAMDEX_ENV=mainnet/' ~/dreamdex-agent/.env`
4. `cd ~/dreamdex-agent && docker compose restart agent`
5. `docker compose logs --tail=30 agent` — confirm `MAINNET mode` banner
6. Unpause via watch SELECT or via the toggle curl

## Post-contest teardown (do all of these)

See `RUNBOOK.md` § Post-contest teardown for the canonical sequence. TL;DR:

```bash
ssh user@<SERVER_HOST>
cd ~/dreamdex-agent
docker compose down
docker rmi dreamdex-agent:latest
shred -u .env                       # wipe key
cd ~ && rm -rf ~/dreamdex-agent
```

Also: delete the `<TUNNEL_HOST>` published-app in the Cloudflare dashboard. Drain remaining funds from the mainnet wallet to your main address.
