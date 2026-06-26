# Handover — auto-generated 2026-06-26 18:15

> Git facts written by pre-compact-handover.sh. Session Notes are Claude-owned.
> See context/progress.md and context/structure.md for full state.

## Branch
feature/profit-maker-agent

## Files In Flight
```
 M context/handover.md
```

## Recent Commits
```
22a1f11 fix(inventory): make base holdings authoritative from chain, not fill counter
e2d8f4f fix(maker): trend guard fails open with no history so chosen pairs can trade
70f8258 config: trade WBTC+WETH only, exclude SOMI (grind-bleed)
341dfea fix: monitor values coins via EMA fallback; trend guard fails safe
37a684b fix(monitor): clamp glitchy book mids to sane band + last-good fallback
```

## Session Notes
**🛑 BOT FULLY STOPPED — capital-preservation mode (2026-06-26).** Both containers `Exited` on server. Funds parked on-chain: **~$137.74 USDso + ~19.5 SOMI ≈ $140**. Real PnL ≈ **−$10** (vs $150) / **−$15** (vs 150+50SOMI at today's price). Money is in the wallet, NOT tied to containers. Full detail in `context/progress.md`.

### Why we stopped (key decisions)
- **Can't win:** ranking is by effective volume = raw × (1+PnL%), and RAW VOLUME DOMINATES. We're #4/6 (effVol 884 vs leaders 12k–53k); top-2 unreachable. Leaders did 16–130× our volume.
- **Can't profit:** sustained bear (SOMI −40%/30d, BTC −22%, ETH −26%). No chop to capture, can't short on spot → any trading loses (toll + grind). The ~$13 we lost = gas + buy-high/sell-low across restart/experiment churn.
- **Dev clarified:** final PnL = convert ALL funds to USDso at contest END. So holding inventory is neutral until the end; only end-liquidation value counts (NOT the live free-USDso snapshot).
- → Goal = keep the money. Hold stablecoin, stop trading.

### To RESTART (only if market turns / user says go)
- Server `irony@100.80.130.21`, dir `~/dreamdex-r3`, branch `feature/profit-maker-agent`. SSH: `ssh -o ControlPath=none -o ConnectTimeout=30 -o BatchMode=yes irony@100.80.130.21 '<cmd>'`.
- Start: `cd ~/dreamdex-r3/backend && docker compose up -d agent` then set enabled=1 in DB (`agent_state`). Monitor stays OFF (avoids Telegram spam + it's separate from agent). Image already built with all fixes.
- **CHECK `agent_state.enabled` BEFORE any restart** — if 0, a restart FLATTENS inventory (sells to USDso). Wallet `0xD84fE2a2220f0269e3d88dab908ADceb2d691E76`, key in server `.env`.

### Live config now (server .env + config.py)
- Pairs **WBTC + WETH only** (SOMI EXCLUDED — gradual grind kept bleeding). alloc 0.5/0.5. `MAKER_MAX_INV_USD=20`, `STRATEGIST_ENABLED=false` (no Gemini tokens), `TREND_LOOKBACK_S=86400`, `TREND_GUARD_PCT=0.015`, stop-loss 10% limit-protected, keepalive `KEEPALIVE_LEG_USD=1`.

### Fixes shipped this session (all committed)
- Monitor reports REAL net worth from on-chain (not desynced tracker) + sane-band price clamp + on-chain EMA price fallback (killed false −$7/−$36/−$10 cards).
- Agent: trend guard (pause buy when down >1.5%/24h, fails open w/ no history) + keepalive; auto-stop reads on-chain value; **`Inventory.sync_base()` — agent holdings now CHAIN-authoritative each tick (ERC20 pairs), killing the fill-tracker desync** (verified: corrected phantom 0.00022 WBTC → 0).

### GOTCHAS
- Leaderboard `pnl`/`usdsoBalance` = free USDso only → undercounts inventory. Trust on-chain net worth (wallet + vault `getWithdrawableBalance`; vault CONFIRMED EMPTY this session).
- Fill-tracker desyncs; now mitigated by `sync_base` for ERC20. Native SOMI still tracker-based (gas+inventory commingled on-chain, can't separate).
- Trend guard catches CLIFFS not SLOPES (>1.5%/24h); gradual grind slips under → trading still bleeds. Only zero-bleed = cash.
- Native SOMI pool place+cancel need gas ≥5M; ERC20-payout fine at 3M.
- One-off container test (agent stopped): `docker compose run --rm --no-deps -T agent python3 -` < localscript.py (pipe via stdin — avoids inline-quoting hell; inline f-strings can't contain backslashes).
- Local: bash cwd resets to repo root between calls → absolute paths or `cd` inside the command.
