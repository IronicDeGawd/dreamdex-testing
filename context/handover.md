# Handover — auto-generated 2026-06-30 21:17

> Git facts written by pre-compact-handover.sh. Session Notes are Claude-owned.
> See context/progress.md and context/structure.md for full state.

## Branch
feature/profit-maker-agent

## Files In Flight
```
 M context/handover.md
 M context/progress.md
 M context/structure.md
?? context/plan/maker-hold-engine.md
?? context/plan/r3-volume-climb.md
```

## Recent Commits
```
b7e04bc fix(volume_climb): honest pause/resume — keep cost window, resume only under ceil
766c8ce feat(volume_climb): Telegram pings for milestones, pause, resume, stop
965c073 feat(volume_climb): cost-aware mode — spread gate + rolling $/1k pause
58dd646 feat(config): env-overridable ELIGIBLE_PAIRS + alloc fallback
2b6b45c fix(maker): gap-safe trend signal; hold-mode supersedes legacy DB guard
```

## Session Notes
**🟢 WEEK 2 LIVE — RAW VOLUME ONLY (dev rule change).** Week-1 snapshot taken; week 2 ignores PnL/multiplier — only actual raw volume counts. No more fund/gas support → FIXED budget. Contest ends **~2026-07-07 21:00 IST**. We are **trader-2, #1, raw ~200k**. Wallet flat **~125 USDso + 51 SOMI ≈ $130** (TEAM capital, not personal → toll is not a loss; only thing that matters is keeping enough to keep trading).

### Active task
`cheap.sh 400000 40` running on server (cost-aware middle-phase volume). Plan: cost-aware churn next 2–4 days → `burst.sh` full-throttle the final 2 days. ~$90 reserved for the final burst.

### Engines (all committed + on server, baked into image)
- **`backend/volume_climb.py`** — taker WETH round-trip churn, ~100% fill, $50 legs, auto-flatten-on-stop, RPC-blip resilient. Cost-aware mode: `CLIMB_SPREAD_GATE_PCT` skips wide-spread trips; `CLIMB_COST_CEIL_PER_1K` pauses when rolling $/1k > ceil (window NOT cleared; resume only when cost back under ceil). Telegram pings (start/milestone every `CLIMB_TG_MILESTONE`/pause/resume/stop) via `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` (in server .env).
- **`backend/cheap.sh [target] [bleed_cap]`** — cost-aware launcher (spread-gate 0.05%, cost-ceil $0.15/1k).
- **`backend/burst.sh [target]`** — full-throttle (cost knobs off). Final-2-day weapon.

### Deploy / control (server irony@100.80.130.21, ~/dreamdex-r3/backend)
- SSH: `ssh -o ControlPath=none -o ConnectTimeout=30 -o BatchMode=yes irony@100.80.130.21 '<cmd>'`
- Code lives in the IMAGE → after editing volume_climb.py: scp to host THEN `docker compose build agent` (else `/app/volume_climb.py` is stale).
- Launchers run detached (survive SSH drop). `--rm` wipes logs on exit → confirm result on-chain + leaderboard, not the log.
- Monitor running run: `docker logs --tail N backend-agent-run-<id>`. Stop: `docker stop <id>` (may leave a bag if mid-trip → re-run flatten, but stop() auto-flattens on clean stop).

### Gotchas
- Realized round-trip cost = spread + DRIFT-between-legs; drift is unpredictable → cost-ceil is reactive (throttles sustained-expensive, can't pre-empt one drift-expensive trip). Spread-gate is the only pre-trade preventer.
- WETH spread ~0.02% = ~$0.10/1k toll FLOOR (can't beat it on taker); market currently choppy, sometimes NEGATIVE cost (profitable).
- Effective-volume MILESTONE ($25/500k) needs PnL>0 (maker, two-way market) — unreachable by churn (peaks ~400-423k eff). Week 2 makes this moot (raw only).
- Leaderboard `-new` URL is live R3; `-super-cool` is frozen R2. Gas via USDso→SOMI (no team support wk2).
- DQ rule (>24h idle) appears NOT strictly enforced (we idled ~2d, stayed listed) — don't rely on it; keep trading.
- Rivals: trader-3 192k raw but capital-poor (coiled spring if market pumps); trader-1 #2 active; trader-4 dead (76 tx); trader-5 inefficient ($2.8/tx). We're most efficient ($11/tx, 69% fill).
