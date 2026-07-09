# backend/monitor/leaderboard.py
"""Polls the dreamDEX mainnet leaderboard for the competition wallet.

The leaderboard is mainnet-only — even when the bot is running on testnet,
we look up the MAINNET competition address so the watch can show our
ranking the moment Vercel deploys the leaderboard."""
import os
import time
import time as _t
import threading
import requests
from collections import deque
from config import LEADERBOARD_URL, LEADERBOARD_ADDRESS, LEADERBOARD_POLL

# Runway stops here, not at $0: you can't fund a leg below this, and gas needs cover.
RUNWAY_RESERVE = float(os.environ.get("RUNWAY_RESERVE_USDSO", 15.0))

class LeaderboardMonitor:
    def __init__(self):
        self.stats = {
            "my_rank":  "?",
            "total":    0,
            "my_tx":    0,
            "third_tx": 0,
            "gap":      0,
            "signal":   "MAINTAIN",
            "address":  LEADERBOARD_ADDRESS,
            "live":     False,  # flips True after first successful fetch
        }
        self.cohort = []   # every trader, volume-ranked, with efficiency metrics
        # Recent balance history per address. The board's balance EXCLUDES inventory
        # held mid-round-trip, so a trader caught between buy and sell reads ~a full
        # leg poorer. Traders are flat most of the time, so the MAX balance over a
        # recent window recovers their true flat-state capital — and it still tracks
        # a genuine decline (the window rolls forward).
        self._bal_hist = {}                       # addr -> deque[(ts, balance)]
        self._flat_window_s = 1800                # 30 min
        self.running = False

    def start(self):
        self.running = True
        threading.Thread(target=self._loop, daemon=True).start()
        print(f"[LeaderboardMonitor] Polling {LEADERBOARD_URL} for {LEADERBOARD_ADDRESS}")

    def _loop(self):
        while self.running:
            self._fetch()
            time.sleep(LEADERBOARD_POLL)

    def _fetch(self):
        try:
            resp = requests.get(LEADERBOARD_URL, timeout=5)
            if resp.status_code != 200:
                # Vercel returns plaintext "DEPLOYMENT_NOT_FOUND" until launch.
                print(f"[LeaderboardMonitor] HTTP {resp.status_code} — {resp.text[:80]}")
                return
            try:
                data = resp.json()
            except ValueError:
                print(f"[LeaderboardMonitor] non-JSON body — leaderboard likely not live yet")
                return

            # Tolerate a few common shapes:
            #   [{"address","tx_count"}, ...]
            #   {"leaderboard":[...]}
            #   {"entries":[...]}  /  {"data":[...]}
            #   {"traders":[...]}  ← real shape from
            #     https://dreamdex-leaderboard-super-cool.vercel.app/api/leaderboard
            lb = data
            if isinstance(data, dict):
                for key in ("traders", "leaderboard", "entries", "data", "results"):
                    if key in data and isinstance(data[key], list):
                        lb = data[key]; break
            if not isinstance(lb, list):
                print(f"[LeaderboardMonitor] unexpected shape: {type(data).__name__}")
                return

            # Normalize field names across API revisions.
            def tx_of(e):
                for k in ("tx_count", "txCount", "transactions", "txs", "count"):
                    if e.get(k) is not None:
                        try: return int(e[k])
                        except (TypeError, ValueError): pass
                return 0

            def vol_of(e):
                for k in ("volumeUsdso", "volume", "volume_usdso"):
                    if e.get(k) is not None:
                        try: return float(e[k])
                        except (TypeError, ValueError): pass
                return 0.0

            # R4 ranks by VOLUME (highest wins; top-2 qualify). Ranking by txCount
            # was an R1 leftover and reported us several places too low.
            lb = sorted(lb, key=vol_of, reverse=True)
            total = len(lb)
            my_rank = "?"
            my_tx = 0
            my_fills = 0
            my_vol = 0.0
            my_pnl = 0.0
            my_bal = 0.0
            target = LEADERBOARD_ADDRESS.lower()
            for idx, entry in enumerate(lb):
                if str(entry.get("address", "")).lower() == target:
                    my_rank  = idx + 1
                    my_tx    = tx_of(entry)
                    my_fills = int(entry.get("fills", 0) or 0)
                    try: my_vol = float(entry.get("volumeUsdso") or entry.get("volume") or 0)
                    except (TypeError, ValueError): my_vol = 0.0
                    try: my_pnl = float(entry.get("pnl") or 0)
                    except (TypeError, ValueError): my_pnl = 0.0
                    try: my_bal = float(entry.get("usdsoBalance") or 0)
                    except (TypeError, ValueError): my_bal = 0.0
                    break

            # Gap in VOLUME. Ranked >1: how much volume to overtake the trader above.
            # Ranked #1: our lead over #2. Positive = the number that matters.
            if isinstance(my_rank, int) and my_rank > 1:
                gap = round(vol_of(lb[my_rank - 2]) - my_vol, 2)
                gap_to = f"#{my_rank - 1}"
            elif isinstance(my_rank, int) and my_rank == 1:
                gap = round(my_vol - (vol_of(lb[1]) if total > 1 else 0.0), 2)
                gap_to = "lead over #2"
            else:
                gap, gap_to = 0.0, "?"

            # Top-2 qualify for the next cohort, so that's the line that matters.
            if isinstance(my_rank, int) and my_rank <= 2:
                signal = "QUALIFYING"
            else:
                signal = "ACCELERATE"

            # Per-trader efficiency. The board defines PnL = usdsoBalance - 150, so a
            # negative PnL is capital burned. cost_per_1k = $ burned per 1k of volume
            # (lower is better); runway = how much MORE volume their remaining balance
            # buys at that burn rate. Caveat: balance excludes inventory held mid-trade,
            # so a trader caught mid-round-trip looks momentarily worse than they are.
            now = _t.time()
            cohort = []
            for idx, e in enumerate(lb):
                v = vol_of(e); tx = tx_of(e)
                try: bal = float(e.get("usdsoBalance") or 0)
                except (TypeError, ValueError): bal = 0.0
                addr = str(e.get("address", ""))

                # Roll the balance window and take its max as the flat-state capital.
                h = self._bal_hist.setdefault(addr, deque())
                h.append((now, bal))
                while h and now - h[0][0] > self._flat_window_s:
                    h.popleft()
                flat_bal = max(b for _, b in h)
                mid_trade = flat_bal - bal > 1.0     # currently holding inventory

                # Efficiency off the FLAT balance, so a mid-trade snapshot can't
                # invent a loss. PnL = balance - 150 by the board's own definition.
                pnl_flat = flat_bal - 150.0
                burned = max(-pnl_flat, 0.0)
                # Needs enough volume to be meaningful — a trader with ~0 volume and a
                # nonzero loss yields an absurd ratio (683,285/1k). Below the floor: n/a.
                cpk = round(burned / v * 1000, 3) if v >= 100 else None
                # Runway stops at a reserve, not $0: you cannot trade a leg you can't
                # fund, and gas has to come from somewhere.
                spendable = max(flat_bal - RUNWAY_RESERVE, 0.0)
                runway = round(spendable / cpk * 1000) if (cpk and cpk > 0) else None
                try: v24 = float(e.get("volumeUsdso24h") or 0)
                except (TypeError, ValueError): v24 = 0.0
                cohort.append({
                    "rank": idx + 1,
                    "handle": e.get("handle") or "—",
                    "address": addr,
                    "tx": tx,
                    "volume": v,
                    "volume24h": v24,
                    "pnl": round(pnl_flat, 2),       # flat-state, inventory-corrected
                    "pnl_raw": round(bal - 150.0, 2),  # what the board shows right now
                    "balance": round(flat_bal, 2),
                    "balance_raw": round(bal, 2),
                    "mid_trade": mid_trade,
                    "cost_per_1k": cpk,          # None = no volume; 0 = not burning
                    "runway_volume": runway,     # None = infinite (profitable / no burn)
                    "avg_tx": round(v / tx, 2) if tx else None,
                    "is_me": addr.lower() == target,
                })
            self.cohort = cohort

            self.stats = {
                "my_rank":   my_rank,
                "total":     total,
                "my_tx":     my_tx,
                "my_fills":  my_fills,
                "my_volume": my_vol,
                "my_pnl":    my_pnl,
                "my_balance": my_bal,
                "gap":       gap,
                "gap_to":    gap_to,
                "signal":    signal,
                "address":   LEADERBOARD_ADDRESS,
                "live":      True,
            }
        except Exception as e:
            print(f"[LeaderboardMonitor] fetch error: {e}")

    def get_my_stats(self) -> dict:
        return self.stats

    def get_cohort(self) -> list:
        """Every trader, volume-ranked, with efficiency + runway. `is_me` marks ours."""
        return self.cohort
