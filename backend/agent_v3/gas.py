"""Gas management — watch native SOMI and refuel from working capital.

Rules: we get 50 SOMI, no refills. More gas only by converting our own USDso →
SOMI, and that spend hurts PnL, so we refuel sparingly and only from working
capital (never the reserve). Native SOMI buys need gas ≥ 5,000,000 (docs §7a),
threaded through place_order(gas_min=...).
"""
import config
from agent_v3.inventory import Inventory

SOMI_PAIR = "SOMI:USDso"


class GasManager:
    def __init__(self, dex):
        self.dex = dex

    def somi_balance(self) -> float:
        try:
            return self.dex.wallet.native_balance()
        except Exception as e:
            print(f"[gas] native balance read failed: {e}", flush=True)
            return 0.0

    def needs_refuel(self) -> bool:
        return self.somi_balance() < config.GAS_RESERVE_SOMI

    def refuel(self, total_usdso: float, somi_mid: float) -> dict:
        """Buy ~GAS_REFUEL_USDSO of SOMI via an IOC buy, funded from working capital.

        Returns the place_order result, or a {"status": "skipped"} dict if we
        can't afford it without touching the reserve.
        """
        budget = config.GAS_REFUEL_USDSO
        if not Inventory.can_spend(budget, total_usdso):
            return {"status": "skipped", "reason": "would dip into reserve"}
        if not somi_mid or somi_mid <= 0:
            return {"status": "skipped", "reason": "no SOMI mid"}

        qty = budget / somi_mid
        mkt = config.MARKETS.get(SOMI_PAIR, {})
        minq = float(mkt.get("minQuantity") or 0.0)
        if minq and qty < minq:
            qty = minq
        print(f"[gas] refueling: buy {qty:.4f} SOMI (~${budget}) gas≥{config.SOMI_BUY_GAS_LIMIT}", flush=True)
        try:
            return self.dex.place_order(
                symbol=SOMI_PAIR,
                side="buy",
                qty=qty,
                order_type="ioc",
                funding="wallet",
                gas_min=config.SOMI_BUY_GAS_LIMIT,
            )
        except Exception as e:
            print(f"[gas] refuel failed: {e}", flush=True)
            return {"status": "error", "error": str(e)}
