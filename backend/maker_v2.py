#!/usr/bin/env python3
"""Maker v2 — two-sided PostOnly spread-capture engine (Arena Phase 1).

Rests a bid and an ask on every configured ERC20 pair and earns the spread as
real flow hits them. Every fill is contest volume (maker side counts) AND
profit — the sell is never quoted below avg_cost + margin, so a round-trip
can't realize a loss. Decisions live in maker_core.py (pure, unit-tested);
this file is plumbing only.

What v2 fixes over the R3 agent_v3 maker (the inconsistency list):
  - decision logic extracted + unit-tested (tests/test_maker_core.py)
  - inventory truth = wallet base + base RESERVED IN THE POOL by our resting
    sell (getWithdrawableBalance) — the R3 wallet-only read saw a resting sell
    as vanished inventory and could thrash cancel/replace
  - no SQLite control DB, no strategist, no thread supervisor — one process,
    env-driven, launchable/stoppable like volume_climb (same "=== STOP:" and
    "tot=$" log contract, so control/watchdog understand it)
  - HARD bleed guard on TRUE capital (wallet + pool-reserved + inventory@mid
    + gas value): drop > MAKER2_MAX_BLEED → cancel-all, flatten, stop
  - runs on the shared hardened layer: 5M gas floor + honest sim, allowance
    check-first, RPC failover, per-pair tick/lot refreshed at boot

Wallet: defaults to the config wallet; MAKER2_PRIVATE_KEY/MAKER2_ADDRESS run
it on a separate wallet (e.g. the R3 wallet for live smoke tests) without
touching the main engine's nonce stream.
"""
import json
import os
import random
import signal
import time

import config
from web3 import Web3
from trading.dreamdex import DreamDEX
from maker_core import (apply_fill, desired_quotes, round_lot, should_requote,
                        snap_down, snap_up, stop_loss_action, trend_mode)

# ── config (env) ───────────────────────────────────────────────────────────
PAIRS_ENV   = os.environ.get("MAKER2_PAIRS", "WBTC:USDso,WETH:USDso")
LEG_USD     = float(os.environ.get("MAKER2_LEG_USD", "15"))
CAP_USD     = float(os.environ.get("MAKER2_MAX_INV_USD", "20"))     # per pair
MARGIN_T    = int(os.environ.get("MAKER2_MARGIN_TICKS", "1"))
DRIFT_T     = float(os.environ.get("MAKER2_DRIFT_TICKS", "3"))
POLL_S      = float(os.environ.get("MAKER2_POLL_S", "3"))
STOP_PCT    = float(os.environ.get("MAKER2_STOP_LOSS_PCT", "0.10"))
STOP_SLIP   = float(os.environ.get("MAKER2_STOP_SLIP_PCT", "0.03"))
STOP_COOL_S = float(os.environ.get("MAKER2_STOP_COOLDOWN_S", "900"))
TREND_UP    = float(os.environ.get("MAKER2_TREND_UP_PCT", "0.01"))
TREND_DOWN  = float(os.environ.get("MAKER2_TREND_DOWN_PCT", "-0.015"))
TREND_TTL_S = float(os.environ.get("MAKER2_TREND_CACHE_S", "600"))
MAX_BLEED   = float(os.environ.get("MAKER2_MAX_BLEED", "2.0"))      # TRUE capital $
RESERVE_USD = float(os.environ.get("MAKER2_RESERVE_USD", "2"))
SOMI_FLOOR  = float(os.environ.get("MAKER2_SOMI_FLOOR", "0.05"))
MAX_S       = float(os.environ.get("MAKER2_MAX_S", "0"))            # 0 = run forever
TARGET_VOL  = float(os.environ.get("MAKER2_TARGET_VOLUME", "0"))    # 0 = no volume stop
# Idle-DQ keepalive (R4 rule 11: >24h without a trade = DQ). Maker fills are
# flow-dependent and can be sparse; the host-side keepalive cron deliberately
# no-ops while an engine is running — so the maker covers itself: if no fill
# for MAKER2_LIVENESS_S, place ONE tiny IOC buy (a real trade that resets the
# clock); the resting ask then sells it back as normal inventory. 0 = off.
LIVENESS_S  = float(os.environ.get("MAKER2_LIVENESS_S", str(18 * 3600)))
KEEPALIVE_USD = float(os.environ.get("MAKER2_KEEPALIVE_USD", "5"))
STATE_FILE  = os.environ.get("MAKER2_STATE_FILE", "data/maker_v2_state.json")
# Arena fair-play shaping: hold a standing inventory floor (a position that
# oscillates in a band above a floor is real trading; flushing to ~zero every
# cycle pattern-matches the "near-flat cycle" wash-trade flag), and jitter the
# leg so buy and sell sizes never mirror each other.
INV_FLOOR_PCT = float(os.environ.get("MAKER2_INV_FLOOR_PCT", "0.3"))   # of cap
LEG_JITTER    = float(os.environ.get("MAKER2_LEG_JITTER_PCT", "0.15"))

