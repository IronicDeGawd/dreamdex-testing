#!/usr/bin/env python3
"""Direct-contract volume burst — bypasses the DreamDEX REST API for order placement.

WHY THIS EXISTS
---------------
`volume_climb.py` places every order via the REST API: POST /v0/markets/.../orders
returns an unsigned tx, we sign + broadcast. That server round-trip costs ~7-8s
PER LEG, so a buy+sell round-trip is ~30s. This engine builds the SAME calldata
locally and broadcasts straight to the pool contract, cutting a round-trip to
~15s (~2x faster, measured live in R3 week 2).

THE KEY FINDING (why the old R2 direct script failed)
-----------------------------------------------------
The working order function is  placeOrder  (selector 0x4e978373)  on the pool
contract, funded from the wallet. The archived R2 script (archive/aware_burst.py)
called  placeTakerOrderWithoutVault  — a DIFFERENT function that expects
vault-deposited funds, so wallet-funded orders reverted (status=0, moved=0).
One function name off. See context/research/dreamdex-round-findings.md.

placeOrder ABI (9 args), reverse-engineered from the API's calldata:
    placeOrder(bool isBid, uint64 userData, uint256 price, uint256 quantity,
               uint64 expireNs, uint8 orderType, uint8 selfMatch,
               address builder, uint96 builderFee)
  - price    = human_price * 10**quoteDecimals  (MUST use Decimal, not float —
               float 1779.66*1e18 loses precision and the order mis-encodes)
  - quantity = qty * 10**baseDecimals, snapped to lot
  - orderType = 2 (IOC),  selfMatch = 0,  builder = 0x0,  fee = 0

SAFETY RAILS (learned the hard way in R3)
-----------------------------------------
1. Encoding self-check: at startup we build our calldata for a sample order and
   assert it matches the API's byte-for-byte. Mismatch -> abort (never broadcast
   a mis-encoded order). This caught the float-precision bug before it traded.
2. Bag-proof: sell_all_weth() runs at the top of EVERY loop and at shutdown, so a
   mis-read fill can never accumulate into a one-sided bag. R3 lost ~$28 into a
   bag once when the settle-read fired before async settlement credited the WETH.
3. Settle delay + re-check: after a buy we wait DP_SETTLE_S, then read the balance;
   if unchanged we wait once more before declaring no-fill (settlement is async).
4. Spread gate: when the book is wide/thin (dislocated), IOC buys stop crossing.
   Unlike volume_climb, the naive loop just spins on no-fills. We gate on spread:
   if spread% > DP_SPREAD_GATE_PCT we pause instead of firing doomed orders.
5. Consecutive-no-fill breaker: if the book stays un-fillable, back off harder.

Env knobs (all optional):
  DP_PAIR(WETH:USDso) DP_LEG_USD(25) DP_SLIP(0.004) DP_TARGET(100000)
  DP_SETTLE_S(1.5) DP_SOMI_FLOOR(3) DP_SPREAD_GATE_PCT(0.15) DP_PAUSE_S(8)
  DP_MAX_NOFILL(6)

Run inside the agent container (has trading/, config, .env key). Prefer the
launcher: ./direct_burst.sh [target] [leg] [slip] [spread_gate_pct]
"""
import os, sys, time, math
from decimal import Decimal
sys.path.insert(0, "/app")
from web3 import Web3
from eth_account import Account
import config
from config import MARKETS, CHAIN_ID
from trading.dreamdex import DreamDEX

PAIR      = os.environ.get("DP_PAIR", "WETH:USDso")
LEG_USD   = float(os.environ.get("DP_LEG_USD", "25"))
SLIP      = float(os.environ.get("DP_SLIP", "0.004"))
TARGET    = float(os.environ.get("DP_TARGET", "100000"))
SETTLE    = float(os.environ.get("DP_SETTLE_S", "1.5"))
GAS_FLOOR = float(os.environ.get("DP_SOMI_FLOOR", "3"))
SPREAD_GATE_PCT = float(os.environ.get("DP_SPREAD_GATE_PCT", "0.15"))
PAUSE_S   = float(os.environ.get("DP_PAUSE_S", "8"))
MAX_NOFILL = int(os.environ.get("DP_MAX_NOFILL", "6"))

