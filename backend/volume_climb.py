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
from trading.legsize import touch_fit_leg, touch_depth_usd

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
# Depth-aware leg: opt-in, active only when BOTH bounds are set. Off ⇒ fixed LEG_USD.
LEG_MIN         = float(os.environ.get("CLIMB_LEG_MIN", "0"))
LEG_MAX         = float(os.environ.get("CLIMB_LEG_MAX", "0"))
TOUCH_FRAC      = float(os.environ.get("CLIMB_TOUCH_FRAC", "0.8"))
DYNAMIC_LEG     = LEG_MIN > 0 and LEG_MAX > 0 and LEG_MAX >= LEG_MIN
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
VAULT_ABI = [
 {"name":"getWithdrawableBalance","type":"function","stateMutability":"view",
  "inputs":[{"name":"user","type":"address"},{"name":"token","type":"address"}],
  "outputs":[{"name":"","type":"uint256"}]},
]

def vault_usdso():
    """USDso sitting inside the pools — an unfilled BUY RESERVES quote there, so it
    leaves the wallet without being spent. Counting only the wallet made a reserved
    order look like a ~$100 loss and falsely tripped the bleed cap."""
    tot = 0.0
    for sp in SPECS.values():
        try:
            c = w.w3.eth.contract(address=sp["pool"], abi=VAULT_ABI)
            tot += c.functions.getWithdrawableBalance(
                w.address, Web3.to_checksum_address(sp["quote"])).call() / (10 ** sp["qdec"])
        except Exception:
            pass
    return tot

def usdso_total():
    """True USDso capital: free in the wallet + reserved/withdrawable in the pools."""
    return ub() + vault_usdso()
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

_leg_desc = (f"${LEG_MIN:.0f}-${LEG_MAX:.0f}(dyn {TOUCH_FRAC:g}x)"
             if DYNAMIC_LEG else f"${LEG_USD:.0f}")
print(f"=== VOLUME CLIMB (rotating) pairs={PAIRS} ===")
print(f"target=${TARGET_VOLUME} leg={_leg_desc} slip={SLIP_PCT*100}% "
      f"caps: gas<{MAX_GAS_SOMI} floor={SOMI_FLOOR} bleed<${MAX_USDSO_BLEED} iters<{MAX_ITERS}")
u_start, s_start = usdso_total(), sb()
print(f"START USDso={u_start:.4f} (wallet+reserved) SOMI={s_start:.4f}")
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
# COST_CEIL_PER_1K is now a PRE-TRADE ceiling on the toll the live book implies
# (effective spread x 5), not on the realized rolling mean. Measured over 54 trips:
# implied toll had stdev 0.045 while realized had 0.234 — 5.2x the noise — because
# realized also carries the price drift during the ~30-60s we hold inventory. Gating
# on realized meant pausing at random rather than when trading was actually dear.
COST_CEIL_PER_1K = float(os.environ.get("CLIMB_COST_CEIL_PER_1K", "0"))
SPREAD_GATE_PCT  = float(os.environ.get("CLIMB_SPREAD_GATE_PCT", "0"))
PAUSE_EXP_S      = float(os.environ.get("CLIMB_PAUSE_EXP_S", "30"))
# Realized cost is demoted to a slow SAFETY BREAKER: a wide window, and it only
# fires when realized runs far past what the book implied — i.e. something is
# structurally wrong (bad fills, hidden slippage), not just noise.
COST_WINDOW      = int(os.environ.get("CLIMB_COST_WINDOW", "50"))
BREAKER_MULT     = float(os.environ.get("CLIMB_REALIZED_BREAKER_MULT", "2.0"))
_costs = deque(maxlen=COST_WINDOW)
try:
    _sob = dex.get_orderbook("SOMI:USDso")
    SOMI_PX = (_sob["bid"]+_sob["ask"])/2 if _sob.get("bid") and _sob.get("ask") else 0.10
except Exception:
    SOMI_PX = 0.10
print(f"cost-aware: spread_gate={SPREAD_GATE_PCT}% cost_ceil=${COST_CEIL_PER_1K}/1k window={COST_WINDOW} somi_px={SOMI_PX:.4f}")