dex = DreamDEX(private_key=os.environ.get("MAKER2_PRIVATE_KEY") or None,
               address=os.environ.get("MAKER2_ADDRESS") or None)
w = dex.wallet
try:
    dex.refresh_market_params()
except Exception as e:
    print(f"!! market-param refresh failed at boot: {e} — ABORT"); raise SystemExit(1)

ERC20_VIEW = [{"name": "balanceOf", "type": "function", "stateMutability": "view",
               "inputs": [{"name": "a", "type": "address"}],
               "outputs": [{"name": "", "type": "uint256"}]}]
VAULT_ABI = [{"name": "getWithdrawableBalance", "type": "function", "stateMutability": "view",
              "inputs": [{"name": "user", "type": "address"}, {"name": "token", "type": "address"}],
              "outputs": [{"name": "", "type": "uint256"}]}]

SPECS = {}
for p in [x.strip() for x in PAIRS_ENV.split(",") if x.strip()]:
    m = config.MARKETS.get(p)
    if not m:
        print(f"  skip {p}: unknown pair"); continue
    if m.get("native") or int(str(m["base"]), 16) == 0:
        print(f"  skip {p}: native-base pair (tracker desync risk) — ERC20 only"); continue
    SPECS[p] = {
        "pair": p, "base": m["base"], "bdec": int(m["baseDecimals"]),
        "quote": m["quote"], "qdec": int(m["quoteDecimals"]),
        "pool": Web3.to_checksum_address(m["contract"]),
        "tick": float(m.get("tickSize", 0.01)), "lot": float(m.get("lotSize", 0.0001)),
        "minq": float(m.get("minQuantity", 0.0001)),
    }
if not SPECS:
    print("!! no tradeable ERC20 pairs — ABORT"); raise SystemExit(1)
QUOTE = next(iter(SPECS.values()))["quote"]; QDEC = next(iter(SPECS.values()))["qdec"]


# ── chain reads ────────────────────────────────────────────────────────────
def wallet_usdso() -> float:
    return w.erc20_balance(QUOTE, QDEC)

def pool_reserved(sp, token, dec) -> float:
    try:
        c = w.w3.eth.contract(address=sp["pool"], abi=VAULT_ABI)
        return c.functions.getWithdrawableBalance(
            w.address, Web3.to_checksum_address(token)).call() / (10 ** dec)
    except Exception:
        return 0.0

def locked_in_orders(pair):
    """(quote_locked, base_locked) sitting inside OUR resting orders. Funds
    backing a live order are invisible to BOTH balanceOf and
    getWithdrawableBalance (locked ≠ withdrawable) — proven live 2026-07-13
    when a resting $24 buy read as a $23 'bleed' and tripped the guard, while
    the post-cancel report showed +$0.03. 4th instance of the value-that-left-
    the-wallet-is-not-lost bug class; here the ledger is our own order state."""
    ql = bl = 0.0
    for side, o in ORDERS[pair].items():
        if not o:
            continue
        rem = max(0.0, o["qty"] - o["filled"])
        if side == "buy":
            ql += rem * o["price"]
        else:
            bl += rem
    return ql, bl

