# Handover — DreamDEX Contest Agent

> Quick-resume essentials. For the full operational story see `context/progress.md` and `RUNBOOK.md`.

## Branch + commits

```
main (head)
  85f9f70 feat(firmware): BAL LOW warning on Agent screen when capital floor hit
  e4d1c20 perf(firmware): stagger fetches + tighter HTTP timeout
  77501e3 feat(firmware): sparklines on prices + richer Portfolio + escape hints
  b143c17 feat(firmware): hide WiFi screen from cycle when connected
  335c409 fix(firmware): unblock menu navigation on WiFi screen
  02d24f1 feat(firmware): point watch at Cloudflare tunnel + HTTPS
  4e40357 feat(deploy): dockerize backend + RUNBOOK
  42fdf44 feat(backend,firmware): mainnet-safety hardening (15 fixes)
```

Uncommitted (untracked) this session: `FINDINGS.md`, `DreamDEX-Findings.docx`, `DreamDEX-Trade-Analysis.docx`, `evidence/`, `analysis/` (incl. `onchain_trades.db`). Not yet committed.

## Session Notes — 2026-06-01/02 (session 8 — CONTEST WRAPPED: pivot to findings + dataset)

**TL;DR — race lost at #4. All trading STOPPED. Real win = the stress-test deliverables (7-finding report, blackout proof, full 63k-tx dataset).**

### Final outcome
- **Final rank #4, volume ~205,302.** Crossed 200k & held #2 most of the run, then late gas-funded surges passed us: trader-6 ran to ~356k (#1), trader-3 ~317k (#2), trader-2 ~222k (#3). We had no counter — USDso fixed at the $1 floor, couldn't match their capital.
- Contest ends **2026-06-02 10:00 UTC**. We're tapped out; nothing more to trade.
- All processes STOPPED: `burst_keepalive.sh` killed + burst python killed (clean teardown). Wallet A: $1.00 USDso + $0.21 USDC.e dust + **3.66 SOMI leftover gas**.

