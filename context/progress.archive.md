# Progress Archive — DreamDEX Contest Agent

> Raw Completed log + stale state from Round 1 (testnet rehearsal → mainnet contest,
> finished rank #4, ~205k volume) and the agent/dashboard build. Compacted out of
> the live `progress.md` on 2026-06-09 when Round 2 began. Lessons (cross-round) stay
> in the live file.

## Round 1 — Completed log (raw)

### 2026-05-27 17:00 — Internal PnL + auto-liquidate on floor breach (commit `6804f4e`)
Dashboard computes real PnL in-browser from multi-bucket inventory (wallet USDso + vault quote + native SOMI + base tokens). `monitor/portfolio.py` exposes `wallet_base`. `agent/agent.py` gains `_live_wallet_value()` (live RPC read avoiding 60s cache staleness that let agent blow past $20 floor to $8) and `_liquidate_inventory()` (auto-sell base >$1.50 before halt). Floor check rewritten: wallet ≤ floor and inventory ≥$2 → auto-liquidate; only halt if wallet empty.

### 2026-05-27 16:45 — Dashboard surfaces micro sub-agent (commit `67a52a1`)
Agent Control split into "Main" + read-only "Micro" block. `pollMicro()` 5s reads `/agent/micro`. Activity log prefixed `[main]`/`[micro]` with outcome icons. Block auto-hides on 404. Deployed via docker cp.

### 2026-05-27 15:31 — Plan-B dual agent: orchestrator + brainless micro on same wallet (commit `96b1bc2`)
One LLM decision/tick directs TWO agents on same EOA via `decide_pair` + `ORCHESTRATOR_PROMPT`. Main ($7–15/120s) + micro (brainless $2–5/90s) fire sequentially on the nonce lock. Per-agent config (dex/name/min/max/loop/fixed_mode/peer/brainless). config bumped AGENT_MAX_TRADE=15, AGENT_STOP_BELOW=20; added MICRO_AGENT_*. server exposes `/agent` + `/agent/micro`. `monitor.db` gained `agent_name` column (3-step migration to avoid executescript abort).

### 2026-05-27 15:30 — Outcome-aware activity log + avoid-list (commit `f0da9b5`)
Trade result status on each `last_decision`. Icons reflect outcome not intent. Brain `_build_prompt` emits `PAIRS TO AVOID THIS TICK` when last 2+ attempts failed.

### 2026-05-27 14:24 — Hard-clamp min trade to $7, dashboard contrast invert (commit `f9ae085`)
AGENT_MIN_TRADE 0.10→7.00. `max(MIN,min(MAX,amt))` clamp guarantees [$7,$8]. Static palette inverted.

### 2026-05-27 14:22 — Theme() CSS variable swap, modal rebuild (commit `d1eb25a`)
Replaced Tailwind `theme()` with `:root` CSS vars. New modal/btn classes.

### 2026-05-27 14:20 — Manual trades mirror to SQLite (commit `6b97aaf`)
Added `db.record_trade(..., mode="manual")` to ManualTrader.execute path.

### 2026-05-27 14:18 — Trade cap $8, floor $30 (commit `54db550`)
AGENT_MAX_TRADE 5→8, AGENT_STOP_BELOW 35→30.

### 2026-05-27 — Three-state mode GRIND/PROFIT/AUTO, sticky overrides (commit `04db1af`)
GRIND/PROFIT manual sticky (auto=false); AUTO re-enables rank flip. `/agent/mode` returns {mode,auto,selected}.

### 2026-05-27 — SQLite agent memory + rank-flip fix (commit `89c65c0`)
`/app/data/agent.db` (volume-mounted). `monitor/db.py`: trades+market_ticks tables + helpers. `/agent/stats` endpoint. Rank-flip threshold `rank>3`→`rank>2`.

### 2026-05-27 16:15 — Suppress lazy HOLD-storms in GRIND (commit `72ac698`)
GRIND_PROMPT flags lazy HOLD invalid; agent-side guard overrides HOLD with $8 BUY when a pair is playable. Fixed UnboundLocalError.

### 2026-05-26 — Diversity rule in PROFIT mode (commit `e37fa71`)
PROFIT_PROMPT rotates pairs across SOMI/USDC.e/WETH after a round-trip.

### 2026-05-26 — Dashboard auto-derives BASE_URL; API key localStorage (commit `a5318a2`)
window.location.origin auto-detect; API key persists. Firmware CAPITAL_FLOOR synced.

### 2026-05-26 23:59 — History scrub via git-filter-repo (6 rewrites)
Redacted API key, Tailscale IP, public domain, SSH prefix, bare domain from all git objects. Originals still in terminal history — rotate before sharing.

### 2026-05-26 18:00 — Live mainnet trading + dashboard rework (6 commits)
Vault-funded IOC never fills → switched to funding=wallet. Leaderboard PnL bleed → auto-drain vault quote post-sell. Vault-delta rewrite (5 balances, two-sided fill proof, `placed_unfilled`). Mode toggle + auto-flip. Shared LeaderboardMonitor fix. Dashboard UX. Commits: 36600c1, 680464e, a21ad60, a8a42c7, 1d6b4c4, 127ca9f.

### 2026-05-26 — Live testnet rehearsal
Docker + CF tunnel + watch HTTPS. +1000 STT funded. 300 STT→49.63 USDso. 3 BUY SOMI:USDso confirmed via vault-delta. Paused.

### 2026-05-26 — Mainnet-safety hardening (commit `42fdf44`)
6 critical (X-API-Key auth, on-chain Portfolio floor, MAX_CONCURRENT_POS gate, vault-delta proof, book-aware vault sufficiency, state history-only), 5 high (ManualTrader lot/min, approve cap, thread-safe nonce, per-pool quoteDecimals, fill price from limit), 4 medium (OPENAI_KEY guard, tick-decimal format, minQty skip, config-priority). EIP-1559 + legacy fallback.

### 2026-05-26 — Containerization + Cloudflare (commit `4e40357`)
Dockerfile + compose `network_mode: host` (bridged blocked on Ubuntu 24.04). RUNBOOK.md. CF tunnel → localhost:5001, SIWE→JWT→orders proven.

### 2026-05-26 — Firmware UX pass (commits `02d24f1`→`85f9f70`)
CF tunnel + HTTPS, WiFi menu fixes, sparklines + richer Portfolio, staggered fetches + tighter timeout, BAL LOW warning.

## Round 1 — Known Issues (stale)
- `Portfolio` reports `base +0` for SOMI native pool vault-delta (getWithdrawableBalance(0x0) doesn't work for native). Quote-delta still proves fills.
- Old leaderboard URL `dreamdex-leaderboard.vercel.app` 404'd (round-2 URL is `dreamdex-leaderboard-super-cool.vercel.app`).
- `docker compose port` lists no host bindings under host network (cosmetic; `ss -lntp` confirms).
- AGENT_STOP_BELOW hardcoded in both config.py and firmware AGENT_FLOOR_USDSO.

## Round 1 — TODO / Backlog (post-contest, mostly stale)
- Behavioural-pattern model from ~43k tx DB + new MAKER data ($15-20 self-fund). Combine taker+maker rows, fine-tune fill-predictor. dreamDEX book dominated by pro MM ~0.02% — maker profit thin.
- Per-pool fill/profit analysis (rank SOMI/USDC.e/WETH/WBTC on fill rate, revert, spread, depth, capital efficiency).
- (Optional) SOMI native-pool base balance into Portfolio via eth_getBalance(pool).
- Old burst_autotune controller (REMOVED, buggy — deadlocked at $9 on 50/50 capital split). Source copies in repo but DISABLED.
- Post-contest: remove contest-only crons (auto_withdraw, legacy burst keepalives), kill bursts, wipe /tmp scratch.

## Round 1 — final resume snapshot (stale)
Floor-breach auto-liquidation deployed; agent paused for review. Dashboard real multi-bucket PnL. `_live_wallet_value()` + `_liquidate_inventory()`. Dual-agent orchestrator (main+micro same EOA). Container paused; wallet $44.51 USDso, 10.07 SOMI, mode=GRIND, loop=300s. Real internal PnL ~−$3.35. Final contest result: rank #4, ~205k volume.

---

## Round-2 maker-era strategy — SUPERSEDED 2026-06-15/16 (archived from progress.md)

The whole "maker-not-taker / hard-cap-100-USDso / 1M-unreachable / end_burst.sh hand-off" plan was overtaken by events. What actually won: a **single-wallet price-AWARE TAKER burst on WETH:USDso (`aware_burst.py`) at slip=50**, fed by emrey SOMI grants, run to **#1 at ~1.09M+**. Superseded claims (do NOT trust these from the old log):
- "🔒 100 USDso HARD CAP / can never add USDso / 1M not reachable / ceiling ~880-910k" — FALSE in the end: we crossed 1M and hit #1. (emrey kept topping up SOMI gas; capital was spent down via taker toll, not preserved.)
- "MAKER not taker for volume" — we went full taker (aware burst) and it took #1. Maker was abandoned. (The maker lesson may still hold for a *next* contest started day-1, but it was NOT the Round-2 endgame winner.)
- The maker phase, `profit_maker.py`, `end_burst.sh` (June-16 10:30 cron hand-off), `maker_keepalive.sh` stall-watchdog, SOMI→capital drip — all PARKED/REMOVED. `end_burst.sh` launches the DEPRECATED `direct_burst.py` (stale-price) — must NOT fire.
- The "Resume From Here" blocks dated 2026-06-09/10/11 (maker config, ~945k projection, dual-wallet) are historical only.

Still-valid from that era: the DEX-quirk Lessons (gas sizes, selfMatchingOption, expireTimestampNs, vault native sentinel, getPoolParams shape, OrderPlaced filled=0, pgrep absent) — retained in live progress.md.

---
## Round 2 — verbose session log (archived 2026-06-23, superseded by Round 3)

### 2026-06-22 — 🏆 WON #1, CONTEST OVER
FINISHED #1. Final 1,342,945, lead +33,177 over t3 (event ended 10:00 UTC Mon 2026-06-22). PnL −93 (most efficient of top 3). Engine STOPPED + all crons removed; wallet H idle (dust). Leftover capital extracted: USDso→WETH (DreamDEX) → 0.0039 WETH to `0xff1661f01687E6e1c50282256CD23D79EADBFCa4`.

### 2026-06-18 — MAKER-TILL-FRIDAY
RANK #1, ~1,135,547, lead ~42k over t3. NO-BLEED MAKER till Friday (contest extended to Sunday). Capital $27.12. `profit_maker.py` PROFIT_FUNDING=wallet fixes the vault-inventory wedge (both legs use it); live full cycle confirmed. `maker_keepalive.sh` cron */5; vault-taker cron removed; heartbeat in `_wait()` stops the 600s watchdog false-killing a resting maker. Gas external-only: wallet B swept 0.52 SOMI→H. 1.0 USDso stranded in B (its ERC20 transfer needs 2M gas; B left without gas to retry).

### 2026-06-16 — taker method died → maker → vault-taker port
`placeTakerOrderWithoutVault` started reverting unconditionally (devs disabled without-vault taker; t3 frozen too). Switched to vault `placeOrder` path. `aware_burst_vault.py` = vault-taker via `dex.place_order(order_type="ioc", funding="wallet")`, live-confirmed status=success. `profit_maker.py` maker retired earlier (vault inventory bug: bought vault-funded, fill landed in WALLET, looped SELL vault-funded → ERC20 transfer-exceeds-balance forever). Board froze at end of original window (all volumes flat, capital/gas exhausted). Capital sweep: us $10.06 · t6 $19.62 · t3 $10.92(frozen) · t2 $8.21 · t4 $1.89.

### 2026-06-15/16 — slip=50 fix → full taker burst → #1 over 1M
Fill rate lagged (64% vs ~100%): crossed at slip=6 off ≤2s-stale buffer → buy limit missed on fast WETH → 36% retries. Fix slip=50 ($0.50): fill→~100%, throughput ~2×, gas/round ~40% less, toll/volume unchanged. Lowered BURST_SOMI_GAS_RESERVE 1.0→0.2. emrey sent 28 SOMI → full-send → passed t4/t2/t3 → #1, crossed 1,000,000, lead ~37k.

---

## Round 4 — week of 2026-06-30 to 2026-07-08 (archived from progress.md)

### 2026-07-08 11:15 — feat(control): serve original R1 dashboard at /r1 for design reference (3 files, +~1600 lines)

### 2026-07-08 10:55 — docs(control): document the dashboard, env keys, and ignore runtime state

### 2026-07-08 10:55 — feat(dashboard): rebuild control panel on the existing design system (6 panels: status, balances, leaderboard, logs, gas, flatten)

### 2026-07-08 10:54 — feat(control): host-run engine-control API for the R3 launchers (FastAPI app.py + engine_manager.py, 10 endpoints, single-engine lock + leg guard)

### 2026-07-07 21:00 — feat(direct_burst): direct-contract placeOrder(0x4e978373) engine with spread gate (2 files, +250 lines)

### 2026-07-04 14:23 — feat(rpc): failover across multiple Somnia RPC endpoints (2 files, +30~5 lines)

### 2026-06-30 07:30 — fix(volume_climb): honest pause/resume — keep cost window, resume only under ceil (1 file, 3±3 lines)

### 2026-06-30 07:20 — feat(volume_climb): Telegram pings for milestones, pause, resume, stop (1 file, +20 lines)

### 2026-06-30 07:10 — feat(volume_climb): cost-aware mode — spread gate + rolling $/1k pause (1 file, +26~2 lines)

### 2026-06-30 06:50 — feat(config): env-overridable ELIGIBLE_PAIRS + alloc fallback (1 file, +4~2 lines)

### 2026-06-30 06:40 — fix(maker): gap-safe trend signal; hold-mode supersedes legacy DB guard (2 files, +16~11 lines)

## Round 4 — full Completed entries rolled up on 2026-07-12 (archived from progress.md)

- 2026-07-09 17:00 — fix(leaderboard): rank by volume, not tx count (4 files); dashboard was reporting rank #6/9 while live R4 board showed #3/9. Root cause: backend/monitor/leaderboard.py re-sorted the API response by txCount (a Round-1 leftover, when tx count was the KPI), not volumeUsdso (the R4 metric). We trade low-tx-high-volume (1,182 tx, $92,163 volume), so the tx sort pushed us down several places. Fixes: (1) leaderboard.py now sorts by volumeUsdso; gap/signal derive from volume instead of txCount; removed third_tx field; gap = volume needed to overtake rank+1 (or lead over #2 if ranked #1), with gap_to label; signal = QUALIFYING if rank ≤ 2 (top-2 for next cohort), else ACCELERATE; (2) control/app.py /leaderboard passthrough includes my_tx + gap_to fields; (3) static/index.html Balances & Rank panel shows Rank (by volume), Volume, PnL (colored), Tx, Gap-to; (4) config.py LEADERBOARD_POLL 300→120s (env-overridable) for real-time tracking. Verified vs live board: rank #3/9, $92,163.78 vol, −$13.85 pnL, 1,182 tx, $51,783.12 gap to #2. Standings: #1 trader-6 $224.8k / −32.84; #2 trader-4 $143.7k / −118.23; #3 us $92.2k / −13.85; #4 trader-5 $14.2k / −116.68. We're by far most capital-efficient (rivals burn ~8× more per unit volume). Deployed control-side (no image rebuild). Engine still running.
- 2026-07-09 — feat(resilience): survive network outages + watchdog (3 files, +145/-62); DNS outage halted at 514 trips/$81,946 vol/$9.85 bleed, engine stopped cleanly (flatten ran, residual={}). Fixes: (1) volume_climb classifies transport errors (DNS/timeout/connection) as TRANSIENT — backoff 30s→15m, retry forever, never touch trade breaker; (2) POST /autorestart + control/watchdog.sh + cron */15 relaunches ONLY unexpected death (checks for "=== STOP:" / "ABORT" in log); refuses after deliberate /stop + self-stop; clamps leg to 0.8× free USDso on relaunch; 3-min startup grace; (3) engine_manager.is_running() reads State.Status (accept running/created/restarting/paused) not State.Running (false on boot); (4) /balances reports every eligible pair's bag, fresh read; (5) dashboard shows WBTC bag, FLATTEN ALL button. Engine relaunched, trading leg $80, WBTC/WETH rotation, ~$89.5k volume, #5/9 leaderboard. WBTC spread drifted wider (0.005%→0.034%), cost pauses more often.
- 2026-07-08 14:XX — fix(audit): harden live R4 engine + control against audit findings (5 files); 4-way audit (correctness/capital, rule-compliance, error-handling, security) found + fixed all CRITICAL/HIGH findings. volume_climb.py: SIGTERM handler (bounded flatten_all so docker stop can't SIGKILL mid-trip → bag); sweep ALL pairs for stray base each iteration (not current only); SELL FULL held balance not trip delta (folds sub-minQ dust into next sell); 24h keepalive (small trip past CLIMB_LIVENESS_S=18h to dodge rule-11 idle DQ); directional price snap (buy up/sell down for IOC cross); floor lot snap; abort on boot if market-param refresh fails; refuse non-eligible pairs (rule 5). control/app.py: honest flatten() (re-checks balance, retries, reports 'flat' or 'bag' with residuals); is_running() guard on /flatten and /gas/topup (nonce races); eligible-pair allowlist on /launch + /trade; per-IP brute-force limiter on /login. control/engine_manager.py: stop() verifies docker stop killed the container. Regression caught during deploy: snap_lot→floor sized sell off TRIP DELTA (ignored dust); WBTC minQty=0.0001 (10 lots) so fee-shave residual piled up ($3.10 trapped, $4.33/1k "bleed"). Fix: sell FULL held balance so dust folds in once total ≥ minQ. After fix: WBTC bag 0.0, USDso recovered 146.4→149.55, cost back to ~$0.18/1k then negative. Audit-confirmed SAFE: IOC everywhere, fills verified by balance delta, eth_call sim gates every order — no tokens-vanish path; risk was trapped inventory + stalls, now addressed.
- 2026-07-08 12:XX — fix(control): fresh leg-guard read + multi-pair flatten (2 files); LiveBackend.free_usdso() now reads wallet fresh on-chain instead of 60s-cached Portfolio poll (was gating leg $80 via stale $99.71 snapshot). LiveBackend.flatten() now sells any base bag on EVERY ERC20 pair (WBTC+WETH), not WETH-only — stops mid-trip can't leave rotated-pair bags. Wallet verified flat + whole at $149.96 USDso, 49.95 SOMI.
- 2026-07-08 — feat(volume_climb): dynamic pair rotation + per-pair tick/lot snapping (3 files, +179/-134); rewrite for DYNAMIC PAIR ROTATION: CLIMB_PAIRS (comma list) → each round-trip reads every eligible pair's book, trades tightest-spread, auto-rotates to cheapest as liquidity shifts. Per-pair tick/lot/min snapping: read after DreamDEX() refresh (module defaults stale). Dashboard /launch takes `pair` (comma) + `spread_gate`; engine_manager passes CLIMB_PAIRS/CLIMB_SPREAD_GATE_PCT. Currently running WBTC:USDso,WETH:USDso, picked WBTC (0.005% spread). First trips bleed ~$0.056/1k, near-zero steady.
- 2026-07-07 23:30 — chore(config): point defaults at Round 4 leaderboard + wallet (1 file); R4 wallet 0x703e…F6 funded $150 USDso + 50 SOMI + registered; server .env + config.py + .env.example wired; R3 key/env backed up ~/secrets/

## Round 3 / V3 era — historical sections (archived 2026-07-12 compaction; removed from progress.md)

### V3 ISSUES — FULL LOG (2026-06-24 → 06-26)
**Scoring / strategy realizations:**
- 🌟 Effective Volume = raw × (1 + (freeUSDso−150)/150) — verified to the $ across all traders. LIVE leaderboard uses FREE USDso (held inventory = scored as loss). Dev says FINAL PnL = liquidate-all-to-USDso at contest end (inventory neutral until then). See [[scoring-uses-free-usdso-inventory-is-poison]].
- 🌟 **Raw volume dominates** despite the PnL multiplier: trader-3 did 127k raw at −$129 → 53k effective, crushing our 963 raw × good multiplier = 884. Can't win without huge volume; huge volume in a bear = bleed. No profitable path in a sustained downtrend (can't short on spot).
- 🌟 Leaders' real net worth (on-chain) ≠ leaderboard pnl: t1 ~$138, t5 ~$147, t3 ~$75, us ~$140 — they did 16–25× our volume AND kept similar capital (held bags, not realized losses). We have neither volume nor a capital edge.
- Arb ruled out (DreamDEX vs GingerSwap SOMI gap ~0.66% < ~1% round-trip cost; LI.FI Somnia = bridge-only = banned top-up). Self-match ruled out (selfMatchingOption=1=cancelMaker + rule #9 DQ). Maker yield negligible.

**Bugs found + FIXED that session:**
- ✅ **Inventory fill-tracker desyncs from chain** (phantom fills, native-SOMI/gas commingle) → caused phantom inventory, bad flattens (revert loops), over-buying, false auto-stop. FIX: `Inventory.sync_base()` makes base CHAIN-authoritative each tick for ERC20 pairs (verified: phantom 0.00022 WBTC → 0). Native SOMI still tracker (gas commingled).
- ✅ **Monitor showed false losses** (−$7 realized-only from desynced tracker; −$36 from a glitched SOMI book price ~0.0145; −$10 from WBTC priced at $0 when book read returned nothing). FIX: monitor reads REAL net worth on-chain + sane-band price clamp + on-chain EMA fallback + last-good cache. Audit-verified: matches chain to 0.0000.
- ✅ **$100 auto-stop FALSE-fired** ($95.81 when real value ~$109) because it valued inventory off the desynced tracker → unprotected flatten realized losses. FIX: auto-stop now reads on-chain value.
- ✅ **Stop-loss whipsaw**: 6% market-IOC dumped into a thin/crashed bid (filled ~8% below trigger), then SOMI bounced. FIX: 10% trigger + limit-protected execution (defer if bid gaps below floor) + 15m cooldown.
- ✅ **WBTC approval underflow**: `int(0.00015*1e8)`=14999 (one unit under) → `ERC20InsufficientAllowance` revert. FIX: `int(round(...))`.
- ✅ Native SOMI place+cancel need gas ≥5M (InsufficientGasForPayout); cancel now verifies receipt.
- ✅ Lot-precision 400s (round-before-floor + decimal snap).
- ✅ Trend guard: pauses BUY when coin down >TREND_GUARD_PCT/24h; fails OPEN with no history (so chosen pairs can trade) + keepalive (tiny buy near 24h idle to avoid DQ). KNOWN LIMIT: only catches CLIFFS not gradual grinds (−1%/day slips under) → trading a slow-bleed market still loses; only zero-bleed = cash.

**Config then:** pairs WBTC+WETH (SOMI EXCLUDED — grind-bled), alloc 0.5/0.5, `MAKER_MAX_INV_USD=20`, `STRATEGIST_ENABLED=false` (no Gemini tokens), `TREND_LOOKBACK_S=86400`, `TREND_GUARD_PCT=0.015`, stop-loss 10%, `KEEPALIVE_LEG_USD=1`.

**Open / unresolved (V3):** native-SOMI inventory still tracker-based (gas commingle — can't separate on-chain); we will not place top-2 (volume gap); ~$13 sunk (unrecoverable without profit). DQ in ~24h of full stop (irrelevant — can't place anyway).

### Round 3 setup → SUPERSEDED
- Wallet `0xD84fE2a2220f0269e3d88dab908ADceb2d691E76` (registered, funded). Key on server `~/dreamdex-r3/backend/.env` as `MAINNET_PRIVATE_KEY`. R2 `.env` backed up `~/dreamdex-agent/.env.r2bak` (dir since removed — backup was pre-removal).

### Round 2 outcome (rollup)
- **🏆 WON #1.** Final 1,342,945, lead +33,177 over t3 (ended 10:00 UTC Mon 2026-06-22). PnL −93 (most efficient of top 3). Crossed 1M with a full taker burst (the old "100-USDso hard cap / 1M unreachable / maker-only" plan was WRONG — see archive).
- **How we won:** (1) direct-RPC engine bypassing the ~14s DreamDEX order-build API (3k→10k vol/hr); (2) let fixed-capital rivals sprint dry while we held/padded, flipping to FAST only to defend a closing rival.
- **Funds extracted** post-contest: USDso→WETH → 0.0039 WETH to `0xff1661f01687E6e1c50282256CD23D79EADBFCa4`.

### Reusable engine assets (from R2 — re-validate against current rules)
- **`aware_burst_vault.py`** — the winning engine. `trade()` builds `placeOrder` calldata locally + broadcasts direct over RPC. Env `BURST_CONFIRMED`: unset=FAST direct-RPC (~10k/hr); =1=slow API+balance-verify (~3k/hr, low gas). Selector `0x4e978373`, orderType 2 (IOC). Reads `MAINNET_PRIVATE_KEY` from container env (compose env_file).
- **`profit_maker.py`** — no-bleed maker. `PROFIT_FUNDING=wallet` makes both legs use the same balance location (fixes the vault-inventory wedge). Heartbeat in `_wait()` keeps the 600s watchdog from false-killing a resting maker.
- **Keepalives:** `aware_vault_keepalive.sh` (burst, cron */3), `maker_keepalive.sh` (maker, cron */5). `cycle_phase.sh` swaps between modes.
- **`wallet.py _gas_fields`** (in container `/app/trading/`): adds 5gwei priority tip (Somnia default tip 0 → txs mempool-queued without it).
- **Deploy a .py (R2-era):** `scp` to `/home/irony/dreamdex-agent/` THEN `docker cp <f> dreamdex-agent:/app/<f>` (/app ≠ host dir). Logs live INSIDE container: `docker exec dreamdex-agent tail /tmp/aware.log` (burst) / `/tmp/avault.log` / `/tmp/maker.log`.
- **Probes:** `audit_balances.py` (true capital = wallet + vault `getWithdrawableBalance`), `probe_realbal.py`, `probe_funding.py`, `probe_trades.py`/`probe2.py`, `deep_check.py`/`deep_watch.py`.
- **Leaderboard (we were trader-9 in R2):** `curl https://dreamdex-leaderboard-super-cool.vercel.app/api/leaderboard`.

### R3 carry-over issues (historical reference)
- **✅ Gas self-funding IS possible (corrected 2026-06-23):** the earlier "dead all paths" belief was WRONG. `0x782b2567` = `InsufficientGasForPayout(uint256 gasLeft)` — a deliberate guard, not a bug. `placeOrder` on SOMI:USDso (BUY, `isBid=true`, `msg.value=0`) works with **`gas≥5,000,000`** (we used 3M → too low for the native-payout headroom check). Our `eth_call` sim lied because it ran with different gas than we broadcast → **simulate with the SAME gas you broadcast.** Dev `emrey.somi` confirmed + added to DreamDEX docs. ⟹ USDso→SOMI self-gas is on the table; wire an auto-top-up. (See `context/research/dreamdex.md` §7a.)
- **DNS-crash robustness gap:** `aware_burst.py` main loop doesn't wrap RPC calls in try/except → a transient `NameResolutionError` crashes the process (keepalive recovers in ~3 min). Fix: try/except-with-retry around main-loop RPC calls.
- **SSH rate-limit:** rapid reconnects trip sshd → exit 255. Use `-o ControlPath=none`, SHORT single-shot commands, retry up to 5×. Balance reads via public RPC need no SSH.
- **Old keys to rotate (R2, repo was PUBLIC):** wallet H `0xF4c825F3C2970153d78B407CF190861dd4E2b905`, wallet B `0x7571…A638` — treat as burned; rotate if reused. Newer keys must NEVER be committed (`env_file` only; never `git add -A`).

### Testnet validation results (2026-06-24)
- ✅ **PostOnly placement + fill works** on testnet SOMI:USDso (bought 9.91 @ 0.1009, filled).
- ✅ **No-bleed invariant held** (sell quoted @ 0.1011 = buy + 2 margin ticks).
- ✅ **`get_open_orders` + `cancel_order` WORK in R3** (R2 blocker resolved — returns full order rows incl. `id`,`remaining`,`filled`,`status`; cancels confirmed). Fill detection is now status-based off this.
- ⚠️ **WETH/WBTC PostOnly buys reverted** on testnet ($1 leg → likely sub-minQuantity or PostOnly-cross). SOMI path clean. Investigate min sizes per pair before relying on WETH/WBTC.
- 🐛 **FIXED — order stacking + missed partial:** re-quote left a stale sell resting (got 2 stacked; one partial-filled 3/9.91) because cancel-before-replace wasn't airtight and the 50%-notional threshold missed the partial. Reworked to id-based fill detection (track our order, read `remaining`) + always cancel our order before re-placing.

### Min order sizes (live mainnet /v0/markets, 2026-06-24)
- WETH:USDso: minQty 0.001, lot 0.0001, tick 0.01 → min order ≈ **$1.66**
- WBTC:USDso: minQty 0.0001, lot 1e-5, tick 0.1 → min order ≈ **$6.23**
- SOMI:USDso: minQty 1.0, lot 0.01, tick 0.0001 → min order ≈ **$0.10**
- The $12 `MAKER_LEG_USD` cleared all three; `_round_lot` floors at minQty. NOTE: with strong inventory skew the WBTC leg could shrink below $6.23 → floored to minQty (fine). Testnet $1 WETH/WBTC reverts were sub-min / occasional PostOnly-cross — the loop logs the revert and retries; not blocking.

### 🌟 ROOT CAUSE + FIX — native-pool ops need ≥5M gas LIMIT (cancel too)
- Sell-cancels on the SOMI (native) pool were **reverting silently** (tx status 0) while `cancel_order` returned a false "cancelled" → orders stuck on the book → apparent "stacking" (saw 3–4 concurrent). Diagnosed: re-cancelling the same resting order with `min_gas=5_000_000` → **status 1, cleared** (gasUsed only 167k). So it's the native-payout `gasleft()` guard (InsufficientGasForPayout), same as native buys — the contract needs a high gas LIMIT even though usage is tiny. ERC-20-payout cancels (buy-side, USDso) work at 3M; native-payout cancels (sell-side SOMI) need ≥5M.
- **Fix:** `cancel_order` uses the 5M floor for native pools AND verifies the receipt (reports "revert" instead of fake "cancelled"). Maker also passes the 5M floor on native-pool placements. Maker reworked to cancel-all-before-place + balance-delta fill detection (captures partials, ignores gas).

### ✅ Testnet validation PASSED (2026-06-24)
- **No stacking:** max concurrent orders/pair = **1**; **0 resting after clean shutdown**.
- **Profitable no-bleed round-trips on SOMI AND WETH** (e.g. buy 0.1009 → sell 0.101 = +0.000495 USDso; WETH 0.001 filled).
- Fill detection (full + partial), inventory persistence across restart, multi-pair concurrency — all confirmed.

### Known Issues — R3 profit agent (as of archive date)
- **Inventory accumulation on a one-way move:** as a maker we buy at bid, so a sustained SOMI downtrend accumulates inventory to the cap; no-bleed means we hold (don't realize the loss) until recovery. Bounded by the **per-pair stop-loss** (`MAKER_STOP_LOSS_PCT` → IOC cut into bid + `MAKER_STOP_COOLDOWN_S` 15m re-entry pause) on top of the $90 cap + $100 auto-stop. KNOWN TRADE-OFF: while holding a bag, locked capital kept the score multiplier ~0.33 (R3 free-USDso scoring); rank stays suppressed until recovery or the stop fires.
- **Gas-refuel vs fill edge:** a USDso→SOMI refuel during an open buy could perturb accounting; rare (SOMI balance ≫ reserve). Isolate later if it ever triggers.
- **✅ FIXED — WBTC PostOnly sell reverted in a loop (approval truncation).** Root cause (debugged via eth_call replay → `ERC20InsufficientAllowance(spender,14999,15000)`): the token-approval scaling did `int(0.00015*1e8)` which floats to 14999.9999… and floored to **14999**, one base-unit below the order's required **15000** → allowance short → revert. The ORDER quantity (built API-side) was correct at 15000; only OUR approval scaling truncated. Fix in `dreamdex.py`: `int(round(app_amount*10**dec))` for the approval + its 2× cap. Verified WBTC sell filled at 62766.9 (profit) and the loop is gone. NOTE: same `int(x*10**dec)` truncation still existed in deposit/withdraw paths (lines ~658/741) — harmless there (conservative), fix if it ever bites.
- **🌟 INCIDENT 2026-06-24 — redeploy-while-stopped dumped inventory + SOMI tracker desync.** Agent had `agent_state.enabled=0` from a prior stop; the stop-loss redeploy restarted it → maker flatten-branch IOC-sold SOMI against the user's "hold" choice. Strategist+monitor run regardless of control, so Telegram looked "live" while trading was off. Tracker desynced from real native SOMI (said sold 899/realized −1.46; on-chain still held 430 SOMI, only ~518 actually sold). TRUE state was ~$155 (UP; SOMI recovered to 0.1028 > 0.1010 cost). FIX: stopped agent, reconciled `inventory_state` SOMI→280 @ 0.1010 (reserved 150 SOMI gas), set `MAKER_MAX_INV_USD=35` (server .env), enabled=1, restarted → healthy two-sided making, no flatten. **Lesson: check `agent_state.enabled` BEFORE any redeploy; trust on-chain SOMI balance over the fill tracker.** See memory [[redeploy-while-stopped-flattens-and-somi-tracker-desync]].

### 🟢 R3 LIVE ON MAINNET (2026-06-24) — deployment + config snapshot
- Server `irony@100.80.130.21`, dir **`~/dreamdex-r3`** (old `~/dreamdex-agent` REMOVED), branch `feature/profit-maker-agent`. Containers `dreamdex-agent` + `dreamdex-monitor`.
- Redeploy (R3-era): `cd ~/dreamdex-r3 && git pull -q origin feature/profit-maker-agent && cd backend && docker compose up -d --build [agent|monitor]`. Config baked into image → rebuild for code/config changes; monitor-only change → `--build monitor`. Clear `inventory_state`/`agent_state` in agent.db if restart desyncs. (NOTE: repo later went PRIVATE; server remote is HTTPS so `git pull` needs creds — deploy by rsync now.)
- Wallet `0xD84f…1E76` funded 150 USDso + 50 SOMI, registered. ADC file mounted host→`/app/adc.json`; project `project-8feccae3-bcae-4254-b60`; Vertex enabled, 2.5-pro reachable.
- Live config (R3): pairs SOMI 80% / WBTC 20% (WETH dropped, 1.4bps too tight). Leg **$65**, inv cap **`MAKER_MAX_INV_USD=35`**, reserve $20, margin 1 tick. SOMI ~10bps was the capturable spread.
- **Stop-loss: 10% trigger (`MAKER_STOP_LOSS_PCT=0.10`) + limit-protected execution (`MAKER_STOP_MAX_SLIP_PCT=0.03`) + 15m cooldown.** Floor = avg×(1−0.10−0.03); if the bid gaps below the floor the stop DEFERS (holds + retries) instead of dumping at the bottom. Was 6% market-IOC → whipsawed on a SOMI flash crash (dumped 335 SOMI @ 0.0899 = −$4.90, then SOMI bounced to 0.104). $100 account-stop = catastrophe backstop.
- **Arb ruled out (2026-06-24):** DreamDEX SOMI/USDso (0.10305) vs GingerSwap/"Somnia Exchange" SOMI/USDC.e (0.103729) = ~0.66% gross gap < ~1%+ round-trip cost (0.3% AMM fee + 0.5-1% slippage on $45k pool + USDC.e↔USDso leg + gas) → net-negative. LI.FI supports Somnia but BRIDGE-ONLY (no same-chain swap route) → using it = cross-chain = banned top-up. Self-matching also ruled out: `selfMatchingOption=1`=cancelMaker (self-cross just cancels, no volume) + rule #9 DQ risk. Real compliant lever = maker yield (3.3% APY pool, score=qty×W(Gaussian@EMA-mid)×secs, both sides must rest) + capturing real flow. GingerSwap addrs: factory 0x6C4853C97b981Aa848C2b56F160a73a46b5DCCD4, WSOMI/USDC.e pair 0xb1A5A70A946667655bf14512599D06ACCa020f62.
- **🌟 METHOD PIVOT → trend-guarded spread capture + cash (2026-06-25).** Diagnosis: our proven method IS spread capture (buy bid/sell ask, earn the gap), but it needs a TWO-WAY (oscillating) book. The market went one-way DOWN (SOMI −13%/7d, ETH −8%, BTC −6%) → only our bid fills → we accumulated a bleeding bag; an overnight auto-stop then false-flattened (−$7 realized, but real net worth ~$153 — the −7 was tracker-realized-only). Fixes shipped: (1) monitor reports REAL net-worth PnL from on-chain (USDso + all native SOMI + WBTC at mids vs 150+50SOMI basis), not the desynced tracker; (2) auto-stop values holdings on-chain; (3) **trend guard** — pause BUYING a coin when mid fell >`TREND_GUARD_PCT` over `TREND_LOOKBACK_S` (live: 24h / 1.5%), sell side stays on, auto-resumes when flat/up; (4) keepalive tiny buy (`KEEPALIVE_LEG_USD=1`) past the guard if idle nears 24h DQ. Went to CASH: flattened SOMI to ~$147 USDso + 60 SOMI gas, net worth ~$153, mult ~0.98.
- **Maker-yield decoded + decision (2026-06-24):** `getMidpointEmaState()` selector `0x2d1590a0` → (word0=EMA price, word1=ts_ns); **EMA mid = word0/1e18** (≈ spot on both pools; one anomalous 256×-off read seen → ALWAYS clamp to within ~10% of book mid). KEY: EMA mid ≈ book mid, and our maker already rests two-sided at the touch = already on the yield peak. "Quote at EMA mid" ≈ current behavior. Only further lever = quote TIGHTER than touch → more fills → adverse selection (the $5 whipsaw) for unmeasured, likely-small yield. **Decision: don't tighten blind — measure real yield (wealth drift vs trading PnL) first; baseline ts 1782299800 USDso 107.86 / wealth ~147.3 / realized −5.23.**
- Strategist Gemini 2.5 Pro every ~8 min (rationale relayed to Telegram). Monitor: summary every 10 min + on fills, market preview every 2h, /start /stop /status, auto-stop < $100. Monitor computes OUR PnL (realized+unrealized vs 150); leaderboard only for vol/rank.

### Strategy pivot → two-sided market making (2026-06-24)
- Recon: **no house MM** in docs — the tight book was a competitor MM (likely trader-3, who bled to −$108 PnL via taking → effective vol crushed to 7.7k). Live spreads then: WETH ~1.4bps, WBTC ~2bps, **SOMI ~10bps (only one worth capturing)**. Yield at our size ≈ $0.16/2wk → negligible; profit must come from spread capture.
- **New engine = bounded two-sided MM** (`maker.py` rewritten): rest PostOnly bid+ask, capture spread; fills tracked via `get_open_orders` remaining (reservation ≠ fill on a two-sided book); SELL only ≥ avg_cost+margin (no realized loss); base inventory capped per pair. Pairs SOMI 80% / WBTC 20%, $25 legs. Logic unit-tested (flat→buy-only, long→both sides, at-cap→sell-only).
- **First mainnet attempt found + fixed:** lot-precision 400s; phantom buy-fills (USDso reservation misread → fixed: buys detect base received); native-pool cancels needed 5M gas + receipt verify.
- **Final V3 state:** rank ~2/6, realized +0.15, holding ~$40 SOMI inventory, gas ~49.9 SOMI. Two-sided no stacking, fills via `get_open_orders.remaining`, no live errors. Stray untracked test DBs `backend/data/agent_ttest*.db` — safe to delete. Repo to be shared at program end (R3 rule) — `.env` never committed.
