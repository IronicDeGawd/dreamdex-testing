# DreamDEX Round-3 Findings & Engine Playbook

> Written at end of R3 (2026-07-07). Companion to `context/research/dreamdex.md`
> (the mechanics fact sheet). This file = what we learned RUNNING it, plus the
> two final engines and how to use them. Read before the next round.

## TL;DR — the two engines

| Engine | File | Speed | When to use |
|--------|------|-------|-------------|
| **Volume climb** | `backend/volume_climb.py` + `cheap.sh` | ~30s/trip | Default. Safe, cost-aware, self-healing. Use for steady volume and to *hold* a lead. |
| **Direct burst** | `backend/direct_burst.py` + `direct_burst.sh` | ~15s/trip (~2x) | Max throughput when you need to close a gap fast AND the book is liquid. |

Both: WETH:USDso taker round-trips (buy at ask, sell at bid), end flat, ~$0.11/1k
toll. Never run both at once (same wallet → nonce collision).

Plan that worked in R3: **burst to build a cushion over the rival, then switch to
climb to hold it** at minimal risk.

## THE key finding — direct contract order placement

`volume_climb` places orders via REST (`POST /v0/markets/{sym}/orders` → unsigned
tx → sign → broadcast). That server round-trip is ~7-8s **per leg** — the single
biggest latency. `direct_burst` builds the same calldata locally and broadcasts
straight to the pool, halving trip time.

- **Working function: `placeOrder`, selector `0x4e978373`**, on the pool contract
  (`0xa936da11B57b50A344e1293AAaE5232885ea2bDE` for WETH:USDso), funded from wallet.
- The archived R2 script (`archive/aware_burst.py`) called
  **`placeTakerOrderWithoutVault`** — a *different* function expecting
  vault-deposited funds → wallet-funded orders reverted (`status=0, moved=0`).
  **The whole R2 "direct burst failed" mystery was one function name.**
