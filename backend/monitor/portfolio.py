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
        # Synchronous first refresh so agent's first tick has on-chain data to
        # gate its capital-floor check against (C2). Without this, the first
        # tick would see last_refresh=0 and hold ("portfolio stale") — fine
        # but noisy.
        try:
            self._refresh()
        except Exception as e:
            print(f"[Portfolio] initial refresh failed: {e}")
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

        # H4: divide by the pool's actual quoteDecimals, not a hardcoded 1e18.
        # USDso is 18d today but the pattern would silently break a base-token
        # vault check on WBTC (8d) or USDC.e (6d).
        # USDso's own decimals come from any pool's quoteDecimals (they're identical).
        usdso_decimals = 18
        for _mkt in MARKETS.values():
            usdso_decimals = int(_mkt.get("quoteDecimals", 18))
            break

        # Wallet ERC-20 USDso balance
        erc20 = self.w3.eth.contract(address=usdso, abi=ERC20_ABI)
        try:
            wallet_raw = erc20.functions.balanceOf(me).call()
            wallet_usdso = wallet_raw / (10 ** usdso_decimals)
        except Exception as e:
            print(f"[Portfolio] balanceOf error: {e}")
            wallet_usdso = 0.0

        # Vault balances per pool (USDso side — quoteDecimals scaling)
        vault_totals: dict[str, float] = {}
        for pair, mkt in MARKETS.items():
            try:
                pool = self.w3.eth.contract(
                    address=Web3.to_checksum_address(mkt["contract"]), abi=VAULT_ABI
                )
                raw = pool.functions.getWithdrawableBalance(me, usdso).call()
                vault_totals[pair] = raw / (10 ** int(mkt.get("quoteDecimals", 18)))
            except Exception as e:
                vault_totals[pair] = 0.0

        total_vault = sum(vault_totals.values())
        total_usdso = wallet_usdso + total_vault

        # Native gas-token balance (SOMI on mainnet, STT on testnet) — small
        # quantity used for tx gas. Expose so the dashboard can show "gas:".
        try:
            native_wei = self.w3.eth.get_balance(me)
            native_balance = native_wei / 1e18
        except Exception:
            native_balance = 0.0

        with self._lock:
            self._stats = {
                # Single source of truth for the wallet's USDso position.
                "agent_balance":  total_usdso,
                # Reserved "manual" budget is purely a planning device — there is
                # no separate manual wallet. Kept for backward compat but no
                # longer summed into total_value.
                "manual_balance": MANUAL_CAPITAL,
                # Real on-chain total of trade-able USDso (wallet + all pool vaults).
                # NOT inflated by a phantom manual reserve. Dashboard PnL = total_value - 50.
                "total_value":    total_usdso,
                "usdso_wallet":   wallet_usdso,
                "usdso_vaults":   vault_totals,
                "native_balance": native_balance,        # SOMI/STT for gas
                "last_refresh":   time.time(),           # C2: agent uses this to detect stale data
            }

    def summary(self) -> dict:
        with self._lock:
            return dict(self._stats)
