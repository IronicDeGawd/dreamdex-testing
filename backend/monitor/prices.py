# backend/monitor/prices.py
"""
PriceFeed — polls DreamDEX for live prices.

Primary:  REST GET /v0/markets/{symbol}/tickers  (24h snapshot, includes last price)
Fallback: REST GET /v0/markets/{symbol}/trades   (most recent fill = last mid)

Price is stored as mid = (best_bid + best_ask) / 2 when available, 
or last trade price otherwise.

The PriceFeed also maintains a WebSocket orderbook subscriber (ws_orderbook.py)
for real-time bid/ask updates — but that runs separately and calls .update() here.
"""
import time
import threading
import requests
from collections import deque
from config import DREAMDEX_HTTP, MARKETS, PRICE_POLL_SECONDS, PRICE_HISTORY_LEN


class PriceFeed:
    def __init__(self):
        self._lock    = threading.Lock()
        self._prices  = {
            pair: {"mid": 0.0, "bid": 0.0, "ask": 0.0, "spread": 0.0, "history": deque(maxlen=PRICE_HISTORY_LEN)}
            for pair in MARKETS
        }
        self.running      = False
        self._subscribers = []   # callables: fn(pair, bid, ask)
        self._session     = requests.Session()

    def start(self):
        self.running = True
        threading.Thread(target=self._loop, daemon=True).start()
        print("[PriceFeed] Started REST polling")

    def stop(self):
        self.running = False

    def add_subscriber(self, callback):
        self._subscribers.append(callback)

    # ── Called by WebSocket listener (real-time) ──────────
    def update_book(self, pair: str, bid: float, ask: float):
        """Ingest a real-time best bid/ask from the WS orderbook channel."""
        if bid <= 0 or ask <= 0:
            return
        mid    = (bid + ask) / 2
        spread = (ask - bid) / mid * 100 if mid else 0
        with self._lock:
            p = self._prices.get(pair)
            if p is None:
                return
            p["bid"]    = bid
            p["ask"]    = ask
            p["mid"]    = mid
            p["spread"] = spread
            p["history"].append({"mid": mid, "bid": bid, "ask": ask, "ts": time.time()})
        for sub in self._subscribers:
            try:
                sub(pair, bid, ask)
            except Exception as e:
                print(f"[PriceFeed] subscriber error: {e}")

    # ── REST polling fallback ─────────────────────────────
    def _loop(self):
        while self.running:
            self._fetch_all_rest()
            time.sleep(PRICE_POLL_SECONDS)

    def _fetch_all_rest(self):
        for pair in MARKETS:
            self._fetch_ticker(pair)

    def _fetch_ticker(self, pair: str):
        """GET /v0/markets/{symbol}/tickers — returns 24h OHLCV snapshot.
        The API accepts WETH:USDso with literal colon in the URL path.
        Response shape: {"symbols": [{"close": "0", "high": "0", ...}]}
        """
        try:
            url  = f"{DREAMDEX_HTTP}/v0/markets/{pair}/tickers"
            resp = self._session.get(url, timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                # Real shape: {"symbols": [{"close": ..., "high": ..., "low": ..., "open": ..., "symbol": ..., "timestamp": ...}]}
                if isinstance(data, dict) and "symbols" in data:
                    syms = data["symbols"]
                    data = syms[0] if syms else {}
                elif isinstance(data, list) and data:
                    data = data[0]
                close = float(data.get("close", 0))
                last  = float(data.get("lastPrice", close))
                if last > 0:
                    bid = last * 0.9999
                    ask = last * 1.0001
                    self.update_book(pair, bid, ask)
                    return
            # Fallback: most recent trade
            self._fetch_last_trade(pair)
        except Exception as e:
            print(f"[PriceFeed] REST ticker error {pair}: {e}")
            self._fetch_last_trade(pair)

    def _fetch_last_trade(self, pair: str):
        try:
            url  = f"{DREAMDEX_HTTP}/v0/markets/{pair}/trades"
            resp = self._session.get(url, params={"limit": 1}, timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                trades_list = []
                if isinstance(data, dict):
                    trades_list = data.get("trades", [])
                elif isinstance(data, list):
                    trades_list = data
                
                if trades_list:
                    t  = trades_list[0]
                    px = float(t.get("price", 0))
                    if px > 0:
                        bid = px * 0.9999
                        ask = px * 1.0001
                        self.update_book(pair, bid, ask)
        except Exception as e:
            print(f"[PriceFeed] last-trade fallback error {pair}: {e}")

    # ── Public API ────────────────────────────────────────
    def latest(self) -> dict:
        """Returns snapshot suitable for Flask JSON response."""
        with self._lock:
            return {
                pair: {
                    "mid":     p["mid"],
                    "bid":     p["bid"],
                    "ask":     p["ask"],
                    "spread":  p["spread"],
                }
                for pair, p in self._prices.items()
            }

    def snapshot_with_history(self) -> dict:
        """Returns full snapshot including price history (for brain.py)."""
        with self._lock:
            return {
                pair: {
                    "mid":     p["mid"],
                    "bid":     p["bid"],
                    "ask":     p["ask"],
                    "spread":  p["spread"],
                    "history": list(p["history"]),
                }
                for pair, p in self._prices.items()
                if p["mid"] > 0
            }
