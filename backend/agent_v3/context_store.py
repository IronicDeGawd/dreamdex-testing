"""Rich per-quote / per-fill context store — the dataset the LLM validates against.

Every quote, fill, cancel, refuel, and skip writes one row capturing the full
market context at that instant (spread, depth, volatility, our inventory, PnL,
gas). Lives in the same SQLite file as monitor/db.py (AGENT_DB_PATH) but in its
own `quote_context` table so the two never collide.
"""
import os
import sqlite3
import time

DB_PATH = os.environ.get("AGENT_DB_PATH", "/app/data/agent.db")

# Columns written by log_event(). Anything missing from a row defaults to NULL.
_COLS = [
    "ts", "event", "pair", "side", "qty", "our_px", "mid",
    "best_bid", "best_ask", "spread_abs", "spread_bps",
    "bid_depth", "ask_depth", "short_vol",
    "inv_base", "inv_usdso", "working_capital", "gas_somi",
    "order_type", "status", "tx_hash", "realized_pnl_delta", "cum_pnl", "note",
]


def _connect() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init() -> None:
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS quote_context (
                id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                ts                 REAL,
                event              TEXT,   -- quote | fill | cancel | refuel | skip | error
                pair               TEXT,
                side               TEXT,   -- buy | sell | None
                qty                REAL,
                our_px             REAL,
                mid                REAL,
                best_bid           REAL,
                best_ask           REAL,
                spread_abs         REAL,
                spread_bps         REAL,
                bid_depth          REAL,
                ask_depth          REAL,
                short_vol          REAL,   -- short-window realized volatility (bps)
                inv_base           REAL,
                inv_usdso          REAL,
                working_capital    REAL,
                gas_somi           REAL,
                order_type         TEXT,
                status             TEXT,
                tx_hash            TEXT,
                realized_pnl_delta REAL,
                cum_pnl            REAL,
                note               TEXT
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_qc_ts ON quote_context(ts)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_qc_pair_ts ON quote_context(pair, ts)")
        # Persisted inventory so a restart resumes the real position (not flat).
        conn.execute(
            "CREATE TABLE IF NOT EXISTS inventory_state ("
            "pair TEXT PRIMARY KEY, base REAL, avg_cost REAL, updated REAL)"
        )
        conn.execute("CREATE TABLE IF NOT EXISTS agent_state (k TEXT PRIMARY KEY, v REAL)")


def save_inventory(positions: dict, realized_pnl: float) -> None:
    try:
        with _connect() as conn:
            conn.execute("DELETE FROM inventory_state")
            for pair, pos in positions.items():
                conn.execute(
                    "INSERT INTO inventory_state(pair, base, avg_cost, updated) VALUES (?,?,?,?)",
                    (pair, pos.get("base", 0.0), pos.get("avg_cost", 0.0), time.time()),
                )
            conn.execute("INSERT OR REPLACE INTO agent_state(k, v) VALUES ('realized_pnl', ?)", (realized_pnl,))
    except Exception as e:
        print(f"[context_store] save_inventory failed: {e}", flush=True)


def load_inventory() -> tuple[dict, float]:
    positions: dict = {}
    realized = 0.0
    try:
        with _connect() as conn:
            for r in conn.execute("SELECT pair, base, avg_cost FROM inventory_state").fetchall():
                positions[r["pair"]] = {"base": r["base"] or 0.0, "avg_cost": r["avg_cost"] or 0.0}
            row = conn.execute("SELECT v FROM agent_state WHERE k='realized_pnl'").fetchone()
            if row and row["v"] is not None:
                realized = float(row["v"])
    except Exception as e:
        print(f"[context_store] load_inventory failed: {e}", flush=True)
    return positions, realized


def mid_ago(pair: str, lookback_s: float):
    """The most recent recorded mid for `pair` at or before (now - lookback_s),
    for trend detection. None if we have no history that far back."""
    try:
        cutoff = time.time() - lookback_s
        with _connect() as conn:
            r = conn.execute(
                "SELECT mid FROM quote_context WHERE pair=? AND mid IS NOT NULL "
                "AND ts<=? ORDER BY ts DESC LIMIT 1",
                (pair, cutoff),
            ).fetchone()
            return float(r["mid"]) if r and r["mid"] is not None else None
    except Exception as e:
        print(f"[context_store] mid_ago failed: {e}", flush=True)
        return None


def log_event(row: dict) -> None:
    """Insert one context row. Unknown keys are ignored; missing keys → NULL."""
    row = dict(row)
    row.setdefault("ts", time.time())
    cols = [c for c in _COLS if c in row]
    placeholders = ", ".join("?" for _ in cols)
    sql = f"INSERT INTO quote_context ({', '.join(cols)}) VALUES ({placeholders})"
    try:
        with _connect() as conn:
            conn.execute(sql, [row[c] for c in cols])
    except Exception as e:  # never let logging crash the trader
        print(f"[context_store] log failed: {e}", flush=True)


def recent(pair: str | None = None, n: int = 50) -> list[dict]:
    q = "SELECT * FROM quote_context"
    args: list = []
    if pair:
        q += " WHERE pair = ?"
        args.append(pair)
    q += " ORDER BY ts DESC LIMIT ?"
    args.append(n)
    with _connect() as conn:
        return [dict(r) for r in conn.execute(q, args).fetchall()]


def summary(since_s: int = 3600) -> dict:
    """Per-pair rollup the strategist reasons over."""
    cutoff = time.time() - since_s
    out: dict = {}
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM quote_context WHERE ts >= ? ORDER BY ts ASC", (cutoff,)
        ).fetchall()
    for r in rows:
        p = r["pair"] or "?"
        s = out.setdefault(p, {
            "quotes": 0, "fills": 0, "realized_pnl": 0.0,
            "spread_bps_sum": 0.0, "spread_n": 0, "vol_last": None,
            "last_mid": None, "inv_base_last": None,
        })
        if r["event"] == "quote":
            s["quotes"] += 1
        elif r["event"] == "fill":
            s["fills"] += 1
            s["realized_pnl"] += r["realized_pnl_delta"] or 0.0
        if r["spread_bps"] is not None:
            s["spread_bps_sum"] += r["spread_bps"]
            s["spread_n"] += 1
        if r["short_vol"] is not None:
            s["vol_last"] = r["short_vol"]
        if r["mid"] is not None:
            s["last_mid"] = r["mid"]
        if r["inv_base"] is not None:
            s["inv_base_last"] = r["inv_base"]
    for p, s in out.items():
        s["avg_spread_bps"] = (s["spread_bps_sum"] / s["spread_n"]) if s["spread_n"] else None
        del s["spread_bps_sum"], s["spread_n"]
    return out


