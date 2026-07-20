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
import json
import time as _time

from fastapi import FastAPI, Header, HTTPException, Depends, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from control.engine_manager import EngineManager, EngineError, MOCK, STATE_DIR

# R4 rule 5: eligible pairs ONLY. USDC.e:USDso is a STABLECOIN pair and must never
# be traded (removal risk). Enforced at /launch and /trade so it can't be entered.
ELIGIBLE_PAIRS = {"WBTC:USDso", "WETH:USDso", "SOMI:USDso"}

# Idle-DQ keepalive (contest rule: >24h without a trade = DQ). A cron hits
# POST /keepalive hourly; we act only when lifetime volume hasn't moved for
# KEEPALIVE_AGE_S. 20h + hourly cron ⇒ worst-case trade at ~21h idle, inside 24h.
KEEPALIVE_AGE_S = float(os.environ.get("CONTROL_KEEPALIVE_AGE_S", 20 * 3600))
KEEPALIVE_USDSO = float(os.environ.get("CONTROL_KEEPALIVE_USDSO", 1.0))
KEEPALIVE_FILE = STATE_DIR / "keepalive.json"

def _keepalive_state() -> dict:
    try:
        return json.loads(KEEPALIVE_FILE.read_text())
    except (FileNotFoundError, ValueError):
        return {}

