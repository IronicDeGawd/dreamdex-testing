# backend/control/app.py
"""
Control API — a small FastAPI service the dashboard talks to.

It wraps the two R3 engines (steady `volume_climb`, fast `direct_burst`) behind
launch/stop/status, exposes on-chain balances + leaderboard rank, and offers the
safety levers (gas top-up, flatten, one-off manual trade). One engine at a time
(nonce safety) and a leg-vs-free-USDso guard are enforced here so the UI can't
start a run that would instantly pre-revert.

Auth: every API call needs the `X-API-Key` header (value = CONTROL_API_KEY).
On mainnet the service refuses to start without a key (fail closed). In mock mode
(CONTROL_MOCK=1) a missing key disables auth so you can poke it locally.

Two backends:
  • LiveBackend  — real Portfolio/Leaderboard/DreamDEX (server, keys present).
  • MockBackend  — stub numbers, no network/keys (CONTROL_MOCK=1, local dev).
"""
import os
from pathlib import Path

# Load backend/.env FIRST so CONTROL_* (and wallet keys) reach os.environ before
# engine_manager reads CONTROL_MOCK and we read CONTROL_API_KEY at import time.
# Explicit shell env vars still win (load_dotenv does not override).
from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, Header, HTTPException, Depends
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from control.engine_manager import EngineManager, EngineError, MOCK

API_KEY     = os.environ.get("CONTROL_API_KEY", "")
BACKEND_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR  = BACKEND_DIR / "static"
INDEX_HTML  = STATIC_DIR / "index.html"

# Fail closed on a real deployment with no key set.
if not MOCK and not API_KEY:
    raise RuntimeError(
        "CONTROL_API_KEY is required (set a long random string in .env). "
        "Run with CONTROL_MOCK=1 for keyless local testing."
    )


# ── Backends ──────────────────────────────────────────────────────────────
class MockBackend:
    """Deterministic stub data for local UI dev — no chain, no keys."""
    def balances(self):
        return {"usdso": 200.0, "somi": 30.0, "weth": 0.0,
                "usdso_vault": 0.0, "total_usdso": 200.0}

    def free_usdso(self):
        return 200.0

    def leaderboard(self):
        return {"my_rank": 2, "total": 6, "my_volume": 1086202.0,
                "my_pnl": 0.0, "gap": 7200, "signal": "MAINTAIN", "live": True}

    def gas_topup(self, somi_usdso):
        return {"status": "success", "mock": True, "spent_usdso": somi_usdso}

    def flatten(self):
        return {"status": "flat", "mock": True, "weth": 0.0}

    def trade(self, pair, side, amount_usdso, skip_sim):
        return {"status": "success", "mock": True,
                "pair": pair, "side": side, "amount_usdso": amount_usdso}


class LiveBackend:
    """Real readers + trader. Constructed only when NOT in mock mode."""
    def __init__(self):
        import config
        from monitor.portfolio import Portfolio
        from monitor.leaderboard import LeaderboardMonitor
        from trading.manual import ManualTrader
        self.cfg = config
        self.portfolio = Portfolio(); self.portfolio.start()
        self.lb = LeaderboardMonitor(); self.lb.start()
        self.manual = ManualTrader()
        self.dex = self.manual.dex   # reuse one DreamDEX/wallet (shared nonce)

    def _summary(self):
        return self.portfolio.summary()

    def balances(self):
        s = self._summary()
        weth = 0.0
        wb = s.get("wallet_base", {}).get("WETH:USDso")
        if wb:
            weth = wb.get("qty", 0.0)
        vault = sum(s.get("usdso_vaults", {}).values())
        usdso = s.get("usdso_wallet", 0.0)
        return {"usdso": round(usdso, 4), "somi": round(s.get("native_balance", 0.0), 4),
                "weth": round(weth, 6), "usdso_vault": round(vault, 4),
                "total_usdso": round(usdso + vault, 4)}

    def free_usdso(self):
        return self._summary().get("usdso_wallet", 0.0)

    def leaderboard(self):
        st = self.lb.get_my_stats()
        return {"my_rank": st.get("my_rank"), "total": st.get("total"),
                "my_volume": st.get("my_volume", 0.0), "my_pnl": st.get("my_pnl", 0.0),
                "gap": st.get("gap"), "signal": st.get("signal"), "live": st.get("live")}

    def gas_topup(self, somi_usdso):
        ob = self.dex.get_orderbook("SOMI:USDso")
        bid, ask = ob.get("bid"), ob.get("ask")
        if not bid or not ask:
            return {"status": "error", "error": "no SOMI:USDso book"}
        mid = (bid + ask) / 2
        qty = somi_usdso / mid
        res = self.dex.place_order("SOMI:USDso", "buy", qty, order_type="ioc",
                                   funding="wallet", gas_min=self.cfg.SOMI_BUY_GAS_LIMIT)
        return {"status": res.get("status"), "somi_qty": round(qty, 4),
                "spent_usdso": somi_usdso, "result": res}

    def flatten(self):
        mkt = self.cfg.MARKETS["WETH:USDso"]
        dec = int(mkt["baseDecimals"])
        weth = self.dex.wallet.erc20_balance(mkt["base"], dec)
        if weth <= 0:
            return {"status": "flat", "weth": 0.0}
        ob = self.dex.get_orderbook("WETH:USDso")
        bid = ob.get("bid")
        if not bid:
            return {"status": "error", "error": "no WETH bid to sell into", "weth": weth}
        px = round(bid * (1 - 0.004), 2)
        res = self.dex.place_order("WETH:USDso", "sell", round(weth, 8),
                                   order_type="ioc", limit_price=px, funding="wallet")
        return {"status": res.get("status"), "sold_weth": round(weth, 6), "result": res}

    def trade(self, pair, side, amount_usdso, skip_sim):
        # ManualTrader needs a prices dict; pull a fresh book mid for the pair.
        ob = self.dex.get_orderbook(pair)
        bid, ask = ob.get("bid") or 0, ob.get("ask") or 0
        mid = (bid + ask) / 2 if (bid and ask) else (bid or ask)
        prices = {pair: {"mid": mid, "bid": bid, "ask": ask}}
        return self.manual.execute(pair=pair, side=side, amount_usdso=amount_usdso,
                                   prices=prices, skip_sim=skip_sim)


