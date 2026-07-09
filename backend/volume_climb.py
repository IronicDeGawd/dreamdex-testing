#!/usr/bin/env python3
"""Volume-climb engine — taker round-trip churn across eligible pairs. Ends flat.

Buys then immediately sells the same size back, generating raw volume while holding
~zero inventory. The only cost is the spread crossed (toll) + gas, so it trades the
CHEAPEST book available.

DYNAMIC ROTATION (R4): pass CLIMB_PAIRS as a comma list (e.g.
"WBTC:USDso,WETH:USDso,SOMI:USDso"). Each round-trip it reads every pair's live
book and trades whichever has the tightest spread that clears the gate — so it
auto-rotates to the cheapest chain as liquidity shifts. A single CLIMB_PAIR still
works (behaves like the old single-pair engine).

Per-pair correctness (this is what made WBTC fail before): price is snapped to the
pair's TICK size (WBTC tick=0.1), qty to its LOT, and tick/lot/min are read AFTER
DreamDEX() refreshes market params (the defaults are wrong).

Every cap STOPS the loop and auto-flattens. Lessons baked in:
 - taker IOC needs a protective limit that crosses the touch (buy ask+slip, sell
   bid-slip); it fills at the touch so the wide limit is free insurance.
 - one-time pre-approval per pool avoids a re-approval tx on every leg.
 - poll balances after each leg (settlement lag) so a slow read never skips a sell.
"""
import os, time, math, signal, config
import requests as _rq
from web3 import Web3
from trading.dreamdex import DreamDEX

# A host/DNS/RPC outage is TRANSIENT — it must not burn the 5-strike trade breaker
# and halt the run (halting risks the 24h-idle DQ). Back off and keep retrying;
# the engine resumes the moment the network returns.
NET_BACKOFF_S = [30, 60, 120, 300, 900]   # escalate to 15 min, then hold there
def _is_network_err(e) -> bool:
    if isinstance(e, (_rq.exceptions.RequestException, ConnectionError, TimeoutError, OSError)):
        return True
    s = str(e).lower()
    return any(k in s for k in (
        "nameresolution", "name resolution", "max retries exceeded", "connection",
        "timed out", "timeout", "unreachable", "temporarily unavailable", "dns"))

# Rule 5: eligible (non-stablecoin) pairs only. USDC.e:USDso must never be traded.
ELIGIBLE = {"WBTC:USDso", "WETH:USDso", "SOMI:USDso"}
# Force a keepalive trade before this many idle seconds so a market-wide spread
# widening can't leave us idle >24h and get us auto-DQ'd (rule 11). 18h margin.
LIVENESS_S    = float(os.environ.get("CLIMB_LIVENESS_S", str(18 * 3600)))
KEEPALIVE_LEG = float(os.environ.get("CLIMB_KEEPALIVE_LEG", "5"))

# Pair list: CLIMB_PAIRS (comma) takes precedence; else CLIMB_PAIR (single).
_pairs_env = os.environ.get("CLIMB_PAIRS", os.environ.get("CLIMB_PAIR", "WETH:USDso"))
PAIRS = [p.strip() for p in _pairs_env.split(",") if p.strip()]

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
RESID_RETRIES   = int(os.environ.get("CLIMB_RESID_RETRIES", "8"))
RESID_WAIT_S    = float(os.environ.get("CLIMB_RESID_WAIT_S", "4"))

dex = DreamDEX(); w = dex.wallet   # constructor refreshes config.MARKETS in place
# Re-run the refresh WITHOUT swallowing — a boot-time network blip leaves tick/lot
# at wrong defaults (WBTC tick is really 0.1), which turns every order into an
# invalid_price revert loop. Abort cleanly instead of silently degrading.
try:
    dex.refresh_market_params()
except Exception as e:
    print(f"!! market-param refresh failed at boot: {e} — ABORT (tick/lot would be wrong)")
    raise SystemExit(1)

