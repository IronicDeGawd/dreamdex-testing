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

import hmac
import time as _time

from fastapi import FastAPI, Header, HTTPException, Depends, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from control.engine_manager import EngineManager, EngineError, MOCK

# R4 rule 5: eligible pairs ONLY. USDC.e:USDso is a STABLECOIN pair and must never
# be traded (removal risk). Enforced at /launch and /trade so it can't be entered.
ELIGIBLE_PAIRS = {"WBTC:USDso", "WETH:USDso", "SOMI:USDso"}

# Simple per-IP brute-force limiter for /login (no external dep).
_login_fails: dict = {}
_LOGIN_MAX_FAILS = 8
_LOGIN_WINDOW_S = 300
def _login_rate_ok(ip: str) -> bool:
    now = _time.time()
    fails = [t for t in _login_fails.get(ip, []) if now - t < _LOGIN_WINDOW_S]
    _login_fails[ip] = fails
    return len(fails) < _LOGIN_MAX_FAILS
def _login_record_fail(ip: str) -> None:
    _login_fails.setdefault(ip, []).append(_time.time())

API_KEY     = os.environ.get("CONTROL_API_KEY", "")
# Login gate in front of the panel (it's reachable over the public tunnel). The
# username is not a secret; the password lives in .env (gitignored). On a correct
# login the browser is handed the real API key, which still gates every endpoint.
LOGIN_USER  = os.environ.get("CONTROL_USERNAME", "admin-aditya")
LOGIN_PASS  = os.environ.get("CONTROL_PASSWORD", "")
BACKEND_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR  = BACKEND_DIR / "static"
INDEX_HTML  = STATIC_DIR / "index.html"
R1_HTML     = STATIC_DIR / "r1.html"   # the original R1 dashboard, served for design reference

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
        return {"usdso": 200.0, "somi": 30.0, "usdso_vault": 0.0, "total_usdso": 200.0,
                "bags": {"WBTC:USDso": 0.0, "WETH:USDso": 0.0},
                "wbtc": 0.0, "weth": 0.0}

    def free_usdso(self):
        return 200.0

    def leaderboard(self):
        return {"my_rank": 3, "total": 9, "my_volume": 91843.9, "my_pnl": -13.83,
                "my_tx": 1176, "gap": 51823.64, "gap_to": "#2",
                "signal": "ACCELERATE", "live": True}

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
        # Read the wallet FRESH — the engine rotates pairs, so a bag can sit on any
        # of them and a 60s-cached snapshot would hide it (WBTC was invisible before).
        s = self._summary()
        vault = sum(s.get("usdso_vaults", {}).values())
        try:
            usdso = self.dex.wallet.erc20_balance(self.cfg.USDSO_ADDRESS, 18)
            somi = self.dex.wallet.native_balance()
        except Exception:
            usdso = s.get("usdso_wallet", 0.0); somi = s.get("native_balance", 0.0)
        bags = {}
        for pair, m in self.cfg.MARKETS.items():
            if pair not in ELIGIBLE_PAIRS or m.get("native") or int(str(m["base"]), 16) == 0:
                continue
            try:
                bags[pair] = round(self.dex.wallet.erc20_balance(m["base"], int(m["baseDecimals"])), 8)
            except Exception:
                bags[pair] = 0.0
        return {"usdso": round(usdso, 4), "somi": round(somi, 4),
                "usdso_vault": round(vault, 4), "total_usdso": round(usdso + vault, 4),
                "bags": bags,
                "wbtc": bags.get("WBTC:USDso", 0.0), "weth": bags.get("WETH:USDso", 0.0)}

    def free_usdso(self):
        # FRESH on-chain read — the leg guard must not use the 60s-cached poll
        # (which can snapshot a mid-round-trip dip and wrongly reject a valid leg).
        try:
            return self.dex.wallet.erc20_balance(self.cfg.USDSO_ADDRESS, 18)
        except Exception:
            return self._summary().get("usdso_wallet", 0.0)

    def leaderboard(self):
        st = self.lb.get_my_stats()
        return {"my_rank": st.get("my_rank"), "total": st.get("total"),
                "my_volume": st.get("my_volume", 0.0), "my_pnl": st.get("my_pnl", 0.0),
                "my_tx": st.get("my_tx", 0),
                "gap": st.get("gap"), "gap_to": st.get("gap_to"),
                "signal": st.get("signal"), "live": st.get("live")}

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
        # Sell any base bag on EVERY ERC20 pair, then RE-CHECK the balance and
        # RETRY with widening slip. Reports "flat" only if every pair is confirmed
        # cleared on-chain; "bag" with residuals otherwise. Qty is FLOORED to lot
        # so we never request more base than held (which would revert).
        import math, time as _t
        residual = {}
        for pair, m in self.cfg.MARKETS.items():
            if m.get("native") or int(str(m["base"]), 16) == 0:
                continue
            dec = int(m["baseDecimals"])
            lot = float(m.get("lotSize", 0.0001)); minq = float(m.get("minQuantity", lot))
            tick = float(m.get("tickSize", 0.01))
            b = self.dex.wallet.erc20_balance(m["base"], dec)
            if b < minq:
                continue
            for att in range(6):
                ob = self.dex.get_orderbook(pair); bid = ob.get("bid")
                if not bid:
                    _t.sleep(2); continue
                qty = round(math.floor(b / lot) * lot, 10)
                px = round(math.floor(bid * (1 - 0.004 * (att + 1)) / tick) * tick, 10)
                try:
                    self.dex.place_order(pair, "sell", qty, order_type="ioc",
                                         limit_price=px, funding="wallet")
                except Exception:
                    pass
                _t.sleep(2)
                b = self.dex.wallet.erc20_balance(m["base"], dec)
                if b < minq:
                    break
            if b >= minq:
                residual[pair] = round(b, 8)
        return {"status": "flat" if not residual else "bag", "residual": residual}

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
    pair: str | None = None         # e.g. WBTC:USDso (default WETH:USDso)
    slip: float | None = None
    bleed_cap: float | None = None  # steady only
    cost_ceil: float | None = None  # steady only
    spread_gate: float | None = None  # steady + fast


