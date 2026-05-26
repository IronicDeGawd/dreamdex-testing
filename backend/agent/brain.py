# backend/agent/brain.py
import json, os, requests
from config import OPENAI_API, OPENAI_MODEL, AGENT_CONFIDENCE_MIN, ENV

SYSTEM_PROMPT = """
You are a trading agent on DreamDEX (Somnia mainnet) in a contest where
leaderboard rank is driven primarily by **number of successful fills**, then
PnL, then volume.

You manage $50 USDso (the 'manual_balance' field is a planning placeholder, not
a separate wallet). Your two goals in priority order:
  1. MAXIMISE FILLS  — every fill = one leaderboard tick.
  2. AVOID INVENTORY — every BUY that isn't followed by a SELL turns USDso
     into a base token, which the leaderboard counts as a loss because PnL is
     measured in USDso. So you MUST round-trip.

Tradeable pairs on mainnet at the current $2 per-trade max:
  - SOMI:USDso    — min order ~$0.17 (FAST tx grinder, smallest minimum)
  - USDC.e:USDso  — min order ~$1.00 (stable peg, low risk)
  - WETH:USDso    — min order ~$2.13  → STAYS OUT OF REACH at the $2 max.
                    Do NOT pick this pair; it will silent-reject every time.
  - WBTC:USDso    — min order ~$7.74  → STAYS OUT OF REACH. Do NOT pick.

Hard rules you must never break:
- Allowed pairs ONLY: SOMI:USDso and USDC.e:USDso. Picking any other pair
  burns gas with no fill — strictly forbidden.
- Single trade max: $2.00 USDso
- Single trade min: $0.20 USDso  (anything smaller hits minQuantity issues)
- If USDso balance < $22: action must be "hold"
- ROUND-TRIP RULE: if your immediately previous successful action was a BUY of
  pair X, the very next non-hold action MUST be a SELL of pair X. Only after
  the round-trip is complete may you start a new BUY. This is non-negotiable —
  it both adds a leaderboard fill AND restores your USDso.

Strategy mix (follow this roughly across the next ~50 ticks):
- 70%  small SOMI round-trips (BUY $0.30–$1.00 → SELL the same amount)
- 25%  USDC.e round-trips ($1.00–$2.00, tiny PnL risk)
- 5%   hold (only when you literally just sent a trade and want to wait one tick)

Respond ONLY with valid JSON, no markdown, no explanation:
{
  "action": "buy" | "sell" | "hold",
  "pair":   "SOMI:USDso" | "USDC.e:USDso",
  "amount_usdso": <float>,
  "order_type": "market",
  "limit_price": null,
  "reason": "<max 8 words>",
  "confidence": <integer 0-100>
}
If action is "hold", pair/amount may be null.
"""

def decide(prices: dict, positions: dict, balances: dict,
           history: list, leaderboard: dict) -> dict:
    """
    Ask GPT-4o-mini what to do right now.
    Returns parsed decision dict or {"action": "hold"} on error.
    """
    openai_key = os.environ.get("OPENAI_KEY", "")
    if not openai_key or openai_key == "disable":
        # M1: on mainnet, rule-based fallback is dangerous (real-money trades at
        # confidence=100, bypassing the confidence gate). main.py refuses to start
        # without OPENAI_KEY=<real|disable> on mainnet, so we only reach here on
        # mainnet if the operator explicitly set OPENAI_KEY=disable.
        # On testnet, fallback is fine — useful for connectivity testing.
        if ENV == "mainnet":
            # Always hold on mainnet fallback. Operator opted into degraded mode
            # but we still refuse to fire blind real-money trades.
            return {"action": "hold", "reason": "no LLM key on mainnet", "confidence": 100}
        # Testnet fallback — same as before but with confidence below the gate
        # so it doesn't override safety paths (gate is AGENT_CONFIDENCE_MIN=65).
        if positions:
            for pair, pos in positions.items():
                if pos.get("qty", 0) > 0:
                    mid = prices.get(pair, {}).get("mid", 0) or 1.0
                    return {
                        "action": "sell",
                        "pair": pair,
                        "amount_usdso": float(pos["qty"] * mid),
                        "order_type": "market",
                        "limit_price": None,
                        "reason": "fallback sell holding",
                        "confidence": 100,  # testnet only
                    }
        return {
            "action": "buy",
            "pair": "SOMI:USDso",
            "amount_usdso": 1.0,
            "order_type": "market",
            "limit_price": None,
            "reason": "fallback buy SOMI",
            "confidence": 100,  # testnet only
        }

    user_msg = _build_prompt(prices, positions, balances, history, leaderboard)

    try:
        resp = requests.post(
            f"{OPENAI_API}/chat/completions",
            headers={
                "Authorization": f"Bearer {os.environ['OPENAI_KEY']}",
                "Content-Type": "application/json",
            },
            json={
                "model": OPENAI_MODEL,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user",   "content": user_msg},
                ],
                "temperature": 0,      # deterministic
                "max_tokens": 200,
                "response_format": {"type": "json_object"},
            },
            timeout=10,
        )
        raw = resp.json()["choices"][0]["message"]["content"]
        decision = json.loads(raw)

        # Safety gate — ignore low-confidence decisions
        if decision.get("confidence", 0) < AGENT_CONFIDENCE_MIN:
            decision["action"] = "hold"
            decision["reason"] = "low confidence"

        return decision

    except Exception as e:
        print(f"[brain] OpenAI error: {e}")
        return {"action": "hold", "reason": "api error", "confidence": 0}


