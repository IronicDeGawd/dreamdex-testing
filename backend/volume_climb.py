#!/usr/bin/env python3
"""R3 volume-climb engine — WETH:USDso taker round-trip churn. Ends flat. Hard-capped.

Why this exists: R3 ranks by effective volume = raw x (1 + PnL%). Raw volume still
dominates the rank (leaders are deeply unprofitable yet top). Flat round-trip churn
(buy then immediately sell the same size back) generates raw volume while holding ~zero
inventory, so PnL stays ~flat (multiplier ~1.0) — strictly better than the leaders'
discounted multipliers, and bear-proof. Toll on WETH is ~zero (spread 0.02%); gas is the
only real cost, minimised with big-ish legs + a one-time approval.

PROVEN: drove ~30k volume in one run for ~$2.4 USDso bleed + ~6 SOMI gas, ended flat.

Run on the server via the agent container (reads MAINNET_PRIVATE_KEY from its env):
  cd ~/dreamdex-r3/backend && docker compose run --rm --no-deps -T \
    -e CLIMB_TARGET_VOLUME=30000 -e CLIMB_LEG_USD=15 -e CLIMB_SLIP_PCT=0.003 \
    -e CLIMB_MAX_GAS_SOMI=8 -e CLIMB_SOMI_FLOOR=8 -e CLIMB_MAX_USDSO_BLEED=15 \
    -e CLIMB_MAX_ITERS=1300 -e CLIMB_PAUSE_S=0.4 -e CLIMB_PREAPPROVE=1 \
    agent python3 volume_climb.py
NOTE: an SSH drop kills the *viewing* pipe but the container keeps running detached —
follow it with `docker logs -f backend-agent-run-<id>`. Container is --rm so logs vanish
on exit; confirm the result on-chain (WETH balance ~0 = flat) + leaderboard, not the log.

Every cap STOPS the loop. Lessons baked in:
 - taker IOC needs a WIDE protective limit (~0.3%) to cross the JIT-defended book; it fills
   at the touch, so the wide limit costs nothing (free insurance). +5 ticks => no-match.
 - one-time pre-approval avoids a re-approval tx on every leg (allowance is consumed
   cumulatively by transferFrom, so approve >= target volume).
 - poll balances after each leg (settlement lag) so a slow read never skips a sell -> bag.
"""
import os, time, config
from web3 import Web3
from trading.dreamdex import DreamDEX

PAIR  = os.environ.get("CLIMB_PAIR", "WETH:USDso")
TARGET_VOLUME   = float(os.environ.get("CLIMB_TARGET_VOLUME", "300"))
LEG_USD         = float(os.environ.get("CLIMB_LEG_USD", "15"))
SLIP_PCT        = float(os.environ.get("CLIMB_SLIP_PCT", "0.003"))
MAX_GAS_SOMI    = float(os.environ.get("CLIMB_MAX_GAS_SOMI", "8"))
SOMI_FLOOR      = float(os.environ.get("CLIMB_SOMI_FLOOR", "8"))
MAX_USDSO_BLEED = float(os.environ.get("CLIMB_MAX_USDSO_BLEED", "15"))
MAX_ITERS       = int(os.environ.get("CLIMB_MAX_ITERS", "1300"))
PAUSE_S         = float(os.environ.get("CLIMB_PAUSE_S", "0.4"))
PREAPPROVE      = os.environ.get("CLIMB_PREAPPROVE", "1") == "1"
MAX_CONSEC_FAIL = 5

mkt = config.MARKETS[PAIR]
BASE = mkt["base"]; BDEC = int(mkt["baseDecimals"])
QUOTE = mkt["quote"]; QDEC = int(mkt["quoteDecimals"])
POOL = Web3.to_checksum_address(mkt["contract"])
LOT  = float(mkt.get("lotSize", 0.0001)); MINQ = float(mkt.get("minQuantity", LOT))

dex = DreamDEX(); w = dex.wallet
def ub(): return w.erc20_balance(QUOTE, QDEC)
def bb(): return w.erc20_balance(BASE, BDEC)
def sb(): return w.native_balance()
def snap_lot(q):
    n = max(round(q/LOT), 1); q = round(n*LOT, 10)
    return q if q >= MINQ else round(MINQ, 10)
