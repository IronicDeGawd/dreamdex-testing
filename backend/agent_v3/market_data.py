"""Per-pair live market data: book, mid, spread, short-window volatility, depth.

Canonical mid is the book mid ((best_bid+best_ask)/2) — reliable and what the
maker quotes around. Volatility is the stdev of recent mid returns in bps, used
to widen the spread when the market is moving.
"""
import statistics
import time
from collections import deque

import config


class MarketData:
    def __init__(self, dex, history_len: int = 30):
        self.dex = dex
        self._mids: dict[str, deque] = {}
        self._history_len = history_len
        self._trend_cache: dict[str, tuple] = {}   # pair -> (ts, pct_24h)

    def trend_pct_24h(self, pair: str):
        """24h price change from DreamDEX candles (last close vs ~24h-ago close).
        Cached for MAKER_TREND_CACHE_S so the per-tick maker doesn't hammer REST.
        Returns a float fraction (e.g. +0.04 = +4%) or None if unavailable."""
        now = time.time()
        hit = self._trend_cache.get(pair)
        if hit and now - hit[0] < config.MAKER_TREND_CACHE_S:
            return hit[1]
        pct = None
        try:
            c = self.dex.get_candles(pair, interval="1h", limit=48)
            if c and len(c) >= 2:
                last = float(c[-1]["close"])
                idx = max(0, len(c) - 1 - 24)
                old = float(c[idx]["close"])
                if old > 0:
                    pct = (last - old) / old
        except Exception as e:
            print(f"[market_data] trend {pair} failed: {e}", flush=True)
        self._trend_cache[pair] = (now, pct)
        return pct

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
