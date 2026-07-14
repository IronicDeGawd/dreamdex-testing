#!/usr/bin/env python3
"""Atomic round-trip taker — EIP-7702 buy+sell in ONE transaction.

WHY THIS EXISTS
---------------
`direct_burst.py` does a round-trip as two txs (buy → wait → sell). That leaves
an inventory window (price drift, ~$0.04/1k) and a bag risk if the sell fails.
This engine delegates the wallet to RoundTrip7702 (EIP-7702) and calls
roundTrip(...) ON ITSELF: the pool's IOC buy and IOC sell settle atomically in
one tx. No drift, no buy-side bag (a failed sell reverts the buy), ~2x
throughput (one confirmation), and an on-chain toll cap. The EOA stays
msg.sender for placeOrder, so contest volume is still attributed to the wallet.

Delegation is installed ONCE (a type-4 tx); trades are then cheap type-2
self-calls. Set ATOM_TX_MODE=type4 to re-authorize on every trip instead (the
reference competitor's style — proven, but ~1.2M more gas per trip).

SAFETY
------
1. eth_call preflight before every trip: a trip that would revert (no fill /
   toll breach) is caught for free and never broadcast (no wasted gas).
2. Spread gate: skip dislocated books instead of firing doomed trips.
3. On-chain toll cap inside the contract: a bad sell reverts the whole trip.
4. Residual sweep: an IOC partial sell leaves sub-lot dust; we flatten it.
5. Delegation re-check each loop: reinstall if it was cleared out from under us.

Env knobs (all optional):
  ATOM_PAIR(WETH:USDso) ATOM_LEG_USD(25) ATOM_SLIP(0.004) ATOM_TARGET(100000)
  ATOM_SPREAD_GATE_PCT(0.15) ATOM_MAX_TOLL_PER_1K(0.30) ATOM_SOMI_FLOOR(3)
  ATOM_PAUSE_S(8) ATOM_SETTLE_S(1.0) ATOM_TX_MODE(type2|type4)
  ATOM_DELEGATE_ADDR(0x..)  ← required; the deployed RoundTrip7702
  ATOM_PRIVATE_KEY / ATOM_ADDRESS  ← optional wallet override (R3 smoke)
"""
import os, sys, time
from decimal import Decimal
sys.path.insert(0, "/app")
from web3 import Web3
from eth_account import Account
import config
from config import MARKETS, CHAIN_ID
from trading.dreamdex import DreamDEX
from trading.delegate import encode_roundtrip, decode_trip

PAIR       = os.environ.get("ATOM_PAIR", "WETH:USDso")
LEG_USD    = float(os.environ.get("ATOM_LEG_USD", "25"))
SLIP       = float(os.environ.get("ATOM_SLIP", "0.004"))
TARGET     = float(os.environ.get("ATOM_TARGET", "100000"))
SPREAD_GATE_PCT = float(os.environ.get("ATOM_SPREAD_GATE_PCT", "0.15"))
MAX_TOLL_PER_1K = float(os.environ.get("ATOM_MAX_TOLL_PER_1K", "0.30"))
GAS_FLOOR  = float(os.environ.get("ATOM_SOMI_FLOOR", "3"))
PAUSE_S    = float(os.environ.get("ATOM_PAUSE_S", "8"))
SETTLE     = float(os.environ.get("ATOM_SETTLE_S", "1.0"))
TX_MODE    = os.environ.get("ATOM_TX_MODE", "type2").lower()
GAS        = int(os.environ.get("ATOM_GAS", "6000000"))
DELEGATE   = os.environ.get("ATOM_DELEGATE_ADDR", "")
MAX_S      = float(os.environ.get("ATOM_MAX_S", "0"))   # 0 = run until target/stopped

if not DELEGATE:
    sys.exit("ATOM_DELEGATE_ADDR is required (deploy via tools/deploy_delegate.py)")
DELEGATE = Web3.to_checksum_address(DELEGATE)

dex = DreamDEX(private_key=os.environ.get("ATOM_PRIVATE_KEY") or None,
               address=os.environ.get("ATOM_ADDRESS") or None)
