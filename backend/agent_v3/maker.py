"""Per-pair no-bleed market maker (the execution core).

Proven R2 cycle, made pair-agnostic and parameter-driven:
  flat → place PostOnly BUY at the bid → wait → on fill go long
  long → place PostOnly SELL at max(ask, buy_cost + margin_ticks) → wait → on fill go flat

Every SELL is floored at buy_cost + margin, so a round-trip can never lose (the
PnL% multiplier is protected). Orders rest near mid, so they also earn maker
yield. Fills are detected by wallet USDso balance delta (orders are wallet-funded,
so the quote token moves in the wallet on a fill — a uniform signal across pairs).

This module places REAL orders. Validate on testnet (DREAMDEX_ENV=testnet)
before pointing it at the mainnet contest wallet.
"""
import math
import time

import config
from agent_v3 import context_store as ctx

USDSO_DECIMALS = 18


class PairMaker:
    def __init__(self, dex, market_data, inventory, pair, params_fn, capital_fn, stop_event, dry_run=False):
        self.dex = dex
        self.md = market_data
        self.inv = inventory
        self.pair = pair
        self.params_fn = params_fn      # () -> {spread_mult, leg_usd, max_inv_usd, pause}
        self.capital_fn = capital_fn    # () -> total USDso (wallet) for working-capital checks
        self.stop = stop_event
        self.dry_run = dry_run
        self.state = "flat"
        self.buy_px = 0.0
        self.buy_qty = 0.0

    # ── helpers ───────────────────────────────────────────────────────────
    def _usdso(self) -> float:
        try:
            return self.dex.wallet.erc20_balance(config.USDSO_ADDRESS, USDSO_DECIMALS)
        except Exception:
            return 0.0

    def _base_balance(self) -> float:
        """Wallet balance of the pair's base token (native for SOMI, else ERC-20).
        Used as the buy-fill signal — base only arrives on a real fill."""
        mkt = config.MARKETS.get(self.pair, {})
        try:
            if mkt.get("native"):
                return self.dex.wallet.native_balance()
            return self.dex.wallet.erc20_balance(mkt["base"], int(mkt.get("baseDecimals", 18)))
        except Exception:
            return 0.0

    @staticmethod
    def _snap_px(px: float, tick: float) -> float:
        if not tick:
            return px
        dec_str = f"{tick:.10f}".rstrip("0")
        decimals = len(dec_str.split(".")[1]) if "." in dec_str else 0
        return round(round(px / tick) * tick, decimals)

    @staticmethod
    def _dec_places(step: float) -> int:
        if not step:
            return 0
        s = f"{step:.12f}".rstrip("0")
        return len(s.split(".")[1]) if "." in s else 0

    def _round_lot(self, qty: float, lot: float, minq: float) -> float:
        """Snap qty down to a whole number of lots, formatted to the lot's decimal
        precision — otherwise float noise (0.00720000001) trips the API's
        "must be a multiple of lot size" check."""
        dec = self._dec_places(lot) if lot else 8
        if lot:
            qty = math.floor(round(qty / lot, 6)) * lot   # round-before-floor avoids FP undershoot
            qty = round(qty, dec)
        if minq and qty < minq:
            qty = round(minq, dec)
        return qty

    def _cancel_open(self):
        """Best-effort cancel of our resting orders on this pair before re-quoting."""
        try:
            for o in self.dex.get_open_orders(self.pair) or []:
                oid = o.get("orderId") or o.get("id") or o.get("order_id")
                if oid:
                    self.dex.cancel_order(self.pair, str(oid))
        except Exception as e:
            print(f"[maker {self.pair}] cancel_open failed: {e}", flush=True)

    def _ctx_row(self, snap, **kw) -> dict:
        row = {
            "pair": self.pair, "mid": snap.get("mid"), "best_bid": snap.get("bid"),
            "best_ask": snap.get("ask"), "spread_abs": snap.get("spread_abs"),
            "spread_bps": snap.get("spread_bps"), "bid_depth": snap.get("bid_qty"),
            "ask_depth": snap.get("ask_qty"), "short_vol": snap.get("short_vol"),
            "inv_base": self.inv.base(self.pair), "cum_pnl": self.inv.realized_pnl,
        }
        row.update(kw)
        return row

    # ── lifecycle ──────────────────────────────────────────────────────────
    def run(self):
        # Clear any orders left resting by a prior crash so we start clean.
        if not self.dry_run:
            self._cancel_open()
        # Resume a prior long position if we already hold inventory.
        if self.inv.base(self.pair) > 0:
            self.state = "long"
            self.buy_px = self.inv.avg_cost(self.pair)
            self.buy_qty = self.inv.base(self.pair)
        print(f"[maker {self.pair}] start (state={self.state}, dry_run={self.dry_run})", flush=True)
        while not self.stop.is_set():
            try:
                self._tick()
            except Exception as e:
                print(f"[maker {self.pair}] tick error: {e}", flush=True)
                self.stop.wait(config.MAKER_POLL_S)

    def _tick(self):
        params = self.params_fn()
        if params.get("pause"):
            self.stop.wait(config.MAKER_POLL_S)
            return
        snap = self.md.snapshot(self.pair)
        if not snap or not snap.get("tick"):
            ctx.log_event(self._ctx_row(snap or {"mid": None}, event="skip", note="no book/params"))
            self.stop.wait(config.MAKER_POLL_S)
            return
        if self.state == "flat":
            self._buy_leg(snap, params)
        else:
            self._sell_leg(snap, params)

    def _buy_leg(self, snap, params):
        tick = snap["tick"]
        mid = snap["mid"]
        # At/over the inventory cap → don't add; flip to selling if we hold base.
        if self.inv.skew(self.pair, mid) >= 1.0:
            if self.inv.base(self.pair) > 0:
                self.state = "long"
                self.buy_px = self.inv.avg_cost(self.pair)
                self.buy_qty = self.inv.base(self.pair)
            else:
                self.stop.wait(config.MAKER_POLL_S)
            return

        buy_px = self._snap_px(snap["bid"], tick)
        leg_usd = params.get("leg_usd", config.MAKER_LEG_USD)
        size_mult = max(0.1, 1.0 - self.inv.skew(self.pair, mid))  # buy less the longer we are
        qty = self._round_lot((leg_usd * size_mult) / buy_px, snap.get("lot"), snap.get("minq"))
        cost = qty * buy_px
        if cost <= 0 or not self.inv.can_spend(cost, self.capital_fn()):
            ctx.log_event(self._ctx_row(snap, event="skip", side="buy", note="insufficient working capital"))
            self.stop.wait(config.MAKER_POLL_S)
            return

        ctx.log_event(self._ctx_row(snap, event="quote", side="buy", our_px=buy_px, qty=qty, order_type="postonly"))
        if self.dry_run:
            self.stop.wait(config.MAKER_POLL_S)
            return

        filled = self._place_and_wait(snap, "buy", buy_px, qty, tick)
        if filled > 0:
            self.inv.record_fill(self.pair, "buy", buy_px, filled)
            self.buy_px, self.buy_qty = buy_px, self.inv.base(self.pair)
            self.state = "long"
            ctx.log_event(self._ctx_row(snap, event="fill", side="buy", our_px=buy_px, qty=filled,
                                        status="success", realized_pnl_delta=0.0))

    def _sell_leg(self, snap, params):
        tick = snap["tick"]
        qty = self.inv.base(self.pair)
        if qty <= 0:
            self.state = "flat"
            return
        floor = self.buy_px + config.MAKER_MARGIN_TICKS * tick           # no-bleed invariant
        sell_px = self._snap_px(max(snap["ask"], floor), tick)
        if sell_px <= snap["bid"]:                                       # never cross → stay PostOnly
            sell_px = self._snap_px(snap["bid"] + tick, tick)
        if sell_px < floor:                                              # guard: never below cost+margin
            sell_px = self._snap_px(floor, tick)

        ctx.log_event(self._ctx_row(snap, event="quote", side="sell", our_px=sell_px, qty=qty, order_type="postonly"))
        if self.dry_run:
            self.stop.wait(config.MAKER_POLL_S)
            return

        filled = self._place_and_wait(snap, "sell", sell_px, qty, tick)
        if filled > 0:
            delta = self.inv.record_fill(self.pair, "sell", sell_px, filled)
            ctx.log_event(self._ctx_row(snap, event="fill", side="sell", our_px=sell_px, qty=filled,
                                        status="success", realized_pnl_delta=delta))
            print(f"[maker {self.pair}] sold {filled:.4f} +{delta:.4f} USDso (cum {self.inv.realized_pnl:.4f})", flush=True)
        if self.inv.base(self.pair) <= (snap.get("minq") or 0):
            self.state = "flat"
            self.buy_px, self.buy_qty = 0.0, 0.0

    def _place_and_wait(self, snap, side, px, qty, tick) -> float:
        """Cancel any resting order on this pair, place ONE PostOnly order, and wait.

        Returns the filled quantity, inferred from the wallet USDso delta (which
        captures partial fills exactly and ignores gas, since gas is paid in the
        native token). Cancelling everything before placing — and again before
        returning — guarantees at most one order ever rests, so re-quotes and
        partial fills can never stack.
        """
        self._cancel_open()                       # clear anything resting first
        # Baseline the asset we RECEIVE on a fill — NOT the one we reserve. A
        # wallet-funded buy debits (reserves) USDso at placement, so watching
        # USDso would false-positive; the base token only arrives on a real fill.
        if side == "buy":
            recv0 = self._base_balance()
        else:
            recv0 = self._usdso()
        # Native pools (SOMI) need the >=5M gas floor for the payout guard.
        gas_min = config.SOMI_BUY_GAS_LIMIT if config.MARKETS.get(self.pair, {}).get("native") else 0
        res = self.dex.place_order(self.pair, side, qty, order_type="postonly",
                                   limit_price=px, funding="wallet", skip_sim=True, gas_min=gas_min)
        status = res.get("status")
        if status not in ("placed_unfilled", "unverified", "success"):
            ctx.log_event(self._ctx_row(snap, event="error", side=side, status=status,
                                        tx_hash=res.get("tx_hash"), note=str(res)[:120]))
            self.stop.wait(config.MAKER_POLL_S)
            return 0.0

        deadline = time.time() + config.MAKER_REQUOTE_S

        def _filled() -> float:
            if side == "buy":
                return max(0.0, self._base_balance() - recv0)        # base received
            return max(0.0, (self._usdso() - recv0) / px) if px else 0.0  # base sold

        while not self.stop.is_set():
            self.stop.wait(config.MAKER_POLL_S)
            f = _filled()
            if f >= qty * 0.99:                   # fully filled
                return qty
            s = self.md.snapshot(self.pair)
            touch = (s["bid"] if side == "buy" else s["ask"]) if s else px
            if abs(touch - px) > config.MAKER_DRIFT_TICKS * tick or time.time() > deadline:
                self._cancel_open()               # sweep the (partial) resting order
                return _filled()                  # whatever actually filled
        self._cancel_open()
        return _filled()