dex = DreamDEX(); dex._ensure_auth()
w = dex.wallet
KEY = w.private_key
acct = Account.from_key(KEY)
w3 = w.w3
m = MARKETS[PAIR]
pool = Web3.to_checksum_address(m["contract"])
base = Web3.to_checksum_address(m["base"]); quote = Web3.to_checksum_address(m["quote"])
bdec = int(m["baseDecimals"]); qdec = int(m["quoteDecimals"])
ZERO = "0x0000000000000000000000000000000000000000"

PLACE_ABI = [{"inputs":[
    {"name":"isBid","type":"bool"},{"name":"userData","type":"uint64"},
    {"name":"price","type":"uint256"},{"name":"quantity","type":"uint256"},
    {"name":"expireNs","type":"uint64"},{"name":"orderType","type":"uint8"},
    {"name":"selfMatch","type":"uint8"},{"name":"builder","type":"address"},
    {"name":"builderFee","type":"uint96"}],
    "name":"placeOrder","outputs":[{"name":"s","type":"bool"},{"name":"o","type":"uint128"}],
    "stateMutability":"payable","type":"function"}]
ERC20 = [{"name":"balanceOf","type":"function","stateMutability":"view",
          "inputs":[{"name":"a","type":"address"}],"outputs":[{"type":"uint256"}]}]
PP_ABI = [{"inputs":[],"name":"getPoolParams","outputs":[{"type":"address"},{"type":"address"},
  {"type":"uint256"},{"type":"uint256"},{"type":"uint256"},{"type":"uint256"},{"type":"uint256"}],
  "stateMutability":"view","type":"function"}]
c  = w3.eth.contract(address=pool, abi=PLACE_ABI)
bc = w3.eth.contract(address=base, abi=ERC20)
qc = w3.eth.contract(address=quote, abi=ERC20)
pc = w3.eth.contract(address=pool, abi=PP_ABI)

def px_raw(human, dec):
    """Exact price/qty scaling. Decimal, NOT float — float loses precision and
    the resulting calldata mis-encodes (verified against the API byte-for-byte)."""
    return int(Decimal(str(human)) * (10 ** dec))

def build_calldata(is_bid, price_raw, qty_raw, expire):
    return c.encode_abi("placeOrder", args=[bool(is_bid), 0, int(price_raw), int(qty_raw),
                                            int(expire), 2, 0, ZERO, 0])

def wbal(): return bc.functions.balanceOf(acct.address).call()
def qbal(): return qc.functions.balanceOf(acct.address).call()

# ── SAFETY 1: verify our local encoding == the API's for identical params ──
def verify_encoding():
    book = dex.get_orderbook(PAIR); ask = book["ask"]
    tick = float(m.get("tickSize", 0.0001))
    p_arg = round(ask + 5 * tick, 2)
    payload = {"side":"buy","amount":"0.02","walletAddress":config.MY_ADDRESS,"fundingSource":"wallet",
               "orderType":"immediateOrCancel","type":"limit","price":str(p_arg)}
    api = dex._session.post(f"{dex.base_url}/v0/markets/{PAIR}/orders", json=payload, timeout=15).json()
    def words(hexstr):
        b = hexstr[2:] if hexstr.startswith("0x") else hexstr
        return b[:8], [b[8:][i:i+64] for i in range(0, len(b[8:]), 64)]
    api_sel, api_w = words(api["data"])
    my_sel, my_w = words(build_calldata(True, px_raw(p_arg, qdec), px_raw("0.02", bdec), int(api_w[4],16)))
    # skip word 4 (expireNs — dynamic timestamp)
    bad = [i for i in range(len(api_w)) if i != 4 and api_w[i] != my_w[i]]
    ok = (api_sel == my_sel and not bad)
    print(f"[verify] api_sel={api_sel} my_sel={my_sel} mismatch_words={bad if not ok else '[]'}")
    return ok

if not verify_encoding():
    print("[verify] ENCODING MISMATCH — refusing to trade direct. Aborting."); sys.exit(1)
print("[verify] OK — local encoding matches API byte-for-byte. Going direct.")

pp = pc.functions.getPoolParams().call(); lot = pp[6]; minq = pp[5]

def send(is_bid, price_raw, qty_raw):
    expire = (int(time.time()) + 3600) * 1_000_000_000
    n = w3.eth.get_transaction_count(acct.address, "pending")
    tx = c.functions.placeOrder(bool(is_bid), 0, int(price_raw), int(qty_raw), expire, 2, 0, ZERO, 0
        ).build_transaction({"from": acct.address, "nonce": n, "gas": 3_000_000,
                             "gasPrice": w3.eth.gas_price, "chainId": CHAIN_ID, "value": 0})
    h = w3.eth.send_raw_transaction(w3.eth.account.sign_transaction(tx, KEY).raw_transaction)
    return w3.eth.wait_for_transaction_receipt(h, timeout=40).status