dex._ensure_auth()
w = dex.wallet
acct = Account.from_key(w.private_key)
# The 7702 delegation lives on whoever SIGNS, so the key is authoritative for
# the address — never trust a mismatched ATOM_ADDRESS (would read/install the
# designator on the wrong account).
if w.address.lower() != acct.address.lower():
    print(f"[wallet] address {w.address} != key address {acct.address} — using key's")
    w.address = acct.address
w3 = w.w3

m = MARKETS[PAIR]
pool = Web3.to_checksum_address(m["contract"])
base = Web3.to_checksum_address(m["base"]); quote = Web3.to_checksum_address(m["quote"])
bdec = int(m["baseDecimals"]); qdec = int(m["quoteDecimals"])

ERC20 = [{"name":"balanceOf","type":"function","stateMutability":"view",
          "inputs":[{"name":"a","type":"address"}],"outputs":[{"type":"uint256"}]}]
PP_ABI = [{"inputs":[],"name":"getPoolParams","outputs":[{"type":"address"},{"type":"address"},
  {"type":"uint256"},{"type":"uint256"},{"type":"uint256"},{"type":"uint256"},{"type":"uint256"}],
  "stateMutability":"view","type":"function"}]
bc = w3.eth.contract(address=base, abi=ERC20)
qc = w3.eth.contract(address=quote, abi=ERC20)
pc = w3.eth.contract(address=pool, abi=PP_ABI)


def px_raw(human, dec):
    """Exact price/qty scaling — Decimal, not float (float mis-encodes)."""
    return int(Decimal(str(human)) * (10 ** dec))


def wbal(): return bc.functions.balanceOf(acct.address).call()
def qbal(): return qc.functions.balanceOf(acct.address).call()


pp = pc.functions.getPoolParams().call(); lot = pp[6]; minq = pp[5]


def ensure_delegated():
    """Install the delegation if this wallet isn't (or is no longer) pointed at
    our RoundTrip7702. Returns True if delegated to us afterwards."""
    tgt = w.delegation_target()
    if tgt == DELEGATE:
        return True
    print(f"[deleg] target={tgt or '(none)'} — installing {DELEGATE}")
    h, r = w.install_delegation(DELEGATE, gas=GAS)
    ok = (r.status == 1 and w.delegation_target() == DELEGATE)
    print(f"[deleg] install tx {h} status={r.status} now={w.delegation_target()}")
    return ok


