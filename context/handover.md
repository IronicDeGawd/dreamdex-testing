# Handover — auto-generated 2026-06-24 08:04

> Git facts written by pre-compact-handover.sh. Session Notes are Claude-owned.
> See context/progress.md and context/structure.md for full state.

## Branch
feature/profit-maker-agent

## Files In Flight
```
 M context/structure.md
?? backend/data/agent_ttest.db
?? backend/data/agent_ttest2.db
```

## Recent Commits
```
9be5c55 tune(maker): leg $65, SOMI inventory cap $90
28b2894 feat: start/stop control + flatten-to-USDso + $100 auto-stop
6d226ad docs: add .env.example for the V3 profit-maker approach
c81ba68 style(monitor): rename feed to DreamDEX V3, market preview every 2h
879ecc3 feat(monitor): relay Gemini reasoning + market preview; tidy summary
```

## Session Notes
**🟢 V3 profit-maker is LIVE on mainnet.** Bounded two-sided market maker. Full state + known issues + deploy notes in `context/progress.md` (LIVE section). R2 burst code archived in `backend/archive/` (`ARCHIVE.md`).

### Deployment (server `irony@100.80.130.21`)
- Dir is **`~/dreamdex-r3`** (old `~/dreamdex-agent` REMOVED). Branch `feature/profit-maker-agent`. Containers: `dreamdex-agent` + `dreamdex-monitor`.
- Redeploy: `cd ~/dreamdex-r3 && git pull -q origin feature/profit-maker-agent && cd backend && docker compose up -d --build [agent|monitor]`. Config baked into image → **rebuild needed for code/config changes**; monitor-only changes → `--build monitor` (agent untouched).
- SSH: `ssh -o ControlPath=none -o ConnectTimeout=30 -o BatchMode=yes irony@100.80.130.21 '<short cmd>'`.
- Wallet `0xD84fE2a2220f0269e3d88dab908ADceb2d691E76` (registered, funded 150 USDso + 50 SOMI). Key in `~/dreamdex-r3/backend/.env` `MAINNET_PRIVATE_KEY`; verified derives the wallet.

### Strategy / config (live)
- Pairs **SOMI 80% / WBTC 20%** (WETH dropped — 1.4bps too tight). Leg **$65**, inv cap **SOMI $90 / WBTC $22**, reserve $20, margin 1 tick (sell ≥ cost+margin = no realized loss). SOMI ~10bps is the only capturable spread.
- Strategist: **Gemini 2.5 Pro via Vertex ADC** (project `project-8feccae3-bcae-4254-b60`, ADC file mounted `/app/adc.json` from host `~/.config/gcloud/...`; gcloud CLI NOT needed at runtime).
- Telegram (token+chat in .env): **/stop** (flatten to USDso + idle), **/start**, **/status**. Auto-stop if total value < **$100**. Monitor computes OUR PnL (realized+unrealized vs $150); leaderboard used ONLY for vol/rank.

### Current live state (at handover)
- Rank ~2/6, realized PnL **+0.15**, holding ~$40 SOMI inventory (small unrealized −), gas ~49.9 SOMI. PnL ~flat = winning profile (beat the bleeders; trader-3 bled to −$108).

### GOTCHAS (V3-specific, hard-won this session)
- **Native SOMI pool place AND cancel need gas LIMIT ≥5M** (InsufficientGasForPayout guard; actual use tiny) — handled via `gas_min`/`min_gas`. ERC20-payout (USDso) ops fine at 3M.
- **Fill detection:** wallet-funded order RESERVES funding token at placement (looks like a fill on balance delta). Detect BUY fill by BASE received, SELL by USDso received; two-sided uses `get_open_orders` `remaining` (reservation ≠ fill).
- `cancel_order` now verifies receipt (was reporting false "cancelled" while tx reverted → stuck orders).
- Leaderboard `pnl` = free USDso − 150 only (ignores open orders + inventory → phantom loss). Ignore it.
- Local: `.venv` at `backend/.venv`; **bash cwd resets to repo root between calls → use absolute paths or `cd` inside the command**.
- Stray untracked test DBs `backend/data/agent_ttest*.db` — safe to delete.
