# Progress — DreamDEX Contest Agent (Round 3)

**STATUS 2026-06-24: ROUND 3 — RULES IN, REWRITING AS PROFIT AGENT.** R2 WON #1. Fresh wallet created. **R3 rules received** (`context/plan/round3-rules.md`). **Scoring inverted: ranked by Effective Volume = Raw Volume × (1 + PnL%)** → profit now matters as much as turnover; a wipe = 0. 14 days, $150 start, no top-ups, 50 SOMI gas (convert USDso for more), eligible pairs BTC/ETH/SOMI vs USDso (no stablecoin), **>24h idle = DQ**. New profit lever: maker yield ~3.3% APY weighted by proximity-to-mid, both sides must rest. Decision: **abandon the volume-burst playbook, build a fresh profitable market-making AGENT** on a new branch; archive all R2 code. Nothing running. Do NOT start the engine until built, funded, and user says go.

> **📖 Full verified DreamDEX mechanics/economics/slippage/fill-rate reference: `context/research/dreamdex.md`** (zero pool fees; toll ~$0.09–0.10/1k; ceiling = capital×~10k; slip=50 → ~100% fill). **Re-verify these still hold in R3 before relying on them.**

## Round 3 setup (current)
- **New wallet (R3 deposit address):** `0xD84fE2a2220f0269e3d88dab908ADceb2d691E76`. Private key on server `~/dreamdex-agent/.env` as `MAINNET_PRIVATE_KEY` (piped over SSH stdin — never on host process list/history; hash-verified == generated key). Local copy in session scratchpad `round3_wallet.txt` (temporary — move to durable store before session ends).
- **R2 `.env` backed up** to `~/dreamdex-agent/.env.r2bak` (old wallet H key preserved there).
- **Server:** `irony@100.80.130.21`, dir `/home/irony/dreamdex-agent`. Container `dreamdex-agent` currently DOWN (R2 cleanup).
- **Pending:** receive R3 rules → decide strategy → confirm engine still valid → fund the new address → `docker compose up` → go.

## Round 2 outcome (rollup — full log in `context/progress.archive.md`)
- **🏆 WON #1.** Final 1,342,945, lead +33,177 over t3 (ended 10:00 UTC Mon 2026-06-22). PnL −93 (most efficient of top 3). Crossed 1M with a full taker burst (the old "100-USDso hard cap / 1M unreachable / maker-only" plan was WRONG — see archive).
- **How we won:** (1) direct-RPC engine bypassing the ~14s DreamDEX order-build API (3k→10k vol/hr); (2) let fixed-capital rivals sprint dry while we held/padded, flipping to FAST only to defend a closing rival.
- **Funds extracted** post-contest: USDso→WETH → 0.0039 WETH to `0xff1661f01687E6e1c50282256CD23D79EADBFCa4`.

## Reusable engine assets (from R2 — re-validate against R3 rules)
- **`aware_burst_vault.py`** — the winning engine. `trade()` builds `placeOrder` calldata locally + broadcasts direct over RPC. Env `BURST_CONFIRMED`: unset=FAST direct-RPC (~10k/hr); =1=slow API+balance-verify (~3k/hr, low gas). Selector `0x4e978373`, orderType 2 (IOC). Reads `MAINNET_PRIVATE_KEY` from container env (compose env_file).
- **`profit_maker.py`** — no-bleed maker. `PROFIT_FUNDING=wallet` makes both legs use the same balance location (fixes the vault-inventory wedge). Heartbeat in `_wait()` keeps the 600s watchdog from false-killing a resting maker.
- **Keepalives:** `aware_vault_keepalive.sh` (burst, cron */3), `maker_keepalive.sh` (maker, cron */5). `cycle_phase.sh` swaps between modes.
- **`wallet.py _gas_fields`** (in container `/app/trading/`): adds 5gwei priority tip (Somnia default tip 0 → txs mempool-queued without it).
- **Deploy a .py:** `scp` to `/home/irony/dreamdex-agent/` THEN `docker cp <f> dreamdex-agent:/app/<f>` (/app ≠ host dir). Logs live INSIDE container: `docker exec dreamdex-agent tail /tmp/aware.log` (burst) / `/tmp/avault.log` / `/tmp/maker.log`.
- **Probes:** `audit_balances.py` (true capital = wallet + vault `getWithdrawableBalance`), `probe_realbal.py`, `probe_funding.py`, `probe_trades.py`/`probe2.py`, `deep_check.py`/`deep_watch.py`.
- **Leaderboard (we were trader-9 in R2 — will differ in R3):** `curl https://dreamdex-leaderboard-super-cool.vercel.app/api/leaderboard`.

