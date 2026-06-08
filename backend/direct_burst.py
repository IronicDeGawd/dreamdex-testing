#!/usr/bin/env python3
"""
Direct-submission IOC burst.

Bypasses the dreamDEX REST API entirely. Calls SpotPool.placeTakerOrderWithoutVault
directly via web3 eth_sendRawTransaction. Pre-approves USDso once.

ENV:
  MAINNET_PRIVATE_KEY    — signer key
  BURST_PAIR             — default SOMI:USDso
  BURST_USDSO            — USD value per leg (default 5.0)
  BURST_CYCLES           — number of BUY+SELL round-trips (default 50)
  BURST_DELAY_MS         — between submissions (default 600)
  BURST_SLIPPAGE_TICKS   — ticks past book top to set limit (default 10)
"""
import os, sys, time, json, queue, threading, atexit
sys.path.insert(0, "/app")

from web3 import Web3
from eth_account import Account
from config import MARKETS, SOMNIA_RPC, CHAIN_ID
# Trigger SDK to populate MARKETS with tick/lot/min from /v0/markets
from trading.dreamdex import DreamDEX
from monitor import db
_sdk = DreamDEX()  # populates MARKETS in place
db.init()          # ensure trades table exists before we log to it

PAIR        = os.environ.get("BURST_PAIR", "SOMI:USDso")
USDSO_LEG   = float(os.environ.get("BURST_USDSO", "15.0"))
CYCLES      = int(os.environ.get("BURST_CYCLES", "50"))
DELAY_MS    = int(os.environ.get("BURST_DELAY_MS", "0"))
SLIP_TICKS  = int(os.environ.get("BURST_SLIPPAGE_TICKS", "10"))
SKIP_SIM    = os.environ.get("BURST_SKIP_SIM", "1") not in ("0", "false", "False", "")
STATS_PATH  = os.environ.get("BURST_STATS_PATH", "/tmp/direct_burst_stats.json")
STATS_EVERY = int(os.environ.get("BURST_STATS_EVERY", "2"))  # write stats every N cycles
# How many cycles before we re-read wallet balances from chain (local accounting
# in between to save RPC calls). Lower = more accurate but slower.
BAL_REFRESH_EVERY = int(os.environ.get("BURST_BAL_REFRESH_EVERY", "10"))
# Reserve this many SOMI in wallet for gas (~10 txs worth).
SOMI_GAS_RESERVE = float(os.environ.get("BURST_SOMI_GAS_RESERVE", "1.0"))

KEY = os.environ.get("MAINNET_PRIVATE_KEY")
if not KEY:
    raise SystemExit("set MAINNET_PRIVATE_KEY")

acct = Account.from_key(KEY)
w3 = Web3(Web3.HTTPProvider(SOMNIA_RPC))
print(f"signer={acct.address} chainId={CHAIN_ID} block={w3.eth.block_number}")

mkt   = MARKETS[PAIR]
pool  = Web3.to_checksum_address(mkt["contract"])
base  = mkt["base"]
quote = Web3.to_checksum_address(mkt["quote"])
is_native_base = bool(mkt.get("native")) and (int(base, 16) == 0)
base_dec  = int(mkt["baseDecimals"])
quote_dec = int(mkt["quoteDecimals"])

print(f"pool={pool}  native_base={is_native_base}  baseDec={base_dec}  quoteDec={quote_dec}")

# Minimal ABI: placeTakerOrderWithoutVault + native variant + getPoolParams + ERC20 approve
POOL_ABI = [
    {"inputs":[
        {"name":"isBid","type":"bool"},
        {"name":"userData","type":"uint64"},
        {"name":"price","type":"uint256"},
        {"name":"quantity","type":"uint256"},
        {"name":"expireTimestampNs","type":"uint64"},
        {"name":"orderType","type":"uint8"},
        {"name":"selfMatchingOption","type":"uint8"},
        {"name":"builder","type":"address"},
        {"name":"builderFeeBpsTimes1k","type":"uint96"},
     ],"name":"placeTakerOrderWithoutVault","outputs":[
        {"name":"success","type":"bool"},
        {"name":"orderId","type":"uint128"},
     ],"stateMutability":"payable","type":"function"},
    {"inputs":[],"name":"getPoolParams","outputs":[
        {"name":"baseToken","type":"address"},
        {"name":"quoteToken","type":"address"},
        {"name":"makerFeeBpsTimes1k","type":"uint256"},
        {"name":"takerFeeBpsTimes1k","type":"uint256"},
        {"name":"tickSize","type":"uint256"},
        {"name":"minQuantity","type":"uint256"},
        {"name":"lotSize","type":"uint256"},
     ],"stateMutability":"view","type":"function"},
    {"inputs":[{"name":"isBid","type":"bool"},{"name":"numLevels","type":"uint64"}],
     "name":"getBookLevels","outputs":[{"components":[
        {"name":"price","type":"uint256"},
        {"name":"quantity","type":"uint256"},
     ],"name":"","type":"tuple[]"}],
     "stateMutability":"view","type":"function"},
]
ERC20_ABI = [
    {"inputs":[{"name":"spender","type":"address"},{"name":"amount","type":"uint256"}],
     "name":"approve","outputs":[{"type":"bool"}],"stateMutability":"nonpayable","type":"function"},
    {"inputs":[{"name":"owner","type":"address"},{"name":"spender","type":"address"}],
     "name":"allowance","outputs":[{"type":"uint256"}],"stateMutability":"view","type":"function"},
    {"inputs":[{"name":"account","type":"address"}],
     "name":"balanceOf","outputs":[{"type":"uint256"}],"stateMutability":"view","type":"function"},
]

