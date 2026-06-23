"""Telegram monitor bot — profit/trade updates for the R3 contest.

Standalone process (run separately from the agent) that combines:
  - the official leaderboard API (our rank, raw vs PnL-weighted volume, fills),
  - our local trade store (realized PnL, recent fills, positions),
  - on-chain balances (USDso working capital, native SOMI gas) via public RPC.

Sends a periodic summary plus instant alerts on notable events. Telegram creds
come from env (TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID); if absent it prints to
stdout so it still works over SSH.

Run:  python -m agent_v3.monitor_bot
Env:  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, MONITOR_SUMMARY_S (default 1800),
      MONITOR_POLL_S (default 90), DREAMDEX_ENV, WALLET_ADDRESS
"""
import os
import time

import requests
from web3 import Web3

import config
from agent_v3 import context_store as ctx

TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "")
SUMMARY_S = int(os.environ.get("MONITOR_SUMMARY_S", 600))
PREVIEW_S = int(os.environ.get("MONITOR_PREVIEW_S", 7200))  # market-preview cadence (2h)
POLL_S = int(os.environ.get("MONITOR_POLL_S", 90))
# Pairs shown in the market preview: the ones we trade (🎯) plus WETH for reference.
PREVIEW_PAIRS = list(config.ELIGIBLE_PAIRS) + [p for p in ["WETH:USDso"] if p not in config.ELIGIBLE_PAIRS]
GAS_ALERT_SOMI = float(os.environ.get("MONITOR_GAS_ALERT_SOMI", 8.0))
MILESTONE_USDSO = 500_000          # $25 reward per 500k volume (rules)
OUR_ADDR = config.LEADERBOARD_ADDRESS.lower()

_ERC20 = [{"name": "balanceOf", "type": "function", "stateMutability": "view",
           "inputs": [{"name": "a", "type": "address"}], "outputs": [{"name": "", "type": "uint256"}]}]


def send(text: str):
    print(f"[monitor] {text}", flush=True)
    if not (TG_TOKEN and TG_CHAT):
        return
    try:
        requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                      json={"chat_id": TG_CHAT, "text": text, "parse_mode": "HTML",
                            "disable_web_page_preview": True}, timeout=15)
    except Exception as e:
        print(f"[monitor] telegram send failed: {e}", flush=True)


def _tg_get(method: str, params: dict) -> dict:
    r = requests.get(f"https://api.telegram.org/bot{TG_TOKEN}/{method}", params=params, timeout=15)
    return r.json()


def drain_commands() -> int:
    """Return the next update offset, skipping any messages sent before startup."""
    if not (TG_TOKEN and TG_CHAT):
        return 0
    try:
        ups = _tg_get("getUpdates", {"timeout": 0}).get("result", [])
        return ups[-1]["update_id"] + 1 if ups else 0
    except Exception:
        return 0


def poll_commands(offset: int) -> tuple[int, bool]:
    """Handle /stop /start /status from the configured chat. Returns (new_offset, want_status)."""
    want_status = False
    if not (TG_TOKEN and TG_CHAT):
        return offset, want_status
    try:
        data = _tg_get("getUpdates", {"offset": offset, "timeout": 0})
    except Exception:
        return offset, want_status
    for u in data.get("result", []):
        offset = u["update_id"] + 1
        msg = u.get("message") or {}
        text = (msg.get("text") or "").strip().lower()
        chat = str((msg.get("chat") or {}).get("id", ""))
        if chat != str(TG_CHAT):
            continue
        if text.startswith("/stop"):
            ctx.set_control(False)
        elif text.startswith("/start"):
            ctx.set_control(True)
        elif text.startswith("/status"):
            want_status = True
    return offset, want_status


def fetch_leaderboard() -> dict | None:
    try:
        r = requests.get(config.LEADERBOARD_URL, timeout=20)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"[monitor] leaderboard fetch failed: {e}", flush=True)
        return None


def _fetch_book(pair: str) -> dict | None:
    """Top-of-book + a little depth for a pair (public, no auth)."""
    try:
        r = requests.get(f"{config.DREAMDEX_HTTP}/v0/orderbooks",
                         params={"symbols": pair, "depth": 3}, timeout=12)
        ob = r.json()["orderbooks"][0]
        bid = float(ob["bids"][0]["price"]); ask = float(ob["asks"][0]["price"])
        bq = sum(float(x["quantity"]) for x in ob["bids"][:3])
        aq = sum(float(x["quantity"]) for x in ob["asks"][:3])
        mid = (bid + ask) / 2
        return {"bid": bid, "ask": ask, "mid": mid, "bid_qty": bq, "ask_qty": aq,
                "spread_bps": (ask - bid) / mid * 1e4 if mid else 0.0}
    except Exception:
        return None