## Known Issues (carry-over to R3)
- **✅ Gas self-funding IS possible (corrected 2026-06-23):** the earlier "dead all paths" belief was WRONG. `0x782b2567` = `InsufficientGasForPayout(uint256 gasLeft)` — a deliberate guard, not a bug. `placeOrder` on SOMI:USDso (BUY, `isBid=true`, `msg.value=0`) works with **`gas≥5,000,000`** (we used 3M → too low for the native-payout headroom check). Our `eth_call` sim lied because it ran with different gas than we broadcast → **simulate with the SAME gas you broadcast.** Dev `emrey.somi` confirmed + added to DreamDEX docs. ⟹ USDso→SOMI self-gas is on the table for R3; wire an auto-top-up. (See `context/research/dreamdex.md` §7a.)
- **DNS-crash robustness gap:** `aware_burst.py` main loop doesn't wrap RPC calls in try/except → a transient `NameResolutionError` crashes the process (keepalive recovers in ~3 min). Fix: try/except-with-retry around main-loop RPC calls.
- **SSH rate-limit:** rapid reconnects trip sshd → exit 255. Use `-o ControlPath=none`, SHORT single-shot commands, retry up to 5×. Balance reads via public RPC need no SSH.
- **Old keys to rotate (R2, repo is PUBLIC):** wallet H `0xF4c825F3C2970153d78B407CF190861dd4E2b905`, wallet B `0x7571…A638` — treat as burned; rotate if reused. New R3 key must NEVER be committed (`env_file` only; never `git add -A`).

## Lessons (durable findings — preserved across rounds)
- **🌟 slip on an IOC taker is FREE insurance, not a cost.** IOC fills at the BOOK touch, not your limit — a wide limit (slip) costs zero extra toll as long as your leg ≤ top-of-book depth; it only prevents misses from price drift. Tight slip=6 was a mistake (saved nothing, caused 36% misses). slip=50 → ~100% fill, same toll. Downside only if leg > touch depth (book-walking) or thin/flash-spike book → use MODERATE slip, not infinite.
- **🌟 Ceiling vs Rate are DIFFERENT.** (1) **Ceiling** = capital ÷ spread ≈ capital × ~10k volume — INDEPENDENT of leg size. (2) **Rate** = legs/sec × leg_size, leg_size ∝ remaining capital → rate **DECAYS as capital bleeds, for EVERYONE.** Never assume an opponent sustains a fixed rate while you decay — they slow too (verified: t6 decayed ~46k/hr→~15k/hr as capital dropped $32→$26). With symmetric decay, a volume head-start usually holds.
- **🌟 Leaderboard `usdsoBalance` is unreliable for capital.** It's free wallet USDso at one cycle-phase snapshot — swings $0↔$26 just from holding WETH vs USDso mid-round (NOT a top-up). For real capital read on-chain: native SOMI + wallet `balanceOf`(USDso/WETH) + **vault** `getWithdrawableBalance(user,token)` + open orders. ABI in `audit_balances.py`. See [[leaderboard-balance-unreliable]].
- **No external top-ups detected (R2):** funding scan showed all incoming USDso to rivals came from the POOL (own trade proceeds); tester wallet sent nothing; all vaults empty. Apparent "refuels" were cycle-phase artifacts.
- **vol per SOMI ≈ 11.7k at slip=50; toll ≈ $0.09–0.10/1k volume; gas burn ~0.5–0.6 SOMI/30min** (R2 calibration for projections).
- **Bigger legs = more volume per unit gas** (gas/tx ~constant). Leg = `min(target, free-USDso-affordable)` → low free USDso caps the leg small → slow rate. More USDso capital → bigger legs → faster deploy AND higher ceiling.
- **DEX quirks (verify still true in R3):** `expireTimestampNs=0` silently rejected (use `(now+3600)*1e9`). `getPoolParams()` = flat 7-tuple (base,quote,makerBps,takerBps,tick,minQty,lot). `selfMatchingOption=1` (CancelMaker). Somnia gas non-standard (ERC20 transfers need `gas=2,000,000`). native sentinel for SOMI vault = `0x28f34DeFd2b4CB48d9eE6d89f2Be4Bc601694c00`, NOT address(0). `OrderPlaced` emits filled=0 even on real fills + `getOwnOpenOrders` returns empty → use balance delta as fill signal. **pgrep ABSENT in container** → detect process via `/proc` cmdline filtered to `comm=python*` (naive grep matches your OWN command).
- **Opponents' methods (R2, decoded):** `placeTakerOrderWithoutVault` was deprecated mid-R2 (started reverting unconditionally — devs disabled it). `placeOrder` (vault-based, selector `0x4e978373`, IOC) was the live path. All top traders fill ~100% by crossing aggressively or polling fast — our 64% was self-inflicted (tight slip + verify latency).