c_pool  = w3.eth.contract(address=pool, abi=POOL_ABI)
c_quote = w3.eth.contract(address=quote, abi=ERC20_ABI)
c_base  = None if is_native_base else w3.eth.contract(address=Web3.to_checksum_address(base), abi=ERC20_ABI)


def ensure_approve(token_c, label, need_amount):
    cur = token_c.functions.allowance(acct.address, pool).call()
    if cur >= need_amount:
        print(f"[approve/{label}] already sufficient ({cur})")
        return
    print(f"[approve/{label}] approving 2^256-1 ...")
    MAX = 2**256 - 1
    nonce = w3.eth.get_transaction_count(acct.address)
    tx = token_c.functions.approve(pool, MAX).build_transaction({
        "from": acct.address, "nonce": nonce,
        "gas": 2_000_000, "gasPrice": w3.eth.gas_price, "chainId": CHAIN_ID,
    })
    s = w3.eth.account.sign_transaction(tx, KEY)
    h = w3.eth.send_raw_transaction(s.raw_transaction)
    r = w3.eth.wait_for_transaction_receipt(h, timeout=60)
    print(f"[approve/{label}] tx={h.hex()} status={r.status} gas={r.gasUsed}")


def fetch_params():
    # Read directly from contract (7-tuple per ISpotPool interface).
    p = c_pool.functions.getPoolParams().call()
    # (baseToken, quoteToken, makerBps, takerBps, tickSize, minQty, lotSize)
    tick_raw = p[4]
    minq_raw = p[5]
    lot_raw  = p[6]
    return {"tick": tick_raw, "minQty": minq_raw, "lotSize": lot_raw}


def fetch_book_top():
    """Return (top_bid_price_raw, top_ask_price_raw). 0 if side is empty."""
    bids = c_pool.functions.getBookLevels(True, 1).call()
    asks = c_pool.functions.getBookLevels(False, 1).call()
    top_bid = bids[0][0] if bids else 0
    top_ask = asks[0][0] if asks else 0
    return top_bid, top_ask


def sim_call(is_bid, price_raw, qty_raw, expire_ns):
    """eth_call to simulate. Returns (success, orderId) or (False, 0) on revert."""
    try:
        result = c_pool.functions.placeTakerOrderWithoutVault(
            bool(is_bid), 0, int(price_raw), int(qty_raw),
            int(expire_ns), 2, 1,
            "0x0000000000000000000000000000000000000000", 0,
        ).call({"from": acct.address})
        return bool(result[0]), int(result[1])
    except Exception:
        return False, 0


def build_burst_tx(is_bid, price_raw, qty_raw, value_native=0, nonce=None, expire_ns=None):
    # DOC DISCREPANCY: docs say expireTimestampNs=0 means "no expiry"; deployed contract
    # silently rejects (returns success=false). Always pass a future timestamp in ns.
    if expire_ns is None:
        expire_ns = (int(time.time()) + 3600) * 1_000_000_000
    fn = c_pool.functions.placeTakerOrderWithoutVault(
        bool(is_bid),
        0,             # userData
        int(price_raw),
        int(qty_raw),
        expire_ns,     # expireTimestampNs (must be future, NOT 0)
        2,             # orderType IOC
        1,             # selfMatchingOption CancelMaker
        "0x0000000000000000000000000000000000000000",  # builder
        0,             # builderFeeBpsTimes1k
    )
    return fn.build_transaction({
        "from": acct.address,
        "nonce": nonce if nonce is not None else w3.eth.get_transaction_count(acct.address),
        "gas": 2_000_000,
        "gasPrice": w3.eth.gas_price,
        "chainId": CHAIN_ID,
        "value": value_native,
    })


