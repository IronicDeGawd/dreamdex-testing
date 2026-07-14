#!/usr/bin/env python3
"""Atomic round-trip taker — EIP-7702 buy+sell in ONE transaction, full-featured.

Same volume-churn objective as volume_climb (buy then sell the same size back,
booking raw volume at ~zero inventory), but each round-trip is a SINGLE tx: the
wallet delegates to RoundTrip7702 (EIP-7702) and calls roundTrip(...) on itself,
so the IOC buy and IOC sell settle atomically. No inter-leg drift, no bag on a
failed sell (it reverts the buy), ~1 confirmation per trip, and an on-chain toll
cap. The EOA stays msg.sender, so contest volume attributes to the wallet.

Feature parity with the steady taker:
  - MULTI-PAIR rotation: ATOM_PAIRS is a comma list; each round-trip picks the
    pair with the lowest EFFECTIVE spread (VWAP-at-leg, not the top-of-book
    quote), skipping pairs too thin for the leg.
  - BOOST-AWARE (Arena): pairs ranked by eff-spread ÷ boost; gates scale by
    boost. Boosts come from data/boosts.json (re-read ~60s, no restart).
  - WEEKLY window: Monday-00:00-UTC weeks; ATOM_WEEKLY_TARGET idles (not stops)
    until the next week. Leave 0 under R4 (idle DQ).
  - COST gating: pre-trade book-implied toll ceiling + spread gate, plus the
    on-chain per-trip toll cap. Pauses on a dear book instead of burning gas.
  - Telegram milestone / pause / resume alerts.
  - 18h liveness keepalive: forces a tiny trip over the gate to dodge idle DQ.

Delegation is installed ONCE (a type-4 tx); trades are cheap type-2 self-calls
(ATOM_TX_MODE=type4 re-authorizes every trip instead). Delegate address from
ATOM_DELEGATE_ADDR or config.ROUNDTRIP_DELEGATE.

R4-ONLY: atomic same-block round-trips are exactly what Algo Arena fair-play
flags. Use only while raw volume is scored with no shape rules.

Env knobs (all optional): ATOM_PAIRS ATOM_PAIR ATOM_TARGET ATOM_LEG_USD ATOM_SLIP
  ATOM_SPREAD_GATE_PCT ATOM_COST_CEIL_PER_1K ATOM_MAX_TOLL_PER_1K
  ATOM_WEEKLY_TARGET ATOM_BOOSTS_FILE ATOM_SOMI_FLOOR ATOM_MAX_GAS_SOMI
  ATOM_MAX_USDSO_BLEED ATOM_DELEGATE_ADDR ATOM_TX_MODE ATOM_GAS
  ATOM_LIVENESS_S ATOM_KEEPALIVE_LEG ATOM_TG_MILESTONE ATOM_PAUSE_S
  ATOM_PAUSE_EXP_S ATOM_MAX_ITERS ATOM_MAX_S ATOM_PRIVATE_KEY ATOM_ADDRESS
"""
import os, sys, time, math, signal
from decimal import Decimal
sys.path.insert(0, "/app")
import config
import requests as _rq
from web3 import Web3
from eth_account import Account
from trading.dreamdex import DreamDEX
from trading.delegate import encode_roundtrip, decode_trip

ELIGIBLE = {"WBTC:USDso", "WETH:USDso", "SOMI:USDso"}

_pairs_env = os.environ.get("ATOM_PAIRS", os.environ.get("ATOM_PAIR", "WETH:USDso"))
PAIRS_IN   = [p.strip() for p in _pairs_env.split(",") if p.strip()]
TARGET     = float(os.environ.get("ATOM_TARGET", "100000"))
LEG_USD    = float(os.environ.get("ATOM_LEG_USD", "25"))
SLIP       = float(os.environ.get("ATOM_SLIP", "0.004"))
SPREAD_GATE_PCT = float(os.environ.get("ATOM_SPREAD_GATE_PCT", "0.15"))
COST_CEIL_PER_1K = float(os.environ.get("ATOM_COST_CEIL_PER_1K", "0"))   # 0 = off
MAX_TOLL_PER_1K = float(os.environ.get("ATOM_MAX_TOLL_PER_1K", "0.30"))  # on-chain cap
WEEKLY_TARGET   = float(os.environ.get("ATOM_WEEKLY_TARGET", "0"))
GAS_FLOOR  = float(os.environ.get("ATOM_SOMI_FLOOR", "3"))
MAX_GAS_SOMI = float(os.environ.get("ATOM_MAX_GAS_SOMI", "120"))
MAX_USDSO_BLEED = float(os.environ.get("ATOM_MAX_USDSO_BLEED", "40"))
TX_MODE    = os.environ.get("ATOM_TX_MODE", "type2").lower()
GAS        = int(os.environ.get("ATOM_GAS", "6000000"))
LIVENESS_S = float(os.environ.get("ATOM_LIVENESS_S", str(18 * 3600)))
KEEPALIVE_LEG = float(os.environ.get("ATOM_KEEPALIVE_LEG", "5"))
PAUSE_S    = float(os.environ.get("ATOM_PAUSE_S", "0.4"))
PAUSE_EXP_S = float(os.environ.get("ATOM_PAUSE_EXP_S", "45"))
MAX_ITERS  = int(os.environ.get("ATOM_MAX_ITERS", "40000"))
MAX_S      = float(os.environ.get("ATOM_MAX_S", "0"))
MAX_CONSEC_FAIL = 5
DELEGATE   = os.environ.get("ATOM_DELEGATE_ADDR", "") or getattr(config, "ROUNDTRIP_DELEGATE", "")
BOOSTS_FILE = os.environ.get("ATOM_BOOSTS_FILE", "data/boosts.json")