# ── Arena Phase-0: pair boosts + weekly window ────────────────────────────
# The Algo Arena scores volume × a per-pair weekly boost (1.2–1.5×, announced
# manually each Monday) over Monday-00:00-UTC → Sunday weeks. Boosts arrive via
# data/boosts.json (written by the control API's POST /boosts — ./data is
# volume-mounted into the container) and are re-read at most every 60s, so a
# Monday announcement takes effect mid-run without a restart. No file / empty
# file ⇒ every boost 1.0 ⇒ exactly the old rotation (safe under R4 too).
BOOSTS_FILE   = os.environ.get("CLIMB_BOOSTS_FILE", "data/boosts.json")
WEEKLY_TARGET = float(os.environ.get("CLIMB_WEEKLY_TARGET", "0"))  # 0 = no weekly cap

_boosts = {"ts": 0.0, "map": {}}
def pair_boosts() -> dict:
    now = time.time()
    if now - _boosts["ts"] >= 60:
        _boosts["ts"] = now
        try:
            import json
            with open(BOOSTS_FILE) as fh:
                raw = json.load(fh)
            src = raw.get("boosts", raw) if isinstance(raw, dict) else {}
            m = {}
            for k, v in src.items():
                try:
                    b = float(v)
                except (TypeError, ValueError):
                    continue
                if 0.5 <= b <= 5.0:   # sanity band — a typo'd 15 must not hijack rotation
                    m[str(k)] = b
            _boosts["map"] = m
        except FileNotFoundError:
            _boosts["map"] = {}
        except Exception as e:
            print(f"  boosts.json read err {str(e)[:60]} — keeping {_boosts['map']}")
    return _boosts["map"]