def held_base(sp) -> float:
    """TRUE base inventory: wallet + pool-withdrawable + locked in our resting
    sells. Missing the last term made the maker under-count inventory while
    sells rested and buy past its cap (held $63 against a $40 cap)."""
    return (w.erc20_balance(sp["base"], sp["bdec"])
            + pool_reserved(sp, sp["base"], sp["bdec"])
            + locked_in_orders(sp["pair"])[1])

def book(sp):
    ob = dex.get_orderbook(sp["pair"])
    bid, ask = ob.get("bid"), ob.get("ask")
    if not bid or not ask or ask <= bid:
        return None
    return {"bid": bid, "ask": ask, "mid": (bid + ask) / 2}

def networth(mids: dict) -> float:
    """TRUE capital in USDso terms: free quote + pool-reserved quote + quote
    locked in our resting buys + base inventory at mid (incl. base locked in
    resting sells, via held_base) + gas at SOMI price. The bleed guard gates
    on THIS — every location value can sit in, or it cries wolf."""
    nw = wallet_usdso() + w.native_balance() * SOMI_PX
    for p, sp in SPECS.items():
        nw += pool_reserved(sp, sp["quote"], sp["qdec"]) + locked_in_orders(p)[0]
        b = held_base(sp)
        if b > 0 and mids.get(p):
            nw += b * mids[p]
    return nw


# ── state ──────────────────────────────────────────────────────────────────
POS = {p: {"qty": 0.0, "avg": 0.0} for p in SPECS}          # avg-cost book
ORDERS = {p: {"buy": None, "sell": None} for p in SPECS}    # resting: {id,price,qty,filled}
REENTER = {p: 0.0 for p in SPECS}                           # stop-loss cooldowns
TREND = {p: {"mode": "neutral", "ts": 0.0, "pct": None} for p in SPECS}
vol = 0.0; fills = 0; realized = 0.0
last_fill_ts = time.time()   # liveness clock — reset by every recorded fill

def load_state():
    try:
        st = json.loads(open(STATE_FILE).read())
        for p, pos in (st.get("pos") or {}).items():
            if p in POS:
                POS[p]["avg"] = float(pos.get("avg", 0.0))  # qty comes from chain
        return st
    except (FileNotFoundError, ValueError):
        return {}

