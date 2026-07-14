# DreamDEX — Verified Fact Sheet (Round 2 → carried into Round 3)

> Authoritative reference for DreamDEX mechanics, economics, and contest tactics.
> Every number here was measured/decoded on-chain, not assumed.
> Last verified: 2026-06-22 (end of Round 2). Chain: Somnia mainnet, chainId 5031,
> RPC `https://api.infra.mainnet.somnia.network/`.
> ⚠️ **Round 3 caveat:** these were verified in R2. Re-test the load-bearing ones
> (native-pool revert, fee=0, deprecations) before relying on them in R3 — devs
> may have changed mechanics.

> 🏆 **ROUND 2 RESULT: WON #1.** Final volume 1,342,945, lead +33,177 over t3
> (crossed 1,000,000). The earlier "100-USDso hard cap / 1M unreachable /
> maker-only endurance" thesis was **WRONG** — we won with a fast **taker burst**.
> Capital is dev-allocated (~100 USDso, fixed) → the contest is **most volume per
> fixed capital** (efficiency). We finished PnL −93, the most efficient of the
> top 3.

> ⚠️ **ENGINE STATUS (end of R2): `placeTakerOrderWithoutVault` is DEPRECATED
> (dev-confirmed) — use `placeOrder` as-is.** It reverted unconditionally
> (empty `0x`, every size/slip/orderType, funds+allowance+book all fine).
> `aware_burst.py` RETIRED. **Winning engine = `aware_burst_vault.py`**, and the
> decisive change was bypassing the HTTP order-build API — see §2a. The no-bleed
> maker (`profit_maker.py`) wedge was later FIXED via `PROFIT_FUNDING=wallet`
> (both legs read the same balance location).

---

## 1. What DreamDEX is (and why it's cheap to trade)
- On-chain **CLOB** (central limit order book), one pool contract per pair.
- **Pool fees are ZERO.** Verified `getPoolParams()` → `makerBpsTimes1k=0`,
  `takerBpsTimes1k=0` on WETH:USDso. So the ONLY cost of a trade is:
  **(a) the spread you cross** (taker) and **(b) native SOMI gas.** A maker who
  never crosses pays neither — see §6.
- Contest leaderboard tracks **`volumeUsdso` per registered trader address.**
  We are **trader-9, wallet H = `0xF4c825F3C2970153d78B407CF190861dd4E2b905`.**
  Volume is credited to whichever wallet places the order — both sides of a
  fill get volume credit (so a maker fill counts too).

## 2. Order mechanics (hard-won, enforced in code)
- **`getPoolParams()`** returns a flat 7-tuple:
  `(baseToken, quoteToken, makerBpsTimes1k, takerBpsTimes1k, tick, minQty, lot)`.
  Differs per pair — **never hard-code.** WETH:USDso (2026-06-16):
  `tick=0.01, minQty=0.001 WETH, lot=0.0001 WETH`.
- **`placeTakerOrderWithoutVault`** — taker; delivers fills straight to the
  WALLET (not vault). `priceRaw` MUST cross the book (BUY = ask+slip,
  SELL = bid−slip). This is OUR engine's method (and t3's).
- **`placeOrder`** (selector `0x4e978373`, signature
  `placeOrder(bool,uint64,uint256,uint256,uint64,uint8,uint8,address,uint96)`)
  — VAULT-based order; capital sits in the pool vault. t2 and t4 use this.
- **PostOnly** order type → only ever JOINS the book, never crosses. Our maker
  uses this. On this pool a **PostOnly SELL fills fine; a taker SELL of a native
  SOMI base silently rejects** (native-SOMI maker SELL is impossible — use an
  ERC20 base like WETH).
- `expireTimestampNs=0` is **silently rejected** → use `(now+3600)*1e9`.
- `selfMatchingOption=1` (CancelMaker).
- **Somnia gas is non-standard** → for ERC20 ops set gas limit `2,000,000`.
- **native SOMI vault sentinel = `0x28f34DeFd2b4CB48d9eE6d89f2Be4Bc601694c00`**
  (NOT `address(0)`).
