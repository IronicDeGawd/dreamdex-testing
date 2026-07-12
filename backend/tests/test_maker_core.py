# backend/tests/test_maker_core.py
"""Unit tests for the maker v2 decision core (pure logic, no chain).

Run: pytest backend/tests/test_maker_core.py -q
The WBTC numbers mirror live mainnet params (tick=0.1, lot=1e-5, minq=1e-4).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from maker_core import (apply_fill, desired_quotes, round_lot, should_requote,
                        snap_down, snap_up, stop_loss_action, trend_mode)

WBTC = {"tick": 0.1, "lot": 1e-5, "minq": 1e-4}


def base(**kw):
    # NOTE: WBTC min order = minq × price ≈ $11.8 at 118k — legs below that are
    # unquotable by design (round_lot drops sub-minq to 0). Caught by these very
    # tests; the engine enforces leg ≥ minq notional at startup.
    m = {"bid": 118_000.0, "ask": 118_030.0, "inv_base": 0.0, "avg_cost": 0.0,
         "leg_usd": 15.0, "cap_usd": 20.0, "quote_avail": 30.0,
         "margin_ticks": 1, "allow_buy": True, **WBTC}
    m.update(kw)
    return m


# ── snapping ───────────────────────────────────────────────────────────────
def test_snap_up_never_lowers_and_down_never_raises():
    assert snap_up(118_000.01, 0.1) == 118_000.1
    assert snap_up(118_000.1, 0.1) == 118_000.1        # already aligned: unchanged
    assert snap_down(118_000.09, 0.1) == 118_000.0
    assert snap_down(118_000.1, 0.1) == 118_000.1


def test_round_lot_floors_and_drops_dust():
    assert round_lot(0.000123, 1e-5, 1e-4) == 0.00012   # floors to lot
    assert round_lot(0.00009, 1e-5, 1e-4) == 0.0        # sub-minq → DO NOT quote
    assert round_lot(0.0001, 1e-5, 1e-4) == 0.0001


# ── quoting invariants ─────────────────────────────────────────────────────
def test_flat_book_quotes_buy_only():
    q = desired_quotes(base())
    assert "buy" in q and "sell" not in q
    px, qty = q["buy"]
    assert px == 118_000.0                              # joins the bid exactly
    assert qty > 0 and qty * px <= 15.0 + 1e-6          # never exceeds the leg


def test_buy_never_crosses_unaligned_bid():
    q = desired_quotes(base(bid=118_000.07, ask=118_030.0))
    assert q["buy"][0] == 118_000.0                     # snapped DOWN, ≤ bid


def test_holding_quotes_both_sides_with_no_bleed_floor():
    m = base(inv_base=0.00004, avg_cost=118_010.0)      # small position, under cap
    m["minq"] = 1e-5                                    # inventory ≥ minq for this case
    q = desired_quotes(m)
    assert "buy" in q and "sell" in q
    spx, sqty = q["sell"]
    assert spx >= 118_010.0 + 0.1 - 1e-9                # ≥ avg + margin tick — NO BLEED
    assert sqty <= 0.00004 + 1e-12                      # never sells more than held


def test_sell_floor_wins_when_ask_below_cost():
    # market dropped: ask sits below our cost — quote must sit at cost+margin, not the ask
    m = base(bid=117_000.0, ask=117_010.0, inv_base=0.0001, avg_cost=118_000.0)
    q = desired_quotes(m)
    assert q["sell"][0] >= 118_000.1 - 1e-9


def test_at_cap_sell_only():
    m = base(inv_base=0.00017, avg_cost=118_000.0)      # ~$20 at mid ≥ cap
    q = desired_quotes(m)
    assert "buy" not in q and "sell" in q


def test_trend_gate_blocks_buy_sell_stays():
    m = base(inv_base=0.0001, avg_cost=118_000.0, allow_buy=False)
    q = desired_quotes(m)
    assert "buy" not in q and "sell" in q


def test_buy_respects_quote_avail():
    # avail below the ~$11.8 min order → refuses to quote rather than overspend
    assert "buy" not in desired_quotes(base(quote_avail=5.0))
    # avail between min order and leg → spends at most avail
    q = desired_quotes(base(quote_avail=13.0))
    px, qty = q["buy"]
    assert qty * px <= 13.0 + 1e-9


def test_no_quotes_on_unusable_book():
    assert desired_quotes(base(bid=0.0)) == {}
    assert desired_quotes(base(ask=117_000.0)) == {}    # crossed/invalid book


def test_dust_inventory_not_quoted():
    m = base(inv_base=5e-5, avg_cost=118_000.0)         # below minq=1e-4
    assert "sell" not in desired_quotes(m)


# ── requote dead-band ──────────────────────────────────────────────────────
def test_requote_only_past_drift():
    assert not should_requote(118_000.0, 118_000.2, 0.1, 3)   # 2 ticks < 3
    assert should_requote(118_000.0, 118_000.4, 0.1, 3)       # 4 ticks > 3


# ── stop-loss ──────────────────────────────────────────────────────────────
def test_stop_holds_above_trigger():
    m = {"inv_base": 0.0001, "avg_cost": 100.0, "mid": 91.0, "bid": 90.9,
         "minq": 1e-4, "stop_pct": 0.10, "max_slip_pct": 0.03}
    assert stop_loss_action(m) == "hold"                 # -9% > -10% trigger


def test_stop_cuts_inside_slip_floor():
    m = {"inv_base": 0.0001, "avg_cost": 100.0, "mid": 89.0, "bid": 88.5,
         "minq": 1e-4, "stop_pct": 0.10, "max_slip_pct": 0.03}
    assert stop_loss_action(m) == "cut"                  # bid 88.5 ≥ floor 87.0


def test_stop_defers_on_gapped_bid():
    m = {"inv_base": 0.0001, "avg_cost": 100.0, "mid": 86.0, "bid": 85.0,
         "minq": 1e-4, "stop_pct": 0.10, "max_slip_pct": 0.03}
    assert stop_loss_action(m) == "defer"                # bid below floor: don't dump


def test_stop_disabled_or_flat_holds():
    assert stop_loss_action({"inv_base": 0.0, "avg_cost": 100.0, "mid": 50.0,
                             "bid": 50.0, "minq": 1e-4, "stop_pct": 0.10}) == "hold"
    assert stop_loss_action({"inv_base": 1.0, "avg_cost": 100.0, "mid": 50.0,
                             "bid": 50.0, "minq": 1e-4, "stop_pct": 0.0}) == "hold"


# ── trend hysteresis ───────────────────────────────────────────────────────
def test_trend_hysteresis_deadband():
    assert trend_mode("neutral", 0.02, 0.01, -0.015) == "up"
    assert trend_mode("up", -0.005, 0.01, -0.015) == "up"       # dead-band keeps prior
    assert trend_mode("up", -0.02, 0.01, -0.015) == "down"
    assert trend_mode("down", 0.005, 0.01, -0.015) == "down"    # dead-band again
    assert trend_mode("down", None, 0.01, -0.015) == "down"     # unknown keeps prior


# ── avg-cost accounting ────────────────────────────────────────────────────
def test_apply_fill_averages_and_realizes():
    pos = {"qty": 0.0, "avg": 0.0}
    assert apply_fill(pos, "buy", 100.0, 1.0) == 0.0
    assert apply_fill(pos, "buy", 110.0, 1.0) == 0.0
    assert pos["avg"] == 105.0 and pos["qty"] == 2.0
    realized = apply_fill(pos, "sell", 106.0, 1.0)
    assert abs(realized - 1.0) < 1e-9                    # (106-105) × 1
    assert pos["qty"] == 1.0 and pos["avg"] == 105.0
    apply_fill(pos, "sell", 105.5, 1.0)
    assert pos["qty"] == 0.0 and pos["avg"] == 0.0       # flat resets the avg


def test_round_trip_at_margin_is_profitable():
    """End-to-end no-bleed: buy at our bid, sell at the core's own floor quote —
    realized PnL must be ≥ one margin tick on the quantity."""
    pos = {"qty": 0.0, "avg": 0.0}
    m = base()
    bpx, bqty = desired_quotes(m)["buy"]
    apply_fill(pos, "buy", bpx, bqty)
    m2 = base(inv_base=pos["qty"], avg_cost=pos["avg"],
              bid=bpx - 50, ask=bpx - 40)                # market moved DOWN after our buy
    m2["minq"] = 1e-5
    spx, sqty = desired_quotes(m2)["sell"]
    realized = apply_fill(pos, "sell", spx, sqty)
    assert realized >= 0.1 * sqty - 1e-9                 # ≥ margin (1 tick = $0.1) × qty
