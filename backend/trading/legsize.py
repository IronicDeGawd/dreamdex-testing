"""Depth-aware leg sizing for the taker engines (atomic_round, volume_climb).

A fixed leg is wrong in two directions: on a thin book it walks past the touch
and pays a wider effective spread than quoted (more toll); on a deep book it is
far smaller than it could be, so we send many small txs and burn more gas per
$1k than needed. Sizing each leg to the current top-of-book depth fixes both —
fit the touch to pay the tightest spread, go big when the book is deep to cut
tx count (gas).

Pure math, no I/O — the caller supplies the touch depth and mid it already has
from the order book, so this is trivially unit-testable and shared 1:1 by both
engines.
"""


def touch_fit_leg(depth_usd, mid, minq, *, leg_min, leg_max, frac, fixed_leg, dynamic):
    """Return the USD leg to trade this round.

    Off (dynamic=False) → the caller's fixed leg, unchanged (backward-compat).
    On → clamp ``depth_usd * frac`` into ``[lo, leg_max]`` where ``lo`` is the
    configured floor raised to the pair's minimum fillable notional
    (``minq * mid``) so we never target a leg the book can't fill.

    - Thin touch (depth*frac below lo) → floors at ``lo``.
    - Deep touch (depth*frac above leg_max) → caps at ``leg_max`` (the gas win).
    - If even the cap is below the pair's minimum fillable, returns ``leg_max``;
      the caller's qty<minq skip / spread gate then passes on the pair.
    """
    if not dynamic:
        return fixed_leg
    lo = max(leg_min, minq * mid)
    if lo >= leg_max:
        return leg_max
    return min(max(depth_usd * frac, lo), leg_max)


def touch_depth_usd(bids, asks):
    """Binding round-trip depth at the touch, in USD.

    A round-trip must fit both a buy (takes from ``asks``) and a sell (takes
    from ``bids``), so the binding side is the smaller of the two touch
    notionals. Returns 0.0 if either side is empty.
    """
    if not bids or not asks:
        return 0.0
    buy_depth = asks[0][0] * asks[0][1]
    sell_depth = bids[0][0] * bids[0][1]
    return min(buy_depth, sell_depth)