def get_mid():
    # Use SDK ticker fetch (already handles auth + base_url)
    try:
        tk = _sdk.get_ticker(PAIR)
        sym = (tk.get("symbols", [{}]) or [{}])[0]
        v = float(sym.get("close") or 0)
        if v > 0:
            return v
    except Exception as e:
        print(f"[mid] SDK fetch err: {e}")
    # Fallback to env override
    env_mid = os.environ.get("BURST_MID")
    if env_mid:
        return float(env_mid)
    return 0.0


# ── Async tx logger ────────────────────────────────────────────────────────
# Every broadcast tx is logged to agent.db so nothing has to be scraped
# on-chain later (round-1 gap: ~43k burst txs never hit the DB). Logging runs
# on a background thread draining an in-process queue in batches, so the burst
# hot path never blocks on a DB write. Worst-case loss on a hard kill is the
# sub-second of rows still in the queue — and those are still on-chain anyway.
_LOG_Q: "queue.Queue" = queue.Queue(maxsize=100_000)
_LOG_SENTINEL = object()


def _log_worker():
    batch = []
    while True:
        try:
            item = _LOG_Q.get(timeout=1.0)
        except queue.Empty:
            db.record_trades_batch(batch, mode="volume", agent_name="burst"); batch = []
            continue
        if item is _LOG_SENTINEL:
            db.record_trades_batch(batch, mode="volume", agent_name="burst")
            return
        batch.append(item)
        if len(batch) >= 50:
            db.record_trades_batch(batch, mode="volume", agent_name="burst"); batch = []


def log_tx(side, qty_raw, price_raw, tx_hash, status="sent"):
    """Enqueue one broadcast tx for the background writer. Non-blocking; drops
    silently if the queue is somehow full rather than ever stalling the burst."""
    qty_h = qty_raw / (10 ** base_dec)
    px_h  = price_raw / (10 ** quote_dec)
    try:
        _LOG_Q.put_nowait({
            "action": "buy" if side == "BUY" else "sell",
            "pair": PAIR,
            "qty": qty_h,
            "amount_usdso": qty_h * px_h,
            "mid": px_h,
            "reason": "direct_burst",
            "confidence": 0,
            "result": {"status": status, "tx_hash": tx_hash},
        })
    except queue.Full:
        pass