if not DELEGATE:
    sys.exit("ATOM_DELEGATE_ADDR / config.ROUNDTRIP_DELEGATE required (deploy via tools/deploy_delegate.py)")
DELEGATE = Web3.to_checksum_address(DELEGATE)

dex = DreamDEX(private_key=os.environ.get("ATOM_PRIVATE_KEY") or None,
               address=os.environ.get("ATOM_ADDRESS") or None)
dex._ensure_auth()
try:
    dex.refresh_market_params()
except Exception as e:
    print(f"!! market-param refresh failed at boot: {e} — ABORT (tick/lot would be wrong)")
    raise SystemExit(1)
w = dex.wallet
acct = Account.from_key(w.private_key)
# The 7702 delegation lives on the SIGNER — key is authoritative for the address.
if w.address.lower() != acct.address.lower():
    print(f"[wallet] address {w.address} != key address {acct.address} — using key's")
    w.address = acct.address
w3 = w.w3


def spec_for(pair):
    m = config.MARKETS[pair]
    lot = float(m.get("lotSize", 0.0001))
    return {
        "pair":  pair,
        "base":  Web3.to_checksum_address(m["base"]),  "bdec": int(m["baseDecimals"]),
        "quote": Web3.to_checksum_address(m["quote"]), "qdec": int(m["quoteDecimals"]),
        "pool":  Web3.to_checksum_address(m["contract"]),
        "tick":  float(m.get("tickSize", 0.01)),
        "lot":   lot,
        "minq":  float(m.get("minQuantity", lot)),
    }


SPECS = {}
for p in PAIRS_IN:
    if p not in ELIGIBLE:
        print(f"  skip {p}: not an eligible pair (rule 5)"); continue
    m = config.MARKETS[p]
    if m.get("native") or int(str(m["base"]), 16) == 0:
        print(f"  skip {p}: native-base pair not supported by the ERC20 round-trip path"); continue
    SPECS[p] = spec_for(p)
if not SPECS:
    print("!! no tradeable ERC20 pairs in ATOM_PAIRS — ABORT"); raise SystemExit(1)
PAIRS = list(SPECS.keys())
QUOTE = SPECS[PAIRS[0]]["quote"]; QDEC = SPECS[PAIRS[0]]["qdec"]


def px_raw(human, dec): return int(Decimal(str(human)) * (10 ** dec))
def ub():      return w.erc20_balance(QUOTE, QDEC)              # free USDso
def bb(sp):    return w.erc20_balance(sp["base"], sp["bdec"])
def sb():      return w.native_balance()
def snap_lot(sp, q):
    n = math.floor(q / sp["lot"]); q = round(n * sp["lot"], 10)
    return q if q >= sp["minq"] else round(sp["minq"], 10)
def snap_price(sp, p, up=False):
    t = sp["tick"]; n = math.ceil(p / t) if up else math.floor(p / t)
    return round(n * t, 10)

try:
    _sob = dex.get_orderbook("SOMI:USDso")
    SOMI_PX = (_sob["bid"] + _sob["ask"]) / 2 if _sob.get("bid") and _sob.get("ask") else 0.10
except Exception:
    SOMI_PX = 0.10

# ── Arena boosts + weekly window ────────────────────────────────────────────
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
                if 0.5 <= b <= 5.0:
                    m[str(k)] = b
            _boosts["map"] = m
        except FileNotFoundError:
            _boosts["map"] = {}
        except Exception as e:
            print(f"  boosts.json read err {str(e)[:60]} — keeping {_boosts['map']}")
    return _boosts["map"]

