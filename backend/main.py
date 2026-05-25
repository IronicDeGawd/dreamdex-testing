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
    from config import PRIVATE_KEY
    assert PRIVATE_KEY, \
        "Set your wallet key: export TESTNET_PRIVATE_KEY=0x... (or MAINNET_PRIVATE_KEY for mainnet)"
    if not os.environ.get("OPENAI_KEY"):
        print("[main] ⚠️  OPENAI_KEY not set. Agent will run in Rule-Based Fallback mode.")

    from config import ENV, SOMNIA_RPC, DREAMDEX_HTTP, MY_ADDRESS, FLASK_PORT
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
    agent     = TradingAgent()

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
