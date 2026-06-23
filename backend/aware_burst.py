#!/usr/bin/env python3
"""Price-AWARE WETH burst.

Why: direct_burst.py fetched the book once per cycle and reused that stale price
for both legs. On a volatile pair (WETH) the bid moves before the SELL mines, so
the limit no longer crosses and the sell silently no-fills (status=1, filled=0) —
WETH piles up one-way. Stable pairs (USDC.e) hid this; WETH exposed it.

Fix:
  - A background thread refreshes top-of-book into a shared buffer every ~2s, so
    every leg uses a FRESH price.
  - Each trade is VERIFIED by base-balance delta (status=1 is not proof of fill).
  - SELLs RETRY with a fresh bid until they actually fill, so round-trips complete.

Env: BURST_PAIR(WETH:USDso) BURST_USDSO(leg $) BURST_SLIPPAGE_TICKS(10)
     BURST_SOMI_GAS_RESERVE(1.0) BURST_PRICE_POLL_S(2) BURST_SELL_RETRIES(5)
     BURST_CYCLES(99999) MAINNET_PRIVATE_KEY
"""
import os, sys, time, threading
sys.path.insert(0, "/app")
from web3 import Web3
from eth_account import Account
from config import SOMNIA_RPC, CHAIN_ID, MARKETS

PAIR        = os.environ.get("BURST_PAIR", "WETH:USDso")
LEG_USD     = float(os.environ.get("BURST_USDSO", "45"))
SLIP        = int(os.environ.get("BURST_SLIPPAGE_TICKS", "10"))
GAS_RESERVE = float(os.environ.get("BURST_SOMI_GAS_RESERVE", "1.0"))
POLL        = float(os.environ.get("BURST_PRICE_POLL_S", "2"))
SELL_RETRIES= int(os.environ.get("BURST_SELL_RETRIES", "5"))
CYCLES      = int(os.environ.get("BURST_CYCLES", "99999"))
KEY = os.environ["MAINNET_PRIVATE_KEY"]
acct = Account.from_key(KEY)
w3 = Web3(Web3.HTTPProvider(SOMNIA_RPC, request_kwargs={"timeout": 20}))

m = MARKETS[PAIR]
pool  = Web3.to_checksum_address(m["contract"])
base  = Web3.to_checksum_address(m["base"])
quote = Web3.to_checksum_address(m["quote"])
base_dec  = int(m.get("baseDecimals", 18))
quote_dec = int(m.get("quoteDecimals", 18))

POOL_ABI = [
    {"inputs":[{"name":"isBid","type":"bool"},{"name":"userData","type":"uint64"},
        {"name":"price","type":"uint256"},{"name":"quantity","type":"uint256"},
        {"name":"expireTimestampNs","type":"uint64"},{"name":"orderType","type":"uint8"},
        {"name":"selfMatchingOption","type":"uint8"},{"name":"builder","type":"address"},
        {"name":"builderFeeBpsTimes1k","type":"uint96"}],
     "name":"placeTakerOrderWithoutVault","outputs":[{"name":"s","type":"bool"},{"name":"o","type":"uint128"}],
     "stateMutability":"payable","type":"function"},
    {"inputs":[],"name":"getPoolParams","outputs":[{"name":"b","type":"address"},{"name":"q","type":"address"},
        {"name":"mf","type":"uint256"},{"name":"tf","type":"uint256"},{"name":"tick","type":"uint256"},
        {"name":"minq","type":"uint256"},{"name":"lot","type":"uint256"}],"stateMutability":"view","type":"function"},
    {"inputs":[{"name":"isBid","type":"bool"},{"name":"n","type":"uint64"}],"name":"getBookLevels",
     "outputs":[{"components":[{"name":"price","type":"uint256"},{"name":"quantity","type":"uint256"}],
        "name":"","type":"tuple[]"}],"stateMutability":"view","type":"function"},
]
ERC20 = [
    {"inputs":[{"name":"s","type":"address"},{"name":"a","type":"uint256"}],"name":"approve",
     "outputs":[{"type":"bool"}],"stateMutability":"nonpayable","type":"function"},
    {"inputs":[{"name":"o","type":"address"},{"name":"s","type":"address"}],"name":"allowance",
     "outputs":[{"type":"uint256"}],"stateMutability":"view","type":"function"},
    {"inputs":[{"name":"a","type":"address"}],"name":"balanceOf","outputs":[{"type":"uint256"}],
     "stateMutability":"view","type":"function"},
]
c  = w3.eth.contract(address=pool,  abi=POOL_ABI)
bc = w3.eth.contract(address=base,  abi=ERC20)
qc = w3.eth.contract(address=quote, abi=ERC20)
pp = c.functions.getPoolParams().call()
tick, minq, lot = pp[4], pp[5], pp[6]
print(f"[init] {PAIR} tick={tick} minQty={minq/10**base_dec} lot={lot/10**base_dec} leg=${LEG_USD} slip={SLIP} poll={POLL}s")