# Per-pair spec, read AFTER the refresh so tick/lot/min are the real values.
def spec_for(pair):
    m = config.MARKETS[pair]
    lot = float(m.get("lotSize", 0.0001))
    return {
        "pair":  pair,
        "base":  m["base"],  "bdec": int(m["baseDecimals"]),
        "quote": m["quote"], "qdec": int(m["quoteDecimals"]),
        "pool":  Web3.to_checksum_address(m["contract"]),
        "tick":  float(m.get("tickSize", 0.01)),
        "lot":   lot,
        "minq":  float(m.get("minQuantity", lot)),
    }
# Only ERC20-base pairs work with this round-trip path. Native-base pairs (SOMI,
# base = 0x0) need the payable depositNative flow — skip them (also the widest
# book, so no loss). This prevents a balanceOf(0x0) crash on startup.
SPECS = {}
for p in PAIRS:
    if p not in ELIGIBLE:
        print(f"  skip {p}: not an eligible pair (rule 5) — refusing to trade it")
        continue
    m = config.MARKETS[p]
    if m.get("native") or int(str(m["base"]), 16) == 0:
        print(f"  skip {p}: native-base pair not supported by the ERC20 round-trip path")
        continue
    SPECS[p] = spec_for(p)
if not SPECS:
    print("!! no tradeable ERC20 pairs in CLIMB_PAIRS — ABORT"); raise SystemExit(1)
PAIRS = list(SPECS.keys())
QUOTE = SPECS[PAIRS[0]]["quote"]; QDEC = SPECS[PAIRS[0]]["qdec"]  # USDso: same across pairs

def ub():      return w.erc20_balance(QUOTE, QDEC)
def bb(sp):    return w.erc20_balance(sp["base"], sp["bdec"])
def sb():      return w.native_balance()
def snap_lot(sp, q):
    # FLOOR to lot so a sell never asks for more base than held (over-ask reverts).
    n = math.floor(q / sp["lot"]); q = round(n * sp["lot"], 10)
    return q if q >= sp["minq"] else round(sp["minq"], 10)
def snap_price(sp, p, up=False):
    # Directional snap so the IOC still crosses the touch: BUY limit rounds UP
    # (stays >= ask), SELL limit rounds DOWN (stays <= bid).
    t = sp["tick"]; n = math.ceil(p / t) if up else math.floor(p / t)
    return round(n * t, 10)
def poll_base(sp, target_cmp, want_above, timeout=3.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = bb(sp)
        if (want_above and v >= target_cmp) or (not want_above and v <= target_cmp):
            return v
        time.sleep(0.2)
    return bb(sp)

ERC20 = [
 {"name":"approve","type":"function","stateMutability":"nonpayable",
  "inputs":[{"name":"s","type":"address"},{"name":"a","type":"uint256"}],"outputs":[{"name":"","type":"bool"}]},
 {"name":"allowance","type":"function","stateMutability":"view",
  "inputs":[{"name":"o","type":"address"},{"name":"s","type":"address"}],"outputs":[{"name":"","type":"uint256"}]},
]
def ensure_allowance(pool, token, dec, need_human):
    c = w.w3.eth.contract(address=Web3.to_checksum_address(token), abi=ERC20)
    cur = c.functions.allowance(w.address, pool).call() / (10 ** dec)
    if cur >= need_human:
        return
    raw = int(need_human * (10 ** dec))
    tx = c.functions.approve(pool, raw).build_transaction(
        {"from": w.address, "nonce": w.reserve_nonce(), **w._gas_fields()})
    h = w.sign_and_send(tx); w.wait_for_receipt(h)
    print(f"  pre-approved {token[:10]} @ {pool[:10]} -> {need_human} (tx {h[:14]})")

print(f"=== VOLUME CLIMB (rotating) pairs={PAIRS} ===")
print(f"target=${TARGET_VOLUME} leg=${LEG_USD} slip={SLIP_PCT*100}% "
      f"caps: gas<{MAX_GAS_SOMI} floor={SOMI_FLOOR} bleed<${MAX_USDSO_BLEED} iters<{MAX_ITERS}")
u_start, s_start = ub(), sb()
print(f"START USDso={u_start:.4f} SOMI={s_start:.4f}")
for sp in SPECS.values():
    if bb(sp) > sp["minq"]:
        print(f"!! starting base for {sp['pair']} > min — NOT flat. ABORT."); raise SystemExit(1)

if PREAPPROVE:
    print("pre-approving each pool (one-time)...")
    for sp in SPECS.values():
        ensure_allowance(sp["pool"], sp["quote"], sp["qdec"], TARGET_VOLUME)
        ensure_allowance(sp["pool"], sp["base"],  sp["bdec"], TARGET_VOLUME / 1000.0)

vol = 0.0; trips = 0; consec_fail = 0; _net_fails = 0
_last_trade_ts = time.time()   # keepalive clock — reset on every successful trip
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

TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", ""); TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "")
TG_MS = float(os.environ.get("CLIMB_TG_MILESTONE", "25000"))
def tg(msg):
    if not TG_TOKEN or not TG_CHAT: return
    try:
        import requests
        requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                      json={"chat_id": TG_CHAT, "text": msg}, timeout=10)
    except Exception: pass
