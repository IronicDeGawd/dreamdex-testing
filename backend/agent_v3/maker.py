"""Per-pair two-sided market maker (the execution core).

Rests a PostOnly BID and a PostOnly ASK simultaneously and captures the spread
as flow hits either side. Discipline that protects the PnL-weighted score:
  - SELL is only ever posted at >= avg_cost + margin_ticks → no realized loss.
  - base inventory is capped per pair (cap_usd) → bounded price exposure.
  - quotes rest across ticks; we only cancel/replace a side on drift or fill,
    to avoid burning gas (cancels on the native SOMI pool cost a 5M-gas tx).

Fills are tracked via get_open_orders `remaining` (NOT balance delta): on a
two-sided book, placing an order RESERVES the funding token immediately, so a
balance delta can't tell a reservation from a fill — but `remaining` can.

This places REAL orders. Validate on testnet before mainnet.
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
        self.params_fn = params_fn        # () -> {leg_usd, pause, ...}
        self.capital_fn = capital_fn      # () -> total wallet USDso
        self.stop = stop_event
        self.dry_run = dry_run
        self.native = bool(config.MARKETS.get(pair, {}).get("native"))
        self.gas_min = config.SOMI_BUY_GAS_LIMIT if self.native else 0
        # per-pair base-inventory cap, scaled by this pair's capital allocation
        alloc = config.MAKER_PAIR_ALLOC.get(pair, 1.0 / max(1, len(config.ELIGIBLE_PAIRS)))
        max_alloc = max(config.MAKER_PAIR_ALLOC.values()) if config.MAKER_PAIR_ALLOC else 1.0
        self.alloc = alloc
        self.cap_usd = config.MAKER_MAX_INV_USD * (alloc / max_alloc)
        # our currently-resting orders, per side: {"id","price","qty","filled"}
        self.orders: dict[str, dict | None] = {"buy": None, "sell": None}
        self._reenter_after = 0.0          # epoch; buy side paused until this (post stop-loss)
        self._start_ts = time.time()       # idle baseline before any trade history exists

    # ── helpers ───────────────────────────────────────────────────────────
    @staticmethod
    def _dec_places(step: float) -> int:
        if not step:
            return 0
        s = f"{step:.12f}".rstrip("0")
        return len(s.split(".")[1]) if "." in s else 0

    def _snap_px(self, px: float, tick: float) -> float:
        if not tick:
            return px
        dec = self._dec_places(tick)
        return round(round(px / tick) * tick, dec)

    def _round_lot(self, qty: float, lot: float, minq: float) -> float:
        dec = self._dec_places(lot) if lot else 8
        if lot:
            qty = math.floor(round(qty / lot, 6)) * lot
            qty = round(qty, dec)
        if minq and qty < minq:
            qty = round(minq, dec)
        return qty

    def _open_map(self) -> dict:
        try:
            return {str(o.get("orderId") or o.get("id") or o.get("order_id")): o
                    for o in (self.dex.get_open_orders(self.pair) or [])}
        except Exception as e:
            print(f"[maker {self.pair}] get_open_orders failed: {e}", flush=True)
            return {}

    def _cancel_open(self):
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

    def _apply_fill(self, side, px, qty, snap):
        delta = self.inv.record_fill(self.pair, side, px, qty)
        ctx.log_event(self._ctx_row(snap, event="fill", side=side, our_px=px, qty=qty,
                                    status="success", realized_pnl_delta=(delta if side == "sell" else 0.0)))
        if side == "sell":
            print(f"[maker {self.pair}] sold {qty:.6f} @ {px} → +{delta:.4f} USDso "
                  f"(cum {self.inv.realized_pnl:.4f})", flush=True)

    # ── lifecycle ──────────────────────────────────────────────────────────
    def run(self):
        if not self.dry_run:
            self._cancel_open()                # start with a clean book
        self.orders = {"buy": None, "sell": None}
        print(f"[maker {self.pair}] start two-sided (cap=${self.cap_usd:.0f}, native={self.native}, "
              f"inv={self.inv.base(self.pair):.6f}, dry_run={self.dry_run})", flush=True)
        while not self.stop.is_set():
            try:
                self._tick()
            except Exception as e:
                print(f"[maker {self.pair}] tick error: {e}", flush=True)
            self.stop.wait(config.MAKER_POLL_S)

    def _tick(self):
        if not ctx.control_enabled():            # stopped → flatten to USDso and idle
            if not self.dry_run:
                snap = self.md.snapshot(self.pair)
                if snap:
                    self._flatten(snap)
            return
        params = self.params_fn()
        if params.get("pause"):
            if not self.dry_run and (self.orders["buy"] or self.orders["sell"]):
                self._cancel_open()
                self.orders = {"buy": None, "sell": None}
            return
        snap = self.md.snapshot(self.pair)
        if not snap or not snap.get("tick"):
            return
        if not self.dry_run:
            self._poll_fills(snap)             # record any fills on resting orders first
            if self._check_stop_loss(snap):    # position underwater past the stop → cut + cooldown
                return
        desired = self._desired(snap, params)
        self._reconcile(desired, snap)

    def _desired(self, snap, params) -> dict:
        """Target {side: (price, qty)} given inventory, caps, and no-loss rule."""
        tick, mid, bid, ask = snap["tick"], snap["mid"], snap["bid"], snap["ask"]
        lot, minq = snap.get("lot"), snap.get("minq") or 0.0
        leg = params.get("leg_usd", config.MAKER_LEG_USD)
        inv_base = self.inv.base(self.pair)
        inv_usd = inv_base * mid
        avg = self.inv.avg_cost(self.pair)
        quote_avail = self.inv.working_capital(self.capital_fn()) * self.alloc
        out: dict = {}

        # Trend guard: spread capture needs a two-way book. If this coin fell more
        # than TREND_GUARD_PCT over the lookback, it's a one-way downtrend — pause
        # BUYING (don't catch the knife / accumulate a bleeding bag). SELL stays on.
        trend_down = False
        if config.TREND_GUARD_PCT > 0:
            m_ago = ctx.mid_ago(self.pair, config.TREND_LOOKBACK_S)
            if m_ago is None:
                trend_down = True          # no trend data → FAIL SAFE: don't buy/accumulate
            elif mid < m_ago * (1 - config.TREND_GUARD_PCT):
                trend_down = True

        # Keepalive: if we've gone too long without a trade (trend-guarded into cash),
        # allow ONE tiny buy to reset the 24h idle-DQ clock. Tiny leg → negligible bag.
        lt = ctx.last_trade_ts() or self._start_ts
        keepalive = (time.time() - lt) > config.LIVENESS_MAX_IDLE_S

        # BUY — add inventory while under the cap and we can afford a min order
        # (paused during the post-stop-loss cooldown, and while trending down — unless
        # a keepalive is due, in which case we place a tiny buy regardless of trend)
        if inv_usd < self.cap_usd and time.time() >= self._reenter_after and (not trend_down or keepalive):
            our_bid = self._snap_px(bid, tick)
            eff_leg = config.KEEPALIVE_LEG_USD if (trend_down and keepalive) else leg
            buy_usd = min(eff_leg, self.cap_usd - inv_usd, quote_avail)
            if our_bid > 0 and buy_usd >= max(minq * our_bid, 0):
                qty = self._round_lot(buy_usd / our_bid, lot, minq)
                if qty > 0 and qty * our_bid <= quote_avail + 1e-9:
                    out["buy"] = (our_bid, qty)

        # SELL — reduce inventory only at >= avg_cost + margin (never a realized loss)
        if inv_base > minq:
            floor = (avg + config.MAKER_MARGIN_TICKS * tick) if avg > 0 else ask
            our_ask = self._snap_px(max(ask, floor), tick)
            if our_ask <= bid:                       # keep it a maker order
                our_ask = self._snap_px(bid + tick, tick)
            if our_ask >= floor:                     # guard: never below cost+margin
                qty = self._round_lot(min(inv_base, leg / our_ask), lot, minq)
                if qty > 0:
                    out["sell"] = (our_ask, qty)
        return out

    def _reconcile(self, desired: dict, snap):
        tick = snap["tick"]
        for side in ("buy", "sell"):
            want = desired.get(side)
            cur = self.orders[side]
            if want is None:
                if cur and not self.dry_run:
                    self._cancel_side(side)
                continue
            px, qty = want
            if self.dry_run:
                ctx.log_event(self._ctx_row(snap, event="quote", side=side, our_px=px, qty=qty,
                                            order_type="postonly", note="dry_run"))
                continue
            if cur is None:
                self._place_side(side, px, qty, snap)
            elif abs(cur["price"] - px) > config.MAKER_DRIFT_TICKS * tick:
                self._cancel_side(side)
                self._place_side(side, px, qty, snap)
            # else: leave the resting order in place

    def _place_side(self, side, px, qty, snap):
        before = set(self._open_map().keys())
        res = self.dex.place_order(self.pair, side, qty, order_type="postonly",
                                   limit_price=px, funding="wallet", skip_sim=True, gas_min=self.gas_min)
        status = res.get("status")
        ctx.log_event(self._ctx_row(snap, event="quote", side=side, our_px=px, qty=qty,
                                    order_type="postonly", status=status, tx_hash=res.get("tx_hash")))
        if status == "success":                      # filled inside the settle window
            self._apply_fill(side, px, qty, snap)
            return
        if status not in ("placed_unfilled", "unverified"):
            ctx.log_event(self._ctx_row(snap, event="error", side=side, status=status, note=str(res)[:120]))
            return
        oid = None
        for _ in range(3):
            new = set(self._open_map().keys()) - before
            if new:
                oid = next(iter(new))
                break
            self.stop.wait(1)
        if oid:
            self.orders[side] = {"id": oid, "price": px, "qty": qty, "filled": 0.0}

    def _cancel_side(self, side):
        cur = self.orders[side]
        if cur:
            try:
                self.dex.cancel_order(self.pair, str(cur["id"]))
            except Exception as e:
                print(f"[maker {self.pair}] cancel {side} failed: {e}", flush=True)
            self.orders[side] = None

    def _flatten(self, snap):
        """Cancel resting orders and IOC-sell base inventory back to USDso.
        Crosses the spread (accepts a sliver of cost) to guarantee we end flat."""
        if self.orders["buy"] or self.orders["sell"]:
            self._cancel_open()
            self.orders = {"buy": None, "sell": None}
        base = self.inv.base(self.pair)
        minq = snap.get("minq") or 0.0
        if base <= minq:
            return
        qty = self._round_lot(base, snap.get("lot"), minq)
        px = self._snap_px(snap["bid"], snap["tick"])     # sell into the bid (immediate)
        res = self.dex.place_order(self.pair, "sell", qty, order_type="ioc",
                                   limit_price=px, funding="wallet", skip_sim=True, gas_min=self.gas_min)
        if res.get("status") == "success":
            self._apply_fill("sell", px, qty, snap)
            print(f"[maker {self.pair}] flattened {qty:g} → USDso", flush=True)
        else:
            ctx.log_event(self._ctx_row(snap, event="error", side="sell", status=res.get("status"),
                                        note=f"flatten: {str(res)[:90]}"))

    def _check_stop_loss(self, snap) -> bool:
        """If the position is underwater past MAKER_STOP_LOSS_PCT below avg cost,
        cut it all into the bid (overriding the no-loss rule) and start a re-entry
        cooldown. Returns True when a stop fired (skip the rest of the tick)."""
        pct = config.MAKER_STOP_LOSS_PCT
        if pct <= 0:
            return False
        inv_base = self.inv.base(self.pair)
        minq = snap.get("minq") or 0.0
        avg = self.inv.avg_cost(self.pair)
        mid = snap.get("mid") or 0.0
        if inv_base <= minq or avg <= 0 or mid <= 0 or mid > avg * (1 - pct):
            return False
        # limit-protected: never dump below this floor. If the book has gapped
        # below it (flash crash / thin bid), DEFER — hold through the spike and
        # retry next tick rather than sell at the bottom of a dip that may bounce
        # (the whipsaw that cost us). The $100 account-stop is the catastrophe backstop.
        floor = avg * (1 - pct - config.MAKER_STOP_MAX_SLIP_PCT)
        bid = snap.get("bid") or 0.0
        if bid < floor:
            ctx.log_event(self._ctx_row(snap, event="stop_deferred", side="sell",
                                        note=f"bid {bid:.6f} < floor {floor:.6f} (avg {avg:.6f})"))
            return False
        if self.orders["buy"] or self.orders["sell"]:
            self._cancel_open()
            self.orders = {"buy": None, "sell": None}
        qty = self._round_lot(inv_base, snap.get("lot"), minq)
        px = self._snap_px(bid, snap["tick"])              # cross into the bid (>= floor) → immediate
        res = self.dex.place_order(self.pair, "sell", qty, order_type="ioc",
                                   limit_price=px, funding="wallet", skip_sim=True, gas_min=self.gas_min)
        if res.get("status") == "success":
            delta = self.inv.record_fill(self.pair, "sell", px, qty)
            ctx.log_event(self._ctx_row(snap, event="stop_loss", side="sell", our_px=px, qty=qty,
                                        status="success", realized_pnl_delta=delta))
            self._reenter_after = time.time() + config.MAKER_STOP_COOLDOWN_S
            print(f"[maker {self.pair}] STOP-LOSS cut {qty:g} @ {px} "
                  f"(avg {avg:.6f}, mid {mid:.6f}, {delta:.4f} USDso) → "
                  f"cooldown {config.MAKER_STOP_COOLDOWN_S}s", flush=True)
            return True
        ctx.log_event(self._ctx_row(snap, event="error", side="sell", status=res.get("status"),
                                    note=f"stop_loss: {str(res)[:90]}"))
        return False

    def _poll_fills(self, snap):
        om = self._open_map()
        for side in ("buy", "sell"):
            cur = self.orders[side]
            if not cur:
                continue
            o = om.get(cur["id"])
            if o is None:                            # gone from book → fully filled
                newly = cur["qty"] - cur["filled"]
                if newly > 1e-12:
                    self._apply_fill(side, cur["price"], newly, snap)
                self.orders[side] = None
            else:
                remaining = float(o.get("remaining") or cur["qty"])
                filled_now = max(0.0, cur["qty"] - remaining)
                newly = filled_now - cur["filled"]
                if newly > 1e-12:
                    self._apply_fill(side, cur["price"], newly, snap)
                    cur["filled"] = filled_now