def week_idx(ts: float) -> int:
    """Monday-00:00-UTC week number (epoch was a Thursday → +3-day shift)."""
    return int((ts + 3 * 86400) // 604800)

# ── Telegram alerts ─────────────────────────────────────────────────────────
TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", ""); TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "")
TG_MS = float(os.environ.get("ATOM_TG_MILESTONE", "25000"))
def tg(msg):
    if not TG_TOKEN or not TG_CHAT: return
    try:
        _rq.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                 json={"chat_id": TG_CHAT, "text": msg}, timeout=10)
    except Exception: pass

# ── Book depth + effective-spread selection (boost-aware) ───────────────────
def book_levels(pair, n=8):
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

def pick_cheapest(leg_usd):
    """Return (spec, ob, eff_pct, quoted_pct, boost, within_gate) for the pair
    with the lowest effective-spread ÷ boost, or (None,...) if none has a usable
    book for the leg. Falls back to top-of-book if the depth fetch fails."""
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
            eff = eff_spread_pct(bids, asks, leg_usd)
            if eff is None:
                print(f"  {sp['pair']}: visible book too thin for a ${leg_usd:.0f} leg — skip")
                continue
        except Exception:
            try:
                ob = dex.get_orderbook(sp["pair"])
            except Exception:
                continue
            if not ob.get("bid") or not ob.get("ask"):
                continue
            mid = (ob["bid"] + ob["ask"]) / 2
            quoted = eff = (ob["ask"] - ob["bid"]) / mid * 100
        bst = boosts.get(sp["pair"], 1.0)
        if best is None or eff / bst < best[0]:
            best = (eff / bst, sp, ob, eff, quoted, bst)
    if best is None:
        return None, None, None, None, 1.0, False
    _, sp, ob, eff, quoted, bst = best
    implied_toll = eff * 5.0   # round-trip crosses the eff spread once over 2x leg
    within = True
    if SPREAD_GATE_PCT > 0 and eff / bst > SPREAD_GATE_PCT:
        within = False
    if COST_CEIL_PER_1K > 0 and implied_toll / bst > COST_CEIL_PER_1K:
        within = False
    return sp, ob, eff, quoted, bst, within

# ── Delegation + trip execution ─────────────────────────────────────────────
def ensure_delegated():
    tgt = w.delegation_target()
    if tgt == DELEGATE:
        return True
    print(f"[deleg] target={tgt or '(none)'} — installing {DELEGATE}")
    h, r = w.install_delegation(DELEGATE, gas=GAS)
    ok = (r.status == 1 and w.delegation_target() == DELEGATE)
    print(f"[deleg] install tx {h} status={r.status} now={w.delegation_target()}")
    return ok

def build_trip(sp, ob, leg_usd):
    mid = (ob["bid"] + ob["ask"]) / 2
    qty = snap_lot(sp, leg_usd / mid)
    buy_px = snap_price(sp, ob["ask"] * (1 + SLIP), up=True)
    sell_px = snap_price(sp, ob["bid"] * (1 - SLIP))
    lot_raw = int(round(sp["lot"] * 10**sp["bdec"]))
    max_toll_quote = int(MAX_TOLL_PER_1K * (leg_usd * 2 / 1000.0) * 10**sp["qdec"])
    cd = encode_roundtrip(sp["base"], sp["quote"], sp["pool"],
                          px_raw(buy_px, sp["qdec"]), px_raw(sell_px, sp["qdec"]),
                          px_raw(qty, sp["bdec"]),
                          (int(time.time()) + 3600) * 1_000_000_000,
                          max_toll_quote, lot_raw)
    return cd, qty, mid

def preflight(calldata):
    try:
        w3.eth.call({"from": acct.address, "to": acct.address, "data": calldata, "gas": GAS})
        return True, ""
    except Exception as e:
        return False, str(e)[:90]

def send_trip(calldata) -> str:
    if TX_MODE == "type4":
        n = w.reserve_nonce()
        auth = w.sign_authorization(DELEGATE, n + 1)
        return w.send_type4_tx(acct.address, calldata, auth, gas=GAS, tx_nonce=n)
    return w.send_unsigned_tx({"to": acct.address, "data": calldata, "value": 0}, min_gas=GAS)

