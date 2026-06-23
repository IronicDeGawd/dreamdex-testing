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
