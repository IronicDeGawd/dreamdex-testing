"""Round 3 profit-maker entrypoint.

Wires the pieces together and supervises them:
  - one PairMaker thread per eligible pair (the deterministic hot loop)
  - a strategist thread (Gemini 2.5 Pro, periodic) that updates per-pair params
  - a gas thread that refuels SOMI from working capital when it runs low
  - a liveness guard so we never go >24h without a trade (auto-DQ rule)

Run:  python -m agent_v3.runner
Env:  DREAMDEX_ENV=mainnet  DRY_RUN=1 (quote/log only, no orders)
"""
import os
import signal
import threading
import time

import config
from agent_v3 import context_store as ctx
from agent_v3.gas import GasManager
from agent_v3.inventory import Inventory
from agent_v3.market_data import MarketData
from agent_v3.maker import PairMaker
from agent_v3.strategist import Strategist, default_decision

DRY_RUN = os.environ.get("DRY_RUN", "0") == "1"


class Runner:
    def __init__(self):
        from trading.dreamdex import DreamDEX
        from monitor import db
        db.init()
        ctx.init()

        self.dex = DreamDEX()
        self.md = MarketData(self.dex)
        self.inv = Inventory()
        self.gas = GasManager(self.dex)
        self.strategist = Strategist()

        self.stop = threading.Event()
        self._lock = threading.Lock()
        self.decision = default_decision()

        # cached working-capital reader (avoid hammering RPC from every leg)
        self._cap_val = 0.0
        self._cap_ts = 0.0

    # ── shared state accessors ───────────────────────────────────────────
    def params_for(self, pair: str) -> dict:
        with self._lock:
            d = self.decision
            active = pair in d.get("active_pairs", [])
            p = dict(d.get("per_pair", {}).get(pair, {}))
        if not active:
            p["pause"] = True
        return p

    def total_usdso(self) -> float:
        now = time.time()
        if now - self._cap_ts < 30:
            return self._cap_val
        try:
            self._cap_val = self.dex.wallet.erc20_balance(config.USDSO_ADDRESS, 18)
            self._cap_ts = now
        except Exception:
            pass
        return self._cap_val

    # ── background threads ───────────────────────────────────────────────
    def _strategist_loop(self):
        while not self.stop.is_set():
            try:
                state = {
                    "pnl_pct": round(self.inv.pnl_pct(self.total_usdso() +
                                     self.inv.inventory_value_usdso(self._mids())), 4),
                    "realized_pnl": round(self.inv.realized_pnl, 4),
                    "working_capital": round(self.inv.working_capital(self.total_usdso()), 2),
                    "gas_somi": round(self.gas.somi_balance(), 3),
                    "per_pair_stats": ctx.summary(since_s=3600),
                    "live": {p: self.md.snapshot(p) for p in config.ELIGIBLE_PAIRS},
                }
                decision = self.strategist.decide(state)
                with self._lock:
                    self.decision = decision
                print(f"[strategist] {decision.get('rationale','')} | active={decision.get('active_pairs')}", flush=True)
            except Exception as e:
                print(f"[strategist] loop error: {e}", flush=True)
            self.stop.wait(config.STRATEGIST_INTERVAL_S)

    def _gas_loop(self):
        while not self.stop.is_set():
            try:
                if self.gas.needs_refuel() and not DRY_RUN:
                    snap = self.md.snapshot("SOMI:USDso")
                    mid = snap["mid"] if snap else 0.0
                    res = self.gas.refuel(self.total_usdso(), mid)
                    ctx.log_event({"event": "refuel", "pair": "SOMI:USDso", "gas_somi": self.gas.somi_balance(),
                                   "status": res.get("status"), "note": str(res)[:120]})
            except Exception as e:
                print(f"[gas] loop error: {e}", flush=True)
            self.stop.wait(300)

    def _liveness_loop(self):
        while not self.stop.is_set():
            try:
                last = ctx.last_trade_ts()
                idle = time.time() - last if last else config.LIVENESS_MAX_IDLE_S + 1
                if idle > config.LIVENESS_MAX_IDLE_S:
                    # Approaching the 24h DQ — force the most liquid pair active + tight.
                    with self._lock:
                        if config.ELIGIBLE_PAIRS:
                            forced = config.ELIGIBLE_PAIRS[0]
                            self.decision.setdefault("active_pairs", [])
                            if forced not in self.decision["active_pairs"]:
                                self.decision["active_pairs"].append(forced)
                            self.decision["per_pair"].setdefault(forced, {})["pause"] = False
                    print(f"[liveness] idle {idle/3600:.1f}h > limit — forcing activity", flush=True)
            except Exception as e:
                print(f"[liveness] loop error: {e}", flush=True)
            self.stop.wait(600)

    def _mids(self) -> dict:
        out = {}
        for p in config.ELIGIBLE_PAIRS:
            s = self.md.snapshot(p)
            if s:
                out[p] = s["mid"]
        return out

    # ── run ──────────────────────────────────────────────────────────────
    def run(self):
        signal.signal(signal.SIGINT, lambda *_: self.stop.set())
        signal.signal(signal.SIGTERM, lambda *_: self.stop.set())

        threads = []
        for pair in config.ELIGIBLE_PAIRS:
            m = PairMaker(self.dex, self.md, self.inv, pair,
                          params_fn=lambda p=pair: self.params_for(p),
                          capital_fn=self.total_usdso, stop_event=self.stop, dry_run=DRY_RUN)
            t = threading.Thread(target=m.run, name=f"maker-{pair}", daemon=True)
            threads.append(t)
        threads.append(threading.Thread(target=self._strategist_loop, name="strategist", daemon=True))
        threads.append(threading.Thread(target=self._gas_loop, name="gas", daemon=True))
        threads.append(threading.Thread(target=self._liveness_loop, name="liveness", daemon=True))

        print(f"[runner] starting — env={config.ENV} dry_run={DRY_RUN} pairs={config.ELIGIBLE_PAIRS}", flush=True)
        for t in threads:
            t.start()
        try:
            while not self.stop.is_set():
                self.stop.wait(5)
        finally:
            self.stop.set()
            print("[runner] stopping — waiting for threads", flush=True)
            for t in threads:
                t.join(timeout=30)


def main():
    Runner().run()


if __name__ == "__main__":
    main()