def cum_pnl() -> float:
    """Latest recorded cumulative PnL (USDso) across all events."""
    with _connect() as conn:
        r = conn.execute(
            "SELECT cum_pnl FROM quote_context WHERE cum_pnl IS NOT NULL ORDER BY ts DESC LIMIT 1"
        ).fetchone()
    return float(r["cum_pnl"]) if r and r["cum_pnl"] is not None else 0.0


def set_control(enabled: bool) -> None:
    """Shared on/off flag — monitor (Telegram) sets it, agent reads it each tick."""
    try:
        with _connect() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS agent_state (k TEXT PRIMARY KEY, v REAL)")
            conn.execute("INSERT OR REPLACE INTO agent_state(k, v) VALUES ('enabled', ?)",
                         (1.0 if enabled else 0.0,))
    except Exception as e:
        print(f"[context_store] set_control failed: {e}", flush=True)


def control_enabled() -> bool:
    """True unless explicitly stopped. Defaults to enabled if never set."""
    try:
        with _connect() as conn:
            r = conn.execute("SELECT v FROM agent_state WHERE k='enabled'").fetchone()
        return True if r is None or r["v"] is None else bool(r["v"])
    except Exception:
        return True


def latest_strategy() -> dict | None:
    """Most recent Gemini strategist rationale (for the Telegram feed)."""
    with _connect() as conn:
        r = conn.execute(
            "SELECT ts, note, pair FROM quote_context WHERE event='strategy' ORDER BY ts DESC LIMIT 1"
        ).fetchone()
    return {"ts": r["ts"], "note": r["note"], "pairs": r["pair"]} if r else None


def last_trade_ts() -> float:
    """Unix ts of the most recent fill (for the liveness/DQ guard)."""
    with _connect() as conn:
        r = conn.execute(
            "SELECT ts FROM quote_context WHERE event = 'fill' ORDER BY ts DESC LIMIT 1"
        ).fetchone()
    return float(r["ts"]) if r else 0.0
