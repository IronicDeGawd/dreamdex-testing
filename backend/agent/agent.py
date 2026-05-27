# backend/agent/agent.py
import time, threading, json
from datetime import datetime
from config import (AGENT_LOOP_SECONDS, AGENT_STOP_BELOW,
                    AGENT_MIN_TRADE, AGENT_MAX_TRADE, AGENT_MAX_ORDERS,
                    MAX_CONCURRENT_POS)
from agent.brain     import decide
from agent.strategy  import PriceAnalyzer
from agent.state     import AgentState
from trading.dreamdex import DreamDEX
from monitor.leaderboard import LeaderboardMonitor
from monitor import db as agent_db


class TradingAgent:
    def __init__(self, portfolio=None, lb=None):
        self.analyzer       = PriceAnalyzer()
        self.state          = AgentState()
        self.dex            = DreamDEX()
        # R6: re-use the running LeaderboardMonitor from main.py instead of
        # spinning up a new (never-started) one. Auto-flip rank check needs a
        # live my_rank value; the default-init instance returns "?" forever.
        self.lb             = lb if lb is not None else LeaderboardMonitor()
        self.portfolio      = portfolio  # C2: source of truth for capital-floor check
        self.running        = False
        self.paused         = False
        self.loop_secs      = AGENT_LOOP_SECONDS
        self.max_orders     = AGENT_MAX_ORDERS   # 0 = unlimited
        self.last_decision  = {}
        self.log_path       = "logs/trades.jsonl"

    # ── Prices feed subscriber ─────────────────────────────
    def on_price_update(self, pair: str, bid: float, ask: float):
        """Called by PriceFeed on every price update — feeds the analyzer."""
        self.analyzer.update(pair, bid, ask)

    # ── Public controls (Flask / ESP32) ───────────────────
    def start(self):
        self.running = True
        threading.Thread(target=self._loop, daemon=True).start()
        print("[agent] Started")

    def pause(self):  self.paused = True
    def resume(self): self.paused = False

    def set_speed(self, speed: str):
        speeds = {"slow": 600, "normal": 300, "fast": 120, "max": 45}
        self.loop_secs = speeds.get(speed.lower(), 300)
        print(f"[agent] Speed → {speed} ({self.loop_secs}s loop)")

    def set_max_orders(self, n: int):
        self.max_orders = max(0, int(n))
        print(f"[agent] max_orders → {self.max_orders} (0 = unlimited)")

    def get_status(self) -> dict:
        tx = self.state.summary().get("tx_count", 0)
        remaining = max(0, self.max_orders - tx) if self.max_orders else None
        return {
            "running":         self.running,
            "paused":          self.paused,
            "loop_secs":       self.loop_secs,
            "last_decision":   self.last_decision,
            "state":           self.state.summary(),
            "max_orders":      self.max_orders,        # 0 = unlimited
            "orders_done":     tx,
            "orders_remaining": remaining,             # null = unlimited
        }

    # ── Internal loop ──────────────────────────────────────
    def _loop(self):
        while self.running:
            if not self.paused:
                try:
                    self._tick()
                except Exception as e:
                    print(f"[agent] tick error: {e}")
            time.sleep(self.loop_secs)

    def _tick(self):
        # R5/R7: rank-based auto-flip — only runs when the user has opted in
        # via mode="auto" (AGENT_AUTO). Manual selection of grind or profit
        # is sticky: rank changes won't override it until the user explicitly
        # re-enables auto.
        try:
            from agent import brain as _brain
            if _brain.is_auto():
                lb_stats = self.lb.get_my_stats() if self.lb else {}
                current_mode = _brain.get_mode()
                rank = lb_stats.get("my_rank")
                if isinstance(rank, int):
                    if rank <= 2 and current_mode == "grind":
                        print(f"[agent] 🎯 [auto] Rank ≤ 2 reached (#{rank}) — flip grind → profit")
                        _brain.set_mode_internal("profit")
                    elif rank > 2 and current_mode == "profit":
                        print(f"[agent] 📉 [auto] Rank #{rank} outside top-2 — flip profit → grind")
                        _brain.set_mode_internal("grind")
        except Exception as e:
            print(f"[agent] mode-flip check failed: {e}")

        # 0. Max-orders cap (0 = unlimited)
        tx_done = self.state.summary().get("tx_count", 0)
        if self.max_orders and tx_done >= self.max_orders:
            self.last_decision = {
                "action": "hold",
                "reason": f"max orders reached ({tx_done}/{self.max_orders})",
                "confidence": 100, "time": _now(),
            }
            return

        # 1. Capital-floor safety check (C2 fix: use on-chain Portfolio truth,
        #    not local AgentState which drifts on silent rejects + mid-vs-fill).
        #    Falls back to local state only if Portfolio is missing — never on mainnet.
        chain_usdso = None
        if self.portfolio is not None:
            stats = self.portfolio.summary()
            chain_usdso = stats.get("agent_balance")
            last_ref = stats.get("last_refresh", 0)
            # Hold if portfolio is stale (>120s since last successful refresh)
            if last_ref and time.time() - last_ref > 120:
                print(f"[agent] ⚠️  Portfolio stale ({int(time.time() - last_ref)}s) — holding")
                self.last_decision = {
                    "action": "hold", "reason": "portfolio stale",
                    "confidence": 100, "time": _now()
                }
                return
        balances = self.state.balances()
        usdso_for_floor = chain_usdso if chain_usdso is not None else balances["usdso"]
        if usdso_for_floor <= AGENT_STOP_BELOW:   # L1 fix: <=, not <, so $22 itself blocks
            print(f"[agent] ⚠️  USDso ${usdso_for_floor:.2f} <= floor ${AGENT_STOP_BELOW:.2f} — holding")
            self.last_decision = {
                "action": "hold", "reason": "capital floor hit",
                "confidence": 100, "time": _now()
            }
            return

        # 2. Current data
        prices    = self.analyzer.get_snapshot()
        positions = self.state.open_positions()
        lb_data   = self.lb.get_my_stats()

        if not prices:
            print("[agent] No price data yet, skipping tick")
            return

        # Persist this tick's market state to sqlite so we can read it back
        # after container restarts and feed historical context to the brain.
        try:
            momentum_now = {
                p: ((pd.get("history", [])[-1]["mid"] - pd.get("history", [])[-6]["mid"]) /
                    pd.get("history", [])[-6]["mid"] * 100)
                if len(pd.get("history", [])) >= 6 else 0.0
                for p, pd in prices.items()
            }
            agent_db.record_tick(prices, momentum_now)
        except Exception as e:
            print(f"[agent] tick persistence failed: {e}")

        # 3. Ask GPT — pass DB-backed history + per-pair PnL so the prompt
        # has cross-restart context.
        try:
            db_history = agent_db.last_trades(20)
            db_pnl     = agent_db.pnl_by_pair(24)
        except Exception:
            db_history, db_pnl = [], {}
        decision = decide(prices, positions, balances,
                          self.state.history(), lb_data,
                          db_history=db_history, db_pnl=db_pnl)
        decision["time"] = _now()
        self.last_decision = decision
        print(f"[agent] 🧠 {decision['action'].upper()} "
              f"| {decision.get('pair', '-')} "
              f"| conf={decision.get('confidence', 0)}% "
              f"| {decision.get('reason', '')}")

        # 4. Execute if not hold
        if decision["action"] in ("buy", "sell"):
            self._execute(decision, prices)

    def _execute(self, decision: dict, prices: dict):
        pair      = decision.get("pair")
        action    = decision.get("action")
        amt_usdso = float(decision.get("amount_usdso", AGENT_MIN_TRADE))

        # C3: enforce MAX_CONCURRENT_POS in code (the LLM prompt alone isn't a guarantee).
        # Only blocks new BUYs — sells can always close positions.
        if action == "buy" and len(self.state.open_positions()) >= MAX_CONCURRENT_POS:
            print(f"[agent] ⚠️  {len(self.state.open_positions())} positions open >= MAX_CONCURRENT_POS={MAX_CONCURRENT_POS} — skipping buy")
            return

        # Clamp
        amt_usdso = max(AGENT_MIN_TRADE, min(AGENT_MAX_TRADE, amt_usdso))

        mid = prices.get(pair, {}).get("mid", 0)
        if not mid:
            print(f"[agent] No price for {pair}, skipping")
            return

        from config import MARKETS
        mkt = MARKETS.get(pair)
        if not mkt:
            print(f"[agent] Unknown pair {pair}")
            return

        # Convert USDso → base quantity
        qty = amt_usdso / mid

        # Snap to lot size (read from MARKETS — populated at boot from /v0/markets)
        try:
            lot     = float(mkt.get("lotSize", 0.0001))
            min_qty = float(mkt.get("minQuantity", 0.001))
            qty = round(round(qty / lot) * lot, 8)
            # M3: if bumping to min_qty would exceed AGENT_MAX_TRADE in USDso terms,
            # skip the trade rather than silently overshooting. e.g. WBTC minQty
            # costs ~$7.69 — if LLM said "$0.10 trade", we'd be 77× over.
            if qty < min_qty:
                min_qty_usdso = min_qty * mid
                if min_qty_usdso > AGENT_MAX_TRADE:
                    print(f"[agent] ⚠️  {pair} min qty {min_qty} costs ~${min_qty_usdso:.2f} > AGENT_MAX_TRADE ${AGENT_MAX_TRADE} — skipping")
                    return
                qty = min_qty
        except Exception as e:
            print(f"[agent] Error snapping qty: {e}")

        # Determine funding source
        from config import AGENT_FUNDING_SOURCE
        funding = AGENT_FUNDING_SOURCE

        # Auto-fallback if gas is too low for vault operations
        if funding == "vault":
            try:
                native_bal = self.dex.wallet.native_balance()
                if native_bal < 0.05:
                    print(f"[agent] ⚠️ Gas balance is low ({native_bal:.6f} STT). Falling back to wallet funding to save gas.")
                    funding = "wallet"
            except Exception as e:
                print(f"[agent] Error checking gas balance: {e}")

        # Ensure sufficient funds in vault if using vault funding
        if funding == "vault":
            try:
                from web3 import Web3
                pool_addr = Web3.to_checksum_address(mkt["contract"])
                quote_addr = Web3.to_checksum_address(mkt["quote"])
                base_addr = Web3.to_checksum_address(mkt["base"])

                vault_abi = [
                    {"name": "getWithdrawableBalance", "type": "function", "stateMutability": "view",
                     "inputs": [{"name": "user", "type": "address"}, {"name": "token", "type": "address"}],
                     "outputs": [{"name": "", "type": "uint256"}]}
                ]
                pool = self.dex.wallet.w3.eth.contract(address=pool_addr, abi=vault_abi)

                if action == "buy":
                    # C5: size deposit at the SAME price the order will use (best ask + tick).
                    tick = float(mkt.get("tickSize", 0.0001))
                    book = self.dex.get_orderbook(pair)
                    best_ask = book.get("ask") or 0
                    raw_price = (best_ask + tick) if best_ask else (mid + tick)
                    limit_price = round(round(raw_price / tick) * tick, 6)

                    decimals = mkt["quoteDecimals"]
                    raw_needed = int(qty * limit_price * 1.02 * (10 ** decimals))
                    raw_bal = pool.functions.getWithdrawableBalance(self.dex.wallet.address, quote_addr).call()
                    if raw_bal < raw_needed:
                        # Affordability check: vault + wallet USDso must cover the deposit.
                        wallet_quote = self.dex.wallet.erc20_balance(mkt["quote"], decimals)
                        wallet_raw = int(wallet_quote * (10 ** decimals))
                        if raw_bal + wallet_raw < raw_needed:
                            affordable_qty = ((raw_bal + wallet_raw) / (10 ** decimals)) / (limit_price * 1.02)
                            affordable_qty = round(round(affordable_qty / lot) * lot, 8)
                            if affordable_qty < min_qty:
                                print(f"[agent] ⚠️  BUY {pair} unaffordable: vault {raw_bal/(10**decimals):.4f} + wallet {wallet_quote:.4f} USDso < need {raw_needed/(10**decimals):.4f}. Skipping.")
                                result = {"status": "skipped", "reason": "insufficient quote (wallet+vault)"}
                                self._log({**decision, "qty": qty, "result": result, "mid": mid})
                                return
                            print(f"[agent] ⚠️  Resizing BUY {pair} from {qty} → {affordable_qty} to match wallet+vault.")
                            qty = affordable_qty
                            raw_needed = int(qty * limit_price * 1.02 * (10 ** decimals))
                        deficit = round((raw_needed - raw_bal) / (10 ** decimals) * 1.01, 4)
                        print(f"[agent] Vault deficit for buy (limit {limit_price}, 2% buf): {deficit} USDso. Depositing...")
                        self.dex.vault_deposit(pair, mkt["quote"], deficit)
                elif action == "sell":
                    decimals = mkt["baseDecimals"]
                    raw_needed = int(qty * (10 ** decimals))
                    raw_bal = pool.functions.getWithdrawableBalance(self.dex.wallet.address, base_addr).call()
                    if raw_bal < raw_needed:
                        # Affordability check for sells: vault base + wallet base must cover the deposit.
                        # For native pools (e.g. SOMI), wallet base = native_balance minus gas reserve.
                        if mkt.get("native"):
                            wallet_base = max(0.0, self.dex.wallet.native_balance() - 0.05)
                        else:
                            wallet_base = self.dex.wallet.erc20_balance(mkt["base"], decimals)
                        wallet_raw = int(wallet_base * (10 ** decimals))
                        if raw_bal + wallet_raw < raw_needed:
                            affordable_qty = (raw_bal + wallet_raw) / (10 ** decimals)
                            affordable_qty = round(round(affordable_qty / lot) * lot, 8)
                            if affordable_qty < min_qty:
                                print(f"[agent] ⚠️  SELL {pair} unfundable: vault {raw_bal/(10**decimals):.6f} + wallet {wallet_base:.6f} base < need {raw_needed/(10**decimals):.6f}. Skipping (no SOMI to round-trip).")
                                result = {"status": "skipped", "reason": "no base inventory to sell"}
                                self._log({**decision, "qty": qty, "result": result, "mid": mid})
                                return
                            print(f"[agent] ⚠️  Resizing SELL {pair} from {qty} → {affordable_qty} to match wallet+vault.")
                            qty = affordable_qty
                            raw_needed = int(qty * (10 ** decimals))
                        deficit = round((raw_needed - raw_bal) / (10 ** decimals) * 1.01, 8)
                        print(f"[agent] Vault deficit for sell: {deficit} base. Depositing...")
                        self.dex.vault_deposit(pair, mkt["base"], deficit)
            except Exception as e:
                print(f"[agent] Error checking/depositing to vault: {e}")
                result = {"status": "skipped", "reason": f"vault check failed: {e}"}
                self._log({**decision, "qty": qty, "result": result, "mid": mid})
                return

        # Submit via DreamDEX API
        result = self.dex.place_order(
            symbol      = pair,
            side        = action,
            qty         = qty,
            order_type  = decision.get("order_type", "market"),
            limit_price = decision.get("limit_price"),
            funding     = funding,
        )

        # Log
        log_entry = {**decision, "qty": qty, "result": result, "mid": mid}
        self._log(log_entry)
        # Only mutate local state on vault-delta-PROVEN success. silent_reject,
        # unverified, reverted, error all skip — they either didn't move money
        # or can't be authoritatively confirmed.
        if result.get("status") == "success":
            self.state.record_trade(log_entry)
            # R4: drain quote vault back to wallet after every SELL.
            # The contest leaderboard counts wallet USDso only (not vault).
            # Without this drain each sell parks ~$2 in the vault and the agent
            # silently bleeds wallet runway despite "profitable" round-trips.
            if action == "sell":
                self._drain_quote_vault(pair)
        else:
            print(f"[agent] Skipping state update — order result: {result.get('status')}")

    def _drain_quote_vault(self, pair: str, min_drain: float = 0.10):
        """Withdraw whatever USDso is sitting in the pool's quote vault back
        to the wallet. min_drain avoids wasting gas on dust."""
        try:
            from web3 import Web3
            from config import MARKETS, USDSO_ADDRESS
            mkt = MARKETS.get(pair)
            if not mkt:
                return
            pool_addr = Web3.to_checksum_address(mkt["contract"])
            quote_addr = Web3.to_checksum_address(mkt["quote"])
            abi = [{"name": "getWithdrawableBalance", "type": "function", "stateMutability": "view",
                    "inputs": [{"name": "u", "type": "address"}, {"name": "t", "type": "address"}],
                    "outputs": [{"name": "", "type": "uint256"}]}]
            pool = self.dex.wallet.w3.eth.contract(address=pool_addr, abi=abi)
            raw = pool.functions.getWithdrawableBalance(self.dex.wallet.address, quote_addr).call()
            human = raw / (10 ** int(mkt.get("quoteDecimals", 18)))
            if human < min_drain:
                return
            # withdraw 99.9% to avoid rounding-up issues
            amount = round(human * 0.999, 6)
            print(f"[agent] 💸 Auto-drain {pair}: pulling ${amount:.4f} USDso vault → wallet")
            self.dex.vault_withdraw(pair, mkt["quote"], amount)
        except Exception as e:
            print(f"[agent] auto-drain {pair} failed: {e}")

    def _log(self, entry: dict):
        import os
        os.makedirs("logs", exist_ok=True)
        with open(self.log_path, "a") as f:
            f.write(json.dumps(entry) + "\n")
        # Mirror to sqlite. We pull the current brain mode for tagging so
        # post-contest analysis can split grind- vs profit-mode results.
        try:
            from agent import brain as _brain
            agent_db.record_trade(entry, mode=_brain.get_mode())
        except Exception as e:
            print(f"[agent] db.record_trade failed: {e}")


def _now() -> str:
    return datetime.now().strftime("%H:%M:%S")
