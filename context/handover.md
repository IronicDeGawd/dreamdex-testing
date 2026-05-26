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

**Status:** agent **paused** at 3 successful testnet trades. Container running. Tunnel live. Watch firmware ready (re-flash if older than `85f9f70`).

**Wallet snapshot:**
- Testnet `0xe21c64…42dd`: 857 STT, 49 USDso
- Mainnet `0xF4c8…2b905`: 0 / 0 (awaiting contest seed)

**Hot path commands** (memorise or alias):
```bash
# Status (no auth)
curl https://<TUNNEL_HOST>/agent | jq

# Toggle pause/resume (auth)
curl -X POST -H "X-API-Key: <see backend/.env FLASK_API_KEY>" \
  -H "Content-Type: application/json" -d '{}' \
  https://<TUNNEL_HOST>/agent/toggle

# Server logs
ssh user@<SERVER_HOST> 'cd ~/dreamdex-agent && docker compose logs -f agent'

# Restart service (after .env edit)
ssh user@<SERVER_HOST> 'cd ~/dreamdex-agent && docker compose restart agent'
```

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