def flatten_all(max_attempts=6):
    """Sell any residual base on every pair back to USDso (never exit with a bag).
    Uses the plain API order path — no delegation involved."""
    for sp in SPECS.values():
        b = bb(sp)
        if b <= sp["minq"]:
            continue
        print(f"[flat] selling residual {b:.6f} {sp['pair'].split(':')[0]}")
        for att in range(max_attempts):
            try:
                ob = dex.get_orderbook(sp["pair"])
                if not ob.get("bid"):
                    time.sleep(2); continue
                dex.place_order(sp["pair"], "sell", snap_lot(sp, bb(sp)), order_type="ioc",
                                limit_price=snap_price(sp, ob["bid"] * (1 - SLIP * (att + 1))),
                                funding="wallet")
                time.sleep(2)
                if bb(sp) <= sp["minq"]:
                    break
            except Exception as e:
                print(f"[flat] {sp['pair']} attempt {att} err {str(e)[:60]}"); time.sleep(2)


vol = 0.0; trips = 0; reverts = 0; consec_fail = 0
wk_cur = week_idx(time.time()); wk_vol = 0.0
_paused = False; _last_ms = 0.0; _last_trade_ts = time.time()
somi0 = sb(); usdso0 = ub()

def stop(reason):
    flatten_all()
    u, s = ub(), sb()
    bags = {p: bb(sp) for p, sp in SPECS.items() if bb(sp) > sp["minq"]}
    print(f"=== STOP: {reason} ===")
    print(f"trips={trips} volume=${vol:.2f} reverts={reverts} USDso={u:.4f} "
          f"(bleed ${usdso0-u:+.4f}) gas={somi0-s:.4f} SOMI residual={bags}", flush=True)
    if bags:
        print(f"!! WARNING residual base {bags} — flatten manually")
    tg(f"🛑 Atomic stopped: {reason} | vol ${vol:,.0f} | bleed ${usdso0-u:.2f} | "
       f"gas {somi0-s:.1f} SOMI | flat={'yes' if not bags else 'NO'}")
    raise SystemExit(0)

def _on_term(signum, frame):
    print(f"[signal {signum}] flatten-and-exit...")
    try:
        flatten_all(max_attempts=3)
    except Exception as e:
        print(f"  term-flatten err {e}")
    raise SystemExit(0)
signal.signal(signal.SIGTERM, _on_term)

print(f"START ATOMIC pairs={PAIRS} leg=${LEG_USD} target=${TARGET} tx_mode={TX_MODE} "
      f"gate={SPREAD_GATE_PCT}% cost_ceil=${COST_CEIL_PER_1K}/1k toll_cap=${MAX_TOLL_PER_1K}/1k "
      f"delegate={DELEGATE} USDso={usdso0:.4f} SOMI={somi0:.4f}", flush=True)
tg(f"🚀 Atomic run: pairs {PAIRS} target ${TARGET:,.0f}, leg ${LEG_USD:.0f}")

if not ensure_delegated():
    print("=== STOP: delegation install failed ==="); raise SystemExit(1)
flatten_all()