_paused = False; _last_ms = 0.0
tg(f"🚀 Volume run: pairs {PAIRS} target ${TARGET_VOLUME:,.0f}, leg ${LEG_USD:.0f}, cost-ceil ${COST_CEIL_PER_1K}/1k")

def flatten_all(max_attempts=None):
    """Sell any residual base on every pair back to USDso (never exit with a bag).
    max_attempts bounds the retries (the SIGTERM path uses a small budget to finish
    inside docker's stop grace)."""
    n = max_attempts if max_attempts else max(3, RESID_RETRIES)
    for sp in SPECS.values():
        b = bb(sp)
        if b <= sp["minq"]:
            continue
        for attempt in range(n):
            try:
                ob = dex.get_orderbook(sp["pair"])
                if not ob.get("bid"):
                    time.sleep(RESID_WAIT_S); continue
                dex.place_order(sp["pair"], "sell", snap_lot(sp, b), order_type="ioc",
                                limit_price=snap_price(sp, ob["bid"]*(1-SLIP_PCT*(attempt+1))), funding="wallet")
                time.sleep(max(3, RESID_WAIT_S))
                b = bb(sp)
                if b <= sp["minq"]: break
            except Exception as e:
                print(f"  flatten {sp['pair']} attempt {attempt} failed: {e}"); time.sleep(2)

def stop(reason):
    flatten_all()
    u, s = ub(), sb()
    print(f"\n=== STOP: {reason} ===")
    bags = {p: bb(sp) for p, sp in SPECS.items() if bb(sp) > sp["minq"]}
    print(f"trips={trips} volume=${vol:.2f} USDso_bleed=${u_start-u:.4f} gas={s_start-s:.4f} SOMI residual={bags}")
    if bags: print(f"!! WARNING residual base {bags} — flatten did not clear it, flatten manually")
    tg(f"🛑 Stopped: {reason} | vol ${vol:,.0f} | bleed ${u_start-u:.2f} | gas {s_start-s:.1f} SOMI | flat={'yes' if not bags else 'NO'}")
    raise SystemExit(0)

def _on_term(signum, frame):
    # docker stop sends SIGTERM before SIGKILL — flatten (bounded) so an external
    # stop mid-round-trip can't leave a bag. Kept small to fit the stop grace.
    print(f"\n[signal {signum}] flatten-and-exit...")
    try:
        flatten_all(max_attempts=3)
    except Exception as e:
        print(f"  term-flatten err {e}")
    raise SystemExit(0)
signal.signal(signal.SIGTERM, _on_term)

def pick_cheapest():
    """Return (spec, ob, spread_pct, within_gate). Always returns the tightest-spread
    pair that has a live book (or (None,None,None,False) if no book). within_gate is
    False when even the cheapest exceeds SPREAD_GATE_PCT — the caller pauses, unless
    it must force a keepalive trip."""
    best = None
    for sp in SPECS.values():
        try:
            ob = dex.get_orderbook(sp["pair"])
        except Exception:
            continue
        if not ob.get("bid") or not ob.get("ask"):
            continue
        mid = (ob["bid"]+ob["ask"])/2
        spread_pct = (ob["ask"]-ob["bid"])/mid*100
        if best is None or spread_pct < best[2]:
            best = (sp, ob, spread_pct)
    if best is None:
        return None, None, None, False
    sp, ob, spread_pct = best
    within = not (SPREAD_GATE_PCT > 0 and spread_pct > SPREAD_GATE_PCT)
    return sp, ob, spread_pct, within