def _save_keepalive_state(st: dict) -> None:
    tmp = KEEPALIVE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(st))
    tmp.replace(KEEPALIVE_FILE)

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
# Arena pair boosts (weekly 1.2–1.5× score multipliers, announced manually each
# Monday). POST /boosts writes data/boosts.json; ./data is volume-mounted into
# the engine container, and volume_climb re-reads it every ~60s — so a Monday
# update reaches a RUNNING engine without a restart.
BOOSTS_FILE = BACKEND_DIR / "data" / "boosts.json"

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

    def run_snapshot(self):
        return {"ts": _time.time(), "source": "mock", "usdso": 200.0, "vault": 0.0,
                "somi": 30.0, "somi_px": 0.10, "bags": {}, "bags_usd": 0.0,
                "networth": 203.0}

    def leaderboard(self):
        return {"my_rank": 3, "total": 9, "my_volume": 91843.9, "my_pnl": -13.83,
                "my_tx": 1176, "gap": 51823.64, "gap_to": "#2",
                "signal": "ACCELERATE", "live": True}

    def cohort(self):
        return [
            {"rank":1,"handle":"trader-6","address":"0x63A9","tx":4527,"volume":224800.27,
             "volume24h":224800.27,"pnl":-32.84,"balance":117.16,"cost_per_1k":0.146,
             "runway_volume":802466,"avg_tx":49.66,"is_me":False},
            {"rank":2,"handle":"trader-4","address":"0x99e9","tx":1911,"volume":143667.54,
             "volume24h":143667.54,"pnl":-118.23,"balance":31.77,"cost_per_1k":0.823,
             "runway_volume":38602,"avg_tx":75.18,"is_me":False},
            {"rank":3,"handle":"trader-1","address":"0x703e","tx":1176,"volume":91843.9,
             "volume24h":91843.9,"pnl":-13.83,"balance":136.17,"cost_per_1k":0.151,
             "runway_volume":901788,"avg_tx":78.10,"is_me":True},
        ]

    def gas_topup(self, somi_usdso):
        return {"status": "success", "mock": True, "spent_usdso": somi_usdso}

    def convert_stable(self, amount, direction):
        return {"status": "success", "mock": True,
                "direction": direction, "qty_usdce": amount}

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

    def run_snapshot(self):
        # Exact capital + gas at a run boundary. Only valid when the wallet is
        # flat (launch, post-flatten stop) — mid-run, funds locked in resting
        # orders are invisible to every balance read. networth mirrors the
        # maker's own definition (USDso + vault + bags@mid + SOMI@mid) so the
        # two figures are directly comparable.
        b = self.balances()
        somi_px = None
        try:
            ob = self.dex.get_orderbook("SOMI:USDso")
            if ob.get("bid") and ob.get("ask"):
                somi_px = (ob["bid"] + ob["ask"]) / 2
        except Exception:
            pass
        bags_usd = 0.0
        for pair, qty in (b.get("bags") or {}).items():
            if qty <= 1e-9:
                continue
            try:
                ob = self.dex.get_orderbook(pair)
                mid = ((ob.get("bid") or 0) + (ob.get("ask") or 0)) / 2
                bags_usd += qty * mid
            except Exception:
                pass
        networth = b["usdso"] + b["usdso_vault"] + bags_usd
        if somi_px:
            networth += b["somi"] * somi_px
        return {"ts": _time.time(), "source": "live", "usdso": b["usdso"],
                "vault": b["usdso_vault"], "somi": b["somi"],
                "somi_px": round(somi_px, 6) if somi_px else None,
                "bags": b.get("bags") or {}, "bags_usd": round(bags_usd, 4),
                "networth": round(networth, 4)}

    def leaderboard(self):
        st = self.lb.get_my_stats()
        return {"my_rank": st.get("my_rank"), "total": st.get("total"),
                "my_volume": st.get("my_volume", 0.0), "my_pnl": st.get("my_pnl", 0.0),
                "my_tx": st.get("my_tx", 0),
                "gap": st.get("gap"), "gap_to": st.get("gap_to"),
                "signal": st.get("signal"), "live": st.get("live")}

    def cohort(self):
        rows = self.lb.get_cohort()
        # For OUR row we don't need the flat-balance heuristic — read the chain.
        # True capital = free USDso + inventory valued at mid, so a mid-round-trip
        # snapshot can't fake a loss. Rivals still use the rolling-max estimate.
        try:
            from monitor.leaderboard import RUNWAY_RESERVE
            usdso = self.dex.wallet.erc20_balance(self.cfg.USDSO_ADDRESS, 18)
            inv = 0.0
            for pair, m in self.cfg.MARKETS.items():
                if pair not in ELIGIBLE_PAIRS:
                    continue
                native = m.get("native") or int(str(m["base"]), 16) == 0
                b = 0.0 if native else self.dex.wallet.erc20_balance(m["base"], int(m["baseDecimals"]))
                # Funds locked in OUR resting orders are invisible to balanceOf
                # (5th instance of the bug class) — while the maker quotes, a
                # resting $40 order read as burned capital and inflated $/1k.
                locked_base = 0.0
                try:
                    for o in (self.dex.get_open_orders(pair) or []):
                        rem = float(o.get("remaining") or 0)
                        px = float(o.get("price") or 0)
                        if rem <= 0 or px <= 0:
                            continue
                        if o.get("side") == "buy":
                            usdso += rem * px
                        else:
                            locked_base += rem
                except Exception:
                    pass
                if b + locked_base <= 0:
                    continue
                ob = self.dex.get_orderbook(pair)
                mid = ((ob.get("bid") or 0) + (ob.get("ask") or 0)) / 2
                inv += (b + locked_base) * mid
            cap = usdso + inv
            for r in rows:
                if not r.get("is_me"):
                    continue
                v = r["volume"]
                burned = max(150.0 - cap, 0.0)
                cpk = round(burned / v * 1000, 3) if v >= 100 else None
                spendable = max(cap - RUNWAY_RESERVE, 0.0)
                r.update({
                    "balance": round(cap, 2),
                    "pnl": round(cap - 150.0, 2),
                    "mid_trade": inv > 1.0,
                    "cost_per_1k": cpk,
                    "runway_volume": round(spendable / cpk * 1000) if (cpk and cpk > 0) else None,
                    "onchain": True,
                })
        except Exception:
            pass   # fall back to the board-derived row
        return rows

    def gas_topup(self, somi_usdso):
        import math
        ob = self.dex.get_orderbook("SOMI:USDso")
        bid, ask = ob.get("bid"), ob.get("ask")
        if not bid or not ask:
            return {"status": "error", "error": "no SOMI:USDso book"}
        mid = (bid + ask) / 2
        # The SOMI order quantity must be a whole multiple of the pool's lot size,
        # or the order is rejected (invalid_amount). Snap DOWN to the lot.
        lot = float(self.cfg.MARKETS.get("SOMI:USDso", {}).get("lotSize", 0.01)) or 0.01
        qty = round(math.floor((somi_usdso / mid) / lot) * lot, 10)
        if qty <= 0:
            return {"status": "error", "error": f"amount too small for lot {lot}"}
        res = self.dex.place_order("SOMI:USDso", "buy", qty, order_type="ioc",
                                   funding="wallet", gas_min=self.cfg.SOMI_BUY_GAS_LIMIT)
        return {"status": res.get("status"), "somi_qty": qty,
                "spent_usdso": somi_usdso, "result": res}

    def convert_stable(self, amount, direction):
        # USDC.e <-> USDso on the USDC.e:USDso book. IOC crossing the touch, so it
        # fills at ~1:1 (spread is a fraction of a cent). amount <= 0 converts the
        # whole free balance. Qty is snapped DOWN to the lot or the pool rejects it.
        import math
        PAIR = "USDC.e:USDso"
        m = self.cfg.MARKETS.get(PAIR)
        if not m:
            return {"status": "error", "error": f"no {PAIR} market"}
        ob = self.dex.get_orderbook(PAIR)
        bid, ask = ob.get("bid"), ob.get("ask")
        if not bid or not ask:
            return {"status": "error", "error": f"no {PAIR} book"}
        lot = float(m.get("lotSize", 0.01)) or 0.01
        minq = float(m.get("minQuantity", lot) or lot)
        tick = float(m.get("tickSize", 0.0001)) or 0.0001
        bdec, qdec = int(m["baseDecimals"]), int(m["quoteDecimals"])
        w = self.dex.wallet

        if direction == "usdce_to_usdso":
            held = w.erc20_balance(m["base"], bdec)
            want = held if amount <= 0 else min(amount, held)
            qty = round(math.floor(want / lot) * lot, 10)
            if qty < minq:
                return {"status": "error",
                        "error": f"{qty} USDC.e below min order {minq} (held {held})"}
            px = round(math.floor(bid * 0.999 / tick) * tick, 10)
            res = self.dex.place_order(PAIR, "sell", qty, order_type="ioc",
                                       limit_price=px, funding="wallet")
        elif direction == "usdso_to_usdce":
            free = w.erc20_balance(self.cfg.USDSO_ADDRESS, qdec)
            spend = free if amount <= 0 else min(amount, free)
            px = round(math.ceil(ask * 1.001 / tick) * tick, 10)
            qty = round(math.floor((spend / px) / lot) * lot, 10)
            if qty < minq:
                return {"status": "error",
                        "error": f"${spend} buys {qty} USDC.e, below min order {minq}"}
            res = self.dex.place_order(PAIR, "buy", qty, order_type="ioc",
                                       limit_price=px, funding="wallet")
        else:
            return {"status": "error", "error": f"unknown direction {direction}"}
        return {"status": res.get("status"), "direction": direction,
                "qty_usdce": qty, "limit": px, "result": res}

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
    mode: str                       # "steady" | "fast" | "maker" | "atomic"
    target: float
    leg: float
    pair: str | None = None         # e.g. WBTC:USDso (default WETH:USDso)
    slip: float | None = None
    bleed_cap: float | None = None  # steady + maker
    cost_ceil: float | None = None  # steady only
    spread_gate: float | None = None  # steady + fast + atomic
    weekly_target: float | None = None  # steady only — Arena weekly volume cap
    cap: float | None = None        # maker only — per-pair inventory cap ($)
    inv_floor: float | None = None  # maker only — standing-inventory fraction of cap
    toll_cap: float | None = None   # atomic only — max net quote lost per $1k
    tx_mode: str | None = None      # atomic only — "type2" (default) | "type4"
    delegate: str | None = None     # atomic only — override RoundTrip7702 address
    somi_floor: float | None = None  # atomic — stop below this SOMI (default 3)
    leg_min: float | None = None     # steady + atomic — dynamic leg lower bound ($)
    leg_max: float | None = None     # steady + atomic — dynamic leg upper bound ($)
    touch_frac: float | None = None  # steady + atomic — fraction of touch depth (default 0.8)