t_start = time.time()
for i in range(MAX_ITERS):
    try:
        if vol >= TARGET:
            stop("target volume reached")
        if MAX_S and time.time() - t_start > MAX_S:
            stop(f"max_s {MAX_S}s reached")

        # Weekly rollover + optional weekly cap (idles, doesn't stop).
        nw = week_idx(time.time())
        if nw != wk_cur:
            wk_cur = nw; wk_vol = 0.0
            print(f"[{i}] new contest week (idx {nw}) — weekly counter reset")
            tg(f"📅 New contest week — weekly counter reset (lifetime ${vol:,.0f})")
        if WEEKLY_TARGET > 0 and wk_vol >= WEEKLY_TARGET:
            print(f"[{i}] weekly target ${WEEKLY_TARGET:,.0f} reached — idling until Monday")
            time.sleep(PAUSE_EXP_S * 4); continue

        somi = sb()
        if somi0 - somi >= MAX_GAS_SOMI: stop("max gas hit")
        if somi <= GAS_FLOOR: stop("SOMI floor hit")

        bagged = [sp for sp in SPECS.values() if bb(sp) > sp["minq"]]
        if bagged:
            print(f"[{i}] stray bag on {[sp['pair'] for sp in bagged]} — flattening first")
            flatten_all()
            bagged = [sp for sp in SPECS.values() if bb(sp) > sp["minq"]]
        if not bagged and usdso0 - ub() >= MAX_USDSO_BLEED:
            stop("max USDso bleed hit")
        if consec_fail >= MAX_CONSEC_FAIL:
            stop(f"{MAX_CONSEC_FAIL} consecutive failures")

        if not ensure_delegated():
            print("[deleg] reinstall failed — pause"); time.sleep(PAUSE_EXP_S); continue

        sp, ob, spread_pct, quoted_pct, boost, within = pick_cheapest(LEG_USD)
        if sp is None:
            print(f"[{i}] no pair deep enough for a ${LEG_USD:.0f} leg — pause {PAUSE_EXP_S:.0f}s")
            time.sleep(PAUSE_EXP_S); continue

        idle_s = time.time() - _last_trade_ts
        force_keepalive = (not within) and idle_s > LIVENESS_S
        if not within and not force_keepalive:
            toll = spread_pct * 5.0
            bnote = f" (boost {boost:g}x)" if boost != 1.0 else ""
            if not _paused:
                tg(f"⏸ Atomic paused @ ${vol:,.0f} vol — book too dear{bnote}"); _paused = True
            print(f"[{i}] book too dear on {sp['pair']}: eff {spread_pct:.3f}% -> toll ${toll:.3f}/1k{bnote} "
                  f"— pause {PAUSE_EXP_S:.0f}s (idle {idle_s/3600:.1f}h)")
            time.sleep(PAUSE_EXP_S); continue

        leg_this = LEG_USD
        if force_keepalive:
            leg_this = KEEPALIVE_LEG
            print(f"[{i}] KEEPALIVE: idle {idle_s/3600:.1f}h — forcing ${leg_this:.0f} trip on {sp['pair']}")
            tg(f"🫀 Atomic keepalive @ ${vol:,.0f} vol — idle {idle_s/3600:.1f}h, small trade to dodge idle DQ")

        calldata, qty, mid = build_trip(sp, ob, leg_this)
        if px_raw(qty, sp["bdec"]) <= 0 or qty < sp["minq"]:
            time.sleep(PAUSE_S); continue
        tag = sp["pair"].split(":")[0]

        ok, reason = preflight(calldata)
        if not ok:
            consec_fail += 1
            print(f"[{i}] {tag} preflight revert ({reason})")
            time.sleep(PAUSE_EXP_S if ("toll" in reason or "nofill" in reason) else PAUSE_S)
            continue

        try:
            h = send_trip(calldata)
        except Exception as e:
            consec_fail += 1
            print(f"[{i}] {tag} send err {str(e)[:80]}"); time.sleep(PAUSE_S); continue
        r = w.wait_for_receipt(h, timeout=60)
        if r.status != 1:
            reverts += 1; consec_fail += 1
            print(f"[{i}] {tag} REVERTED on-chain tx={h}"); time.sleep(PAUSE_S); continue

        trip = decode_trip(r)
        if not trip:
            print(f"[{i}] {tag} no Trip event tx={h} — sweeping"); flatten_all(); consec_fail += 1; continue
        if trip["sold_base"] < trip["got_base"]:
            flatten_all("partial")

        buy_usd = trip["got_base"] / 10**sp["bdec"] * mid
        sell_usd = trip["sold_base"] / 10**sp["bdec"] * mid
        vt = buy_usd + sell_usd
        vol += vt; trips += 1; consec_fail = 0; _last_trade_ts = time.time()
        if week_idx(time.time()) != wk_cur:
            wk_cur = week_idx(time.time()); wk_vol = 0.0
        wk_vol += vt

        u = ub(); s = sb()
        toll_usd = trip["spent_quote"] / 10**sp["qdec"]
        gas_usd = r.gasUsed * (w3.eth.gas_price / 1e18) * SOMI_PX
        cost_1k = (toll_usd + gas_usd) / vt * 1000 if vt > 0 else 0.0
        impact = (spread_pct or 0) - (quoted_pct or 0)
        bnote = f" boost {boost:g}x" if boost != 1.0 else ""
        print(f"[{i}] trip {trips} {tag} @eff {spread_pct:.3f}% (impact {impact:+.3f}%){bnote}: "
              f"vol+=${vt:.2f} tot=${vol:.2f} wk=${wk_vol:.2f} toll={toll_usd:.5f} "
              f"USDso={u:.4f}(bleed ${usdso0-u:+.4f}) SOMI={s:.4f} gasUsed={r.gasUsed} "
              f"cost ${cost_1k:.3f}/1k", flush=True)
        if _paused:
            tg(f"▶️ Atomic resumed @ ${vol:,.0f} vol — book cheap again"); _paused = False
        if vol - _last_ms >= TG_MS:
            _last_ms = vol
            tg(f"📈 Atomic ${vol:,.0f} vol · {trips} trips · bleed ${usdso0-u:.2f} · gas {somi0-s:.1f} SOMI")

        time.sleep(PAUSE_S)
    except SystemExit:
        raise
    except Exception as e:
        consec_fail += 1
        print(f"[{i}] loop err {str(e)[:100]}"); time.sleep(PAUSE_S)

stop("max iters reached")
