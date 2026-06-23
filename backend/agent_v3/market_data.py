"""Per-pair live market data: book, mid, spread, short-window volatility, depth.

Canonical mid is the book mid ((best_bid+best_ask)/2) — reliable and what the
maker quotes around. Volatility is the stdev of recent mid returns in bps, used
to widen the spread when the market is moving.
"""
import math
import statistics
from collections import deque

import config


class MarketData:
    def __init__(self, dex, history_len: int = 30):
        self.dex = dex
        self._mids: dict[str, deque] = {}
        self._history_len = history_len

    def snapshot(self, pair: str) -> dict | None:
        """Live book snapshot for `pair`, or None if the book is unusable."""
        try:
            ob = self.dex.get_orderbook(pair)
        except Exception as e:
            print(f"[market_data] orderbook {pair} failed: {e}", flush=True)
            return None
        bid, ask = ob.get("bid"), ob.get("ask")
        if not bid or not ask or bid <= 0 or ask <= 0 or ask < bid:
            return None  # one-sided / empty book → no quote, no yield

        mid = (bid + ask) / 2.0
        spread_abs = ask - bid
        spread_bps = (spread_abs / mid) * 1e4 if mid else 0.0

        mkt = config.MARKETS.get(pair, {})
        self._push_mid(pair, mid)
        return {
            "pair": pair,
            "bid": bid,
            "ask": ask,
            "bid_qty": ob.get("bid_qty", 0.0),
            "ask_qty": ob.get("ask_qty", 0.0),
            "mid": mid,
            "spread_abs": spread_abs,
            "spread_bps": spread_bps,
            "short_vol": self.short_vol(pair),
            "tick": mkt.get("tickSize"),
            "lot": mkt.get("lotSize"),
            "minq": mkt.get("minQuantity"),
        }

    def _push_mid(self, pair: str, mid: float) -> None:
        dq = self._mids.setdefault(pair, deque(maxlen=self._history_len))
        dq.append(mid)

    def short_vol(self, pair: str) -> float:
        """Short-window realized volatility in bps (stdev of pct mid-returns)."""
        dq = self._mids.get(pair)
        if not dq or len(dq) < 3:
            return 0.0
        mids = list(dq)
        rets = [
            (mids[i] - mids[i - 1]) / mids[i - 1]
            for i in range(1, len(mids))
            if mids[i - 1]
        ]
        if len(rets) < 2:
            return 0.0
        try:
            return statistics.stdev(rets) * 1e4
        except statistics.StatisticsError:
            return 0.0

    def desired_half_spread_ticks(self, snap: dict) -> int:
        """How many ticks off mid to quote each side.

        spread target = max(protocol tick floor, k × volatility). Translated to a
        per-side tick offset around mid, never below the configured margin.
        """
        tick = snap.get("tick") or 0.0
        mid = snap["mid"]
        if not tick or not mid:
            return config.MAKER_MARGIN_TICKS
        # volatility-driven half-spread, expressed in price then ticks
        vol_frac = (snap["short_vol"] / 1e4) * config.MAKER_SPREAD_K
        vol_price = mid * vol_frac
        vol_ticks = int(math.ceil(vol_price / tick)) if tick else 0
        return max(config.MAKER_MARGIN_TICKS, vol_ticks)
