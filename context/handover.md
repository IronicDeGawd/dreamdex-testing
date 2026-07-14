# Handover — auto-generated 2026-07-13 16:25

> Git facts written by pre-compact-handover.sh. Session Notes are Claude-owned.
> See context/progress.md and context/structure.md for full state.

## Branch
feature/maker-v2

## Files In Flight
```
 M context/dreamdex-native-pool-revert-bug.docx
 M context/dreamdex-taker-revert-bug.docx
 M context/dreamdex-taker-revert-bug.md
 M context/handover.md
 M context/plan/algo-arena.md
 M context/plan/dashboard.md
 M context/plan/round2.md
 M context/plan/round3-rules.md
 M context/plan/somnia-faucet-bot.md
 M context/progress.archive.md
 M context/progress.md
 M context/research/dreamdex-r3-findings.md
 M context/research/dreamdex.md
 M context/research/maker-feasibility-arena.md
 M context/structure.md
?? context/plan/r4-improvements.md
?? context/plan/run-pnl-snapshot.md
?? kit-fix/
```

## Recent Commits
```
33f23d5 feat(dashboard): run P&L and gas-used rows in Live Status
a28fc14 feat(control): per-run capital+gas baseline with P&L verdict and run history
d30f43d fix(maker): count funds locked in our resting orders as capital
faddd72 feat(control): maker as a first-class engine mode + one-shot 600k handover
cb1fa04 docs(plan): Arena fair-play detection rules + operator-key wallet architecture
```

## Session Notes
**🔴 R4 LIVE — RANK #1/9, MAKER trading real money.** Vol 601.5k, lead +64.9k (#2 adding ~4-5k/h — watch their burn, they have no top-ups). Maker run 3: leg $40 ±15%, cap $55, bleed $3, WETH, log `maker-1783933035.log`, START networth $78.1742. realized +$0.0021, mark −$0.22 (unrealized inventory). **⚠️ ENDGAME: /stop maker (flattens) BEFORE final snapshot.** User will start taker later themselves — maker runs until then.

### CRITICAL corrections this session
- **R4 milestones use RAW volume, NOT effective** (user-corrected; "effective" was an R3 rule wrongly carried into notes). 601.5k raw ⇒ 500k milestone passed; next $25 at 1M raw (~$50 taker burn at $0.12/1k).
- **~/Project/Somniaforge was DELETED mid-session and restored from a 2-day-old snapshot.** Server files were current → rsynced back + re-committed. Lost: old git hashes (dbfa94a, 5219f57, 61c7c38, 49564a7, 96d1466 → now d30f43d, a28fc14, 33f23d5). Both branches PUSHED to private origin (user approved).

### Run P&L feature (built+deployed today, plan context/plan/run-pnl-snapshot.md)
- /launch + /autorestart snapshot capital+gas → `baseline` in engine.json; /status parses live run_pnl/gas from engine logs (START-line fallback for old runs); /stop + self-stop observer (30s grace) write final verdict + one record/run to control/state/runs.jsonl; dashboard Live Status has Run P&L + Gas rows.
- **/cohort now counts order-locked funds via account-keyed get_open_orders (5th instance of the locked-funds class)** — was showing balance $33 / $0.194 per 1k; true $75.8 / $0.123.

### Ops (unchanged core)
- Maker launch: POST /launch `{"mode":"maker","target":0,"leg":40,"pair":"WETH:USDso","bleed_cap":3,"cap":55,"inv_floor":0}`; engine code baked in image (`docker compose build agent` safe while running).
- Crons: watchdog */15, keepalive 17 * * * *, transition */10 (inert, marker set). Control restart: kill by PORT (`kill $(lsof -ti :8787)`), never pkill by name; `nohup ./control/run.sh 8787 > /tmp/control.log 2>&1 &`; LiveBackend boot ~15-20s.
- pkill on server: bracket pattern `pkill -f "[m]aker_v2"`. ssh rapid reconnects → 255 rate-limit, cool down.

### Gotchas
- Order-locked funds invisible to every balance read — ANY balance-derived metric must add them (engine: own state; control: open-orders API).
- Local repo restored from stale snapshot: docx/context mode-changes cleaned via `git checkout -- . ":(exclude)context"`; context/ deliberately kept from working copies.
- Maker rate baseline (run 3, leg $40): START $78.1742 @ ~07:37 UTC; ~$140-180/h vol, ~$0.17/1k realized blended, gas ~$0.11/day → 9-day projection +$22-43k vol, +$2-5 profit.
- kit-fix/ untracked — user remove-or-gitignore decision still pending.

### Open
- Maker 24-48h profitability verdict vs $78.1742.
- User starts taker later (raw-vol push toward 1M); maker+taker must never share a pair (self-cross).
- Arena registration → G1 attribution test → operator plumbing.
