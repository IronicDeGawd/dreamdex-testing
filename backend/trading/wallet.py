# backend/trading/wallet.py
"""
SomniaWallet — signs and broadcasts EVM transactions on Somnia.

Two use paths (matching DreamDEX docs):
  A) HTTP-API path:  call /v0/markets/{symbol}/orders → get unsigned tx → sign → broadcast
  B) Direct-contract path: build tx ourselves, sign, broadcast

We use Path A for everything that the REST API supports (place, cancel).
Direct contract calls are used as fallback / for vault deposit/withdraw.
"""
import os
import time
import json
import requests
from eth_account import Account
from eth_account.messages import encode_defunct
from web3 import Web3
from config import SOMNIA_RPC, CHAIN_ID, MY_ADDRESS, PRIVATE_KEY as _CONFIG_KEY


class SomniaWallet:
    def __init__(self):
        self.address     = MY_ADDRESS
        self.chain_id    = CHAIN_ID
        self.private_key = _CONFIG_KEY  # your Ethereum wallet private key (0x-prefixed), set via TESTNET_PRIVATE_KEY or MAINNET_PRIVATE_KEY
        self.w3          = Web3(Web3.HTTPProvider(SOMNIA_RPC))
        self._nonce_cache: int | None = None

        if not self.private_key:
            print(f"[wallet] ⚠️  Wallet key not set — set {'MAINNET' if 'mainnet' in SOMNIA_RPC else 'TESTNET'}_PRIVATE_KEY")

    # ── Send a pre-built unsigned tx dict returned by DreamDEX API ────
    def send_unsigned_tx(self, tx: dict) -> str:
        """
        Sign and broadcast a tx dict like:
          { "to": "0x...", "data": "0x...", "value": "0", "gasLimit": "250000" }
        Returns tx hash string.
        """
        nonce = self.w3.eth.get_transaction_count(self.address, "pending")
        # The API's gasLimit estimate is often within ~5K of the matcher's
        # actual consumption. SpotPool matcher also has internal gas-headroom
        # checks (cf. gasBufferBps in stop registry) — observed custom revert
        # at gasUsed=985K with gasLimit=1M. Use 3M floor + 2x buffer; unused
        # gas is refunded so generous is cheap.
        api_gas = int(tx.get("gasLimit", 300_000))
        gas = max(3_000_000, int(api_gas * 2))
        signed_tx = Account.sign_transaction(
            {
                "to":       Web3.to_checksum_address(tx["to"]),
                "data":     tx.get("data", "0x"),
                "value":    int(tx.get("value", 0)),
                "gas":      gas,
                "gasPrice": self.w3.eth.gas_price,
                "nonce":    nonce,
                "chainId":  self.chain_id,
            },
            self.private_key,
        )
        sent = self.w3.eth.send_raw_transaction(signed_tx.raw_transaction)
        return sent.hex()

    def wait_for_receipt(self, tx_hash: str, timeout: int = 30) -> dict:
        return self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=timeout)

    # ── SIWE auth helpers ─────────────────────────────────────────────
    def sign_message(self, message: str) -> str:
        """Sign an arbitrary string (used for SIWE login). Returns 0x-prefixed hex."""
        msg = encode_defunct(text=message)
        signed = Account.sign_message(msg, self.private_key)
        # API requires 0x-prefixed hex — signed.signature is a HexBytes object
        hex_sig = signed.signature.hex()
        return hex_sig if hex_sig.startswith("0x") else f"0x{hex_sig}"

    # ── Balance helpers ───────────────────────────────────────────────
    def native_balance(self) -> float:
        """STT / SOMI native balance in human units."""
        raw = self.w3.eth.get_balance(self.address)
        return raw / 1e18

    def erc20_balance(self, token_addr: str, decimals: int = 18) -> float:
        erc20_abi = [{"name":"balanceOf","type":"function","stateMutability":"view",
                      "inputs":[{"name":"account","type":"address"}],
                      "outputs":[{"name":"","type":"uint256"}]}]
        contract = self.w3.eth.contract(
            address=Web3.to_checksum_address(token_addr), abi=erc20_abi
        )
        raw = contract.functions.balanceOf(self.address).call()
        return raw / (10 ** decimals)
