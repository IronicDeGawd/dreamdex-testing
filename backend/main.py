# backend/main.py
import os, sys, threading
from agent.agent         import TradingAgent
from monitor.prices      import PriceFeed
from monitor.leaderboard import LeaderboardMonitor
from monitor.portfolio   import Portfolio
from trading.manual      import ManualTrader
import server

def main():
    # Verify secrets
    from config import PRIVATE_KEY, ENV, FLASK_API_KEY
    assert PRIVATE_KEY, \
        "Set your wallet key: export TESTNET_PRIVATE_KEY=0x... (or MAINNET_PRIVATE_KEY for mainnet)"
    if not os.environ.get("OPENAI_KEY"):
        if ENV == "mainnet":
            # M1: hard refuse mainnet without OPENAI_KEY. Rule-based fallback fires real
            # trades with confidence=100 (bypassing the confidence gate) — too dangerous
            # with real money. Force the user to explicitly opt out by setting OPENAI_KEY=disable.
            if os.environ.get("OPENAI_KEY", "") != "disable":
                raise RuntimeError(
                    "OPENAI_KEY is unset on MAINNET. Rule-based fallback would trade with real money "
                    "and bypasses the confidence gate. Set OPENAI_KEY=<real key>, or "
                    "OPENAI_KEY=disable to acknowledge fallback-only operation."
                )
            print("[main] ⚠️  OPENAI_KEY=disable on mainnet — fallback only, will trade SOMI $1 every tick.")
        else:
            print("[main] ⚠️  OPENAI_KEY not set. Agent will run in Rule-Based Fallback mode.")
    if ENV == "mainnet" and not FLASK_API_KEY:
        # Belt-and-suspenders — server.init also checks this, but failing here gives a
        # clearer error message before any subsystem boots.
        raise RuntimeError("FLASK_API_KEY env var is REQUIRED on mainnet — set it before launch.")

    from config import SOMNIA_RPC, DREAMDEX_HTTP, MY_ADDRESS, FLASK_PORT
    print("="*55)
    print(f"  DreamDEX Trading Bot — {ENV.upper()} mode")
    print(f"  Wallet:  {MY_ADDRESS}")
    print(f"  RPC:     {SOMNIA_RPC}")
    print(f"  DEX API: {DREAMDEX_HTTP}")
    print(f"  Flask:   http://0.0.0.0:{FLASK_PORT}")
    print("="*55)

    # Init components
    prices    = PriceFeed()
    lb        = LeaderboardMonitor()
    portfolio = Portfolio()
    manual    = ManualTrader()
    # C2: agent reads capital from on-chain Portfolio, not local AgentState.
    agent     = TradingAgent(portfolio=portfolio)

    # Wire: prices → agent analyzer
    prices.add_subscriber(agent.on_price_update)

    # Wire Flask
    server.init(agent, prices, lb, portfolio, manual)

    # Start background threads
    prices.start()      # REST poll every 30s
    lb.start()          # leaderboard every 5min
    portfolio.start()   # on-chain balance every 60s
    agent.start()       # AI loop every 5min

    # Flask blocks main thread
    server.run()


if __name__ == "__main__":
    main()