for i in range(MAX_ITERS):
    try:
        if vol >= TARGET_VOLUME: stop("target volume reached")
        s_now, u_now = sb(), ub()
        if s_start - s_now >= MAX_GAS_SOMI: stop("max gas hit")
        if s_now <= SOMI_FLOOR: stop("SOMI floor hit")
        if u_start - u_now >= MAX_USDSO_BLEED: stop("max USDso bleed hit")
        if consec_fail >= MAX_CONSEC_FAIL: stop(f"{MAX_CONSEC_FAIL} consecutive failures")

        # Sweep ALL pairs for a stray bag before trading — a prior trip's sell may
        # have failed on a pair the rotation isn't currently picking, which would
        # otherwise ride invisibly. Flatten it first (not a hard stop).
        for _sp in SPECS.values():
            if bb(_sp) > _sp["minq"]:
                print(f"[{i}] stray bag on {_sp['pair']} {bb(_sp):.6f} — flattening first")
                flatten_all(); break

        sp, ob, spread_pct, within = pick_cheapest()
        if sp is None:
            print(f"[{i}] no live book on any pair — pause {PAUSE_EXP_S:.0f}s"); time.sleep(PAUSE_EXP_S); continue

        idle_s = time.time() - _last_trade_ts
        force_keepalive = (not within) and idle_s > LIVENESS_S
        if not within and not force_keepalive:
            if not _paused: tg(f"⏸ Paused @ ${vol:,.0f} vol — cheapest spread {spread_pct:.3f}% > gate {SPREAD_GATE_PCT}%"); _paused = True
            print(f"[{i}] cheapest {spread_pct:.3f}% > gate {SPREAD_GATE_PCT}% — pause {PAUSE_EXP_S:.0f}s (idle {idle_s/3600:.1f}h)")
            time.sleep(PAUSE_EXP_S); continue

        leg_this = LEG_USD
        if force_keepalive:
            leg_this = KEEPALIVE_LEG
            print(f"[{i}] KEEPALIVE: idle {idle_s/3600:.1f}h > {LIVENESS_S/3600:.0f}h — forcing ${leg_this:.0f} trip on {sp['pair']} @ {spread_pct:.3f}% (over gate, staying alive)")
            tg(f"🫀 Keepalive @ ${vol:,.0f} vol — idle {idle_s/3600:.1f}h, forcing a small trade to dodge the 24h-idle DQ")

        mid = (ob["bid"]+ob["ask"])/2
        qty = snap_lot(sp, leg_this/mid)
        buy_lim  = snap_price(sp, ob["ask"]*(1+SLIP_PCT), up=True)   # round UP so IOC crosses
        sell_lim = snap_price(sp, ob["bid"]*(1-SLIP_PCT))            # round DOWN so IOC crosses
        tag = sp["pair"].split(":")[0]

        b_pre = bb(sp)
        rb = dex.place_order(sp["pair"],"buy",qty,order_type="ioc",limit_price=buy_lim,funding="wallet")
        if rb.get("status") != "success":
            print(f"[{i}] {tag} BUY {rb.get('status')}"); consec_fail += 1; time.sleep(PAUSE_S); continue
        got = poll_base(sp, b_pre + sp["minq"]*0.5, want_above=True) - b_pre
        if got < sp["minq"]*0.5:
            print(f"[{i}] {tag} BUY ok but no base delta — STOP to be safe"); stop("buy/settle desync")

        # Sell the FULL held balance, not just this trip's delta — folds in any
        # sub-min-qty dust left by fee-shave on prior trips (which no standalone
        # order can clear) so it doesn't accumulate into a trapped bag.
        dex.place_order(sp["pair"],"sell",snap_lot(sp, bb(sp)),order_type="ioc",limit_price=sell_lim,funding="wallet")
        resid = poll_base(sp, sp["minq"], want_above=False)
        if resid > sp["minq"]:
            dex.place_order(sp["pair"],"sell",snap_lot(sp, resid),order_type="ioc",
                            limit_price=snap_price(sp, ob["bid"]*(1-SLIP_PCT*3)),funding="wallet")
            resid = poll_base(sp, sp["minq"], want_above=False)
        if resid > sp["minq"]:
            if not _paused:
                tg(f"⏸ Paused @ ${vol:,.0f} vol — thin {tag} book, retrying sell of {resid:.6f}"); _paused = True
            print(f"[{i}] {tag} residual {resid:.6f} — thin book, patient-retry up to {RESID_RETRIES}x")
            for r in range(RESID_RETRIES):
                time.sleep(RESID_WAIT_S)
                try:
                    ob2 = dex.get_orderbook(sp["pair"])
                    if not ob2.get("bid"): continue
                    dex.place_order(sp["pair"],"sell",snap_lot(sp, resid),order_type="ioc",
                                    limit_price=snap_price(sp, ob2["bid"]*(1-SLIP_PCT*(2+r))),funding="wallet")
                except Exception as e:
                    print(f"[{i}] {tag} resid-sell retry {r} err {str(e)[:80]}")
                resid = poll_base(sp, sp["minq"], want_above=False)
                if resid <= sp["minq"]:
                    print(f"[{i}] {tag} residual cleared after {r+1}"); break
            if resid > sp["minq"]:
                stop(f"SELL failed after {RESID_RETRIES} retries on {tag} — residual {resid:.6f} (bag)")

        vt = got*mid*2
        vol += vt; trips += 1; consec_fail = 0; _last_trade_ts = time.time()
        if _net_fails:
            print(f"[{i}] network recovered after {_net_fails} failed attempt(s)")
            tg(f"🌐 Network back @ ${vol:,.0f} vol — trading resumed"); _net_fails = 0
        u, s = ub(), sb()
        cost_1k = ((u_now-u) + (s_now-s)*SOMI_PX)/vt*1000 if vt > 0 else 0.0
        _costs.append(cost_1k); roll = sum(_costs)/len(_costs)
        print(f"[{i}] trip {trips} {tag} @spread {spread_pct:.3f}%: vol+=${vt:.2f} tot=${vol:.2f} "
              f"USDso={u:.4f}(bleed ${u_start-u:.4f}) SOMI={s:.4f} | cost ${cost_1k:.3f}/1k roll ${roll:.3f}/1k")
        if _paused and (COST_CEIL_PER_1K <= 0 or roll <= COST_CEIL_PER_1K):
            tg(f"▶️ Resumed @ ${vol:,.0f} vol — cost ${roll:.3f}/1k (under ceil)"); _paused = False
        if vol >= _last_ms + TG_MS:
            _last_ms = vol; tg(f"📊 +${vol:,.0f} vol | roll ${roll:.3f}/1k | USDso ${u:.0f} | gas {s_start-s:.1f} SOMI")
        if COST_CEIL_PER_1K > 0 and len(_costs) >= max(3, COST_WINDOW//2) and roll > COST_CEIL_PER_1K:
            if not _paused: tg(f"⏸ Paused @ ${vol:,.0f} vol — cost ${roll:.3f}/1k > ceil ${COST_CEIL_PER_1K}/1k"); _paused = True
            print(f"[{i}] rolling cost ${roll:.3f}/1k > ceil ${COST_CEIL_PER_1K}/1k — PAUSE {PAUSE_EXP_S:.0f}s"); time.sleep(PAUSE_EXP_S)
        time.sleep(PAUSE_S)
    except SystemExit:
        raise
    except Exception as e:
        try: w.reset_nonce()
        except Exception: pass
        if _is_network_err(e):
            # Transient: host/DNS/RPC outage. Do NOT count toward the trade breaker
            # and do NOT halt — back off (up to 15 min) and retry until it returns.
            _net_fails += 1
            wait = NET_BACKOFF_S[min(_net_fails - 1, len(NET_BACKOFF_S) - 1)]
            print(f"[{i}] NETWORK error #{_net_fails} ({str(e)[:70]}) — retry in {wait}s (not a trade failure)")
            if _net_fails == 1:
                tg(f"🌐 Network down @ ${vol:,.0f} vol — backing off, will retry (engine stays alive)")
            time.sleep(wait)
            continue
        print(f"[{i}] EXC {str(e)[:120]}"); consec_fail += 1
        time.sleep(2)

stop("max iters reached")