- **Fill detection is broken via events:** `OrderPlaced` emits `filled=0` even on
  real fills, and `getOwnOpenOrders` returns empty. → **Detect fills by balance
  delta** (compare base/quote before vs after). This is why both engines
  verify-by-delta, not by event.
- **`pgrep` is ABSENT in the container.** Detect a running engine via
  `/proc/*/cmdline`, **filtered to `comm=python*`** — a naive grep matches its
  OWN command string and false-counts. (Guarded one-liner in handover.md.)

## 2a. 🌟 The throughput win: skip the HTTP order-build API (direct RPC)
- **The bottleneck was NOT the chain — it was the DreamDEX order-build HTTP API.**
  The SDK's `dex.place_order(...)` calls `/v0/markets/{sym}/orders` to build the
  calldata server-side; that added **~14 s per leg**. The chain itself mines a
  `placeOrder` in **~1 s**. So an engine going through the API tops out ~3k vol/hr.
- **Fix (the move that won R2):** build the `placeOrder` calldata **locally** and
  broadcast direct over RPC — no HTTP build call. Throughput **3k → ~10k vol/hr**.
  Cutting `time.sleep`s and adding gas tips alone did NOT help (proved the chain
  was never the limit); removing the API call did.
- Implemented in `aware_burst_vault.py` `trade()`, toggled by env **`BURST_CONFIRMED`**:
  unset = **FAST** (local calldata + direct `send_raw_transaction`, ~10k/hr);
  `=1` = **CONFIRMED** (old SDK API path + balance-verify, ~3k/hr, lower gas).
- FAST `trade()` core (selector `0x4e978373`, orderType 2 = IOC, qty/price raw =
  human×1e18):
  ```python
  bf = w3.eth.get_block("latest").get("baseFeePerGas") or int(6e9)
  tx = c.functions.placeOrder(bool(is_bid), 0, int(price_raw), int(qty_raw),
          expire, 2, 0, "0x0000000000000000000000000000000000000000", 0
      ).build_transaction({"from": acct.address, "nonce": n, "value": 0,
          "gas": 3000000, "chainId": CHAIN_ID,
          "maxFeePerGas": int(bf*2 + 5_000_000_000),
          "maxPriorityFeePerGas": 5_000_000_000})
  h = w3.eth.send_raw_transaction(w3.eth.account.sign_transaction(tx, KEY).raw_transaction)
  ```
- **EIP-1559 priority-tip trap:** Somnia `max_priority_fee` defaults to **0**, so
  zero-tip txs sit unmined in the mempool. **Always add a ~5 gwei priority tip.**
  Fixed globally in `trading/wallet.py _gas_fields`:
  `priority = max(self.w3.eth.max_priority_fee, 5_000_000_000)` (and legacy path
  `gasPrice = gas_price + 5_000_000_000`).

## 3. Reading a trader's REAL capital (leaderboard lies)
- **Leaderboard `usdsoBalance` is UNRELIABLE** — it's free wallet USDso at one
  cycle-phase snapshot, swings wildly ($0↔$26) just from holding WETH vs USDso
  mid-round. It misses vault funds and open orders. Fooled the analysis twice.
- **True capital = on-chain:** native SOMI (gas) + wallet `balanceOf(USDso)` +
  `balanceOf(WETH)×px` + **vault `getWithdrawableBalance(user, token)`** on the
  pool + any open orders. Tools: `probe_realbal.py` (capital+vault),
  `sweep_opponents.py` (all tokens + tx count + open orders).
- Observed: **all vaults empty; nobody holds resting orders** (everyone runs
  pure IOC takers). **No external top-ups** were ever detected via funding scan —
  apparent "refuels" were cycle-phase artifacts.

## 4. The economics: Ceiling vs Rate (DON'T confuse them)
- **Ceiling** = total volume reachable before capital runs out
  = `capital ÷ toll_per_volume` ≈ **`capital × ~10k` volume.**
  INDEPENDENT of leg size — big or small legs extract the same total before the
  money is gone. This sets WHO CAN WIN.