def main():
    # Start the async tx-logger thread; flush on exit.
    _log_thread = threading.Thread(target=_log_worker, daemon=True)
    _log_thread.start()
    def _flush_logs():
        try:
            _LOG_Q.put_nowait(_LOG_SENTINEL)
            _log_thread.join(timeout=5.0)
        except Exception:
            pass
    atexit.register(_flush_logs)

    params = fetch_params()
    tick   = params["tick"]
    minQty = params["minQty"]
    lot    = params["lotSize"]
    print(f"params: tick={tick} ({tick/10**quote_dec}) minQty={minQty} ({minQty/10**base_dec}) lot={lot}")

    # Approve quote token once (always needed for BUYs)
    ensure_approve(c_quote, "USDso", 2**128)

    # If base is ERC20, approve it too (needed for SELLs)
    if c_base is not None:
        ensure_approve(c_base, "BASE", 2**128)

    mid = get_mid()
    if mid <= 0:
        raise SystemExit("could not fetch mid price; set BURST_MID env or check ticker API")
    print(f"mid={mid}")

    # Compute qty so leg ≈ USDSO_LEG worth
    qty_human = USDSO_LEG / mid
    # Round to lot
    qty_raw = int(qty_human * (10**base_dec))
    qty_raw = (qty_raw // lot) * lot
    if qty_raw < minQty:
        qty_raw = minQty
    qty_human = qty_raw / (10**base_dec)
    print(f"qty_raw={qty_raw} qty_human={qty_human}")

    fills = 0
    reverts = 0
    sent_hashes = []
    t0 = time.time()
    skipped = 0
    send_errs = 0

    # Phase 1: local nonce — fetch once, increment per send. Avoid hitting
    # the RPC for "pending" before every tx (saves ~50ms per leg).
    nonce = w3.eth.get_transaction_count(acct.address, "pending")
    print(f"[init] starting nonce={nonce}  skip_sim={SKIP_SIM}  slip_ticks={SLIP_TICKS}  gas=2M")

    # Inline balance accounting. Wallet-funded path means we must pre-check
    # that we have enough quote (USDso) for BUY and enough base for SELL —
    # otherwise the tx broadcasts but reverts on-chain, burning gas.
    #
    # Two base cases:
    #   - Native base (SOMI:USDso): SELL sends msg.value = qty SOMI, so the
    #     usable base is native balance minus a gas reserve.
    #   - ERC20 base (WETH/WBTC/USDC.e): SELL pulls base via allowance, gas is
    #     paid separately in native SOMI. Usable base = ERC20 wallet balance;
    #     we additionally require some native SOMI on hand for gas.
    SOMI_GAS_RESERVE_RAW = int(SOMI_GAS_RESERVE * 1e18)  # native always 18 dec

    def refresh_balances():
        u = c_quote.functions.balanceOf(acct.address).call()
        native = w3.eth.get_balance(acct.address)
        if is_native_base:
            base_usable = max(native - SOMI_GAS_RESERVE_RAW, 0)
            gas_native = native  # same pot
        else:
            base_usable = c_base.functions.balanceOf(acct.address).call()
            gas_native = native
        return u, base_usable, gas_native

    usdso_bal, base_usable, gas_native = refresh_balances()
    print(f"[init] balances: USDso={usdso_bal/10**quote_dec:.2f}  "
          f"base_usable={base_usable/10**base_dec:.4f}  "
          f"gas_native_SOMI={gas_native/1e18:.2f}  "
          f"(reserve {SOMI_GAS_RESERVE} SOMI for gas)")
    consec_fail = 0

    def write_stats(cycle_idx, last_action):
        """Snapshot stats to JSON for the dashboard /direct_burst endpoint."""
        elapsed = max(time.time() - t0, 0.001)
        try:
            tmp = STATS_PATH + ".tmp"
            with open(tmp, "w") as fh:
                json.dump({
                    "pair": PAIR,
                    "leg_size": USDSO_LEG,
                    "cycles_done": cycle_idx,
                    "cycles_total": CYCLES,
                    "sent": len(sent_hashes),
                    "skipped": skipped,
                    "send_errs": send_errs,
                    "tx_per_sec": round(len(sent_hashes) / elapsed, 3),
                    "elapsed_s": round(elapsed, 1),
                    "last_action": last_action,
                    "last_action_ts": time.time(),
                    "pid": os.getpid(),
                    "started_ts": t0,
                }, fh)
            os.replace(tmp, STATS_PATH)
        except Exception as e:
            print(f"[stats] write err: {str(e)[:80]}")

    for i in range(1, CYCLES + 1):
        # Single book fetch per cycle — same top_bid/top_ask used for both legs.
        # Saves one round-trip vs the old "fetch before BUY + fetch before SELL".
        top_bid, top_ask = fetch_book_top()
        expire_ns = (int(time.time()) + 3600) * 1_000_000_000

        # ===== BUY leg: price = top_ask + SLIP_TICKS to cross the book =====
        if top_ask > 0:
            buy_price_raw = top_ask + (SLIP_TICKS * tick)
            # Quote amount we'd pay: qty (base raw) * price (quote raw) / base_unit
            buy_cost_raw = qty_raw * buy_price_raw // (10**base_dec)
            if usdso_bal < buy_cost_raw:
                skipped += 1
                print(f"[c{i}] BUY  skip (USDso {usdso_bal/10**quote_dec:.2f} < {buy_cost_raw/10**quote_dec:.2f})")
            else:
                ok = True if SKIP_SIM else sim_call(True, buy_price_raw, qty_raw, expire_ns)[0]
                if ok:
                    try:
                        tx = build_burst_tx(True, buy_price_raw, qty_raw, value_native=0,
                                            nonce=nonce, expire_ns=expire_ns)
                        s = w3.eth.account.sign_transaction(tx, KEY)
                        h = w3.eth.send_raw_transaction(s.raw_transaction)
                        sent_hashes.append(("BUY", i, h.hex()))
                        log_tx("BUY", qty_raw, buy_price_raw, h.hex())
                        print(f"[c{i}] BUY  n{nonce} @ {buy_price_raw/10**quote_dec:.5f} {h.hex()[:14]}")
                        nonce += 1
                        # Local accounting: pay quote, get base
                        usdso_bal -= buy_cost_raw
                        base_usable += qty_raw
                        consec_fail = 0
                    except Exception as e:
                        send_errs += 1
                        consec_fail += 1
                        print(f"[c{i}] BUY  send-err: {str(e)[:100]}")
                        nonce = w3.eth.get_transaction_count(acct.address, "pending")
                        usdso_bal, base_usable, gas_native = refresh_balances()
                else:
                    skipped += 1
                    print(f"[c{i}] BUY  skip (sim=false) ask={top_ask/10**quote_dec:.5f}")
        else:
            skipped += 1
            print(f"[c{i}] BUY  skip (no ask)")

        if DELAY_MS > 0:
            time.sleep(DELAY_MS / 1000)

        # ===== SELL leg: price = top_bid - SLIP_TICKS to cross =====
        # Reuse top_bid from same cycle's book fetch — no second RPC read.
        if top_bid > 0:
            sell_price_raw = max(top_bid - (SLIP_TICKS * tick), tick)
            # For native-base pools, SELL sends msg.value = qty_raw SOMI.
            # For ERC20-base pools, base is pulled via allowance (need wallet
            # balance) and gas is paid separately in native SOMI.
            need_native = qty_raw if is_native_base else 0
            if base_usable < qty_raw:
                skipped += 1
                print(f"[c{i}] SELL skip (base {base_usable/10**base_dec:.4f} < {qty_raw/10**base_dec:.4f})")
            elif not is_native_base and gas_native < SOMI_GAS_RESERVE_RAW:
                skipped += 1
                print(f"[c{i}] SELL skip (gas SOMI {gas_native/1e18:.3f} < reserve {SOMI_GAS_RESERVE})")
            else:
                ok = True if SKIP_SIM else sim_call(False, sell_price_raw, qty_raw, expire_ns)[0]
                if ok:
                    try:
                        tx = build_burst_tx(False, sell_price_raw, qty_raw, value_native=need_native,
                                            nonce=nonce, expire_ns=expire_ns)
                        s = w3.eth.account.sign_transaction(tx, KEY)
                        h = w3.eth.send_raw_transaction(s.raw_transaction)
                        sent_hashes.append(("SELL", i, h.hex()))
                        log_tx("SELL", qty_raw, sell_price_raw, h.hex())
                        print(f"[c{i}] SELL n{nonce} @ {sell_price_raw/10**quote_dec:.5f} {h.hex()[:14]}")
                        nonce += 1
                        # Local accounting: send base, get quote
                        sell_proceeds_raw = qty_raw * sell_price_raw // (10**base_dec)
                        usdso_bal += sell_proceeds_raw
                        base_usable -= qty_raw
                        consec_fail = 0
                    except Exception as e:
                        send_errs += 1
                        consec_fail += 1
                        print(f"[c{i}] SELL send-err: {str(e)[:100]}")
                        nonce = w3.eth.get_transaction_count(acct.address, "pending")
                        usdso_bal, base_usable, gas_native = refresh_balances()
                else:
                    skipped += 1
                    print(f"[c{i}] SELL skip (sim=false) bid={top_bid/10**quote_dec:.5f}")
        else:
            skipped += 1
            print(f"[c{i}] SELL skip (no bid)")

        # Periodic balance refresh — local accounting drifts due to spread,
        # gas costs, and any fills that diverged from our optimistic update.
        if i % BAL_REFRESH_EVERY == 0:
            usdso_bal, base_usable, gas_native = refresh_balances()

        # Bail out if too many consecutive send failures — let the cron
        # keepalive respawn us with fresh state.
        if consec_fail >= 10:
            print(f"[c{i}] 10 consecutive send errors — exiting for cron respawn")
            break

        if i % 10 == 0:
            elapsed = time.time() - t0
            rate = (i * 2) / elapsed if elapsed > 0 else 0
            print(f"[c{i}] sent {len(sent_hashes)} txs in {elapsed:.1f}s ({rate:.1f} tx/s)")

        if i % STATS_EVERY == 0:
            write_stats(i, f"cycle {i}")

        if DELAY_MS > 0:
            time.sleep(DELAY_MS / 1000)

    # Final stats snapshot
    write_stats(CYCLES, "done")

    # Wait for last batch to settle then count receipts
    print("\nwaiting 8s for last txs to mine...")
    time.sleep(8)
    confirmed = reverted = pending = 0
    for side, c, h in sent_hashes:
        try:
            r = w3.eth.get_transaction_receipt(h)
            if r.status == 1:
                confirmed += 1
                db.set_status_by_hash(h, "confirmed")
            else:
                reverted += 1
                db.set_status_by_hash(h, "reverted")
        except Exception:
            pending += 1
    elapsed = time.time() - t0
    print(f"\n=== done ===")
    print(f"sent: {len(sent_hashes)}  confirmed: {confirmed}  reverted: {reverted}  pending: {pending}")
    print(f"elapsed: {elapsed:.1f}s  rate: {len(sent_hashes)/elapsed:.2f} tx/s")


if __name__ == "__main__":
    main()
