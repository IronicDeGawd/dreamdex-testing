# New Engine Mode: "atomic" — EIP-7702 atomic buy+sell round-trips

## Context
The R4 taker does a round-trip as two transactions (buy → wait → sell), which costs ~$0.04/1k in inter-leg price drift, risks a stuck bag if the sell fails, and needs two confirmations per trip. EIP-7702 lets the wallet delegate to a tiny contract that does **buy → measure → sell in ONE transaction**: no drift, ~2× throughput, zero buy-side bag risk, and an on-chain cost ceiling. Volume attribution is preserved because the EOA stays `msg.sender`.

All the hard unknowns are already resolved: 7702 **works on Somnia mainnet** (type-4 tx, ~1.19M gas floor — verified live from R3), a **competitor (trader-4, `0x99e9…`) runs exactly this in R4 right now** (delegate `0x9119…73d2`, selector `0x799b4396`, `placeOrder` twice atomically), and our `direct_burst.py` already encodes the same `placeOrder` selector (`0x4e978373`). eth-account 0.13.4 / web3 7.6.0 support type-4 signing.

**This session: build + validate through a $5 R3 mainnet smoke, then STOP** and review real gas/cost before any R4 wiring. **Tx style: install delegation once, then trade with cheap type-2 self-calls** (type-4-per-trip is the proven fallback if type-2→delegated-EOA misbehaves).

## The make-or-break gate (Phase 3)
Does `placeOrder` settle **synchronously within the tx**, so `balanceOf` between the two `placeOrder` calls reflects the buy fill? The competitor's live delegate is strong evidence YES (`direct_burst.py`'s past "async settlement" pain was cross-tx RPC lag, not in-tx). If NO, `require(got>0)` reverts every trip and the mode is dead — decide go/no-go at the smoke.

## Files (new + touched)
- **NEW** `backend/contracts/RoundTrip7702.sol` — the delegate contract
- **NEW** `backend/trading/delegate.py` — compiled bytecode constant + `encode_roundtrip()`
- **NEW** `backend/atomic_round.py` — the engine script (clone of `direct_burst.py`)
- **NEW** `backend/tools/deploy_delegate.py`, `backend/tools/clear_delegation.py`
- `backend/trading/wallet.py` — `sign_authorization`, `send_type4_tx`, `install_delegation`, `clear_delegation`, `delegation_target`
- `backend/config.py` — `ROUNDTRIP_DELEGATE` per-ENV constant (added after deploy)
- (Phase 4, NOT this session) `engine_manager.py`, `app.py`, `static/index.html`