- `placeOrder` ABI (9 args, reverse-engineered from the API's calldata):
  `placeOrder(bool isBid, uint64 userData, uint256 price, uint256 quantity,
   uint64 expireNs, uint8 orderType, uint8 selfMatch, address builder,
   uint96 builderFee)`
  - `price = human_price * 10**quoteDec` — **use `Decimal`, not float.**
    `float(1779.66)*1e18` loses precision and the order mis-encodes.
  - `quantity = qty * 10**baseDec`, snapped to `lot` (from `getPoolParams`).
  - `orderType=2` (IOC), `selfMatch=0`, `builder=0x0`, `fee=0`, `value=0`.
- **Encoding self-check** (in `direct_burst.py`): at startup we build our calldata
  for a sample order and assert it matches the API's byte-for-byte, else abort.
  This is what caught the float bug before it ever traded. Keep it.

## Bugs hit in R3 and their fixes (all in the committed code)

1. **RPC breaker tripping constantly** — single Somnia infra node flaky under load.
   → `trading/wallet.py` `FailoverHTTPProvider` rotates across
   `[infra, publicnode, ankr]` on transport errors. `config.SOMNIA_RPCS`,
   override via `SOMNIA_RPC_FALLBACKS`. NOTE: does NOT help a DNS blip (whole
   container can't resolve) or a genuine multi-node outage — those still stop.
2. **Sell-fail hard-stop on thin book** — a couple of failed sells killed the whole
   run. → `volume_climb.py` patient pause-and-retry (`CLIMB_RESID_RETRIES`,
   `CLIMB_RESID_WAIT_S`) instead of stopping.
3. **stop() left a bag** — the emergency flatten only tried 3× with fixed slip and
   gave up in a thin moment. → now reuses the same patient budget
   (`RESID_RETRIES`/`RESID_WAIT_S`).
4. **Fixed leg size reverts as capital shrinks** — a $50 leg pre-reverts once free
   USDso drops under ~$50. → `cheap.sh` takes leg as 3rd arg; **always keep leg
   well BELOW free USDso.** Rule of thumb: leg ≤ 0.8 × free USDso.
5. **Cost ceiling not tunable per run** — → `cheap.sh` 4th arg = cost ceiling.
6. **Direct burst spun on no-fills in a wide book** — no spread gate. → `direct_burst.py`
   has `DP_SPREAD_GATE_PCT` (pause when spread wide) + consecutive-no-fill breaker.
7. **Direct burst bag from fast settle-read** — read WETH balance before async
   settlement credited it → misread fill → skipped sell → bag. → `DP_SETTLE_S`
   default 1.5s + re-check, and `sell_all_weth()` at the top of every loop +
   shutdown so a misread can NEVER accumulate a bag.

## Measured economics (R3 week 2, WETH:USDso)

- **Toll ≈ $0.11 per 1k** raw volume when the book is liquid (spread ~0.02%).
  Sometimes negative (market pays us) in choppy two-way flow.
- **Fill efficiency ~98-99%** (our own de-duped logs). Leaderboard `fills/txCount`
  reads >100% because one tx can match multiple resting orders — inflated, not a
  real "success rate." We were 2-3x more efficient than rivals ($ per volume).
- **Gas ≈ 0.035 SOMI per 1k** volume. SOMI ≈ $0.10-0.12.
- **Trip time:** climb ~30s, direct burst ~15s. Book dislocations (spread blowing
  to 1-4%) happen intermittently and stall both — climb pauses, burst now gates.
- Starting capital was $150 USDso + 50 SOMI (team test funds). ~$150 of toll →
  ~1.35M reachable raw at that rate, capital-limited not time-limited.

## Operational runbook

Server: `irony@100.80.130.21`, code at `~/dreamdex-r3/backend`, baked into the
Docker image (`build: .`). **Editing engine code = scp to host THEN
`docker compose build agent`** or the image is stale.

- SSH: `ssh -o ControlPath=none -o ConnectTimeout=30 -o BatchMode=yes irony@100.80.130.21 '<cmd>'`
- Launchers run detached (survive SSH drop); `--rm` wipes logs on exit → confirm
  result on-chain + leaderboard, not the container.
- **Never `git add -A`.** Secrets (`.env`, private key) only in server `.env`
  (env_file). Repo is public.

Launch (from `~/dreamdex-r3/backend`):
- Steady: `nohup ./cheap.sh <target> <bleed_cap> <leg> <cost_ceil> > /tmp/x.log 2>&1 &`
- Burst:  `nohup ./direct_burst.sh <target> <leg> <slip> <spread_gate> > /tmp/x.log 2>&1 &`

Gas top-up (buy SOMI, native pair needs gas ≥5M via `SOMI_BUY_GAS_LIMIT`):
`dex.place_order("SOMI:USDso","buy",<somi_qty>,order_type="ioc",limit_price=<ask*1.01>,funding="wallet",gas_min=config.SOMI_BUY_GAS_LIMIT)`
— SOMI lot size is **0.01** (snap the qty).

End-of-round liquidation (scoring rewards free USDso; inventory is poison):
1. Stop engine. 2. `sell_all_weth` / flatten any WETH. 3. Sell SOMI → USDso keeping
~0.6-1 SOMI for gas (snap to 0.01 lot). End fully in USDso, flat.

Leaderboard: live R3 board is the `-new` URL
(`https://dreamdex-leaderboard-new.vercel.app/api/leaderboard`), JSON under key
`traders`. The `usdsoBalance` field is a **stale snapshot** — for real capital read
on-chain (wallet ERC20 `balanceOf` + native + vault `getWithdrawableBalance`).

## Lessons (carry forward)

- **Trust on-chain reads from OUR wallet, not one-off scripts or leaderboard
  balance.** A one-off balance script hit a stale RPC replica and under-reported
  our USDso 3x ($10 vs $30) — nearly caused a bad call mid-race.
- **Leg size must track free USDso down.** As capital shrinks, shrink the leg or
  every buy pre-reverts.
- **Speed lever is the API round-trip**, not our sleeps. Direct `placeOrder`
  bypasses it for ~2x. Everything else (settle, poll) is secondary.
- **Never swap the order path live under time pressure** without the encoding
  self-check + bag-proof loop. The one time we did (last-minute), it left a bag.
- **Effective volume falls as you push raw** (multiplier = freeUSDso/150). If a
  round scores effective, there's a peak (~400k eff at ~790k raw); if it scores
  raw only (R3 wk2), just push raw and end flat.