def poll_base(target_cmp, want_above, timeout=8.0):
    t0 = time.time()
    while time.time()-t0 < timeout:
        v = bb()
        if (want_above and v >= target_cmp) or (not want_above and v <= target_cmp):
            return v
        time.sleep(1.0)
    return bb()

ERC20 = [
 {"name":"approve","type":"function","stateMutability":"nonpayable",
  "inputs":[{"name":"s","type":"address"},{"name":"a","type":"uint256"}],"outputs":[{"name":"","type":"bool"}]},
 {"name":"allowance","type":"function","stateMutability":"view",
  "inputs":[{"name":"o","type":"address"},{"name":"s","type":"address"}],"outputs":[{"name":"","type":"uint256"}]},
]
def ensure_allowance(token, dec, need_human):
    c = w.w3.eth.contract(address=Web3.to_checksum_address(token), abi=ERC20)
    cur = c.functions.allowance(w.address, POOL).call()/(10**dec)
    if cur >= need_human:
        print(f"  allowance ok: {token[:10]} {cur:.2f} >= {need_human}"); return
    raw = int(need_human*(10**dec))
    tx = c.functions.approve(POOL, raw).build_transaction(
        {"from": w.address, "nonce": w.reserve_nonce(), **w._gas_fields()})
    h = w.sign_and_send(tx); w.wait_for_receipt(h)
    print(f"  pre-approved {token[:10]} -> {need_human} (tx {h[:14]})")

print(f"=== VOLUME CLIMB {PAIR} ===")
print(f"target=${TARGET_VOLUME} leg=${LEG_USD} slip={SLIP_PCT*100}% "
      f"caps: gas<{MAX_GAS_SOMI} floor={SOMI_FLOOR} bleed<${MAX_USDSO_BLEED} iters<{MAX_ITERS}")
u_start, b_start, s_start = ub(), bb(), sb()
print(f"START USDso={u_start:.4f} base={b_start:.6f} SOMI={s_start:.4f}")
if b_start > MINQ:
    print(f"!! starting base {b_start} > min — NOT flat. ABORT."); raise SystemExit(1)

if PREAPPROVE:
    print("pre-approving (one-time, avoids per-leg approvals)...")
    ensure_allowance(QUOTE, QDEC, TARGET_VOLUME)
    ensure_allowance(BASE,  BDEC, TARGET_VOLUME/1000.0)

vol = 0.0; trips = 0; consec_fail = 0
# Cost-aware mode (week-2 capital efficiency): generate volume only while it's cheap.
# SPREAD_GATE: skip a trip when the live spread is too wide. COST_CEIL: pause when the
# rolling realized $/1k climbs over the ceiling. Both 0 = disabled (plain full burst).
from collections import deque
COST_CEIL_PER_1K = float(os.environ.get("CLIMB_COST_CEIL_PER_1K", "0"))
SPREAD_GATE_PCT  = float(os.environ.get("CLIMB_SPREAD_GATE_PCT", "0"))
PAUSE_EXP_S      = float(os.environ.get("CLIMB_PAUSE_EXP_S", "30"))
COST_WINDOW      = int(os.environ.get("CLIMB_COST_WINDOW", "15"))
_costs = deque(maxlen=COST_WINDOW)
try:
    _sob = dex.get_orderbook("SOMI:USDso")
    SOMI_PX = (_sob["bid"]+_sob["ask"])/2 if _sob.get("bid") and _sob.get("ask") else 0.10
except Exception:
    SOMI_PX = 0.10
print(f"cost-aware: spread_gate={SPREAD_GATE_PCT}% cost_ceil=${COST_CEIL_PER_1K}/1k window={COST_WINDOW} somi_px={SOMI_PX:.4f}")

def stop(reason):
    # auto-flatten: never exit holding a bag (e.g. a sell leg killed by a network
    # blip). Sell any residual base back into the bid before reporting + exiting.
    b = bb()
    if b > MINQ:
        for attempt in range(3):
            try:
                ob = dex.get_orderbook(PAIR)
                if not ob.get("bid"): break
                dex.place_order(PAIR, "sell", snap_lot(b), order_type="ioc",
                                limit_price=round(ob["bid"]*(1-SLIP_PCT*(attempt+1)), 2), funding="wallet")
                time.sleep(3)
                b = bb()
                if b <= MINQ: break
            except Exception as e:
                print(f"  auto-flatten attempt {attempt} failed: {e}"); time.sleep(2)
    u,b,s = ub(),bb(),sb()
    print(f"\n=== STOP: {reason} ===")
    print(f"trips={trips} volume=${vol:.2f} USDso_bleed=${u_start-u:.4f} gas={s_start-s:.4f} SOMI residual_base={b:.6f}")
    if b > MINQ: print(f"!! WARNING residual base {b:.6f} — auto-flatten did not clear it, flatten manually")
    raise SystemExit(0)