# ── live price buffer (background thread) ───────────────────────────────────
PX = {"bid": 0, "ask": 0, "ts": 0.0}
PXLOCK = threading.Lock()
def fetcher():
    while True:
        try:
            b = c.functions.getBookLevels(True, 1).call()
            a = c.functions.getBookLevels(False, 1).call()
            with PXLOCK:
                if b: PX["bid"] = b[0][0]
                if a: PX["ask"] = a[0][0]
                PX["ts"] = time.time()
        except Exception:
            pass
        time.sleep(POLL)
threading.Thread(target=fetcher, daemon=True).start()
time.sleep(POLL + 1)  # let the buffer fill before trading

def weth_bal():  return bc.functions.balanceOf(acct.address).call()
def usdso_bal(): return qc.functions.balanceOf(acct.address).call()

def approve_if_needed():
    if bc.functions.allowance(acct.address, pool).call() >= 2**128:
        return
    n = w3.eth.get_transaction_count(acct.address, "pending")
    tx = bc.functions.approve(pool, 2**256-1).build_transaction(
        {"from": acct.address, "nonce": n, "gas": 2_000_000, "gasPrice": w3.eth.gas_price, "chainId": CHAIN_ID})
    h = w3.eth.send_raw_transaction(w3.eth.account.sign_transaction(tx, KEY).raw_transaction)
    w3.eth.wait_for_transaction_receipt(h, timeout=90)
    print("[approve] base allowance set")
approve_if_needed()

def trade(is_bid, qty_raw):
    """One VERIFIED taker trade at the live buffer price. Returns True if the base
    balance actually moved (real fill), else False."""
    with PXLOCK:
        bid, ask = PX["bid"], PX["ask"]
    if is_bid:
        if not ask: return False
        price_raw = ask + SLIP * tick
    else:
        if not bid: return False
        price_raw = max(bid - SLIP * tick, tick)
    before = weth_bal()
    expire = (int(time.time()) + 3600) * 1_000_000_000
    try:
        n = w3.eth.get_transaction_count(acct.address, "pending")
        tx = c.functions.placeTakerOrderWithoutVault(
            bool(is_bid), 0, int(price_raw), int(qty_raw), expire, 2, 1,
            "0x0000000000000000000000000000000000000000", 0).build_transaction(
            {"from": acct.address, "nonce": n, "gas": 2_000_000, "gasPrice": w3.eth.gas_price,
             "chainId": CHAIN_ID, "value": 0})
        h = w3.eth.send_raw_transaction(w3.eth.account.sign_transaction(tx, KEY).raw_transaction)
        r = w3.eth.wait_for_transaction_receipt(h, timeout=60)
    except Exception as e:
        print(f"[{'BUY' if is_bid else 'SELL'}] err {str(e)[:70]}")
        return False
    time.sleep(1)  # let the node's state settle so the balance read isn't a false-negative
    moved = abs(weth_bal() - before)
    filled = moved >= qty_raw // 2  # at least half the leg actually moved
    print(f"[{'BUY' if is_bid else 'SELL'}] q={qty_raw/10**base_dec:.4f} @ {price_raw/10**quote_dec:.2f} "
          f"status={r.status} filled={filled} moved={moved/10**base_dec:.4f}")
    return filled

rounds = 0
legs = 0
t0 = time.time()
for i in range(1, CYCLES + 1):
    somi = w3.eth.get_balance(acct.address) / 1e18
    if somi < GAS_RESERVE:
        print(f"[gas] SOMI {somi:.3f} < reserve {GAS_RESERVE} — stopping for refuel"); break

    # ── BUY leg: retry with fresh price until it actually fills ──
    bought = False
    for attempt in range(SELL_RETRIES):
        with PXLOCK:
            ask = PX["ask"]
        if not ask:
            time.sleep(1); continue
        u = usdso_bal()
        target_qty = (int(LEG_USD * 10**quote_dec) * 10**base_dec) // (ask + SLIP*tick)
        afford_qty = (u * 10**base_dec) // (ask + SLIP*tick)
        buy_qty = (min(target_qty, afford_qty) // lot) * lot
        if buy_qty < minq:
            print(f"[c{i}] BUY skip — USDso {u/10**quote_dec:.2f} too low"); break
        if trade(True, buy_qty):
            legs += 1; bought = True; break
        time.sleep(POLL)  # fresh ask before retry
    if not bought:
        # couldn't acquire WETH this cycle; skip to sell-any-residual then continue
        pass

    # ── SELL leg: sell what we actually hold, retry with fresh bid until filled ──
    sold = False
    for attempt in range(SELL_RETRIES):
        sell_qty = (weth_bal() // lot) * lot
        if sell_qty < minq:
            sold = True; break  # nothing left to sell = round complete
        if trade(False, sell_qty):
            legs += 1; sold = True; break
        time.sleep(POLL)  # wait for a fresh bid before retrying
    if sold:
        rounds += 1

    if i % 5 == 0:
        el = time.time() - t0
        print(f"[c{i}] rounds={rounds} legs={legs} ({legs/el:.2f} legs/s) "
              f"USDso={usdso_bal()/10**quote_dec:.2f} WETH={weth_bal()/10**base_dec:.5f} SOMI={somi:.2f}")