def _fetch_mid(pair: str) -> float | None:
    b = _fetch_book(pair)
    return b["mid"] if b else None


def market_preview_text() -> str:
    lines = ["📈 <b>Market preview</b>", ""]
    widest = None
    for p in PREVIEW_PAIRS:
        b = _fetch_book(p)
        if not b:
            continue
        tag = " 🎯" if p in config.ELIGIBLE_PAIRS else "  (ref)"
        lines.append(f"<b>{p.split(':')[0]}</b>{tag}")
        lines.append(f"     {_fmt(b['bid'])} / {_fmt(b['ask'])}  ·  <b>{b['spread_bps']:.1f} bps</b>")
        lines.append(f"     depth ~{b['bid_qty']:g} bid / {b['ask_qty']:g} ask")
        if p in config.ELIGIBLE_PAIRS and (widest is None or b["spread_bps"] > widest[1]):
            widest = (p.split(":")[0], b["spread_bps"])
    if widest:
        lines += ["", f"💡 Widest spread we quote: <b>{widest[0]}</b> ({widest[1]:.1f} bps) — most capture per round-trip."]
    return "\n".join(lines)


def our_pnl() -> tuple[float, dict]:
    """Our own PnL vs the $150 start, from the agent's trade accounting:
    realized (completed round-trips) + unrealized (open inventory at current mid).
    Independent of the leaderboard's free-USDso-only snapshot. Capital reserved in
    an unfilled buy is worth its face value, so it contributes 0 here (correct)."""
    positions, realized = ctx.load_inventory()
    unreal = 0.0
    detail = {"realized": realized, "positions": {}}
    for pair, pos in positions.items():
        base = pos.get("base", 0.0) or 0.0
        avg = pos.get("avg_cost", 0.0) or 0.0
        if base <= 0 or avg <= 0:
            continue
        mid = _fetch_mid(pair)
        if mid:
            u = (mid - avg) * base
            unreal += u
            detail["positions"][pair] = {"base": base, "avg": avg, "mid": mid, "unreal": u}
    detail["unreal"] = unreal
    return realized + unreal, detail


def our_standing(lb: dict) -> tuple[dict | None, int, int]:
    """Return (our_row, rank_by_effective, total_traders)."""
    traders = lb.get("traders", []) if lb else []
    ranked = sorted(traders, key=lambda t: t.get("volumeEffective", 0), reverse=True)
    for i, t in enumerate(ranked):
        if t.get("address", "").lower() == OUR_ADDR:
            return t, i + 1, len(ranked)
    return None, 0, len(ranked)


def chain_balances() -> dict:
    out = {"somi": None, "usdso": None}
    try:
        w3 = Web3(Web3.HTTPProvider(config.SOMNIA_RPC))
        addr = Web3.to_checksum_address(config.MY_ADDRESS)
        out["somi"] = w3.eth.get_balance(addr) / 1e18
        usdso = w3.eth.contract(address=Web3.to_checksum_address(config.USDSO_ADDRESS), abi=_ERC20)
        out["usdso"] = usdso.functions.balanceOf(addr).call() / 1e18
    except Exception as e:
        print(f"[monitor] balance read failed: {e}", flush=True)
    return out


def _fmt(x) -> str:
    if x is None:
        return "?"
    return f"{x:,.2f}" if abs(x) >= 100 else f"{x:.4f}"


def summary_text(row, rank, total, bal, opnl, odetail) -> str:
    if not row:
        return "📊 <b>DreamDEX V3</b>\nOur wallet not on the leaderboard yet."
    pnl_pct = opnl / config.STARTING_CAPITAL * 100
    emoji = "🟢" if opnl >= 0 else "🔴"
    somi = f"{bal['somi']:.2f}" if bal["somi"] is not None else "?"
    usdso = f"{bal['usdso']:.2f}" if bal["usdso"] is not None else "?"
    vol = row.get("volumeUsdso", 0)

    lines = [
        "📊 <b>DreamDEX V3</b>",
        "",
        f"🏆 Rank: <b>{rank}/{total}</b>",
        f"📈 Volume: <b>{vol:,.0f}</b> USDso",
        f"🎯 Milestones: {int(vol // MILESTONE_USDSO)} × $25",
        "",
        f"{emoji} PnL: <b>{opnl:+.2f}</b> USDso ({pnl_pct:+.1f}%)",
        f"   realized: {odetail.get('realized', 0):+.2f}",
        f"   unrealized: {odetail.get('unreal', 0):+.2f}",
        "",
        f"🔄 Fills: {row.get('fills', 0)}",
        f"🧾 Tx: {row.get('txCount', 0)}",
        f"👛 USDso: {usdso}",
        f"⛽ SOMI: {somi}",
    ]
    if odetail.get("positions"):
        lines.append("")
        lines.append("📦 Inventory:")
        for p, d in odetail["positions"].items():
            lines.append(f"   {p.split(':')[0]}: {d['base']:g} @ {_fmt(d['avg'])} → {_fmt(d['mid'])}")
    return "\n".join(lines)


