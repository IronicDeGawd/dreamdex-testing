# backend/agent/brain.py
import json, os, requests
from config import OPENAI_API, OPENAI_MODEL, AGENT_CONFIDENCE_MIN

SYSTEM_PROMPT = """
You are a conservative crypto trading agent on DreamDEX (Somnia blockchain).
You manage exactly $30 USDso. Your two goals in priority order:
  1. PRESERVE CAPITAL — never let balance drop below $22
  2. GENERATE VOLUME  — more transactions = better leaderboard position

Available pairs: WETH:USDso, WBTC:USDso, SOMI:USDso, USDC.e:USDso
This is a SPOT exchange only. You can BUY (get base token) or SELL (back to USDso).

Hard rules you must never break:
- Single trade max: $5 USDso
- Single trade min: $0.10 USDso  
- If USDso balance < $22: action must be "hold"
- Max 3 open positions simultaneously
- For tx count: use SOMI or USDC.e (gas is free on those pairs)
- For real profit: use WETH or WBTC (more price movement)
- Never buy if you already hold that asset and it's down >3%

Strategy mix (follow this roughly):
- 60% of actions: small $0.10-$0.50 trades on SOMI/USDC.e (free gas, tx count)
- 30% of actions: medium $1-$3 trades on WETH (real volume + profit potential)
- 10% of actions: hold (when uncertain or capital preservation needed)

Respond ONLY with valid JSON, no markdown, no explanation:
{
  "action": "buy" | "sell" | "hold",
  "pair": "WETH:USDso" | "WBTC:USDso" | "SOMI:USDso" | "USDC.e:USDso",
  "amount_usdso": <float, how much USDso to spend or receive>,
  "order_type": "market" | "limit",
  "limit_price": <float or null>,
  "reason": "<max 8 words>",
  "confidence": <integer 0-100>
}
If action is "hold", pair/amount/order_type can be null.
"""

def decide(prices: dict, positions: dict, balances: dict,
           history: list, leaderboard: dict) -> dict:
    """
    Ask GPT-4o-mini what to do right now.
    Returns parsed decision dict or {"action": "hold"} on error.
    """
    if not os.environ.get("OPENAI_KEY"):
        # Rule-based fallback for testing connectivity and end-to-end flow without OpenAI key
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
                        "confidence": 100
                    }
        return {
            "action": "buy",
            "pair": "SOMI:USDso",
            "amount_usdso": 1.0,
            "order_type": "market",
            "limit_price": None,
            "reason": "fallback buy SOMI",
            "confidence": 100
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

    return f"""
CURRENT PRICES (mid-price):
  WETH:   ${prices.get('WETH:USDso',  {}).get('mid', 0):,.2f}  ({momentum.get('WETH:USDso',  0):+.2f}% / 30min)
  WBTC:   ${prices.get('WBTC:USDso',  {}).get('mid', 0):,.2f}  ({momentum.get('WBTC:USDso',  0):+.2f}% / 30min)
  SOMI:   ${prices.get('SOMI:USDso',  {}).get('mid', 0):.5f}  ({momentum.get('SOMI:USDso',  0):+.2f}% / 30min)
  USDC.e: ${prices.get('USDC.e:USDso',{}).get('mid', 0):.5f}  ({momentum.get('USDC.e:USDso',0):+.2f}% / 30min)

MY BALANCES:
  USDso (free):   ${balances.get('usdso', 0):.4f}
  WETH held:      {balances.get('weth',  0):.6f}
  WBTC held:      {balances.get('wbtc',  0):.8f}
  SOMI held:      {balances.get('somi',  0):.4f}
  Total value:    ${balances.get('total', 0):.4f}

OPEN POSITIONS ({len(positions)}/3 max):
{chr(10).join(pos_lines) if pos_lines else '  None'}

LEADERBOARD:
  My rank:   #{lb.get('my_rank', '?')} of {lb.get('total', 10)}
  My txs:    {lb.get('my_tx', 0)}
  #3 has:    {lb.get('third_tx', 0)} txs
  Gap to #3: {lb.get('gap', 0)} txs
  Signal:    {lb.get('signal', 'MAINTAIN')}

LAST 5 DECISIONS:
{chr(10).join(trade_lines) if trade_lines else '  None yet'}

What should I do right now? Remember the 60/30/10 strategy mix.
"""