class BoostsBody(BaseModel):
    boosts: dict[str, float]        # pair -> weekly score multiplier


class LoginBody(BaseModel):
    username: str
    password: str


class GasBody(BaseModel):
    somi_usdso: float               # USDso to spend on SOMI gas


class ConvertBody(BaseModel):
    amount: float = 0.0             # <= 0 converts the whole free balance
    direction: str = "usdce_to_usdso"   # or "usdso_to_usdce"


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
    out = engine.status()
    # An engine that stops itself (target hit / bleed cap / breaker) never goes
    # through /stop — record its final verdict here, once. The 30s grace keeps
    # a dashboard poll from racing an explicit /stop's stop→flatten→finalize
    # (engines flatten themselves on self-stop, so the read is accurate by then).
    if (not out.get("running") and out.get("started_at") and not out.get("final")
            and out.get("ended_at") and _time.time() - out["ended_at"] > 30):
        try:
            _finalize_run()
            out = engine.status()
        except Exception:
            pass
    return out


@app.get("/balances")
def balances(_=Depends(require_key)):
    return backend.balances()


@app.get("/leaderboard")
def leaderboard(_=Depends(require_key)):
    return backend.leaderboard()


@app.get("/cohort")
def cohort(_=Depends(require_key)):
    """Every trader in the cohort, volume-ranked, with efficiency ($ burned per 1k
    volume) and runway (volume their remaining balance still buys)."""
    return {"traders": backend.cohort()}


