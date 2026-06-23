# Handover — auto-generated 2026-06-23 23:40

> Git facts written by pre-compact-handover.sh. Session Notes are Claude-owned.
> See context/progress.md and context/structure.md for full state.

## Branch
main

## Files In Flight
```
 M backend/liquidate_to_usdso.py
 M backend/profit_maker.py
 M backend/run_direct_burst.sh
 M context/handover.md
 M context/progress.md
 M context/structure.md
?? .playwright-mcp/
?? backend/aware_burst.py
?? backend/aware_burst_vault.py
?? backend/aware_keepalive.sh
?? backend/aware_vault_keepalive.sh
?? backend/cancel_all.py
?? backend/cancel_order.py
?? backend/cycle_phase.sh
?? backend/deep_check.py
?? backend/deep_watch.py
?? backend/end_burst.sh
?? backend/fix_burst.sh
?? backend/fix_flatten.sh
?? backend/gas_autokeep.sh
?? backend/maker_keepalive.sh
?? backend/probe2.py
?? backend/probe_funding.py
?? backend/probe_realbal.py
?? backend/probe_trades.py
?? backend/reset_maker.sh
?? backend/reset_maker_weth.sh
?? backend/sell_somi.py
?? backend/somi_drip.sh
?? backend/stop_and_recover.sh
?? backend/sweep_b_to_h.py
?? backend/sweep_opponents.py
?? backend/switch_pair.sh
?? context/dreamdex-native-pool-revert-bug.docx
?? context/dreamdex-taker-revert-bug.docx
?? context/dreamdex-taker-revert-bug.md
?? context/plan/
?? context/progress.archive.md
?? context/research/
?? dreamdex-docs-auth.png
```

## Recent Commits
```
ea9d497 feat(burst): gas top-up utility + $45 legs
90bc144 feat(profit): no-bleed maker bot + wallet/dreamdex key override
a8e8454 chore(burst): round-2 launch config — USDC.e $40 legs, key off process list
b4c537a fix(burst): elastic leg sizing to prevent fixed-leg inventory deadlock
519496a feat(burst): async tx logging to sqlite + batch writer + status reconcile
```

## Session Notes
**ROUND 3 — SETUP (rules not yet received). Round 2 WON #1 (final 1,342,945, +33,177).** Nothing running. Do NOT start engine until R3 rules read AND user says go. Repo PUBLIC. Full state in `context/progress.md`; mechanics in `context/research/dreamdex.md`.

### DONE this session
- **New R3 wallet created** (rules require fresh wallet). Deposit address: **`0xD84fE2a2220f0269e3d88dab908ADceb2d691E76`**. Key on server `~/dreamdex-agent/.env` as `MAINNET_PRIVATE_KEY` (piped over SSH stdin, hash-verified == generated). Local copy: session scratchpad `round3_wallet.txt` (TEMP — move to durable store before session ends). R2 `.env` backed up → `.env.r2bak`.
- Compacted `progress.md` (R2 verbose log → `progress.archive.md`); kept all findings. Stored findings into knowledge base `context/research/dreamdex.md`.

### 🌟 CORRECTED FINDING (this session) — gas self-funding IS possible
- Earlier "gas self-conversion DEAD" was WRONG. `0x782b2567` = `InsufficientGasForPayout(uint256 gasLeft)` — deliberate guard, not a bug. `placeOrder` BUY on SOMI:USDso (`isBid=true`, `msg.value=0`) WORKS with **`gas≥5,000,000`** (we used 3M → too low for native-payout headroom). Sim lied because it used different gas than broadcast → **simulate with the SAME gas you broadcast.** (Dev `emrey.somi` 2026-06-22, now in DreamDEX docs.) ⟹ wire USDso→SOMI auto-top-up into R3 engine. Detail: `dreamdex.md` §7a.
- ⚠️ `context/dreamdex-native-pool-revert-bug.docx` is now OBSOLETE (claimed a contract bug; was a gas-limit issue). Awaiting user decision: delete or annotate RESOLVED.

### ⚠️ REMAINING R2 CLEANUP (optional, not done)
- ROTATE old keys (repo PUBLIC, treat as burned): wallet H `0xF4c825F3C2970153d78B407CF190861dd4E2b905`, wallet B `0x7571…A638`.
- `docker compose down` old container if not reusing as-is (server `irony@100.80.130.21`, `/home/irony/dreamdex-agent`).

### KEY ENGINE FACTS (preserve)
- **Win move:** `aware_burst_vault.py` builds placeOrder calldata locally + direct RPC broadcast (env `BURST_CONFIRMED` unset=FAST ~10k/hr; =1=slow API+verify ~3k/hr). DreamDEX order-build API added ~14s/leg. 5gwei priority tip in `wallet.py _gas_fields` (Somnia default tip 0 → txs queue).
- Capital dev-allocated (~100 USDso fixed) → contest = most volume per fixed capital (efficiency).
- Engine can be ALIVE-BUT-STUCK (price-buffer hiccup → no fills) — monitor VOLUME, not just process-alive.

### GOTCHAS
- SSH/Tailscale can drop → `-o ControlPath=none`, SHORT cmds, retry 5×. Balance reads via public RPC `https://api.infra.mainnet.somnia.network/` need no SSH.
- Host-path `grep` inside `docker exec` fails (host dir not in container) — run host greps outside docker exec.
- ERC20 transfers on Somnia need `gas=2,000,000` (default ~200k silently reverts, can strand funds).
