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
SUMMARY_S = int(os.environ.get("MONITOR_SUMMARY_S", 1800))
POLL_S = int(os.environ.get("MONITOR_POLL_S", 90))
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


def fetch_leaderboard() -> dict | None:
    try:
        r = requests.get(config.LEADERBOARD_URL, timeout=20)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"[monitor] leaderboard fetch failed: {e}", flush=True)
        return None


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


def summary_text(row, rank, total, bal) -> str:
    if not row:
        return "📊 <b>DreamDEX R3</b>\nOur wallet not on the leaderboard yet."
    pnl = row.get("pnl", 0.0)
    pnl_pct = pnl / config.STARTING_CAPITAL * 100
    emoji = "🟢" if pnl >= 0 else "🔴"
    somi = f"{bal['somi']:.2f}" if bal["somi"] is not None else "?"
    usdso = f"{bal['usdso']:.2f}" if bal["usdso"] is not None else "?"
    return (
        f"📊 <b>DreamDEX R3</b> — rank <b>{rank}/{total}</b>\n"
        f"{emoji} PnL: <b>{pnl:+.2f}</b> USDso ({pnl_pct:+.1f}%)\n"
        f"Effective vol: <b>{row.get('volumeEffective', 0):,.0f}</b> "
        f"(raw {row.get('volumeUsdso', 0):,.0f})\n"
        f"Fills: {row.get('fills', 0)}  •  Tx: {row.get('txCount', 0)}\n"
        f"Wallet: {usdso} USDso  •  ⛽ {somi} SOMI\n"
        f"Milestones: {int(row.get('volumeUsdso', 0) // MILESTONE_USDSO)} × $25"
    )


def run():
    print(f"[monitor] starting — telegram={'on' if (TG_TOKEN and TG_CHAT) else 'OFF (stdout only)'} "
          f"summary={SUMMARY_S}s poll={POLL_S}s wallet={OUR_ADDR}", flush=True)
    state = {"fills": None, "rank": None, "pnl_neg": None, "milestone": None,
             "gas_low": False, "idle_warned": False}
    last_summary = 0.0

    while True:
        lb = fetch_leaderboard()
        row, rank, total = our_standing(lb)
        bal = chain_balances()
        now = time.time()

        if row is not None:
            fills = row.get("fills", 0)
            vol = row.get("volumeUsdso", 0)
            pnl = row.get("pnl", 0.0)

            # ── instant alerts ──
            if state["fills"] is not None and fills > state["fills"]:
                send(f"✅ {fills - state['fills']} new fill(s) — total {fills}. "
                     f"PnL {pnl:+.2f}, eff vol {row.get('volumeEffective', 0):,.0f}")
            ms = int(vol // MILESTONE_USDSO)
            if state["milestone"] is not None and ms > state["milestone"]:
                send(f"🎯 Milestone! Crossed {ms * MILESTONE_USDSO:,} raw volume → {ms} × $25 reward")
            if state["rank"] is not None and rank != state["rank"]:
                arrow = "⬆️" if rank < state["rank"] else "⬇️"
                send(f"{arrow} Rank {state['rank']} → <b>{rank}</b>/{total}")
            neg = pnl < 0
            if state["pnl_neg"] is not None and neg and not state["pnl_neg"]:
                send(f"⚠️ PnL went negative: {pnl:+.2f} USDso — multiplier now <1×")

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
            send(summary_text(row, rank, total, bal))
            last_summary = now

        time.sleep(POLL_S)


if __name__ == "__main__":
    run()
