# backend/agent/state.py
from config import AGENT_CAPITAL

class AgentState:
    def __init__(self):
        self._balances = {
            "usdso": AGENT_CAPITAL,
            "weth": 0.0,
            "wbtc": 0.0,
            "somi": 0.0,
            "usdc.e": 0.0,
            "total": AGENT_CAPITAL
        }
        self._positions = {}
        self._history = []
        self._tx_count = 0

    def balances(self):
        # Calculate total dynamically if we had prices, but for now just return the dictionary
        return self._balances

    def open_positions(self):
        return self._positions

    def history(self):
        return self._history

    def record_trade(self, log_entry: dict):
        self._history.append(log_entry)
        self._tx_count += 1
        
        # Parse log_entry to update balances and positions locally
        # { "action": "buy", "pair": "WETH:USDso", "amount_usdso": 2.0, "qty": 0.005, "mid": 400.0, ... }
        action = log_entry.get("action")
        pair = log_entry.get("pair")
        if not pair or action not in ("buy", "sell"):
            return
            
        base = pair.split(":")[0].lower()
        amt_usdso = log_entry.get("amount_usdso", 0)
        qty = log_entry.get("qty", 0)
        mid = log_entry.get("mid", 0)
        
        if action == "buy":
            self._balances["usdso"] -= amt_usdso
            self._balances[base] = self._balances.get(base, 0) + qty
            
            # Simple position tracking
            if pair in self._positions:
                old_qty = self._positions[pair]["qty"]
                old_entry = self._positions[pair]["entry_price"]
                new_qty = old_qty + qty
                new_entry = ((old_qty * old_entry) + (qty * mid)) / new_qty
                self._positions[pair] = {"qty": new_qty, "entry_price": new_entry}
            else:
                self._positions[pair] = {"qty": qty, "entry_price": mid}
                
        elif action == "sell":
            # Just close position completely for simplicity if qty >= held
            if pair in self._positions:
                held = self._positions[pair]["qty"]
                if qty >= held:
                    del self._positions[pair]
                else:
                    self._positions[pair]["qty"] -= qty
                    
            self._balances[base] = max(0, self._balances.get(base, 0) - qty)
            self._balances["usdso"] += (qty * mid) # approximate

    def summary(self):
        return {
            "tx_count": self._tx_count,
            "usdso_balance": self._balances["usdso"],
            "open_positions": len(self._positions)
        }