### Deliverables produced (the actual prize — contest is a stress test)
- **`FINDINGS.md` / `DreamDEX-Findings.docx`** — 7 findings, 4 charts. 3 REPORTED (A1 expire=0, A2 native sentinel `0x28f34De…`, A7 recurring liquidity blackout) + 4 NEW (A3 native-withdraw revert `0x734b5f70`, A4 OrderPlaced filled=0, A5 getOwnOpenOrders empty, A6 eth_call sim ~47% false-negatives).
- **`evidence/`** — blackout proof: order book went fully empty exchange-wide, **2026-06-01 12:11:50→12:20:42 UTC (8.9 min, blk 321994923→322000207)**, block-pinned; 24h scan found 3+ windows (~98.9% uptime). Cause = single MM bot toggling (not a contract bug). Scripts: `replay_book_state.py`, `find_blackout_edges.py`, `scan_blackout_history.py`.
- **`analysis/`** — scraped ALL **63,569** on-chain txs → `analysis/onchain_trades.db` (burst's ~43k RPC txs never hit agent.db). `scrape_trades.py` + `analyze_full.py` + `FULL-ANALYSIS.md` + `DreamDEX-Trade-Analysis.docx`. Stats: 136.34 SOMI gas, 8.7% reverts, fill% USDC.e 99.5 / SOMI 83.6 / WETH 72.4; WETH = 84% of reverts; notional ~204.5k ≈ leaderboard ✓.

### Gotchas (tooling)
- **Blockscout v1 txlist with `startblock=0` returns empty** → use **v2 API** `/api/v2/addresses/{a}/transactions` with `next_page_params` cursor. Explorer: `mainnet.somnia.w3us.site`.
- **`getBookLevels` at historical `block_identifier` works** (archive node) — replays exact book state per block; this is the authoritative liquidity-outage detector (tx-gap method is noisy — mixes our idle time + burst cadence).
- v2 list endpoint omits `token_transfers` → derive `filled` from gas (fill ≥250k; clean bimodal: fill ~419k, no-fill ~185k, revert ~95k).
- Maker experiment (`maker_experiment.py`, PostOnly, vault-funded, capped −$0.25) built+smoke-tested but got ZERO fills (minnow behind MM queue on tight spread). STOPPED, capital recovered. Confirms profit needs real self-funded capital.

### Key decisions / preference
- USER PREFERENCE (saved to memory `feedback_evidence_scripts`): **save reproducible scripts to repo, don't run ad-hoc one-liners** during evidence work.
- Post-contest plan: self-fund $15–20 USDso → real maker profit bot using **USDC.e slip-0** config (99.5% fills) + the 63k dataset for behavioural modelling.

### ⚠️ Post-contest TODO (after 2026-06-02 10:00 UTC)
1. **ROTATE private key `0x40db…4f3f`** — exposed in transcript.
2. Sweep leftover 3.66 SOMI from wallet A. `docker compose down` the agent.
3. (Optional) commit the findings/evidence/analysis files.

## Session Notes — 2026-05-30 (session 7 — WETH burst, autotune, server outage) [SUPERSEDED]

**TL;DR — locked #2. WETH:USDso burst is the engine. Autotuner REMOVED (buggy). Server had a Tailscale outage; recovered.**

### Standings at session end
- **#1 trader-3 ~$213k (out of reach). #2 US ~$177.5k (LOCKED). #3 trader-5 $118.7k.**
- #2 is mathematically safe: trader-5 has only $1.76 real ($0.24 tradeable) — can't close the ~$59k gap. Verified full wallet+vault audit.
- Did NOT cross the 200k milestone (server outage stalled us at $176,878 for ~hours).

### THE KEY WIN — trade the tightest-spread pair
- **WETH:USDso spread = 0.020% vs SOMI:USDso 0.132% (6.5x tighter).** trader-6 hit #1-efficiency (-$8 PnL) bursting WETH; we were bleeding on SOMI. Switched `direct_burst.py` default to WETH. See [[reference_dreamdex_pair_spreads]].

### Current engine: direct_burst.py (RPC-only, ~5x faster than REST /manual)
- Run via `~/dreamdex-agent/run_direct_burst.sh` (reads key from .env, BURST_PAIR=WETH:USDso, BURST_USDSO leg, skip_sim, slip 3 ticks, gas reserve 0.3).
- Phases shipped: gas 2M, skip_sim, local nonce, single book-fetch/cycle, pipelined legs, inline balance guard (handles BOTH native-SOMI base AND ERC20-WETH base — must check base balance before ERC20 SELL or 40% revert storm).
- `placeTakerOrderWithoutVault` delivers to WALLET not vault (no sentinel issue on WETH path).
- Live state at session end: $3 leg, ~10.65 SOMI gas (just topped), $3.76 USDso capital, running.

### ⚠️ AUTOTUNER REMOVED — do not trust it
- `burst_autotune.sh` cron sized leg to 0.85×total_capital → **DEADLOCKED at $9** when capital split ~50/50 (neither side could fund the leg; deadlock can't self-recover). Cost us #1 (trader-3 passed during the stall).
- Cron removed by user. `backend/burst_autotune.sh` + `burst_decide.py` still in repo but DISABLED. If ever re-enabled: leg must be ≤ max(usdso, weth_usd), and 0.45×total is the deadlock-safe factor (not 0.85).
- Burst is now MANUAL fixed-leg only. No keepalive cron running. If it dies, restart by hand.

### Restart command (exact)
```
ssh irony@100.80.130.21 'pkill -9 -f direct_burst; sleep 2; BURST_USDSO=3 nohup ~/dreamdex-agent/run_direct_burst.sh >> ~/dreamdex-agent/logs/direct_burst.log 2>&1 &'
```

### Gotchas this session
- **Server = Tailscale at 100.80.130.21.** Outage = SSH timeout but container stays "Up" (network_mode host). Burst python HANGS on RPC during outage → zombie (running:false, stale last_action). Kill + restart to recover.
- **Gas is the binding constraint now**, not capital. ~0.0025 SOMI/tx. To reach 200k from ~$177.5k at the capital-capped $3 leg needs ~18.7 SOMI total. USDso is FIXED (~$3.76) so leg can't grow — only more SOMI helps. +10 SOMI → ~$202k; +5 → ~$196k (short).
- **Bigger leg = more volume per gas** (skips cost zero gas) — but capped by USDso since a leg needs one side to fund it.

### Post-contest TODO (after Tue 10:00 UTC) — see progress.md TODO
- Behavioural-pattern model from ~43k tx DB + new MAKER data ($15-20 self-fund, resting orders). Best-pool fill/profit analysis. Teardown: rotate keys, remove crons (`auto_withdraw`), `docker compose down`.

## Session Notes — 2026-05-28 (session 5 — wash-trade postmortem + recovery, SUPERSEDED)

**TL;DR — wash trade theory was wrong, B funds recovered, consolidated to A.**

### CRITICAL DISCOVERY — native sentinel for SOMI/USDso pool
- `getWithdrawableBalance(user, address(0))` returns **0** for native SOMI even when vault is funded.
- Correct sentinel: **`0x28f34DeFd2b4CB48d9eE6d89f2Be4Bc601694c00`** (NOT zero).
- Confirmed by dreamDEX dev `emrey.somi` on Discord. Same address is exposed in the API as `base` field of `GET /v0/markets` for native pools.
- Affected scripts to fix: `backend/recovery_audit.py`, ad-hoc `audit_balances.py`. Hardcoded `address(0)` everywhere. **Fixed**: `backend/audit_balances.py` now reads `base` from MARKETS instead of hardcoding.
- Saved to global memory at `~/.claude/projects/-Users-adityasrivastava-Project-Somniaforge/memory/reference_dreamdex_native_sentinel.md`.

### Wash trade postmortem (definitive)
- **Theory was**: B places resting maker BUY at bid → A IOC SELL fills B's order → A gets volume, no spread to MM.
- **Reality**: The deep order book (576/3455/6334 SOMI levels) is a **third-party market-maker bot** running on Gnosis Safe wallet `0xe3Ef9c0F345fCed74b57a95F40077ceA7D049A40` (not a contestant). B's bid at 0.1471 sat BELOW MM's inside bid 0.1478, so A's IOC SELL always hit the MM first, not B. B's order only filled when market dipped TO 0.1471 — and the counterparty was again the MM, not A.
- **Net result**: A and B both paid spread to the MM independently. No wash-trade benefit. B lost ~$14 of "spread leakage" to the MM via 15 successful placeOrder fills.
- **Lesson**: Wash trading on dreamDEX is impossible by design — the deep MM ladder absorbs all crosses before any user-to-user matching.

### B recovery (97 SOMI + $0.63 USDso = $15.16 found)
- All "lost" $14 was actually in B's vault under the native sentinel — query bug, not real loss.
- Recovery: 4 txs from B → withdraw SOMI, withdraw USDso, send 96 SOMI to A, transfer $0.63 USDso to A.
- B post-sweep: dead wallet, ~1 SOMI dust for future safety.
- A post-sweep: gains $0.63 USDso + ~$14.50 worth of SOMI inventory (auto-converts to USDso through SELL legs).

### Current strategy — single-wallet aggressive burst
- A's IOC burst via Flask `/manual` with `skip_sim: true` is the only volume engine.
- New leg size: sized to min(USDso_wallet, SOMI_value) after sweep — maximize per-tx volume to minimize gas/$1k.
- Burst script: `/tmp/ultra_burst3.sh` (skip_sim=true variant). Older `ultra_burst2.sh` is sim-gated, do not use.
- Target: defend #2 vs trader-4 (gap ~$420), don't bother chasing trader-3 ($29.7k).

### Skip-sim patch — LIVE in container (lost on rebuild)
Three files patched in `dreamdex-agent` container at runtime:
- `/app/trading/dreamdex.py` — added `skip_sim: bool = False` param to `place_order`, bypasses `simulate_order_tx` when True.
- `/app/trading/manual.py` — threads `skip_sim` through `execute()`.
- `/app/server.py` — reads `skip_sim` from `/manual` request body.
- ⚠️ **These edits are in the writable container layer.** A `docker compose up --force-recreate` will wipe them. Pre-merge into source files in `~/dreamdex-agent/` before any rebuild.

### Open follow-ups
1. Fix `recovery_audit.py` to use correct native sentinel (hardcoded `address(0)` is wrong).
2. Send doc-improvement feedback to dreamDEX team about the non-standard native sentinel.
3. Persist the `skip_sim` patches into source files before container rebuild.
4. Monitor A's burst until USDso runs dry or we widen lead vs trader-4 to safe margin.
5. Post-contest: rotate both private keys, sweep all from A to user's main wallet.

## Session Notes — 2026-05-28 (session 4 — wash trade setup, NOW SUPERSEDED)

**Live state at session end:** rank **#2**, volume **~$12,540**, gap to #3 (trader-4) now only **~$65** (closing fast). Wallet A `0xF4c8…2b905`: ~$13 USDso. Wallet B `0x75716940…A638`: ~$1 USDso wallet + $14 deposited to vault. Real loss from $50: ~$22.

### What's running RIGHT NOW on server (irony@100.80.130.21)
- **Burst A:** `nohup bash /tmp/ultra_burst2.sh 999 6.5 > /tmp/burst_a.log 2>&1` (PID ~3367226) — confirmed generating fills via Flask `/manual`
- **Maker B:** `nohup docker exec -e WALLET_B_PRIVATE_KEY=... dreamdex-agent python3 /tmp/maker_bot_b.py > /tmp/maker_b.log 2>&1` (PID ~3365520) — posts vault-funded `normalOrder` LIMIT BUY at bid every 20s
- Docker container `dreamdex-agent` is UP (main agent PAUSED, micro agent running 90s SELL loops)

### Key discoveries this session (hard-won)
- **All pool fees are ZERO** (makerBps=0, takerBps=0). Cycling costs gas only (~$0.008/fill).
- **normalOrder only works with `fundingSource: "vault"`** — wallet-funded resting orders rejected with `invalid_fund_source`.
- **normalOrder REST payload:** `{"side","type":"limit","orderType":"normalOrder","price","amount"(USDso $),"fundingSource":"vault"}` — field is `amount` not `quantity`, field is `fundingSource` not `funding`.
- **All Somnia ERC20 txs need 2M gas** — 100k and 500k both hit the ceiling (63/64 rule recursion). Same for normalOrder placement tx (~234k actual, needs 2M limit). USDso transfer also needed 2M (71k actual).
- **`getWithdrawableBalance` always returns 0 for B** — this is a query bug on our side, the vault IS funded (deposit tx confirmed status=1, normalOrder placement succeeds).
- **`deposit(token, amount)` IS the right ABI** — same as what agent uses in `vault_deposit`. Deposits succeed.
- **B's SELL side as maker is impossible via REST** — `normalOrder` + `fundingSource:vault` only works for BUY (need vault USDso). SELL maker would need vault SOMI (native), which is stuck (0x734b5f70 bug).
- **So B only provides BUY-side liquidity** — A's IOC SELL fills against B. A's IOC BUY hits real book (559 SOMI at ask, plenty).

### maker_bot_b.py — `/tmp/maker_bot_b.py` (also in container `/tmp/`)
Full self-contained script. Key points:
- Patches `MAINNET_PRIVATE_KEY` env before config import BUT `load_dotenv()` in config overwrites it → fixed by writing own SIWE auth from scratch without using DreamDEX class
- Deposits vault USDso automatically on startup if vault < $1
- Posts LIMIT BUY every 20s at current bid, $6.50, vault-funded
- Logs to `/tmp/maker_b.log` on server

### Open follow-ups
1. **Watch burst_a.log + maker_b.log** — confirm fills accumulating, hold #2 vs trader-4
2. **Add B SELL side** — could use `placeTakerOrderWithoutVault` directly (IOC from B) as a maker-like pattern, but B not on leaderboard so volume only counts for A
3. **direct_burst.py** — still broken (sim-broadcast race). Fix: use 2M gas + `ask+10*tick` slippage, no sim gate. Would give 3-5x throughput.
4. **Post-contest teardown** — ROTATE both private keys. `RUNBOOK.md § Post-contest teardown`.

## Session Notes — 2026-05-27 evening (PREVIOUS)

**Live state:** rank **#2/11**, volume **$3633**, gap to #1 = $268. Wallet A `0xF4c8…2b905`: USDso $44.11, WETH dust 0.0003 ($0.62, sub-minQty), USDC.e dust 0.91 ($0.91, sub-minQty $1), SOMI 2.06 (gas). Real PnL **~−$4** from $50 start. Agent **paused**; recent volume came from manual `/manual` bursts (`/tmp/ultra_burst2.sh` on server).

**THE KEY INSIGHT (don't re-learn):** Current code sends `order_type="market"` (IOC) on both legs → BUY at ask+5 ticks + SELL at bid−3 ticks = **guaranteed −0.49% per round-trip, structural.** Cannot profit by tuning prompt/sizing. trader-6 at $2300 vol / −$0.21 PnL is break-even → they post `normalOrder` (resting maker) — **the only path to actual profit.** Dashboard's "Real PnL" mid-marks inventory and lies (showed +$5.99 once when we actually had unmatched WETH); Auditor must use realized USDso delta only.

**4-phase plan (approved):**
1. **Consolidate Wallet A** — done modulo dust. Skip remaining sub-minQty dust.
2. **Wallet B setup** — generate EOA on **server** (`python -c "from eth_account import Account; a=Account.create(); print(a.address, a.key.hex())"`), save as `PROFIT_PRIVATE_KEY` in `~/dreamdex-agent/.env`, transfer $20 USDso + 0.5 SOMI gas from A → B, write `WALLETS.md` documenting both addresses + roles for contest verification.
3. **Two lanes** — Wallet A: server-side bash loop of `/tmp/ultra_burst2.sh 50 5.0` indefinitely ($5/leg, no LLM, drip volume). Wallet B: new `profit_lane.py`, **LLM-driven** (user picked), posts `normalOrder` at bid/ask, cancels stale after ~5min.
4. **Hive architecture** (parallel) — roles: Scout (deterministic), Pricer (ranks pair×size×side EV), Strategist (only big-brain LLM), Executors (each own EOA, specialised), Auditor (realized PnL only). Shared sqlite tables: `market_state`, `opportunities`, `commands`, `trades`, `empirical_model`. **Don't use LangGraph/CrewAI** — wrong shape; build Python classes + sqlite ourselves. Wallet A = volume Executor, Wallet B = profit Executor. `Brain.decide()` abstracts LLM → Somnia decentralised LLM is a one-line swap. **Contest = data-collection run for the long-term project.**

**Uncommitted working tree:**
- `monitor/db.py` — `fill_stats(since_hours, agent_name)` and `consecutive_fail_streak(pair)` helpers added; brain not yet wired to consume.
- `agent/brain.py` — half-built "ultra" mode removed (user explicit: no mechanical bypass; brain learns from data).
- `agent/agent.py` — `_liquidate_inventory()` chunked to `self.max_trade` per call (was selling 145 SOMI in one IOC, sim-reverted every tick).

**Empirical fill-rate priors (from 600+ trades):** $5 leg ~80%, $8 ~65%, $12 ~50%, $15 ~40%. Smaller IOC = more reliable. Don't exceed $8 without specific reason.

**Open follow-ups (next session, in order):**
1. `git add -A && git commit` the three in-flight files.
2. Phase 2: keygen + fund Wallet B + write `WALLETS.md`.
3. Phase 3: `profit_lane.py` with `normalOrder` support.
4. Phase 4: hive scaffolding (roles + shared sqlite tables).

## Hot gotchas (already encoded into the code — don't re-discover)

- **Mainnet refuses to start without `FLASK_API_KEY` AND `OPENAI_KEY`.** Either set both real values OR set `OPENAI_KEY=disable` (which forces hold-only operation, no blind real-money trades).
- **Capital floor is `AGENT_STOP_BELOW = $22 USDso`.** Below this the agent holds and the watch shows BAL LOW. Top up USDso to unblock.
- **WBTC = 8 decimals, USDC.e = 6, others = 18.** Hardcoded in `config.py` MARKETS. Don't trust an API refresh to override these silently — `refresh_market_params` keeps config when the API disagrees (M5 fix).
- **Vault-delta proves a fill, NOT log presence.** SOMI native pool returns base+0 because we can't read native vault — that's expected; quote-delta still proves it.
- **Cloudflare tunnel needs Flask on the host's network namespace** (we use `network_mode: host`). Bridged port-publish (`127.0.0.1:5001:5001`) was blocked by something on this Ubuntu 24.04 box.

## Mainnet flip sequence (when contest starts)

1. Move 50 USDso to `0xF4c825F3C2970153d78B407CF190861dd4E2b905` (mainnet)
2. `ssh user@<SERVER_HOST>`
3. `sed -i 's/^DREAMDEX_ENV=.*/DREAMDEX_ENV=mainnet/' ~/dreamdex-agent/.env`
4. `cd ~/dreamdex-agent && docker compose restart agent`
5. `docker compose logs --tail=30 agent` — confirm `MAINNET mode` banner
6. Unpause via watch SELECT or via the toggle curl

## Post-contest teardown (do all of these)

See `RUNBOOK.md` § Post-contest teardown for the canonical sequence. TL;DR:

```bash
ssh user@<SERVER_HOST>
cd ~/dreamdex-agent
docker compose down
docker rmi dreamdex-agent:latest
shred -u .env                       # wipe key
cd ~ && rm -rf ~/dreamdex-agent
```

Also: delete the `<TUNNEL_HOST>` published-app in the Cloudflare dashboard. Drain remaining funds from the mainnet wallet to your main address.