def save_state():
    os.makedirs(os.path.dirname(STATE_FILE) or ".", exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as fh:
        json.dump({"pos": POS, "vol": vol, "realized": realized, "ts": time.time()}, fh)
    os.replace(tmp, STATE_FILE)


# ── order plumbing ─────────────────────────────────────────────────────────
def open_map(pair):
    try:
        return {str(o.get("orderId") or o.get("id") or o.get("order_id")): o
                for o in (dex.get_open_orders(pair) or [])}
    except Exception as e:
        print(f"  [{pair}] get_open_orders failed: {str(e)[:70]}")
        return None                                          # unknown ≠ empty

def cancel_all(pair):
    om = open_map(pair) or {}
    for oid in om:
        try:
            dex.cancel_order(pair, oid)
        except Exception as e:
            print(f"  [{pair}] cancel {oid} failed: {str(e)[:70]}")
    ORDERS[pair] = {"buy": None, "sell": None}

def record_fill(pair, side, px, qty):
    global vol, fills, realized, last_fill_ts
    if qty <= 0:
        return
    delta = apply_fill(POS[pair], side, px, qty)
    realized += delta; vol += px * qty; fills += 1; last_fill_ts = time.time()
    print(f"FILL {side.upper()} {pair} {qty:.6f}@{px:g} vol+=${px*qty:.2f} tot=${vol:.2f} "
          f"realized={realized:+.4f} inv={POS[pair]['qty']:.6f}@{POS[pair]['avg']:g}", flush=True)
    save_state()

def poll_fills(pair):
    """id-based fill detection via get_open_orders `remaining` (R3-validated:
    balance deltas can't tell a reservation from a fill on a two-sided book)."""
    om = open_map(pair)
    if om is None:
        return                                               # read failed: keep beliefs
    for side in ("buy", "sell"):
        cur = ORDERS[pair][side]
        if not cur:
            continue
        o = om.get(cur["id"])
        if o is None:                                        # gone from book → fully filled
            record_fill(pair, side, cur["price"], cur["qty"] - cur["filled"])
            ORDERS[pair][side] = None
        else:
            remaining = float(o.get("remaining") or cur["qty"])
            newly = max(0.0, cur["qty"] - remaining) - cur["filled"]
            if newly > 1e-12:
                record_fill(pair, side, cur["price"], newly)
                cur["filled"] += newly

def place_side(pair, side, px, qty):
    sp = SPECS[pair]
    before = set((open_map(pair) or {}).keys())
    try:
        res = dex.place_order(pair, side, qty, order_type="postonly",
                              limit_price=px, funding="wallet", skip_sim=True)
    except Exception as e:
        print(f"  [{pair}] place {side} error: {str(e)[:80]}"); return
    st = res.get("status")
    if st == "success":                                      # filled inside the settle window
        record_fill(pair, side, px, qty); return
    if st not in ("placed_unfilled", "unverified"):
        print(f"  [{pair}] place {side} {st}: {str(res)[:90]}"); return
    for _ in range(3):
        new = set((open_map(pair) or {}).keys()) - before
        if new:
            ORDERS[pair][side] = {"id": next(iter(new)), "price": px, "qty": qty, "filled": 0.0}
            print(f"  [{pair}] resting {side} {qty:g}@{px:g}", flush=True)
            return
        time.sleep(1)
    print(f"  [{pair}] placed {side} but id not visible yet — re-checked next tick")

def cancel_side(pair, side):
    cur = ORDERS[pair][side]
    if not cur:
        return
    try:
        dex.cancel_order(pair, str(cur["id"]))
    except Exception as e:
        print(f"  [{pair}] cancel {side} failed: {str(e)[:70]}")
    ORDERS[pair][side] = None

def flatten(pair, snap, attempts=4):
    """IOC-sell all base into the bid — the only place v2 ever crosses the
    spread, used at stop/exit so no bag survives the run."""
    sp = SPECS[pair]
    for att in range(attempts):
        b = w.erc20_balance(sp["base"], sp["bdec"])          # sell what the WALLET holds
        if b < sp["minq"]:
            return True
        q = round_lot(b, sp["lot"], sp["minq"])
        if q <= 0:
            return True                                      # sub-lot dust: unsellable, not a bag
        try:
            ob = book(sp) or snap
            px = snap_down(ob["bid"] * (1 - 0.004 * (att + 1)), sp["tick"])
            dex.place_order(pair, "sell", q, order_type="ioc",
                            limit_price=px, funding="wallet", skip_sim=True)
        except Exception as e:
            print(f"  [{pair}] flatten attempt {att} err {str(e)[:70]}")
        time.sleep(3)
    return w.erc20_balance(sp["base"], sp["bdec"]) < sp["minq"]

def stop(reason):
    print(f"\nshutting down: {reason}")
    for p, sp in SPECS.items():
        cancel_all(p)
    time.sleep(2)                                            # let cancels release funds
    for p, sp in SPECS.items():
        snap = None
        try:
            snap = book(sp)
        except Exception:
            pass
        if snap and not flatten(p, snap):
            print(f"!! WARNING {p} residual base — flatten manually")
    save_state()
    nw = networth({p: (book(sp) or {}).get("mid") for p, sp in SPECS.items()})
    print(f"=== STOP: {reason} ===")
    print(f"fills={fills} volume=${vol:.2f} realized={realized:+.4f} "
          f"networth=${nw:.4f} bleed=${NW_START - nw:+.4f} gas={S_START - w.native_balance():.4f} SOMI")
    raise SystemExit(0)

NW_START = 0.0; S_START = 0.0; SOMI_PX = 0.10   # real values set at startup below;
                                                # placeholders keep an early SIGTERM safe
def _on_term(signum, frame):
    print(f"\n[signal {signum}] cancel + flatten + exit")
    try:
        stop("SIGTERM")
    except SystemExit:
        raise
signal.signal(signal.SIGTERM, _on_term)


def trend_gate(pair) -> bool:
    """allow_buy: pause buying in a confirmed 24h downtrend (hysteresis). The
    sell side always stays on. Fails OPEN (unknown trend keeps the last mode)."""
    t = TREND[pair]
    if time.time() - t["ts"] > TREND_TTL_S:
        pct = None
        try:
            c = dex.get_candles(pair, interval="1h", limit=25)
            if c and len(c) >= 2 and float(c[0]["close"]) > 0:
                pct = (float(c[-1]["close"]) - float(c[0]["close"])) / float(c[0]["close"])
        except Exception:
            pass
        t["mode"] = trend_mode(t["mode"], pct, TREND_UP, TREND_DOWN)
        t["ts"] = time.time(); t["pct"] = pct
    return t["mode"] != "down"


# ── main ───────────────────────────────────────────────────────────────────
print(f"=== MAKER V2 pairs={list(SPECS)} wallet={w.address[:10]}… ===")
print(f"leg=${LEG_USD} cap=${CAP_USD}/pair margin={MARGIN_T}t drift={DRIFT_T}t "
      f"stop={STOP_PCT:.0%}/{STOP_SLIP:.0%} bleed_cap=${MAX_BLEED} max_s={MAX_S or '∞'}")
try:
    _sob = dex.get_orderbook("SOMI:USDso")
    SOMI_PX = (_sob["bid"] + _sob["ask"]) / 2 if _sob.get("bid") and _sob.get("ask") else 0.10
except Exception:
    SOMI_PX = 0.10

for p, sp in SPECS.items():                                  # leg must clear the min order
    snap0 = book(sp)
    if snap0 and LEG_USD < sp["minq"] * snap0["mid"] * 1.05:
        print(f"!! leg ${LEG_USD} < {p} min order ~${sp['minq']*snap0['mid']:.2f} — ABORT")
        raise SystemExit(1)
    cancel_all(p)                                            # start with a clean book

load_state()
for p, sp in SPECS.items():                                  # chain is the qty authority
    POS[p]["qty"] = held_base(sp)
    if POS[p]["qty"] >= sp["minq"] and POS[p]["avg"] <= 0:
        m0 = book(sp)
        POS[p]["avg"] = m0["mid"] if m0 else 0.0
        print(f"!! {p} starts with inventory {POS[p]['qty']:g} and no saved avg — "
              f"using mid {POS[p]['avg']:g} as cost basis")

NW_START = networth({p: (book(sp) or {}).get("mid") for p, sp in SPECS.items()})
S_START = w.native_balance()
T_START = time.time()
print(f"START networth=${NW_START:.4f} SOMI={S_START:.4f} somi_px={SOMI_PX:.4f}", flush=True)

i = 0
while True:
    i += 1
    try:
        if MAX_S and time.time() - T_START > MAX_S:
            stop("max runtime reached")
        if TARGET_VOL and vol >= TARGET_VOL:
            stop("target volume reached")
        if w.native_balance() <= SOMI_FLOOR:
            stop("SOMI gas floor hit")

        # Liveness: one tiny IOC buy if no fill for LIVENESS_S — a real trade
        # that resets the 24h-idle DQ clock; the maker's ask sells it back.
        if LIVENESS_S and time.time() - last_fill_ts > LIVENESS_S:
            for p0, sp0 in SPECS.items():
                snap0 = book(sp0)
                if not snap0:
                    continue
                q0 = round_lot(max(KEEPALIVE_USD, sp0["minq"] * snap0["mid"] * 1.05) / snap0["mid"],
                               sp0["lot"], sp0["minq"])
                if q0 <= 0:
                    continue
                px0 = snap_up(snap0["ask"] * 1.003, sp0["tick"])
                print(f"  [{p0}] KEEPALIVE: idle {(time.time()-last_fill_ts)/3600:.1f}h — tiny IOC buy {q0:g}")
                try:
                    res0 = dex.place_order(p0, "buy", q0, order_type="ioc",
                                           limit_price=px0, funding="wallet", skip_sim=True)
                    if res0.get("status") == "success":
                        record_fill(p0, "buy", px0, q0)
                except Exception as e:
                    print(f"  [{p0}] keepalive err {str(e)[:70]}")
                break

        mids = {}
        for p, sp in SPECS.items():
            poll_fills(p)
            POS[p]["qty"] = held_base(sp)                    # chain-authoritative
            snap = book(sp)
            if not snap:
                continue
            mids[p] = snap["mid"]

            act = stop_loss_action({"inv_base": POS[p]["qty"], "avg_cost": POS[p]["avg"],
                                    "mid": snap["mid"], "bid": snap["bid"], "minq": sp["minq"],
                                    "stop_pct": STOP_PCT, "max_slip_pct": STOP_SLIP})
            if act == "cut":
                print(f"  [{p}] STOP-LOSS: mid {snap['mid']:g} < avg {POS[p]['avg']:g} -{STOP_PCT:.0%}")
                cancel_all(p); time.sleep(2)
                qty_cut = POS[p]["qty"]
                if flatten(p, snap) and qty_cut > 0:
                    record_fill(p, "sell", snap["bid"], qty_cut)   # ≈ bid; bounded by slip floor
                REENTER[p] = time.time() + STOP_COOL_S
                continue
            if act == "defer":
                print(f"  [{p}] stop DEFERRED — bid gapped below slip floor; holding")
                continue

            allow_buy = trend_gate(p) and time.time() >= REENTER[p]
            free = max(0.0, wallet_usdso() - RESERVE_USD)
            leg_j = LEG_USD * (1 + random.uniform(-LEG_JITTER, LEG_JITTER))
            want = desired_quotes({**snap, "tick": sp["tick"], "lot": sp["lot"], "minq": sp["minq"],
                                   "inv_base": POS[p]["qty"], "avg_cost": POS[p]["avg"],
                                   "leg_usd": leg_j, "cap_usd": CAP_USD, "quote_avail": free,
                                   "margin_ticks": MARGIN_T, "allow_buy": allow_buy,
                                   "inv_floor_base": INV_FLOOR_PCT * CAP_USD / snap["mid"]})
            for side in ("buy", "sell"):
                cur, tgt = ORDERS[p][side], want.get(side)
                if tgt is None:
                    if cur:
                        cancel_side(p, side)
                    continue
                px, qty = tgt
                if cur is None:
                    place_side(p, side, px, qty)
                elif should_requote(cur["price"], px, sp["tick"], DRIFT_T):
                    cancel_side(p, side); place_side(p, side, px, qty)

        # hard no-bleed guard on TRUE capital (every ~20 ticks to save RPC)
        if i % 20 == 0 and mids:
            nw = networth(mids)
            if NW_START - nw > MAX_BLEED:
                stop(f"bleed ${NW_START - nw:.2f} > cap ${MAX_BLEED}")
            print(f"[{i}] hb networth=${nw:.4f} ({nw - NW_START:+.4f}) vol=${vol:.2f} "
                  f"fills={fills} realized={realized:+.4f} tot=${vol:.2f}", flush=True)
        time.sleep(POLL_S)
    except SystemExit:
        raise
    except Exception as e:
        try:
            w.reset_nonce()
        except Exception:
            pass
        print(f"[{i}] EXC {str(e)[:120]} — backing off 15s", flush=True)
        time.sleep(15)