def week_idx(ts: float) -> int:
    """Contest week number. Weeks run Monday 00:00 UTC → Sunday 23:59 UTC; the
    unix epoch was a Thursday, so a 3-day shift Monday-aligns the boundary."""
    return int((ts + 3 * 86400) // 604800)

wk_cur = week_idx(time.time()); wk_vol = 0.0   # Arena week window (Mon 00:00 UTC)

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
tg(f"🚀 Volume run: pairs {PAIRS} target ${TARGET_VOLUME:,.0f}, leg {_leg_desc}, cost-ceil ${COST_CEIL_PER_1K}/1k")

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
    u, s = usdso_total(), sb()
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

def book_levels(pair, n=8):
    """Top n levels each side: ([(price,qty)...bids], [(price,qty)...asks])."""
    r = _rq.get(f"{config.DREAMDEX_HTTP}/v0/orderbooks", params={"symbols": pair}, timeout=6)
    r.raise_for_status()
    d = r.json()
    if isinstance(d, dict) and d.get("orderbooks"):
        b = d["orderbooks"][0]
    elif isinstance(d, list) and d:
        b = d[0]
    else:
        b = d
    def side(k):
        out = []
        for L in (b.get(k) or [])[:n]:
            if isinstance(L, dict):
                out.append((float(L["price"]), float(L.get("quantity", L.get("size", 0)))))
            else:
                out.append((float(L[0]), float(L[1])))
        return out
    return side("bids"), side("asks")

def _vwap(levels, need):
    """Volume-weighted fill price for `need` base walking down the levels.
    None when the visible book can't cover it."""
    filled = cost = 0.0
    for p, q in levels:
        take = min(q, need - filled)
        if take <= 0:
            break
        cost += take * p; filled += take
        if filled >= need * 0.999:
            return cost / filled
    return None

def eff_spread_pct(bids, asks, leg_usd):
    """The spread we ACTUALLY pay for a leg of this size — buy VWAP vs sell VWAP.
    Equals the quoted spread when top-of-book covers us; worse when we'd walk.
    None = not enough visible depth (that pair would slip badly; skip it)."""
    if not bids or not asks:
        return None
    mid = (bids[0][0] + asks[0][0]) / 2
    if mid <= 0:
        return None
    need = leg_usd / mid
    buy, sell = _vwap(asks, need), _vwap(bids, need)
    if buy is None or sell is None:
        return None
    return (buy - sell) / mid * 100

def _leg_for(bids, asks, sp):
    """Per-pair leg for this round: depth-fitted into [LEG_MIN,LEG_MAX] when
    dynamic sizing is on, else the fixed LEG_USD (backward-compat default)."""
    mid = (bids[0][0] + asks[0][0]) / 2
    depth = touch_depth_usd(bids, asks)
    return touch_fit_leg(depth, mid, sp["minq"], leg_min=LEG_MIN, leg_max=LEG_MAX,
                         frac=TOUCH_FRAC, fixed_leg=LEG_USD, dynamic=DYNAMIC_LEG)

def pick_cheapest(fixed_leg):
    """Pick the pair with the lowest EFFECTIVE spread — what we'd really pay after
    walking the book — not the top-of-book quote. Each pair is evaluated at ITS
    OWN depth-fitted leg (a touch-fitting leg makes eff ≈ quoted, the tightest
    achievable). A pair whose visible depth can't absorb the leg is skipped.

    Boost-aware (Arena): pairs are ranked by eff ÷ boost — the spread paid per
    unit of SCORE, not per unit of raw volume — and the gates scale the same way
    (a 1.5× pair is allowed 1.5× the toll for the same score). All boosts 1.0
    reduces this to the plain effective-spread ranking.

    Returns (spec, ob, eff_pct, quoted_pct, boost, within_gate, leg); (None,...)
    if no pair has a usable book. `leg` is the winner's depth-fitted size, used
    for the trip. Falls back to top-of-book (and the fixed leg) if depth fails."""
    boosts = pair_boosts()
    best = None
    for sp in SPECS.values():
        try:
            bids, asks = book_levels(sp["pair"])
            if not bids or not asks:
                continue
            ob = {"bid": bids[0][0], "ask": asks[0][0]}
            mid = (ob["bid"] + ob["ask"]) / 2
            quoted = (ob["ask"] - ob["bid"]) / mid * 100
            leg = _leg_for(bids, asks, sp)
            eff = eff_spread_pct(bids, asks, leg)
            if eff is None:
                print(f"  {sp['pair']}: visible book too thin for a ${leg:.0f} leg — skip")
                continue
        except Exception:
            # depth endpoint hiccup — fall back to top-of-book for this pair
            try:
                ob = dex.get_orderbook(sp["pair"])
            except Exception:
                continue
            if not ob.get("bid") or not ob.get("ask"):
                continue
            mid = (ob["bid"] + ob["ask"]) / 2
            quoted = eff = (ob["ask"] - ob["bid"]) / mid * 100
            leg = fixed_leg   # no depth data to size from → fixed leg
        bst = boosts.get(sp["pair"], 1.0)
        if best is None or eff / bst < best[0]:
            best = (eff / bst, sp, ob, eff, quoted, bst, leg)
    if best is None:
        return None, None, None, None, 1.0, False, fixed_leg
    _, sp, ob, eff, quoted, bst, leg = best
    # A round-trip crosses the effective spread once on a notional of `leg`, and
    # books 2x leg of volume — so the toll per 1k of volume is eff% x 5.
    implied_toll = eff * 5.0
    within = True
    if SPREAD_GATE_PCT > 0 and eff / bst > SPREAD_GATE_PCT:
        within = False
    if COST_CEIL_PER_1K > 0 and implied_toll / bst > COST_CEIL_PER_1K:
        within = False
    return sp, ob, eff, quoted, bst, within, leg

for i in range(MAX_ITERS):
    try:
        if vol >= TARGET_VOLUME: stop("target volume reached")

        # Arena week rollover + optional weekly cap. The cap IDLES (engine stays
        # up, resumes Monday) instead of stopping — a stop would end the run and
        # need a manual relaunch every week. NOTE: while idling here nothing
        # trades, which is fine under Arena rules (no idle DQ) — leave
        # CLIMB_WEEKLY_TARGET=0 in contests that DQ idle wallets (R4 rule 11).
        nw = week_idx(time.time())
        if nw != wk_cur:
            wk_cur = nw; wk_vol = 0.0
            print(f"[{i}] new contest week (idx {nw}) — weekly volume counter reset")
            tg(f"📅 New contest week — weekly counter reset (lifetime ${vol:,.0f})")
        if WEEKLY_TARGET > 0 and wk_vol >= WEEKLY_TARGET:
            print(f"[{i}] weekly target ${WEEKLY_TARGET:,.0f} reached (wk ${wk_vol:,.0f}) — idling until Monday")
            time.sleep(PAUSE_EXP_S * 4); continue

        # Sweep stray bags FIRST, so the capital checks below measure a flat wallet.
        # A prior trip's sell may have failed on a pair the rotation isn't currently
        # picking, and base inventory hides value from the USDso reading.
        bagged = [sp for sp in SPECS.values() if bb(sp) > sp["minq"]]
        if bagged:
            print(f"[{i}] stray bag on {[sp['pair'] for sp in bagged]} — flattening first")
            flatten_all()
            bagged = [sp for sp in SPECS.values() if bb(sp) > sp["minq"]]

        s_now = sb()
        u_now = usdso_total()   # wallet + reserved-in-pool, never wallet alone
        if s_start - s_now >= MAX_GAS_SOMI: stop("max gas hit")
        if s_now <= SOMI_FLOOR: stop("SOMI floor hit")
        # Only trust the bleed reading when flat: base inventory we couldn't sell
        # would otherwise read as a loss and stop a perfectly healthy run.
        if bagged:
            print(f"[{i}] holding {[sp['pair'] for sp in bagged]} — skipping bleed check this pass")
        elif u_start - u_now >= MAX_USDSO_BLEED:
            stop("max USDso bleed hit")
        if consec_fail >= MAX_CONSEC_FAIL: stop(f"{MAX_CONSEC_FAIL} consecutive failures")

        # Rank by the spread we'd ACTUALLY pay at our leg size, not the quote.
        sp, ob, spread_pct, quoted_pct, boost, within, leg_pick = pick_cheapest(LEG_USD)
        if sp is None:
            print(f"[{i}] no pair with a usable book for the leg — pause {PAUSE_EXP_S:.0f}s")
            time.sleep(PAUSE_EXP_S); continue

        idle_s = time.time() - _last_trade_ts
        force_keepalive = (not within) and idle_s > LIVENESS_S
        if not within and not force_keepalive:
            toll = spread_pct * 5.0
            bnote = f" (boost {boost:g}x)" if boost != 1.0 else ""
            why = (f"toll ${toll:.3f}/1k{bnote} > ceil ${COST_CEIL_PER_1K}/1k x boost"
                   if (COST_CEIL_PER_1K > 0 and toll / boost > COST_CEIL_PER_1K)
                   else f"eff {spread_pct:.3f}%{bnote} > gate {SPREAD_GATE_PCT}% x boost")
            if not _paused: tg(f"⏸ Paused @ ${vol:,.0f} vol — book too dear: {why}"); _paused = True
            print(f"[{i}] book too dear on {sp['pair']}: eff {spread_pct:.3f}% -> toll ${toll:.3f}/1k ({why}) "
                  f"— pause {PAUSE_EXP_S:.0f}s (idle {idle_s/3600:.1f}h)")
            time.sleep(PAUSE_EXP_S); continue

        leg_this = leg_pick
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
        if week_idx(time.time()) != wk_cur:   # trip landed across a Monday boundary
            wk_cur = week_idx(time.time()); wk_vol = 0.0
        wk_vol += vt
        if _net_fails:
            print(f"[{i}] network recovered after {_net_fails} failed attempt(s)")
            tg(f"🌐 Network back @ ${vol:,.0f} vol — trading resumed"); _net_fails = 0
        u, s = usdso_total(), sb()
        cost_1k = ((u_now-u) + (s_now-s)*SOMI_PX)/vt*1000 if vt > 0 else 0.0
        _costs.append(cost_1k); roll = sum(_costs)/len(_costs)
        impact = spread_pct - quoted_pct
        implied = spread_pct * 5.0
        bnote = f" boost {boost:g}x" if boost != 1.0 else ""
        print(f"[{i}] trip {trips} {tag} leg=${leg_this:.0f} @eff {spread_pct:.3f}% (quoted {quoted_pct:.3f}%, impact {impact:+.3f}%){bnote}: "
              f"vol+=${vt:.2f} tot=${vol:.2f} wk=${wk_vol:.2f} USDso={u:.4f}(bleed ${u_start-u:.4f}) SOMI={s:.4f} "
              f"| implied ${implied:.3f}/1k cost ${cost_1k:.3f}/1k roll ${roll:.3f}/1k")
        if _paused:
            tg(f"▶️ Resumed @ ${vol:,.0f} vol — book cheap again"); _paused = False
        if vol >= _last_ms + TG_MS:
            _last_ms = vol; tg(f"📊 +${vol:,.0f} vol | roll ${roll:.3f}/1k | USDso ${u:.0f} | gas {s_start-s:.1f} SOMI")
        # SAFETY BREAKER (not the throttle): only when realized cost runs far past
        # the book over a FULL wide window does something structural look wrong.
        breaker = COST_CEIL_PER_1K * BREAKER_MULT
        if COST_CEIL_PER_1K > 0 and len(_costs) >= COST_WINDOW and roll > breaker:
            tg(f"🚨 Realized ${roll:.3f}/1k > {BREAKER_MULT:g}x ceil (${breaker:.3f}) over {COST_WINDOW} trips — pausing to reassess")
            print(f"[{i}] BREAKER: realized ${roll:.3f}/1k > {BREAKER_MULT:g}x ceil ${breaker:.3f}/1k over "
                  f"{COST_WINDOW} trips — book implied far less; pausing {PAUSE_EXP_S*4:.0f}s")
            _costs.clear(); time.sleep(PAUSE_EXP_S * 4)
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
