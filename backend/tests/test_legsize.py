"""Unit tests for depth-aware leg sizing (pure math, no chain).

Run: pytest backend/tests/test_legsize.py -q
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from trading.legsize import touch_fit_leg, touch_depth_usd

# WBTC-like: mid 60000, minq 1e-4 → min fillable notional = $6.
DEEP = dict(minq=1e-4, leg_min=20.0, leg_max=200.0, frac=0.8, fixed_leg=45.0, dynamic=True)


def test_dynamic_off_returns_fixed_leg():
    # Bounds ignored entirely when dynamic is off.
    off = dict(DEEP, dynamic=False)
    assert touch_fit_leg(10_000.0, 60000.0, 1e-4, **_kw(off)) == 45.0
    assert touch_fit_leg(1.0, 60000.0, 1e-4, **_kw(off)) == 45.0


def test_deep_book_caps_at_leg_max():
    # depth*frac = 8000 >> leg_max → cap at 200 (the gas win).
    assert touch_fit_leg(10_000.0, 60000.0, 1e-4, **_kw(DEEP)) == 200.0


def test_mid_book_takes_depth_fraction():
    # depth 100 → 100*0.8 = 80, inside [20,200].
    assert touch_fit_leg(100.0, 60000.0, 1e-4, **_kw(DEEP)) == 80.0


def test_thin_book_floors_at_leg_min():
    # depth 10 → 10*0.8 = 8, below leg_min 20 → floors at 20.
    assert touch_fit_leg(10.0, 60000.0, 1e-4, **_kw(DEEP)) == 20.0


def test_min_fillable_raises_the_floor():
    # minq*mid = 1e-3 * 60000 = $60 > leg_min 20 → floor becomes 60,
    # so a $8 touch fraction is lifted to 60, never an unfillable leg.
    cfg = dict(DEEP)
    assert touch_fit_leg(10.0, 60000.0, 1e-3, **_kw(cfg)) == 60.0


def test_min_fillable_above_cap_returns_cap():
    # minq*mid = $300 > leg_max 200 → lo>=leg_max branch returns the cap.
    assert touch_fit_leg(10.0, 60000.0, 5e-3, **_kw(DEEP)) == 200.0


def test_touch_depth_is_binding_side():
    bids = [(100.0, 2.0)]   # $200 resting
    asks = [(101.0, 0.5)]   # $50.5 resting
    assert touch_depth_usd(bids, asks) == 50.5  # smaller side binds


def test_touch_depth_empty_side_is_zero():
    assert touch_depth_usd([], [(1.0, 1.0)]) == 0.0
    assert touch_depth_usd([(1.0, 1.0)], []) == 0.0


def _kw(d):
    return {k: d[k] for k in ("leg_min", "leg_max", "frac", "fixed_leg", "dynamic")}