## Testnet validation results (2026-06-24)
- ✅ **PostOnly placement + fill works** on testnet SOMI:USDso (bought 9.91 @ 0.1009, filled).
- ✅ **No-bleed invariant held** (sell quoted @ 0.1011 = buy + 2 margin ticks).
- ✅ **`get_open_orders` + `cancel_order` WORK in R3** (R2 blocker resolved — returns full order rows incl. `id`,`remaining`,`filled`,`status`; cancels confirmed). Fill detection is now status-based off this.
- ⚠️ **WETH/WBTC PostOnly buys reverted** on testnet ($1 leg → likely sub-minQuantity or PostOnly-cross). SOMI path clean. Investigate min sizes per pair before relying on WETH/WBTC.
- 🐛 **FIXED — order stacking + missed partial:** re-quote left a stale sell resting (got 2 stacked; one partial-filled 3/9.91) because cancel-before-replace wasn't airtight and the 50%-notional threshold missed the partial. Reworked to id-based fill detection (track our order, read `remaining`) + always cancel our order before re-placing.

## Min order sizes (live mainnet /v0/markets, 2026-06-24)
- WETH:USDso: minQty 0.001, lot 0.0001, tick 0.01 → min order ≈ **$1.66**
- WBTC:USDso: minQty 0.0001, lot 1e-5, tick 0.1 → min order ≈ **$6.23**
- SOMI:USDso: minQty 1.0, lot 0.01, tick 0.0001 → min order ≈ **$0.10**
- Our $12 `MAKER_LEG_USD` clears all three; `_round_lot` floors at minQty. No config change. NOTE: with strong inventory skew the WBTC leg could shrink below $6.23 → floored to minQty (fine). Testnet $1 WETH/WBTC reverts were sub-min / occasional PostOnly-cross — the loop logs the revert and retries; not blocking.

## Known Issues — R3 profit agent
- **Execution path:** SOMI cycle validated on testnet; id-based fill/no-stack rework committed + compiles, NOT yet re-validated live (testnet wallet out of USDso). Re-validate after topping up testnet USDso (testnet gas = STT, quote = USDso; "SOMI" pair on testnet is native STT).
- **✅ FIXED — inventory persistence:** positions + avg_cost + realized PnL now persist to SQLite (`inventory_state`/`agent_state` in agent.db) on every fill and reload on startup, so a restart resumes the real position instead of starting flat. Runner also logs any on-chain vs persisted base mismatch for ERC-20 base pairs. Verified across a simulated restart.
- **Testnet wallet state:** low USDso + holding ~7 STT (from validation). Liquidate or top up before the next testnet round.
- **Native SOMI pair making:** SOMI base is native; quote-side (USDso) delta still detects fills, but base inventory tracking on the native pool is less clean — validate or keep SOMI pair gas-only at first.
- **Strategist needs server ADC:** Gemini 2.5 Pro via Vertex requires `GOOGLE_CLOUD_PROJECT` + `gcloud auth application-default login` on the server. Falls back to safe deterministic defaults if absent.
- **Two-sided simultaneous quoting deferred:** v1 uses the proven alternating no-bleed cycle (one resting side at a time — still earns yield). Simultaneous both-sides quoting for extra yield is a future enhancement.

## Resume From Here (2026-06-24 — R3 profit agent built, pre-validation)
- **Done:** R3 rules saved (`context/plan/round3-rules.md`) + docs delta. Checkpointed R2 to `main` and pushed. New branch `feature/profit-maker-agent`. Archived all R2 code → `backend/archive/` + `ARCHIVE.md`. Built `backend/agent_v3/` (context_store, market_data, inventory, gas, strategist, maker, runner), updated `config.py` for R3, added `gas_min`/`min_gas` 5M-gas passthrough to `dreamdex.py`/`wallet.py`, repointed Dockerfile/compose. All modules import clean.
- **Next:** (1) `DRY_RUN=1` local dry run to eyeball quoting/logging; (2) testnet live run to validate fill detection + requote/cancel (see Known Issues); (3) tune leg/margin/timers; (4) wire server ADC for Gemini; (5) fund `0xD84f…1E76` with $150; (6) `docker compose up -d` on user's go.
- **Blockers:** none for building; mainnet launch gated on testnet validation + user go.
- **Optional R2 cleanup still open:** rotate old wallet H + B keys (repo public); `docker compose down` old R2 container before redeploy.
