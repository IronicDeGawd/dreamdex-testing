# backend/monitor/portfolio.py
"""
Portfolio — tracks live balances by querying the SpotPool vault
and the wallet's ERC-20 USDso balance.

Calls getWithdrawableBalance(user, token) on each pool contract to
read vault deposits. Falls back to wallet ERC-20 balance for total.
"""
import time
import threading
from web3 import Web3
from config import (
    SOMNIA_RPC, MARKETS, USDSO_ADDRESS,
    AGENT_CAPITAL, MANUAL_CAPITAL, TOTAL_CAPITAL, MY_ADDRESS
)

VAULT_ABI = [
    {
        "name": "getWithdrawableBalance",
        "type": "function",
        "stateMutability": "view",
        "inputs": [
            {"name": "user",  "type": "address"},
            {"name": "token", "type": "address"},
        ],
        "outputs": [{"name": "", "type": "uint256"}],
    }
]

ERC20_ABI = [
    {
        "name": "balanceOf",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "account", "type": "address"}],
        "outputs": [{"name": "", "type": "uint256"}],
    }
]


class Portfolio:
    def __init__(self):
        self.w3    = Web3(Web3.HTTPProvider(SOMNIA_RPC))
        self._lock = threading.Lock()
        self._stats = {
            "agent_balance":  AGENT_CAPITAL,
            "manual_balance": MANUAL_CAPITAL,
            "total_value":    TOTAL_CAPITAL,
            "usdso_wallet":   0.0,
            "usdso_vaults":   {},
        }
        self.running = False

    def start(self):
        self.running = True
        threading.Thread(target=self._loop, daemon=True).start()
        print("[Portfolio] Started balance tracker")

    def _loop(self):
        while self.running:
            try:
                self._refresh()
            except Exception as e:
                print(f"[Portfolio] refresh error: {e}")
            time.sleep(60)

    def _refresh(self):
        if not self.w3.is_connected():
            print("[Portfolio] RPC not connected, skipping refresh")
            return

        me    = Web3.to_checksum_address(MY_ADDRESS)
        usdso = Web3.to_checksum_address(USDSO_ADDRESS)

        # Wallet ERC-20 USDso balance
        erc20 = self.w3.eth.contract(address=usdso, abi=ERC20_ABI)
        try:
            wallet_raw = erc20.functions.balanceOf(me).call()
            wallet_usdso = wallet_raw / 1e18
        except Exception as e:
            print(f"[Portfolio] balanceOf error: {e}")
            wallet_usdso = 0.0

        # Vault balances per pool
        vault_totals: dict[str, float] = {}
        for pair, mkt in MARKETS.items():
            try:
                pool = self.w3.eth.contract(
                    address=Web3.to_checksum_address(mkt["contract"]), abi=VAULT_ABI
                )
                raw = pool.functions.getWithdrawableBalance(me, usdso).call()
                vault_totals[pair] = raw / 1e18
            except Exception as e:
                vault_totals[pair] = 0.0

        total_vault = sum(vault_totals.values())
        total_usdso = wallet_usdso + total_vault

        with self._lock:
            self._stats = {
                "agent_balance":  total_usdso,          # approximation — full tracking needs positions
                "manual_balance": MANUAL_CAPITAL,        # kept constant (user tracks manually)
                "total_value":    total_usdso + MANUAL_CAPITAL,
                "usdso_wallet":   wallet_usdso,
                "usdso_vaults":   vault_totals,
            }

    def summary(self) -> dict:
        with self._lock:
            return dict(self._stats)