for i in range(MAX_ITERS):
    try:
        # cap checks inside try so a transient RPC/DNS blip is caught + retried, not fatal
        if vol >= TARGET_VOLUME: stop("target volume reached")
        s_now, u_now = sb(), ub()
        if s_start - s_now >= MAX_GAS_SOMI: stop("max gas hit")
        if s_now <= SOMI_FLOOR: stop("SOMI floor hit")
        if u_start - u_now >= MAX_USDSO_BLEED: stop("max USDso bleed hit")
        if consec_fail >= MAX_CONSEC_FAIL: stop(f"{MAX_CONSEC_FAIL} consecutive failures")
        if bb() > MINQ:
            stop(f"unexpected base inventory at trip start {bb():.6f}")
        ob = dex.get_orderbook(PAIR)
        if not ob["bid"] or not ob["ask"]:
            print(f"[{i}] empty book"); consec_fail += 1; time.sleep(PAUSE_S); continue
        mid = (ob["bid"]+ob["ask"])/2
        spread_pct = (ob["ask"]-ob["bid"])/mid*100
        if SPREAD_GATE_PCT > 0 and spread_pct > SPREAD_GATE_PCT:
            print(f"[{i}] spread {spread_pct:.3f}% > gate {SPREAD_GATE_PCT}% — pause {PAUSE_EXP_S:.0f}s"); time.sleep(PAUSE_EXP_S); continue
        qty = snap_lot(LEG_USD/mid)
        buy_lim  = round(ob["ask"]*(1+SLIP_PCT), 2)
        sell_lim = round(ob["bid"]*(1-SLIP_PCT), 2)

        b_pre = bb()
        rb = dex.place_order(PAIR,"buy",qty,order_type="ioc",limit_price=buy_lim,funding="wallet")
        if rb.get("status") != "success":
            print(f"[{i}] BUY {rb.get('status')}"); consec_fail += 1; time.sleep(PAUSE_S); continue
        got = poll_base(b_pre + MINQ*0.5, want_above=True) - b_pre
        if got < MINQ*0.5:
            print(f"[{i}] BUY success but no base delta after poll — STOP to be safe"); stop("buy/settle desync")

        rs = dex.place_order(PAIR,"sell",snap_lot(got),order_type="ioc",limit_price=sell_lim,funding="wallet")
        resid = poll_base(MINQ, want_above=False)
        if resid > MINQ:
            rs2 = dex.place_order(PAIR,"sell",snap_lot(resid),order_type="ioc",limit_price=round(ob["bid"]*(1-SLIP_PCT*3),2),funding="wallet")
            resid = poll_base(MINQ, want_above=False)
            if resid > MINQ:
                stop(f"SELL failed twice — residual {resid:.6f} (bag)")

        vt = got*mid*2
        vol += vt; trips += 1; consec_fail = 0
        u,s = ub(), sb()
        cost_1k = ((u_now-u) + (s_now-s)*SOMI_PX)/vt*1000 if vt > 0 else 0.0
        _costs.append(cost_1k); roll = sum(_costs)/len(_costs)
        print(f"[{i}] trip {trips}: vol+=${vt:.2f} tot=${vol:.2f} USDso={u:.4f}(bleed ${u_start-u:.4f}) SOMI={s:.4f} | cost ${cost_1k:.3f}/1k roll ${roll:.3f}/1k")
        if COST_CEIL_PER_1K > 0 and len(_costs) >= max(3, COST_WINDOW//2) and roll > COST_CEIL_PER_1K:
            print(f"[{i}] rolling cost ${roll:.3f}/1k > ceil ${COST_CEIL_PER_1K}/1k — PAUSE {PAUSE_EXP_S:.0f}s"); time.sleep(PAUSE_EXP_S); _costs.clear()
        time.sleep(PAUSE_S)
    except SystemExit:
        raise
    except Exception as e:
        print(f"[{i}] EXC {str(e)[:120]}"); consec_fail += 1
        try: w.reset_nonce()
        except Exception: pass
        time.sleep(2)

stop("max iters reached")