class LoginBody(BaseModel):
    username: str
    password: str


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


@app.get("/r1")
def r1_reference():
    """The original R1 dashboard, served as a static design reference. Its data
    calls target R1 endpoints this API doesn't expose, so panels render empty —
    it's here to compare layout, not to drive anything."""
    if R1_HTML.is_file():
        return FileResponse(str(R1_HTML))
    raise HTTPException(status_code=404, detail="r1.html not found")


@app.post("/login")
def login(body: LoginBody, request: Request):
    """Validate username/password, hand back the API key on success. Constant-time
    compares, per-IP brute-force limiter. Fails closed with no password configured
    (except mock mode, where a keyless dev login is allowed)."""
    ip = request.client.host if request.client else "unknown"
    if not _login_rate_ok(ip):
        raise HTTPException(status_code=429, detail="too many attempts — wait a few minutes")
    ok_user = hmac.compare_digest(body.username, LOGIN_USER)
    ok_pass = bool(LOGIN_PASS) and hmac.compare_digest(body.password, LOGIN_PASS)
    if MOCK and not LOGIN_PASS:
        ok_user = hmac.compare_digest(body.username, LOGIN_USER)
        ok_pass = True  # keyless local dev
    if not (ok_user and ok_pass):
        _login_record_fail(ip)
        raise HTTPException(status_code=401, detail="invalid username or password")
    return {"api_key": API_KEY}


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
    for k in ("pair", "slip", "bleed_cap", "cost_ceil", "spread_gate"):
        v = getattr(body, k)
        if v is not None:
            params[k] = v

    # Rule 5: only eligible (non-stablecoin) pairs. Reject anything else — this is
    # the wall that keeps USDC.e:USDso out of a run.
    if body.pair:
        bad = [p.strip() for p in body.pair.split(",") if p.strip() not in ELIGIBLE_PAIRS]
        if bad:
            raise HTTPException(status_code=400,
                                detail=f"ineligible pair(s) {bad} — allowed: {sorted(ELIGIBLE_PAIRS)}")

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


@app.post("/autorestart")
def autorestart(_=Depends(require_key)):
    """Watchdog hook (cron, every 15 min). Relaunches the last run ONLY when it died
    unexpectedly — a crash, host/RPC kill, or the container vanishing. It refuses to
    restart after a deliberate /stop, or after the engine stopped itself for a real
    reason (target reached, bleed/gas cap, trade-failure breaker, startup abort)."""
    if engine.is_running():
        return {"action": "none", "reason": "engine already running"}
    st = engine._read_state()
    if not st:
        return {"action": "none", "reason": "no run on record"}
    if not st.get("autorestart"):
        return {"action": "none", "reason": "deliberately stopped — will not restart"}
    # Startup grace: never resurrect a run that only just launched (the container
    # can read as not-yet-running while it boots).
    if _time.time() - (st.get("started_at") or 0) < 180:
        return {"action": "none", "reason": "run started <3m ago — still booting"}
    clean = engine.clean_stop_reason()
    if clean:
        return {"action": "none", "reason": f"engine self-stopped ({clean}) — will not restart"}

    mode = st.get("mode"); params = dict(st.get("params") or {})
    if not mode or "target" not in params:
        return {"action": "none", "reason": "no saved params"}
    # Capital may have shrunk since the original launch — clamp the leg so the
    # relaunch can't trip the pre-revert guard.
    try:
        free = backend.free_usdso()
        if free and params.get("leg", 0) > 0.8 * free:
            params["leg"] = round(0.8 * free, 2)
    except Exception:
        pass
    try:
        engine.launch(mode, params)
    except EngineError as e:
        return {"action": "failed", "error": str(e)}
    engine.audit("autorestart", {"mode": mode, "params": params, "after": st.get("end_reason")})
    return {"action": "relaunched", "mode": mode, "params": params}


@app.post("/gas/topup")
def gas_topup(body: GasBody, _=Depends(require_key)):
    if engine.is_running():
        raise HTTPException(status_code=409,
                            detail="engine running — stop it first (nonce safety)")
    engine.audit("gas_topup", {"somi_usdso": body.somi_usdso})
    try:
        return backend.gas_topup(body.somi_usdso)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)[:200])


@app.post("/flatten")
def flatten(_=Depends(require_key)):
    if engine.is_running():
        raise HTTPException(status_code=409,
                            detail="engine running — stop it first (nonce safety)")
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
    if body.pair not in ELIGIBLE_PAIRS:
        raise HTTPException(status_code=400,
                            detail=f"ineligible pair {body.pair} — allowed: {sorted(ELIGIBLE_PAIRS)}")
    engine.audit("trade", body.model_dump())
    try:
        return backend.trade(body.pair, body.side, body.amount_usdso, body.skip_sim)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)[:200])