- **Rate** = `legs/sec × leg_size`, and `leg_size ∝ remaining capital`
  → the rate **DECAYS as capital bleeds, for EVERYONE.** NEVER assume an opponent
  holds a fixed rate while you decay — they slow too. (Verified: t6 decayed
  ~46k/hr → ~15k/hr as its capital dropped.) Symmetric decay → a volume lead
  tends to hold.
- **Toll ≈ $0.09–0.10 per $1,000 of volume** (taker, slip=50). Cross-checked
  against the live 2.05 bps spread: a round trip crosses the full spread
  (~2 bps of notional) spread over 2× volume ≈ $0.10/$1k. Consistent.
- **vol per SOMI ≈ 11.7k** at slip=50. **Gas burn ≈ 0.5–0.6 SOMI / 30 min.**
- **Bigger legs = more volume per unit gas** (gas/tx ≈ constant). Our leg =
  `min(target $44, free-USDso-affordable)` → low free USDso caps the leg small
  → slow rate. More capital → bigger legs → faster deploy AND higher ceiling.

## 5. Slippage & fill rate (the big lever)
- **Slip on an IOC taker is FREE insurance, not a cost.** An IOC fills at the
  BOOK TOUCH, not at your limit — so a wide limit (slip) costs **zero extra
  toll** as long as your leg ≤ top-of-book depth; it only PREVENTS MISSES from
  price drift between your stale price-read and the tx landing.
- **Measured fill rates:**
  - **slip=6 ($0.06) → ~64% fill.** Tight slip on a ≤2s-stale price buffer:
    on fast-moving WETH the buy limit missed → 36% retries. Self-inflicted.
  - **slip=50 ($0.50) → ~100% fill**, SAME toll (~$0.09/1k), **~2× throughput,
    ~40% less gas.** Adopted permanently. (A/B tested with a 2-SOMI grant.)
- **Cons of slip appear ONLY if** your leg > top-of-book depth (book-walking) or
  the book is thin / flash-spiking. Live depth was ~$67–94 vs our $44 legs → no
  walking. → Use **MODERATE slip (50), not infinite.**
- **Opponents' fill methods (decoded):** t3 + us = `placeTakerOrderWithoutWault`;
  t3 crosses with a HUGE slip (~7900 bps = market-style, never misses). t2 + t4
  = `placeOrder` vault-based, fixed ~$20 legs, IOC, fill in-tx. Everyone hits
  ~100% by crossing aggressively or polling fast — our old 64% was purely the
  tight slip + verify latency, now fixed.

## 6. Two ways to make volume
| | **Taker burst** (`aware_burst.py`) | **No-bleed maker** (`profit_maker.py`) |
|---|---|---|
| Method | `placeTakerOrderWithoutVault`, IOC, slip=50 | PostOnly, joins the book |
| Crosses spread? | Yes → pays toll ~$0.09/1k | No → **earns** the spread |
| Capital bleed | Yes, ~$0.09 per $1k volume | **None** (SELL ≥ buy+margin, holds inventory forever rather than sell at a loss) |
| Speed | Fast (instant fills) | Slower (waits to be hit) |
| Limited by | Capital (bleeds out) | Time (runs forever on fixed capital) |
| Gas reserve | `BURST_SOMI_GAS_RESERVE` (0.05–0.2) | `PROFIT_GAS_RESERVE_SOMI` 0.3 |
| Best for | sprint / converting fresh capital fast | endurance / overnight base |

