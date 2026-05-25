# backend/server.py
from flask import Flask, jsonify, request
from config import FLASK_HOST, FLASK_PORT, MY_ADDRESS
from monitor.prices      import PriceFeed
from monitor.leaderboard import LeaderboardMonitor
from monitor.portfolio   import Portfolio
from trading.manual      import ManualTrader

app = Flask(__name__)

# These are injected from main.py
_agent    = None
_prices   = None
_lb       = None
_portfolio= None
_manual   = None

def init(agent, prices, lb, portfolio, manual):
    global _agent, _prices, _lb, _portfolio, _manual
    _agent=agent; _prices=prices; _lb=lb
    _portfolio=portfolio; _manual=manual

# ── Endpoints the ESP32 calls ──────────────────────────────

@app.route("/prices")
def prices():
    """All 4 pairs, latest mid/bid/ask"""
    return jsonify(_prices.latest())

@app.route("/agent")
def agent_status():
    """Agent status — what it's doing right now"""
    return jsonify(_agent.get_status())

@app.route("/portfolio")
def portfolio():
    """My balances, P&L, open positions"""
    return jsonify(_portfolio.summary())

@app.route("/leaderboard")
def leaderboard():
    """My position only"""
    return jsonify(_lb.get_my_stats())

@app.route("/manual", methods=["POST"])
def manual_trade():
    """ESP32 button triggers a manual trade"""
    data = request.json
    # data = {"pair": "WETH:USDso", "side": "buy", "amount_usdso": 2.0}
    result = _manual.execute(
        pair       = data["pair"],
        side       = data["side"],
        amount_usdso = float(data["amount_usdso"]),
        prices     = _prices.latest()
    )
    return jsonify(result)

@app.route("/agent/speed", methods=["POST"])
def set_speed():
    """ESP32 config menu changes agent speed"""
    speed = request.json.get("speed", "normal")
    _agent.set_speed(speed)
    return jsonify({"ok": True, "speed": speed})

@app.route("/agent/toggle", methods=["POST"])
def toggle_agent():
    """Pause or resume agent"""
    if _agent.paused:
        _agent.resume()
        return jsonify({"status": "resumed"})
    else:
        _agent.pause()
        return jsonify({"status": "paused"})

@app.route("/agent/max_orders", methods=["POST"])
def set_max_orders():
    """ESP32 config menu sets the order budget (0 = unlimited)"""
    n = int(request.json.get("max_orders", 0))
    _agent.set_max_orders(n)
    return jsonify({"ok": True, "max_orders": _agent.max_orders})

@app.route("/vault/deposit", methods=["POST"])
def vault_deposit():
    """Deposit funds into the SpotPool vault"""
    data = request.json
    try:
        tx_hash = _manual.dex.vault_deposit(
            symbol=data["pair"],
            token_addr=data["token"],
            amount=float(data["amount"])
        )
        return jsonify({"status": "success", "tx_hash": tx_hash})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 400

@app.route("/vault/withdraw", methods=["POST"])
def vault_withdraw():
    """Withdraw funds from the SpotPool vault"""
    data = request.json
    try:
        tx_hash = _manual.dex.vault_withdraw(
            symbol=data["pair"],
            token_addr=data["token"],
            amount=float(data["amount"])
        )
        return jsonify({"status": "success", "tx_hash": tx_hash})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 400

@app.route("/wifi_scan")
def wifi_scan():
    """ESP32 asks for known networks (just returns config)"""
    return jsonify({"known": ["Home_Network", "iPhone_Hotspot"]})

def run():
    app.run(host=FLASK_HOST, port=FLASK_PORT, 
            debug=False, use_reloader=False)