def run():
    print(f"[monitor] starting — telegram={'on' if (TG_TOKEN and TG_CHAT) else 'OFF (stdout only)'} "
          f"summary={SUMMARY_S}s poll={POLL_S}s wallet={OUR_ADDR}", flush=True)
    state = {"fills": None, "rank": None, "pnl_neg": None, "milestone": None,
             "gas_low": False, "idle_warned": False, "strategy_ts": None,
             "enabled": ctx.control_enabled()}
    last_summary = 0.0
    last_preview = 0.0
    cmd_offset = drain_commands()
    send("🤖 <b>DreamDEX V3 monitor online.</b> Commands: /stop  /start  /status")

    while True:
        # handle Telegram commands first, then announce any on/off change
        cmd_offset, want_status = poll_commands(cmd_offset)
        enabled = ctx.control_enabled()
        if enabled != state["enabled"]:
            send("🛑 <b>Agent STOPPED</b> — flattening inventory to USDso, then idle."
                 if not enabled else "▶️ <b>Agent STARTED</b> — resuming market-making.")
            state["enabled"] = enabled
        if want_status:
            last_summary = 0.0   # force a summary card this loop

        lb = fetch_leaderboard()
        row, rank, total = our_standing(lb)       # leaderboard: volume + rank only
        bal = chain_balances()
        opnl, odetail = our_pnl()                 # OUR PnL (realized + unrealized vs $150)
        now = time.time()

        if row is not None:
            fills = row.get("fills", 0)
            vol = row.get("volumeUsdso", 0)

            # ── instant alerts ──
            if state["fills"] is not None and fills > state["fills"]:
                send(f"✅ {fills - state['fills']} new fill(s) — total {fills}. "
                     f"raw vol {vol:,.0f}  •  our PnL {opnl:+.2f} USDso")
                last_summary = 0.0   # force a fresh summary card this loop
            ms = int(vol // MILESTONE_USDSO)
            if state["milestone"] is not None and ms > state["milestone"]:
                send(f"🎯 Milestone! Crossed {ms * MILESTONE_USDSO:,} raw volume → {ms} × $25 reward")
            if state["rank"] is not None and rank != state["rank"]:
                arrow = "⬆️" if rank < state["rank"] else "⬇️"
                send(f"{arrow} Rank {state['rank']} → <b>{rank}</b>/{total}")
            neg = opnl < -1.0                      # alert only on a real (our-calc) loss
            if state["pnl_neg"] is not None and neg and not state["pnl_neg"]:
                send(f"⚠️ Our PnL negative: {opnl:+.2f} USDso (real {odetail.get('realized',0):+.2f} / "
                     f"unreal {odetail.get('unreal',0):+.2f})")

            state.update(fills=fills, rank=rank, pnl_neg=neg, milestone=ms)

        # gas alert (on-chain)
        if bal["somi"] is not None:
            if bal["somi"] < GAS_ALERT_SOMI and not state["gas_low"]:
                send(f"⛽ Low gas: {bal['somi']:.2f} SOMI (< {GAS_ALERT_SOMI})")
                state["gas_low"] = True
            elif bal["somi"] >= GAS_ALERT_SOMI:
                state["gas_low"] = False

        # idle / DQ-risk alert (local trade store)
        last_fill = ctx.last_trade_ts()
        if last_fill:
            idle_h = (now - last_fill) / 3600
            if idle_h > 18 and not state["idle_warned"]:
                send(f"😴 No fill in {idle_h:.1f}h — DQ risk at 24h. Check the agent.")
                state["idle_warned"] = True
            elif idle_h <= 18:
                state["idle_warned"] = False

        # periodic summary
        if now - last_summary >= SUMMARY_S:
            send(summary_text(row, rank, total, bal, opnl, odetail))
            last_summary = now

        # relay each new Gemini strategist rationale (its reasoning, surfaced live)
        s = ctx.latest_strategy()
        if s and s.get("note") and s["ts"] != state["strategy_ts"]:
            pairs = (s.get("pairs") or "").replace(",", ", ")
            send(f"🧠 <b>Strategist · Gemini 2.5 Pro</b>\n\n{s['note']}"
                 + (f"\n\n🎯 Focusing: {pairs}" if pairs else ""))
            state["strategy_ts"] = s["ts"]

        # periodic market preview
        if now - last_preview >= PREVIEW_S:
            send(market_preview_text())
            last_preview = now

        time.sleep(POLL_S)


if __name__ == "__main__":
    run()
