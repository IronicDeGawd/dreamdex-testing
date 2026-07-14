# Plan — Somnia STT Faucet Telegram Bot

> Standalone project (`somnia-faucet-bot`), separate repo, Python + Docker, on the
> existing server (100.80.130.21). Build in a fresh session/context — keep it out
> of the DreamDEX trading repo. This doc is the spec to start from.

## Goal
A Telegram bot that lives in the Somnia group and dispenses **STT** (Somnia
**testnet** token, Shannon testnet, chain 50312) to users who ask, with tiered
rate limits, OAuth-based verification for the higher tier, an FAQ auto-responder,
and a low-balance alert to the admin.

## Decisions locked (from requirements round)
- **Verification = OAuth** (Discord *or* GitHub login proves account ownership).
- **Low balance = alert admin only** (DM the admin to manually refill; pause drops
  until refilled). No auto-refill / reserve wallet in v1.
- **Stack = new repo, Python, Docker, same server.**
- **Request UX = command + tag in group**, plus **FAQ auto-replies** on known
  terms (docs, about Somnia, etc.) via regex first, small LLM fallback later.

## Tiers
| Tier | Amount | Cooldown | Unlock |
|------|-------:|---------:|--------|
| Unverified | 25 STT | 24 h | default |
| Verified | 50 STT | 12 h | complete Discord OR GitHub OAuth |

Rate-limit key = **(telegram_user_id) AND (receiving_address)** — both, so one
person can't farm many addresses, and one address can't be farmed by many TG users.

## Architecture (4 pieces + storage)
1. **Bot** — `python-telegram-bot` (async). Handlers:
   - `/faucet 0x...` or `@bot gimme stt` → resolve tier + cooldown → send STT → reply.
   - `/verify` → returns a one-time OAuth link (state token bound to the TG user).
   - `/register 0x...` (optional) → save address so later `/faucet` needs no arg.
   - FAQ: on free text (privacy mode OFF or bot = admin), regex/keyword match →
     canned answers (docs link, about Somnia, how-to-get-STT). Unmatched → optional
     LLM fallback (reuse Gemini/Vertex, or a cheap model) with a curated system
     prompt + links, guardrailed to Somnia topics.
2. **OAuth web service** — small **FastAPI** app. Routes:
   - `GET /auth/discord?state=<tg>` → redirect to Discord OAuth (scope `identify`).
   - `GET /auth/github?state=<tg>` → redirect to GitHub OAuth (scope `read:user`).
   - `GET /callback/discord`, `/callback/github` → exchange code, read account id +
     age, mark the TG user verified in the DB, show a "you're verified, return to
     Telegram" page. Bind via the `state` token to prevent hijack.
   - Needs a **public HTTPS URL** (see Prereqs) — Caddy reverse-proxy on the server.
3. **Sender** — `web3.py`. Faucet hot wallet (testnet key in `.env` only). Native
   STT transfer to the user's EVM address (same pattern as the SOMI transfer used
   in the trading bot: build tx, sign, send, wait receipt). Gas is STT itself.
4. **Low-balance watcher** — periodic task; if faucet native balance < THRESHOLD,
   DM the admin TG id: "Faucet low: X STT left, please refill 0x<faucet>." Set a
   `paused` flag so drops stop until balance recovers; unpause automatically.

**Store (SQLite):**
- `users(tg_id PK, address, verified BOOL, discord_id, github_id, github_created_at, updated_at)`
- `claims(id, tg_id, address, amount, ts)` — for cooldown checks (query last claim).
- `oauth_state(state PK, tg_id, provider, created_at)` — short-lived, for CSRF/binding.

## Request/verify flows
- **Faucet:** user sends `/faucet 0xADDR` (or tags the bot). Bot validates the
  address, looks up tier (verified?), checks `claims` for the last drop within the
  cooldown window (by tg_id and by address), and if clear, sends STT and records a
  claim. Replies with amount + next-eligible time. If on cooldown, replies with
  time remaining. If faucet paused/low, replies "temporarily dry."
- **Verify:** user sends `/verify`. Bot creates an `oauth_state` row and DMs a link
  `https://<host>/auth/discord?state=<token>` (and a GitHub variant). User logs in;
  callback marks them verified. Bot confirms in DM. Optional: require GitHub
  account age > N days to reduce throwaway accounts.

## Anti-abuse (v1, best-effort — flagged as not airtight)
- Rate-limit by tg_id AND address (above).
- OAuth ties the higher tier to a real Discord/GitHub account.
- Optional GitHub account-age / min-repos gate; Discord account-age gate.
- Optional: only serve users who've been group members > N minutes.
- Log every drop (address, tg_id, amount, ts) for audit.
- Testnet faucet can't be perfectly sybil-resistant — tiers + limits keep it sane.

## Tech stack
- Python 3.11, `python-telegram-bot` (v21 async), `fastapi` + `uvicorn`, `web3.py`,
  `sqlite3` (or SQLModel), `httpx` for OAuth token exchange. Docker + compose.
- Caddy (existing `dash-caddy`) reverse-proxies the FastAPI service under the
  faucet subdomain with automatic TLS.
- Secrets in `.env` (gitignored): `TG_BOT_TOKEN`, `FAUCET_PRIVATE_KEY`,
  `FAUCET_ADDRESS`, `ADMIN_TG_ID`, `DISCORD_CLIENT_ID/SECRET`,
  `GITHUB_CLIENT_ID/SECRET`, `PUBLIC_BASE_URL`, `SOMNIA_TESTNET_RPC`,
  (optional) `LLM_API_KEY`.

## Build phases
1. **Skeleton + faucet core** — bot online, `/faucet` with unverified tier, SQLite
   cooldowns, on-chain STT send on testnet, `/start` help. (No OAuth yet.)
2. **Low-balance watcher + admin DM + pause/resume.**
3. **FAQ regex responder** (curated answers + links); privacy mode off.
4. **OAuth service** (Discord + GitHub), verified tier, Caddy route. *Needs domain.*
5. **LLM fallback** for unmatched FAQ (guardrailed to Somnia), if wanted.
6. **Hardening** — address/tg dual rate-limit, account-age gates, audit log,
   metrics, Docker deploy + restart policy.

## Prerequisites to gather before building
- **BotFather token** for the group's bot (→ server `.env`).
- **Public domain/subdomain** for OAuth callback (e.g. `faucet.<domain>`), pointed
  at the server; Caddy handles TLS. (Without it, ship phases 1–3 first, add OAuth
  in phase 4 once the domain exists.)
- **Discord + GitHub OAuth apps** registered (client id/secret, redirect URIs).
- **Faucet wallet** created + funded with STT (testnet). Keep key in `.env` only.
- Confirm **amounts/cooldowns/threshold** (25/24h, 50/12h, low-balance threshold)
  and the **admin's Telegram user id** for alerts.

## Notes / open questions for build session
- Which LLM for the FAQ fallback (reuse Vertex/Gemini vs a small cheap model)? Keep
  it optional/off by default to avoid cost + hallucinations.
- Whether to require GitHub account-age gate (recommended) and what N.
- Group privacy: privacy mode OFF vs bot-as-admin (either enables free-text FAQ).