# ── SAFETY 2: never hold WETH across a buy — sell any residual first ──
def sell_all_weth(reason=""):
    held = wbal()
    if held < minq: return
    if reason: print(f"[flat] selling residual {held/10**bdec:.5f} WETH ({reason})")
    for att in range(8):
        book = dex.get_orderbook(PAIR); bid = book.get("bid")
        if not bid: time.sleep(1); continue
        sq = (wbal() // lot) * lot
        if sq < minq: return
        try:
            send(False, px_raw(round(bid*(1-SLIP*(att+1)), 2), qdec), sq)
        except Exception as e:
            print(f"[SELL] err {str(e)[:60]}")
        time.sleep(SETTLE)
        if wbal() < minq: return
    print(f"[flat] WARN residual {wbal()/10**bdec:.5f} WETH remains — flatten manually")

vol = 0.0; trips = 0; nofill = 0
print(f"[start] direct placeOrder burst leg=${LEG_USD} target=${TARGET} settle={SETTLE}s "
      f"spread_gate={SPREAD_GATE_PCT}%")
sell_all_weth("startup")

while vol < TARGET:
    somi = w3.eth.get_balance(acct.address) / 1e18
    if somi < GAS_FLOOR:
        print(f"[gas] SOMI {somi:.2f} < floor {GAS_FLOOR} — stopping"); break
    sell_all_weth()  # bag-proof: enter every buy flat

    book = dex.get_orderbook(PAIR); ask = book.get("ask"); bid = book.get("bid")
    if not ask or not bid:
        time.sleep(1); continue

    # ── SAFETY 4: spread gate — don't fire doomed buys into a dislocated book ──
    spread_pct = (ask - bid) / ((ask + bid) / 2) * 100
    if SPREAD_GATE_PCT > 0 and spread_pct > SPREAD_GATE_PCT:
        print(f"[gate] spread {spread_pct:.3f}% > {SPREAD_GATE_PCT}% — pause {PAUSE_S:.0f}s")
        time.sleep(PAUSE_S); continue

    qty_raw = (int(round(LEG_USD / ask * 10**bdec)) // lot) * lot
    if qty_raw < minq:
        time.sleep(1); continue

    b0 = wbal()
    try:
        send(True, px_raw(round(ask*(1+SLIP), 2), qdec), qty_raw)
    except Exception as e:
        print(f"[BUY] err {str(e)[:70]}"); time.sleep(SETTLE); continue

    # ── SAFETY 3: settle + one re-check before declaring no-fill ──
    time.sleep(SETTLE)
    got = wbal() - b0
    if got <= 0:
        time.sleep(SETTLE)
        got = wbal() - b0
    if got <= 0:
        nofill += 1
        print(f"[BUY] no fill ({nofill}/{MAX_NOFILL})")
        if nofill >= MAX_NOFILL:
            print(f"[gate] {MAX_NOFILL} consecutive no-fills — book un-fillable, pause {PAUSE_S*2:.0f}s")
            time.sleep(PAUSE_S * 2); nofill = 0
        continue
    nofill = 0

    # sell exactly what we got, widening slip until flat
    for att in range(8):
        sq = (wbal() // lot) * lot
        if sq < minq: break
        book = dex.get_orderbook(PAIR); bid = book.get("bid")
        if not bid: time.sleep(1); continue
        try:
            send(False, px_raw(round(bid*(1-SLIP*(att+1)), 2), qdec), sq)
        except Exception as e:
            print(f"[SELL] err {str(e)[:60]}")
        time.sleep(SETTLE)

    filled_usd = (got / 10**bdec) * ask
    vol += filled_usd * 2; trips += 1
    print(f"[{trips}] vol+=${filled_usd*2:.2f} tot=${vol:.2f} USDso={qbal()/10**qdec:.2f} somi={somi:.2f}")

# ── SAFETY 2: end flat no matter how we exit ──
sell_all_weth("shutdown")
print(f"[done] trips={trips} vol=${vol:.2f} final WETH={wbal()/10**bdec:.6f} USDso={qbal()/10**qdec:.2f}")
