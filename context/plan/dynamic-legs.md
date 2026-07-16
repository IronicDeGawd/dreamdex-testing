# Dynamic Leg Sizing for the Taker Engines

## Context
Both taker engines (`atomic_round.py`, `volume_climb.py`) trade a **fixed** leg (`ATOM_LEG_USD` / `CLIMB_LEG_USD`). A fixed leg is wrong in two directions:
- **On a thin book** it can exceed the size resting at the touch, so the order "walks the book" and pays a wider effective spread than the quote — more toll than necessary.
- **On a deep book** (e.g. WBTC, multi-$M touch) it's far smaller than it could be, so we send many small transactions and burn more gas per $1k than needed — and gas (SOMI) is our nearest cliff.

A competitor (R4 trader-3, `0x62bb…`) and the Arena team both confirmed the technique: **size each leg to the current top-of-book depth**, within a range. Fit the touch → fill at the tightest spread without walking; go big when the book is deep → fewer txs, less gas. Our own notes already flagged this ("(pair, leg) should be chosen jointly for lowest toll") — we built the *measurement* (`eff_spread_pct`/`_vwap`) but never made the leg adaptive.

**Goal:** replace the fixed leg with a depth-aware leg bounded by `[LEG_MIN, LEG_MAX]`, so toll drops on thin books and gas drops on deep books. Opt-in (fixed leg stays the default for backward-compat). Build + A/B test next session.

## Why it helps us specifically
Our atomic run used a fixed $45 leg. On WBTC (huge depth) that's needlessly small — we could run $150–250 legs and cut tx count (gas) ~4×. On WETH thin moments, $45 can walk a ~$70–90 touch — a smaller leg would fit and pay less toll. Dynamic sizing captures both. Gas is the win that matters most (it's the runway constraint on R4).

## The data is already there
`book_levels(pair)` (`atomic_round.py:181`, identical in `volume_climb.py`) returns `(bids, asks)` as lists of `(price, qty)`. So the **touch depth in USD** is:
- buy side: `asks[0][0] * asks[0][1]` (price × resting qty at best ask)
- sell side: `bids[0][0] * bids[0][1]`
The binding side is the smaller (a round-trip must fit both a buy and a sell). Nothing new needs fetching.

## Files to modify
- `backend/atomic_round.py` — primary (current best engine).
- `backend/volume_climb.py` — identical `book_levels`/`_vwap`/`eff_spread_pct`/`pick_cheapest` functions; apply the same change 1:1 (optional second commit).

## Design (atomic_round.py)
1. **New env** (all optional; absent ⇒ current fixed-leg behavior):
   - `ATOM_LEG_MIN`, `ATOM_LEG_MAX` — the range. If both set, dynamic mode is on.
   - `ATOM_TOUCH_FRAC` (default 0.8) — fraction of the touch depth to take, leaving margin so a competing taker at the same instant doesn't push us into walking.
   - Keep `ATOM_LEG_USD` as the fallback fixed leg when the range isn't set.
2. **New helper `touch_fit_leg(bids, asks)`**: `depth = min(asks[0][0]*asks[0][1], bids[0][0]*bids[0][1]); leg = clamp(depth * TOUCH_FRAC, LEG_MIN, LEG_MAX)`. Returns the fixed `LEG_USD` when dynamic mode is off.
3. **`pick_cheapest`** (`atomic_round.py:224`): compute each pair's `touch_fit_leg` first, then evaluate `eff_spread_pct(bids, asks, that_leg)` at the pair's own dynamic leg (a touch-fitting leg makes `eff ≈ quoted`, the tightest achievable). Rank by `eff/boost` as today. Return the winner's leg alongside the existing tuple (add `leg` to the return; update the single call site in the main loop, `atomic_round.py:~330`).
4. **Main loop**: use the returned per-pair `leg` as `leg_this` instead of the fixed `LEG_USD` (keep the `force_keepalive` path overriding to `KEEPALIVE_LEG`). `build_trip(sp, ob, leg_this)` already scales qty, lot-snap, and the on-chain `maxTollQuote` off whatever leg it's handed (`atomic_round.py:275`) — no change needed there.

## Edge cases (must handle)
- **Touch below `LEG_MIN`**: leg floors at `LEG_MIN` (may still walk slightly); the existing spread/cost gate then pauses the pair if that makes it too dear — correct behavior, no special-case.
- **`LEG_MIN` below the pair's `minq × price`**: `build_trip` already returns qty < minq and the loop skips; additionally clamp `LEG_MIN` up to `minq × mid` per pair so we never target an unfillable leg.
- **Deep book**: leg caps at `LEG_MAX` — the gas-efficiency win.
- **Depth fetch fails**: `pick_cheapest` already falls back to top-of-book; in that path use the fixed `LEG_USD` (no qty data to size from).
- **Leg-vs-capital**: a bigger `LEG_MAX` must still respect free USDso. Keep `LEG_MAX ≤ 0.8× free USDso` in mind when configuring; the control `/launch` guard already enforces this on the *launch* leg, but dynamic `LEG_MAX` should be chosen below it.

## Verification (next session)
1. **Offline unit check**: feed synthetic `(bids, asks)` into `touch_fit_leg` — thin touch → `LEG_MIN`, deep touch → `LEG_MAX`, mid → `depth*frac`. Confirm clamping + the `minq` floor.
2. **R3 A/B (the proof)**: run the R3 wallet twice on the same pairs, ~same duration —
   - baseline: fixed `ATOM_LEG_USD=45`;
   - dynamic: `ATOM_LEG_MIN=20 ATOM_LEG_MAX=200`.
   Compare **$/1k toll** and **gas SOMI per $1k** from the STOP summaries. Expect dynamic to show equal-or-lower toll and **lower gas per $1k** (bigger legs on WBTC's deep book). If gas/1k doesn't drop, the feature isn't worth shipping — that's the go/no-go.
3. Deploy control-only isn't needed (engine-side change → image rebuild); wire a dashboard `LEG_MIN/MAX` field only after the A/B proves the win.

## Notes
- R4-relevant now (cheaper volume), and the same depth-aware sizing is exactly what a future Arena grid engine wants — so this is reusable beyond R4.
- Save this plan to `context/plan/dynamic-legs.md` and build on a fresh branch `feature/dynamic-legs` off `main`.
