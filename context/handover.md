# Handover — auto-generated 2026-07-15 19:44

> Git facts written by pre-compact-handover.sh. Session Notes are Claude-owned.
> See context/progress.md and context/structure.md for full state.

## Branch
main

## Files In Flight
```
 M context/handover.md
```

## Recent Commits
```
8576b11 Merge pull request #4 from IronicDeGawd/fix/atomic-pnl-readout
99b19c7 fix(control): lot-snap SOMI gas top-up + show the max leg in the UI
88ad753 fix(atomic): parse run P&L for atomic mode + clean up the P&L rows
7051503 Merge pull request #3 from IronicDeGawd/feature/maker-v2
cebcfc7 docs: refresh progress, plans, research, and handover
```

## Session Notes
**ON `main` NOW** (user merged 4 PRs this session, all no-ff). main has: maker v2, run-P&L dashboard, and the full EIP-7702 atomic mode. New work → feature branch → PR (no-ff), then `git checkout main`.

**🟢 R4 IDLE + delegated + topped up.** The atomic mode is BUILT, VALIDATED, and LIVE-PROVEN on R4. Last run: **130k atomic completed in 6.6h** ($11.52 USDso capital, 9.16 SOMI gas, ~$0.10/1k all-in, ~20k/hr, reverts=0). R4 now idle, delegated to `0xe504aC9a272d4975D3E074ab034f64f68CdBC18c`, **topped up to ~22.9 SOMI / ~$46.6 USDso** (did a $2 USDso→SOMI buy). Nothing running; watchdog won't restart (clean self-stop).

### EIP-7702 atomic mode (this session's big build — done)
- 4th engine mode `atomic` = buy+sell in ONE tx via RoundTrip7702 delegate (self-call guard, on-chain toll cap). Full parity with steady taker: multi-pair rotation, boosts, weekly window, TG alerts, cost gate, keepalive.
- Launch: `POST /launch {"mode":"atomic","target":N,"leg":45,"pair":"WBTC:USDso,WETH:USDso","cost_ceil":0.16,"toll_cap":0.3,"bleed_cap":40,"somi_floor":2}`.
- Delegate: mainnet `0xe504aC9a…C18c`, testnet `0x447121E7…91461`. eth-account bumped 0.13.4→0.13.7 (LocalAccount.sign_authorization). Somnia type-4 needs ~6M gas (1.19M floor); deploys ~8.3M gas (DEPLOY_GAS=10M).
- **Economics vs 2-tx taker: ~30% cheaper toll (kills inter-leg drift), gas ~on par (my earlier "3× gas" claim was WRONG — corrected).** Big win is toll + zero bags + 1 confirmation.
- Memory: `eip7702-atomic-mode-validated.md`. R4-only — Arena-flag poison.

### Algo Arena (separate from R4 — public 8-week comp, Jul 14–Sep 7)
- Score = Volume × PairBoost × Challenges, NO PnL. **Weeks reset TUESDAY 00:00 UTC** (our week_idx is Monday-aligned — a bug to fix for any Arena engine).
- Fair-play: bans (1) round-trip ≤30s, (2) near-flat cycles 3+. **ALL our takers (volume_climb/direct_burst/atomic) trip filter 2 → Arena-non-compliant.** Only maker/grid (real held positions) survive.
- Drafted the user's Arena question (round-trip vs maker, leaderboard looks like round-trips top-to-bottom). R4 trader-3 (`0x62bb…`) forensic: legit taker round-trips, small legs on tight books; its cheap "$0.065/1k" understates cost (gas hidden, mid-trade capital snapshot).

### NEXT SESSION: dynamic leg sizing (plan ready, NOT built)
- Plan in `~/.claude/plans/fluttering-discovering-finch.md` → **step 1 next session: copy to `context/plan/dynamic-legs.md`** (couldn't write it in plan mode).
- Size each leg to touch depth `[LEG_MIN,LEG_MAX]` (book_levels already returns (price,qty)); bigger on deep books = gas win, smaller on thin = toll win. Opt-in env `ATOM_LEG_MIN/MAX/TOUCH_FRAC`.
- Go/no-go = R3 A/B (fixed $45 vs dynamic [20,200]): must show **gas/1k drop** or don't ship.

### Ops / gotchas
- Test venv: server `~/atomicvenv` (eth-account 0.13.7); probe scripts `~/probe7702/`. Deploy control-only: rsync control/*.py + static, restart by PORT (`kill $(lsof -ti :8787)`); engine change = `docker compose build agent`.
- gas_topup needed lot-snap to 0.01 (fixed 99b19c7). Dashboard now shows "max $X" leg = 0.8× free USDso.
- Arena leaderboard NOT on the reachable API (that one is R4 = `dreamdex-leaderboard-total.vercel.app`). To inspect Arena wallets need full addresses (browser or user paste).
- Removed a stray `backend/context/` misfile this session.
