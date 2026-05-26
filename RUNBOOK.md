# DreamDEX Contest Agent — Runbook

Deployed on `user@<SERVER_HOST>` (Tailscale, also reachable via `ssh user@<SERVER_HOST>`).
Frontend: `https://<TUNNEL_HOST>` → Cloudflare tunnel → `localhost:5001` on server.
Wallet: `0xF4c825F3C2970153d78B407CF190861dd4E2b905` (mainnet).
Code: `~/dreamdex-agent/` on server.

## Start / stop / restart

Runs as a **Docker Compose** service with `restart: unless-stopped` and `network_mode: host` (the bridged port-publish path was blocked by something host-level on this Ubuntu 24.04 box — host-mode sidesteps it).

```bash
ssh user@<SERVER_HOST>
cd ~/dreamdex-agent

# Status
docker compose ps

# Stop (use this when contest ends)
docker compose stop          # graceful: stop but keep container/network for fast restart
docker compose down          # nuke: stop + remove container + network

# Start
docker compose up -d         # foreground use `-f` instead of `-d`

# Restart (after .env edit or code change — code change needs --build)
docker compose restart        # restart only, no rebuild
docker compose up -d --build  # rebuild image then start

# Live logs
docker compose logs -f agent
# Or just tail the file:
tail -f ~/dreamdex-agent/logs/agent.log
```

## Post-contest teardown (DO THIS)

After the contest ends, run all of these to fully retire the deployment:

```bash
ssh user@<SERVER_HOST>
cd ~/dreamdex-agent

# 1. Stop + remove container/network
docker compose down

# 2. Remove the image too
docker rmi dreamdex-agent:latest

# 3. (Optional) clean any leftover systemd unit from the earlier attempt
systemctl --user disable --now dreamdex-agent.service 2>/dev/null
rm -f ~/.config/systemd/user/dreamdex-agent.service
systemctl --user daemon-reload

# 4. CRITICAL: wipe the env file (private key inside)
shred -u ~/dreamdex-agent/.env

# 5. (Optional) wipe the code directory entirely
cd ~ && rm -rf ~/dreamdex-agent
```

In the **Cloudflare dashboard** also:
- Delete the `<TUNNEL_HOST>` published-application entry
- (Optional) revoke the CNAME if not auto-cleaned

In your **wallet**:
- Move any remaining USDso + STT off `0xF4c8…2b905` once trading stops, so the live key on the VPS goes cold.

## Flipping testnet ↔ mainnet

```bash
ssh user@<SERVER_HOST>
cd ~/dreamdex-agent
sed -i 's/^DREAMDEX_ENV=.*/DREAMDEX_ENV=mainnet/' .env   # or =testnet
docker compose restart agent
docker compose logs --tail=30 agent                      # confirm correct ENV booted
```

## Manual operations from your laptop

```bash
KEY=<FLASK_API_KEY>

# Pause / resume agent
curl -X POST -H "X-API-Key: $KEY" -H "Content-Type: application/json" -d '{}' \
  https://<TUNNEL_HOST>/agent/toggle

# Status (no auth needed — read-only)
curl -s https://<TUNNEL_HOST>/agent | jq

# Portfolio
curl -s https://<TUNNEL_HOST>/portfolio | jq

# Manual trade
curl -X POST -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"pair":"WETH:USDso","side":"buy","amount_usdso":1.0}' \
  https://<TUNNEL_HOST>/manual

# Cap agent's total order count (0 = unlimited)
curl -X POST -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"max_orders":50}' https://<TUNNEL_HOST>/agent/max_orders

# Speed (slow=10min, normal=5min, fast=2min, max=45s)
curl -X POST -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"speed":"normal"}' https://<TUNNEL_HOST>/agent/speed
```

## Watch firmware

Currently points at `http://192.168.1.5:5001` in `firmware/watch.ino`. Before re-flashing:
- Change `BACKEND = "https://<TUNNEL_HOST>"` (no port — CF terminates HTTPS on 443)
- Confirm `firmware/wifi_secrets.h` has `#define API_KEY "<FLASK_API_KEY>"`

## Common issues

| Symptom | Likely cause | Fix |
|---|---|---|
| 502 from <TUNNEL_HOST> | Flask not running on server | `systemctl --user status dreamdex-agent`; check logs |
| 401 on POST endpoints | Missing/wrong X-API-Key header | Check `FLASK_API_KEY` in server `.env` matches client |
| Agent holds forever, "portfolio stale" | Portfolio refresh failing | Check RPC connectivity from server; restart service |
| Order rejected "below minQuantity" | Trade too small for that pool | Expected — agent skips; not a fault |
| Order rejected "silent reject" | dreamDEX pool rejected (book empty, self-trade, etc.) | Inspect tx on Shannon explorer |
| Agent stops emitting trades | OPENAI_KEY expired or rate-limited | Check `tail -50 logs/agent.log` for OpenAI errors |