@app.get("/logs")
def logs(n: int = 80, _=Depends(require_key)):
    return engine.logs(n)


@app.get("/audit")
def audit(n: int = 50, _=Depends(require_key)):
    return {"entries": engine.read_audit(n)}


# ── Control endpoints ─────────────────────────────────────────────────────
@app.get("/boosts")
def get_boosts(_=Depends(require_key)):
    try:
        return json.loads(BOOSTS_FILE.read_text())
    except (FileNotFoundError, ValueError):
        return {"boosts": {}}


@app.post("/boosts")
def set_boosts(body: BoostsBody, _=Depends(require_key)):
    """Set the week's pair boosts (from the Monday announcement). Replaces the
    whole map — send every boosted pair each time; {} clears all boosts. The
    engine applies it within ~60s, no restart needed."""
    bad = {k: v for k, v in body.boosts.items() if not (0.5 <= v <= 5.0)}
    if bad:
        raise HTTPException(status_code=400,
                            detail=f"multiplier(s) outside the 0.5–5.0 sanity band: {bad}")
    BOOSTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = BOOSTS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps({"boosts": body.boosts, "updated_at": _time.time()}, indent=2))
    tmp.replace(BOOSTS_FILE)
    engine.audit("boosts", body.boosts)
    return {"ok": True, "boosts": body.boosts}


@app.post("/launch")
def launch(body: LaunchBody, _=Depends(require_key)):
    params = {"target": body.target, "leg": body.leg}
    for k in ("pair", "slip", "bleed_cap", "cost_ceil", "spread_gate", "weekly_target",
              "cap", "inv_floor", "toll_cap", "tx_mode", "delegate", "somi_floor",
              "leg_min", "leg_max", "touch_frac"):
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
    # With dynamic sizing the binding leg is leg_max, not the fixed fallback leg.
    guard_leg = body.leg
    guard_name = "leg"
    if body.leg_max and body.leg_min and body.leg_max >= body.leg_min > 0:
        guard_leg = body.leg_max
        guard_name = "leg_max"
    if free is not None and guard_leg > 0.8 * free:
        raise HTTPException(
            status_code=400,
            detail=f"{guard_name} ${guard_leg:.2f} exceeds 0.8× free USDso (${free:.2f}) — "
                   f"buys would pre-revert; use {guard_name} ≤ ${0.8 * free:.2f}",
        )

    try:
        state = engine.launch(body.mode, params, baseline=_run_baseline("launch"))
    except EngineError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"ok": True, "state": state}


def _run_baseline(source: str):
    """Capital+gas snapshot for a run about to start. A failed read must never
    block a launch — return None and let the START-log fallback cover display."""
    try:
        baseline = backend.run_snapshot()
        baseline["source"] = source
        return baseline
    except Exception:
        return None