## Phase 0 — Delegate contract + bytecode constant
`RoundTrip7702.sol` (solc 0.8.20, ~50 lines), one function:
`roundTrip(address base, address quote, address pool, uint256 buyPrice, uint256 sellPrice, uint256 qty, uint64 expireNs, uint256 maxTollQuote, uint256 lot)`
- **`require(msg.sender == address(this), "self")`** — only a tx signed by our own key can self-call, so the rival's griefing vector (their delegate has no such guard) is closed.
- Conditional MAX-approve `pool` for base+quote (skip if allowance already covers — mirrors the volume_climb allowance lesson: approve is absolute, keep it MAX).
- `q0 = quote.balanceOf(this)`; `b0 = base.balanceOf(this)`.
- `placeOrder(true, 0, buyPrice, qty, expireNs, 2, 0, address(0), 0)` — IOC buy. Copy the exact 9-arg signature from `direct_burst.py:82-90`.
- `got = base.balanceOf(this) - b0; require(got > 0, "nofill")`.
- `placeOrder(false, 0, sellPrice, (got/lot)*lot, expireNs, 2, 0, address(0), 0)` — IOC sell, **lot-snapped** (don't sell raw delta).
- `require(q0 - quote.balanceOf(this) <= maxTollQuote, "toll")` — atomic cost ceiling.
- `emit Trip(uint256 spentQuote, uint256 gotBase, uint256 soldBase)` — receipt-parsed for fill/volume; **do NOT revert on partial sell** (emit residual, Python flattens).
- **`receive() external payable {}`** — once delegated, all transfers to the EOA hit this; without it, SOMI transfers to the wallet revert. No `selfdestruct`/`delegatecall`/arbitrary call.

Compile via docker (no new Python dep): `docker run --rm -v $PWD/backend/contracts:/src ethereum/solc:0.8.20 --bin --optimize --optimize-runs 200 /src/RoundTrip7702.sol`. Paste initcode into `delegate.py` as `ROUNDTRIP_INITCODE` + `ROUNDTRIP_ABI` + `encode_roundtrip(...)`.
**Verify:** the inner `placeOrder` calldata our contract builds matches `direct_burst.build_calldata()` byte-for-byte (same layout).

## Phase 1 — Wallet type-4 helpers + engine script
`SomniaWallet` (`wallet.py`, reuse `send_unsigned_tx` tx_fields at 143-155, `_gas_fields` 100, `reserve_nonce` 83):
- `sign_authorization(delegate, auth_nonce)` → `Account.sign_authorization({"chainId": self.chain_id, "address": delegate, "nonce": auth_nonce})`.
- `send_type4_tx(to, data, auth, gas=6_000_000)` — tx_fields + `"type":4, "authorizationList":[auth]`. **Critical: self-sponsored ⇒ `auth.nonce = tx.nonce + 1`** (auth validated after tx nonce increments); `reset_nonce()` after every install/clear so trade nonces don't race.
- `install_delegation(delegate)` / `clear_delegation()` (auth→`0x00…0`) / `delegation_target()` (parse `ef0100||addr` from `eth_getCode`).

`atomic_round.py` — clone `direct_burst.py`, reuse `px_raw` Decimal scaling (100), `getPoolParams()` lot/minQty (135), `verify_encoding()` (113, keeps SIWE via `dex._ensure_auth()` — still needed for `get_orderbook`), spread gate (180), `sell_all_base` residual flattener (147). Loop: gas floor → **`delegation_target()` check, reinstall if cleared** → book + spread gate → build `roundTrip` calldata → **`eth_call` preflight** (`w3.eth.call({from:EOA,to:EOA,data,gas:6M})` — catches no-fill/toll reverts for free, never broadcasts a doomed trip) → **type-2 self-call** send → parse `Trip` log from receipt → flatten residual if `soldBase < gotBase`. Env `ATOM_*` (PAIR, LEG_USD, TARGET, SLIP, SPREAD_GATE_PCT, MAX_TOLL_PER_1K, DELEGATE_ADDR, TX_MODE=type2|type4, SOMI_FLOOR, PRIVATE_KEY/ADDRESS overrides for R3). Print `[{trips}] … tot=${vol:.2f} USDso={..} somi={..}` + START/STOP lines matching the P&L regexes in `engine_manager.py:40-51`. `maxTollQuote = int(MAX_TOLL_PER_1K * (leg*2/1000) * 10**qdec)`.
**Verify:** `import atomic_round` dry-parse; offline calldata self-check.

## Phase 2 — Testnet (chain 50312) mechanics validation — FREE
Deployer `0xe21c…42dd` (116 STT gas). Deploy delegate → install delegation → confirm `eth_getCode == ef0100||addr`. **Prove the type-2 assumption early:** send a type-2 self-call with `roundTrip` calldata priced to fail — it must **revert with our `"nofill"`/`"toll"` string**, which proves the delegate code *executed* under a type-2 tx (the whole install-once bet). Confirm the self-call guard: same call from a second wallet must revert `"self"`. Then clear + reinstall.
Note: testnet USDso is 0.065 (< pool minQty) so a *funded* round-trip likely won't fund — testnet validates lifecycle + type-2 execution + guard only; real trading moves to Phase 3. Don't stall hunting a faucet.

## Phase 3 — Mainnet R3 smoke (`0xD84f…1E76`, key `~/secrets/wallet_r3.txt`) — GO/NO-GO
Deploy delegate on 5031, record address in `config.py`. **First check:** a normal type-2 `placeOrder` still succeeds from the delegated EOA (if the pool gates on `extcodesize(msg.sender)==0` the design dies — competitor says it doesn't, confirm anyway). Install delegation. Run `atomic_round.py ATOM_TARGET=20 ATOM_LEG_USD=5`: one $5 trip via preflight → on-chain. **Measure:** `gasUsed` per type-2 trip, realized $/1k vs `direct_burst` baseline (~$0.11–0.13/1k), confirm `Trip` decoded, USDso delta ≤ maxToll, WETH residual 0. Test the no-fill path (buyPrice below bid → preflight catches, never broadcasts). **Then STOP and report numbers.**

## Explicitly NOT this session (Phase 4, after go/no-go)
Mode wiring (`MODES += "atomic"`, `_atomic_env`, `_build_command` branch, dashboard 4th button, `app.py` `toll_cap` passthrough), docker image rebuild, R4 delegation install + launch. Left as a follow-up once the smoke proves the economics.

## Safety notes
- Leaving a wallet delegated between runs is **safe** given the self-call guard (only our key can trigger a trade); clearing is via the manual `clear_delegation.py`, not a shutdown hook (`docker rm -f` SIGKILLs before handlers run).
- **Never point any wallet at the competitor's unrestricted delegate `0x9119…73d2`** — anyone could drive trades through it.
- R4 wallet is untouched this session. All testing on R3/testnet.

## Risks (carry into implementation)
1. **In-tx settlement** (Phase 3 go/no-go — see above).
2. **Auth nonce off-by-one** — `auth.nonce = tx.nonce+1`, else delegation silently no-ops.
3. **Partial IOC sell** — contract must not revert; Python flattens residual. "Zero bag risk" = buy-side only.
4. **Reverts burn 1–3.4M gas, zero volume** — spread gate + eth_call preflight in front; track revert rate, widen gate if >2%.
5. **Gas: hardcode 6M** (competitor-proven); don't trust `eth_estimateGas` for delegated-EOA self-calls or the pool's `InsufficientGasForPayout` headroom (`wallet.py:116`).
6. **Type-2→delegated-EOA unproven** — Phase 2 proves it via the revert-string test; `ATOM_TX_MODE=type4` fallback ready.
7. **Float price mis-encode** — reuse `px_raw` Decimal + `round(px,2)` tick (`direct_burst.py:24-27`).

## Verification summary
- Phase 0: calldata byte-match vs `direct_burst.build_calldata()`.
- Phase 1: `import atomic_round`; offline calldata self-check.
- Phase 2: testnet receipts for install/clear; type-2 self-call reverts with our string (code executed); foreign-wallet call reverts `"self"`.
- Phase 3: one $5 R3 trip; `Trip` event decoded, residual 0, USDso delta ≤ maxToll, gasUsed + $/1k recorded; no-fill preflight blocks broadcast.

## Housekeeping
- Save this plan to `context/plan/atomic-7702-mode.md`.
- Continue on `feature/maker-v2`; one commit per phase; push after each (near-loss lesson).
- Probe scripts already on server `~/probe7702/` (type-4 support, block scan, delegate bytecode read).