# ── App wiring ────────────────────────────────────────────────────────────
app = FastAPI(title="DreamDEX Engine Control", docs_url=None, redoc_url=None)
engine = EngineManager()
backend = MockBackend() if MOCK else LiveBackend()

if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def require_key(x_api_key: str = Header(default="")):
    if MOCK and not API_KEY:
        return
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="missing or invalid X-API-Key")


class LaunchBody(BaseModel):
    mode: str                       # "steady" | "fast"
    target: float
    leg: float
    slip: float | None = None
    bleed_cap: float | None = None  # steady only
    cost_ceil: float | None = None  # steady only
    spread_gate: float | None = None  # fast only


class GasBody(BaseModel):
    somi_usdso: float               # USDso to spend on SOMI gas


class TradeBody(BaseModel):
    pair: str
    side: str                       # "buy" | "sell"
    amount_usdso: float
    skip_sim: bool = False


# ── Static shell (no auth — carries no data) ──────────────────────────────
@app.get("/")
def index():
    if INDEX_HTML.is_file():
        return FileResponse(str(INDEX_HTML))
    raise HTTPException(status_code=404, detail="index.html not found")


# ── Read endpoints ────────────────────────────────────────────────────────
@app.get("/status")
def status(_=Depends(require_key)):
    return engine.status()


@app.get("/balances")
def balances(_=Depends(require_key)):
    return backend.balances()


@app.get("/leaderboard")
def leaderboard(_=Depends(require_key)):
    return backend.leaderboard()


@app.get("/logs")
def logs(n: int = 80, _=Depends(require_key)):
    return engine.logs(n)


@app.get("/audit")
def audit(n: int = 50, _=Depends(require_key)):
    return {"entries": engine.read_audit(n)}


# ── Control endpoints ─────────────────────────────────────────────────────
@app.post("/launch")
def launch(body: LaunchBody, _=Depends(require_key)):
    params = {"target": body.target, "leg": body.leg}
    for k in ("slip", "bleed_cap", "cost_ceil", "spread_gate"):
        v = getattr(body, k)
        if v is not None:
            params[k] = v

    # Leg-vs-balance guard: a leg bigger than ~0.8× free USDso pre-reverts on
    # the very first buy. Reject with a clear message instead of burning gas.
    try:
        free = backend.free_usdso()
    except Exception:
        free = None
    if free is not None and body.leg > 0.8 * free:
        raise HTTPException(
            status_code=400,
            detail=f"leg ${body.leg:.2f} exceeds 0.8× free USDso (${free:.2f}) — "
                   f"buys would pre-revert; use leg ≤ ${0.8 * free:.2f}",
        )

    try:
        state = engine.launch(body.mode, params)
    except EngineError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"ok": True, "state": state}


@app.post("/stop")
def stop(_=Depends(require_key)):
    try:
        st = engine.stop(reason="dashboard")
    except EngineError as e:
        raise HTTPException(status_code=409, detail=str(e))
    # Best-effort flatten after stop (bag-proof).
    flat = None
    try:
        flat = backend.flatten()
    except Exception as e:
        flat = {"status": "error", "error": str(e)[:160]}
    return {"ok": True, "state": st, "flatten": flat}


@app.post("/gas/topup")
def gas_topup(body: GasBody, _=Depends(require_key)):
    engine.audit("gas_topup", {"somi_usdso": body.somi_usdso})
    try:
        return backend.gas_topup(body.somi_usdso)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)[:200])


@app.post("/flatten")
def flatten(_=Depends(require_key)):
    engine.audit("flatten", {})
    try:
        return backend.flatten()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)[:200])


@app.post("/trade")
def trade(body: TradeBody, _=Depends(require_key)):
    if engine.is_running():
        raise HTTPException(status_code=409,
                            detail="an engine is running — stop it before manual trades (nonce safety)")
    engine.audit("trade", body.model_dump())
    try:
        return backend.trade(body.pair, body.side, body.amount_usdso, body.skip_sim)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)[:200])