def _finalize_run():
    """Record the run's final capital verdict (post-flatten, wallet flat again)
    and its runs.jsonl record. Idempotent via engine.finalize."""
    st = engine._read_state()
    if not st or st.get("final") or not st.get("started_at"):
        return
    final = backend.run_snapshot()
    final["source"] = "final"
    # Launch-time snapshot, or the START-log fallback engine.status() synthesizes.
    base = st.get("baseline") or engine.status().get("baseline") or {}
    pnl = round(final["networth"] - base["networth"], 4) if base.get("networth") is not None else None
    gas = round(base["somi"] - final["somi"], 4) if base.get("somi") is not None else None
    final["pnl"] = pnl
    final["gas_somi"] = gas
    final["gas_usd"] = round(gas * final["somi_px"], 4) if (gas is not None and final.get("somi_px")) else None
    engine.finalize(final)


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
    try:
        _finalize_run()
    except Exception:
        pass   # bookkeeping must never fail the stop
    return {"ok": True, "state": engine._read_state() or st, "flatten": flat}


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
        # Post-crash the wallet may still hold order-locked funds this read can't
        # see; the maker's own START log line (parsed as fallback) corrects networth.
        engine.launch(mode, params, baseline=_run_baseline("autorestart"))
    except EngineError as e:
        return {"action": "failed", "error": str(e)}
    engine.audit("autorestart", {"mode": mode, "params": params, "after": st.get("end_reason")})
    return {"action": "relaunched", "mode": mode, "params": params}


@app.post("/keepalive")
def keepalive(_=Depends(require_key)):
    """Idle-DQ guard (cron, hourly). While an engine runs it trades on its own;
    after a self-stop (target hit, breaker) nothing trades and the >24h-idle DQ
    clock starts. Detection: the leaderboard's lifetime volume only moves on a
    trade, so if it hasn't moved for KEEPALIVE_AGE_S we buy KEEPALIVE_USDSO of
    SOMI — one real IOC that resets the idle clock AND lands as gas we need
    anyway. When the board is unreachable we fall back to the same idle timer
    (a spare $1 trade is cheaper than a DQ)."""
    if engine.is_running():
        return {"action": "none", "reason": "engine running — it trades on its own"}
    now = _time.time()
    st = _keepalive_state()
    try:
        lb = backend.leaderboard()
        vol = lb.get("my_volume") if lb.get("live") else None
    except Exception:
        vol = None
    if not st:
        _save_keepalive_state({"volume": vol, "changed_at": now})
        return {"action": "none", "reason": "first check — baseline recorded", "volume": vol}
    if vol is not None and vol != st.get("volume"):
        _save_keepalive_state({"volume": vol, "changed_at": now})
        return {"action": "none", "reason": "volume moved — a trade happened", "volume": vol}
    idle_s = now - (st.get("changed_at") or now)
    if idle_s < KEEPALIVE_AGE_S:
        return {"action": "none",
                "reason": f"idle {idle_s / 3600:.1f}h < {KEEPALIVE_AGE_S / 3600:.0f}h threshold"}
    engine.audit("keepalive", {"idle_h": round(idle_s / 3600, 1), "usdso": KEEPALIVE_USDSO})
    try:
        res = backend.gas_topup(KEEPALIVE_USDSO)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)[:200])
    _save_keepalive_state({"volume": vol, "changed_at": now})
    return {"action": "traded", "idle_h": round(idle_s / 3600, 1), "result": res}


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


@app.post("/convert")
def convert_stable(body: ConvertBody, _=Depends(require_key)):
    if engine.is_running():
        raise HTTPException(status_code=409,
                            detail="engine running — stop it first (nonce safety)")
    if body.direction not in ("usdce_to_usdso", "usdso_to_usdce"):
        raise HTTPException(status_code=400,
                            detail="direction must be usdce_to_usdso or usdso_to_usdce")
    engine.audit("convert_stable", body.model_dump())
    try:
        return backend.convert_stable(body.amount, body.direction)
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
