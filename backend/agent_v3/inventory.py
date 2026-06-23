"""Position, realized PnL, and reserve / working-capital math.

PnL matters as much as volume in R3 (Effective Volume = Raw × (1 + PnL%)), so we
track realized PnL per round-trip and defend a fixed USDso reserve that working
capital may never dip into.
"""
import config
from agent_v3 import context_store as ctx


class Inventory:
    def __init__(self, persist: bool = True):
        # per-pair: base quantity held and volume-weighted average cost (USDso/base)
        self.positions: dict[str, dict] = {}
        self.realized_pnl: float = 0.0
        self.persist = persist

    def load(self) -> None:
        """Restore positions + realized PnL from SQLite so a restart resumes the
        real on-chain position instead of starting flat."""
        if not self.persist:
            return
        self.positions, self.realized_pnl = ctx.load_inventory()
        if self.positions:
            print(f"[inventory] restored {len(self.positions)} positions, "
                  f"realized={self.realized_pnl:.4f}", flush=True)

    def _save(self) -> None:
        if self.persist:
            ctx.save_inventory(self.positions, self.realized_pnl)

    # ── fills ────────────────────────────────────────────────────────────
    def record_fill(self, pair: str, side: str, px: float, qty: float) -> float:
        """Update position from a fill. Returns realized PnL delta (USDso)."""
        pos = self.positions.setdefault(pair, {"base": 0.0, "avg_cost": 0.0})
        delta = 0.0
        if side == "buy":
            new_base = pos["base"] + qty
            if new_base > 0:
                pos["avg_cost"] = (pos["base"] * pos["avg_cost"] + qty * px) / new_base
            pos["base"] = new_base
        elif side == "sell":
            sell_qty = min(qty, pos["base"]) if pos["base"] > 0 else qty
            delta = (px - pos["avg_cost"]) * sell_qty
            pos["base"] = max(0.0, pos["base"] - qty)
            if pos["base"] == 0.0:
                pos["avg_cost"] = 0.0
            self.realized_pnl += delta
        self._save()
        return delta

    def base(self, pair: str) -> float:
        return self.positions.get(pair, {}).get("base", 0.0)

    def avg_cost(self, pair: str) -> float:
        return self.positions.get(pair, {}).get("avg_cost", 0.0)

    def inventory_value_usdso(self, mids: dict[str, float]) -> float:
        """Mark all base positions to current mid."""
        total = 0.0
        for pair, pos in self.positions.items():
            mid = mids.get(pair)
            if mid:
                total += pos["base"] * mid
        return total

    # ── capital / reserve ────────────────────────────────────────────────
    @staticmethod
    def working_capital(total_usdso: float) -> float:
        """USDso available to trade — the reserve is off-limits."""
        return max(0.0, total_usdso - config.RESERVE_USDSO)

    @staticmethod
    def can_spend(amount: float, total_usdso: float) -> bool:
        return amount <= Inventory.working_capital(total_usdso)

    def pnl_pct(self, total_account_value_usdso: float) -> float:
        """PnL% vs the fixed $150 start — this is the leaderboard multiplier driver."""
        return (total_account_value_usdso - config.STARTING_CAPITAL) / config.STARTING_CAPITAL

    # ── inventory skew (mean-revert toward flat) ──────────────────────────
    def skew(self, pair: str, mid: float) -> float:
        """Fraction in [-1, 1]: how skewed we are long(+)/short(-) vs the inventory cap.

        Used to bias quote sizes — when long, lean on selling; when flat, quote even.
        """
        if not mid or config.MAKER_MAX_INV_USD <= 0:
            return 0.0
        inv_usd = self.base(pair) * mid
        return max(-1.0, min(1.0, inv_usd / config.MAKER_MAX_INV_USD))