def build_trip(ask, bid):
    """Encode a roundTrip(...) for the current book. Returns (calldata, qty_raw,
    buy_px_human) or (None, 0, 0) if the leg is too small."""
    qty_raw = (int(round(LEG_USD / ask * 10**bdec)) // lot) * lot
    if qty_raw < minq:
        return None, 0, 0.0
    buy_px = round(ask * (1 + SLIP), 2)
    sell_px = round(bid * (1 - SLIP), 2)
    # round-trip volume ≈ leg*2 USDso; cap net quote toll to that × per-1k budget
    max_toll_quote = int(MAX_TOLL_PER_1K * (LEG_USD * 2 / 1000.0) * 10**qdec)
    cd = encode_roundtrip(base, quote, pool,
                          px_raw(buy_px, qdec), px_raw(sell_px, qdec), qty_raw,
                          (int(time.time()) + 3600) * 1_000_000_000,
                          max_toll_quote, lot)
    return cd, qty_raw, buy_px


def preflight(calldata) -> tuple[bool, str]:
    """Simulate the trip via eth_call. Returns (ok, reason). A revert here means
    the trip would fail on-chain (no fill / toll) — skip without broadcasting."""
    try:
        w3.eth.call({"from": acct.address, "to": acct.address,
                     "data": calldata, "gas": GAS})
        return True, ""
    except Exception as e:
        return False, str(e)[:90]


def send_trip(calldata) -> str:
    """Broadcast the round-trip. type2 = cheap self-call (delegation installed);
    type4 = re-authorize every trip."""
    if TX_MODE == "type4":
        n = w.reserve_nonce()
        auth = w.sign_authorization(DELEGATE, n + 1)
        return w.send_type4_tx(acct.address, calldata, auth, gas=GAS, tx_nonce=n)
    return w.send_unsigned_tx({"to": acct.address, "data": calldata, "value": 0},
                              min_gas=GAS)


def sell_all_base(reason=""):
    """Flatten any residual base (sub-lot dust from an IOC partial). Uses the
    normal API order path — no delegation involved."""
    held = wbal()
    if held < minq:
        return
    if reason:
        print(f"[flat] selling residual {held/10**bdec:.6f} base ({reason})")
    for att in range(6):
        book = dex.get_orderbook(PAIR); bid = book.get("bid")
        if not bid:
            time.sleep(1); continue
        sq = wbal() / 10**bdec
        if sq * 10**bdec < minq:
            return
        try:
            dex.place_order(PAIR, "sell", round(sq, 8), order_type="immediateOrCancel",
                            limit_price=round(bid * (1 - SLIP * (att + 1)), 2))
        except Exception as e:
            print(f"[flat] sell err {str(e)[:60]}")
        time.sleep(SETTLE)
        if wbal() < minq:
            return
    print(f"[flat] WARN residual {wbal()/10**bdec:.6f} base remains — flatten manually")


# ── main ──────────────────────────────────────────────────────────────────
vol = 0.0; trips = 0; reverts = 0
somi0 = w.native_balance(); usdso0 = qbal() / 10**qdec
print(f"START ATOMIC pair={PAIR} leg=${LEG_USD} target=${TARGET} tx_mode={TX_MODE} "
      f"delegate={DELEGATE} USDso={usdso0:.4f} SOMI={somi0:.4f}", flush=True)

if not ensure_delegated():
    print("=== STOP: delegation install failed ==="); sys.exit(1)
sell_all_base("startup")

t_start = time.time()
while vol < TARGET:
    if MAX_S and time.time() - t_start > MAX_S:
        print(f"=== STOP: max_s {MAX_S}s reached ==="); break
    somi = w.native_balance()
    if somi < GAS_FLOOR:
        print(f"=== STOP: SOMI {somi:.2f} < floor {GAS_FLOOR} ==="); break
    if not ensure_delegated():
        print("[deleg] reinstall failed — pause"); time.sleep(PAUSE_S); continue

    book = dex.get_orderbook(PAIR); ask = book.get("ask"); bid = book.get("bid")
    if not ask or not bid:
        time.sleep(1); continue
    spread_pct = (ask - bid) / ((ask + bid) / 2) * 100
    if SPREAD_GATE_PCT > 0 and spread_pct > SPREAD_GATE_PCT:
        print(f"[gate] spread {spread_pct:.3f}% > {SPREAD_GATE_PCT}% — pause {PAUSE_S:.0f}s")
        time.sleep(PAUSE_S); continue

    calldata, qty_raw, buy_px = build_trip(ask, bid)
    if not calldata:
        time.sleep(1); continue

    ok, reason = preflight(calldata)
    if not ok:
        reverts += 1
        print(f"[skip] preflight revert ({reason}) — pause")
        time.sleep(PAUSE_S if "toll" in reason or "nofill" in reason else 1)
        continue

    try:
        h = send_trip(calldata)
    except Exception as e:
        print(f"[trip] send err {str(e)[:80]}"); time.sleep(SETTLE); continue
    r = w.wait_for_receipt(h, timeout=60)
    if r.status != 1:
        reverts += 1
        print(f"[trip] REVERTED on-chain tx={h}"); time.sleep(SETTLE); continue

    trip = decode_trip(r)
    if not trip:
        print(f"[trip] no Trip event tx={h} — sweeping"); sell_all_base("no-event"); continue
    buy_usd = trip["got_base"] / 10**bdec * ask
    sell_usd = trip["sold_base"] / 10**bdec * bid
    vol += buy_usd + sell_usd; trips += 1
    if trip["sold_base"] < trip["got_base"]:
        sell_all_base("partial")
    print(f"[{trips}] vol+=${buy_usd+sell_usd:.2f} tot=${vol:.2f} "
          f"toll={trip['spent_quote']/10**qdec:.5f} USDso={qbal()/10**qdec:.2f} "
          f"somi={somi:.2f} gasUsed={r.gasUsed}", flush=True)

sell_all_base("shutdown")
somiN = w.native_balance(); usdsoN = qbal() / 10**qdec
print(f"=== STOP: done ===")
print(f"trips={trips} volume=${vol:.2f} reverts={reverts} "
      f"USDso={usdsoN:.4f} (bleed ${usdso0-usdsoN:+.4f}) gas={somi0-somiN:.4f} SOMI", flush=True)
