# backend/maker_core.py
"""Pure decision core for the v2 maker — no I/O, no chain, no clocks.

Every rule that decides WHERE to quote, HOW MUCH, and WHEN to cut lives here as
a pure function of explicit inputs, so the whole strategy is unit-testable
without a network. The engine (maker_v2.py) only does plumbing: read book →
build the input dict → act on the returned intents.

Invariants enforced here (the no-bleed contract):
  - a SELL is never quoted below avg_cost + margin_ticks × tick (prices snap UP
    to the tick, so rounding can only raise the floor, never shave it)
  - a BUY is never quoted above the best bid (PostOnly joins, never crosses)
  - a BUY never spends past the inventory cap or the available quote balance
  - quantities floor to the lot and drop to zero below minQuantity (no bumping
    up to minq — that silently overspent the leg in the R3 maker)
"""
import math

# ── price / size snapping ──────────────────────────────────────────────────
def dec_places(step: float) -> int:
    if not step:
        return 0
    s = f"{step:.12f}".rstrip("0")
    return len(s.split(".")[1]) if "." in s else 0


def snap_down(px: float, tick: float) -> float:
    if not tick:
        return px
    return round(math.floor(px / tick + 1e-9) * tick, dec_places(tick))


def snap_up(px: float, tick: float) -> float:
    if not tick:
        return px
    return round(math.ceil(px / tick - 1e-9) * tick, dec_places(tick))


def round_lot(qty: float, lot: float, minq: float) -> float:
    """Floor to lot; below minQuantity returns 0.0 (do not quote) — never bump
    up to minq, which would overspend the intended notional."""
    if lot:
        qty = round(math.floor(round(qty / lot, 9)) * lot, dec_places(lot))
    return qty if qty >= (minq or 0.0) else 0.0


# ── quoting ────────────────────────────────────────────────────────────────
def desired_quotes(m: dict) -> dict:
    """Target quotes {side: (price, qty)} for one pair.

    Input keys:
      bid, ask, tick, lot, minq          — live book + pair params
      inv_base, avg_cost                 — current position (chain-authoritative)
      leg_usd, cap_usd, quote_avail      — sizing + budget
      margin_ticks                       — min profit per round-trip, in ticks
      allow_buy                          — trend / cooldown gate (caller decides)
      inv_floor_base                     — standing inventory NEVER quoted for
                                           sale (Arena fair-play: a position that
                                           oscillates in a band above a floor is
                                           real trading; one that flushes to zero
                                           every cycle pattern-matches the
                                           "near-flat cycle" wash-trade flag)
    """
    bid, ask, tick = m["bid"], m["ask"], m["tick"]
    lot, minq = m.get("lot") or 0.0, m.get("minq") or 0.0
    inv, avg = m.get("inv_base") or 0.0, m.get("avg_cost") or 0.0
    leg, cap = m["leg_usd"], m["cap_usd"]
    avail = m.get("quote_avail") or 0.0
    margin = m.get("margin_ticks", 1)
    floor_inv = m.get("inv_floor_base") or 0.0
    out: dict = {}
    if not bid or not ask or bid <= 0 or ask <= bid or not tick:
        return out           # unusable book: quote nothing
    mid = (bid + ask) / 2

    # BUY — join the best bid while under the inventory cap
    if m.get("allow_buy", True) and inv * mid < cap:
        px = snap_down(bid, tick)               # never above bid → never crosses
        buy_usd = min(leg, cap - inv * mid, avail)
        if px > 0 and buy_usd > 0:
            qty = round_lot(buy_usd / px, lot, minq)
            if qty > 0 and qty * px <= avail + 1e-9:
                out["buy"] = (px, qty)

    # SELL — reduce inventory ABOVE the standing floor, never below cost + margin
    sellable = inv - floor_inv
    if sellable >= minq and sellable > 0:
        px_floor = (avg + margin * tick) if avg > 0 else ask
        px = snap_up(max(ask, px_floor), tick)  # snap UP: rounding can't shave the floor
        if px <= bid:                           # keep it a maker order
            px = snap_up(bid + tick, tick)
        if px >= px_floor - 1e-12:              # guard held by construction; belt+braces
            qty = round_lot(min(sellable, leg / px), lot, minq)
            if qty > 0:
                out["sell"] = (px, qty)
    return out


def should_requote(cur_px: float, want_px: float, tick: float, drift_ticks: float) -> bool:
    """Cancel/replace only when the wanted price drifted beyond the dead-band —
    every replace is two gas-costing txs, so small drift is left alone."""
    if not tick:
        return cur_px != want_px
    return abs(cur_px - want_px) > drift_ticks * tick


# ── risk ───────────────────────────────────────────────────────────────────
def stop_loss_action(m: dict) -> str:
    """'hold' | 'defer' | 'cut' for the current position.

    cut   — mid fell past stop_pct below avg cost AND the bid is still above the
            slip floor: sell into the bid now (bounded, protected loss).
    defer — stop condition met but the bid gapped below the floor (flash crash /
            thin book): hold and retry, don't dump at the bottom (the R3 whipsaw).
    hold  — no position / no stop condition / stop disabled.
    """
    pct = m.get("stop_pct") or 0.0
    inv, avg = m.get("inv_base") or 0.0, m.get("avg_cost") or 0.0
    mid, bid = m.get("mid") or 0.0, m.get("bid") or 0.0
    minq = m.get("minq") or 0.0
    if pct <= 0 or inv < minq or inv <= 0 or avg <= 0 or mid <= 0:
        return "hold"
    if mid > avg * (1 - pct):
        return "hold"
    floor = avg * (1 - pct - (m.get("max_slip_pct") or 0.0))
    return "cut" if bid >= floor else "defer"


def trend_mode(prev: str, pct_24h: float | None, up_pct: float, down_pct: float) -> str:
    """Hysteresis trend state: 'up' at ≥ up_pct, 'down' at ≤ down_pct, otherwise
    keep the previous mode (dead-band stops flip-flopping). Unknown keeps prev.
    The engine pauses BUYING in 'down' — the sell side always stays on."""
    if pct_24h is None:
        return prev
    if pct_24h >= up_pct:
        return "up"
    if pct_24h <= down_pct:
        return "down"
    return prev


# ── accounting (avg-cost) ──────────────────────────────────────────────────
def apply_fill(pos: dict, side: str, px: float, qty: float) -> float:
    """Update {qty, avg} in place for a fill; returns realized PnL (sells only).
    Avg-cost method: buys re-average, sells realize (px - avg) × qty."""
    if qty <= 0:
        return 0.0
    if side == "buy":
        tot = pos["qty"] + qty
        pos["avg"] = (pos["avg"] * pos["qty"] + px * qty) / tot if tot > 0 else 0.0
        pos["qty"] = tot
        return 0.0
    realized = (px - pos["avg"]) * min(qty, pos["qty"])
    pos["qty"] = max(0.0, pos["qty"] - qty)
    if pos["qty"] <= 0:
        pos["avg"] = 0.0
    return realized