- **Both run on wallet H** so both count for the contest — but **NOT at the same
  time** (same wallet, same nonce, same capital → they'd fight). Hybrid =
  time-multiplex: maker as the always-on base, taker burst only on a capital
  refill or final sprint.
- Maker invariants (enforced in code): PostOnly only; BUY=best bid; SELL=
  `max(ask, buy+MARGIN_TICKS×tick)`; re-quote only after `REQUOTE_S` AND book
  drifted > `DRIFT_TICKS`; gas-reserve floor; no stop-loss ever.
- **Maker vault-inventory wedge — FIXED.** Original bug: it bought vault-funded
  but the fill landed in the WALLET, then looped SELL-ing vault-funded forever
  (`ERC20: transfer amount exceeds balance`). Fix = env **`PROFIT_FUNDING=wallet`**
  so BOTH legs use the same balance location; full cycle then confirmed live.
- **Devs BANNED the stablecoin pair** (USDC.e:USDso) → trade **WETH:USDso.**

## 7. Robustness gaps (known)
- **DNS-crash gap:** `aware_burst.py`'s main loop doesn't wrap RPC calls in
  try/except → a transient `NameResolutionError` crashes the process; the
  keepalive recovers it in ~3 min (only if SOMI is above its relaunch floor).
  Fix later: try/except-retry around main-loop RPC calls → zero downtime.
- **SSH to the server is rate-limited** on rapid reconnects → use
  `-o ControlPath=none`, SHORT commands, retry up to 5×. Use the leaderboard
  `curl` instead of SSH where possible.

## 7a. ⛽ Gas (SOMI) — self-funding IS possible (needs ≥5M gas) ✅ CORRECTED
> **This overturns the earlier "self-conversion is DEAD" finding.** The native
> SOMI pool was never broken — we were starving the tx of gas. (Dev `emrey.somi`,
> 2026-06-22; now in DreamDEX docs.)
- **`0x782b2567` = `InsufficientGasForPayout(uint256 gasLeft)`** — a **deliberate,
  documented guard**, NOT a contract bug and NOT order-specific. The arg is the
  remaining gas at the native-payout check. Our `placeOrder` on SOMI:USDso
  reverted only because the **3,000,000 gas limit was too low** for the native-SOMI
  payout path under Somnia's gas model.
- **Native-base BUY IS supported and our call shape was correct:** `isBid=true`,
  `msg.value=0` is right for a BUY (input is the ERC-20 quote, i.e. USDso). No
  special flag/orderType. The ONLY thing missing was the gas limit.
- **Fix: set the broadcast gas limit to ≥ ~5,000,000.** ~256k is spent reaching
  the guard, which needs ~3.6M headroom-at-the-check; 5M clears it comfortably.
  The 2.1M forwarded to the EOA `receive()` is mostly unused, so **actual gasUsed
  stays low** — the limit just has to PASS the headroom check, it isn't all burned.
- **Why our `eth_call` sim lied:** the sim was run with a different (higher/default)
  gas than the 3M we broadcast, so it passed while the mined tx reverted. **Make
  the sim honest: simulate with the SAME gas limit you will broadcast.** Do NOT
  stop sim-gating — fix the gas mismatch. Not related to `gasSponsored`.
- ⟹ **Gas CAN be self-funded:** USDso → SOMI via `placeOrder` on the SOMI:USDso
  native pool, BUY, `gas≥5,000,000`. External SOMI top-ups are no longer the only
  source. (Drip in small slices to look organic — see [[somi-conversion-drip-not-blob]].)
- **LI.FI** WETH→SOMI quoted via tool "fly" but its swap reverted in R2 — it routes
  through the same native pool, so it likely failed for the SAME gas reason; the
  direct `placeOrder@5M` path above is the proven one, prefer it.
- **Moving USDso/WETH between wallets needs `gas=2,000,000`** (Somnia ERC20 rule).
  A transfer at the default ~200k SILENTLY reverts and can strand funds in a
  wallet that then has no SOMI left to retry.
- **R3 action:** confirm `placeOrder@5M` on the native pool still works, then wire
  a self-gas top-up into the engine (convert a slice of USDso→SOMI when SOMI dips
  below a floor) so the bot never stalls on gas.

## 8. Deploy / inspect cheatsheet
- Deploy a `.py`: `scp` to `/home/irony/dreamdex-agent/` THEN
  `docker cp <f> dreamdex-agent:/app/<f>` (`/app` ≠ the host dir).
- Engine log lives INSIDE the container: `docker exec dreamdex-agent cat /tmp/aware.log`
  (taker) / `/tmp/maker.log` (maker).
- Leaderboard (no SSH): `curl https://dreamdex-leaderboard-super-cool.vercel.app/api/leaderboard`
  (JSON: `traders[]` with `handle, address, volumeUsdso, usdsoBalance, pnl`).
- Key is read from container env (compose `env_file`) → **never** `docker exec -e KEY=`.
