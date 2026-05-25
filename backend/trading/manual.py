# backend/trading/manual.py
from trading.dreamdex import DreamDEX

class ManualTrader:
    def __init__(self):
        self.dex = DreamDEX()
        
    def execute(self, pair: str, side: str, amount_usdso: float, prices: dict = None) -> dict:
        print(f"[ManualTrader] Triggered manual trade: {side} {pair} for ${amount_usdso}")
        
        if not prices:
            return {"status": "error", "error": "No price feed provided"}
            
        mid = prices.get(pair, {}).get("mid", 0)
        if not mid:
            return {"status": "error", "error": f"No price available for {pair}"}
            
        qty = round(amount_usdso / mid, 8)
        
        # Snap to lot size
        try:
            lot_sizes = {
                "WETH:USDso": (0.0001, 0.001),
                "WBTC:USDso": (0.00001, 0.0001),
                "SOMI:USDso": (0.01, 1.0),
            }
            lot, min_qty = lot_sizes.get(pair, (0.0001, 0.001))
            qty = round(round(qty / lot) * lot, 8)
            if qty < min_qty:
                qty = min_qty
        except Exception as e:
            print(f"[ManualTrader] Error snapping qty: {e}")
            
        result = self.dex.place_order(
            symbol=pair,
            side=side,
            qty=qty,
            order_type="market"
        )
        return result
