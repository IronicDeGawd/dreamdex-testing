# backend/monitor/leaderboard.py
"""Polls the dreamDEX mainnet leaderboard for the competition wallet.

The leaderboard is mainnet-only — even when the bot is running on testnet,
we look up the MAINNET competition address so the watch can show our
ranking the moment Vercel deploys the leaderboard."""
import time
import threading
import requests
from config import LEADERBOARD_URL, LEADERBOARD_ADDRESS, LEADERBOARD_POLL

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