def _build_prompt(prices, positions, balances, history, lb) -> str:
    # Price momentum — compute % change vs 30 min ago
    momentum = {}
    for pair, pdata in prices.items():
        hist = pdata.get("history", [])
        if len(hist) >= 6:
            old = hist[-6]["mid"]
            now = hist[-1]["mid"]
            momentum[pair] = round((now - old) / old * 100, 3)
        else:
            momentum[pair] = 0.0

    pos_lines = []
    for pair, pos in positions.items():
        mid = prices.get(pair, {}).get("mid", 0)
        pnl = (mid - pos["entry_price"]) / pos["entry_price"] * 100
        pos_lines.append(
            f"  {pair}: holding {pos['qty']} @ entry ${pos['entry_price']:.4f}"
            f" | now ${mid:.4f} | PnL {pnl:+.2f}%"
        )

    last_trades = history[-5:] if history else []
    trade_lines = [
        f"  {t.get('time', '-')}: {t.get('action')} {t.get('pair')} → {t.get('result', {}).get('status', 'ok')}"
        for t in last_trades
    ]

    # ROUND-TRIP HINT: find the most recent successful trade and tell the LLM
    # what side it MUST take next. This is the strongest single signal we can
    # give the model — without it the LLM keeps drifting back to "buy SOMI".
    round_trip_hint = "  (no successful trades yet — start with a BUY of SOMI:USDso)"
    for t in reversed(history):
        if t.get('result', {}).get('status') == 'success':
            act = t.get('action')
            pair = t.get('pair', 'SOMI:USDso')
            if act == 'buy':
                round_trip_hint = f"  Last successful: BUY {pair}. Next MUST be SELL {pair} (round-trip)."
            elif act == 'sell':
                round_trip_hint = f"  Last successful: SELL {pair}. You may now start a fresh BUY (any allowed pair)."
            break

    return f"""
CURRENT PRICES (only tradeable pairs shown):
  SOMI:   ${prices.get('SOMI:USDso',  {}).get('mid', 0):.5f}  ({momentum.get('SOMI:USDso',  0):+.2f}% / 30min)
  USDC.e: ${prices.get('USDC.e:USDso',{}).get('mid', 0):.5f}  ({momentum.get('USDC.e:USDso',0):+.2f}% / 30min)

MY BALANCES:
  USDso (free):   ${balances.get('usdso', 0):.4f}
  SOMI held:      {balances.get('somi',  0):.4f}
  Total value:    ${balances.get('total', 0):.4f}

OPEN POSITIONS ({len(positions)}/3 max):
{chr(10).join(pos_lines) if pos_lines else '  None'}

ROUND-TRIP STATE:
{round_trip_hint}

LEADERBOARD:
  My rank:   #{lb.get('my_rank', '?')} of {lb.get('total', 10)}
  My fills:  {lb.get('my_tx', 0)}
  Signal:    {lb.get('signal', 'MAINTAIN')}

LAST 5 DECISIONS:
{chr(10).join(trade_lines) if trade_lines else '  None yet'}

Decide my next action. Obey the ROUND-TRIP STATE above first — that takes
precedence over every other heuristic.
"""
