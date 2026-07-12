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
import threading
import requests
from eth_account import Account
from eth_account.messages import encode_defunct
from web3 import Web3
from web3.providers.rpc import HTTPProvider
try:
    from config import SOMNIA_RPCS
except ImportError:  # older config without a failover pool
    from config import SOMNIA_RPC as _RPC
    SOMNIA_RPCS = [_RPC]
from config import SOMNIA_RPC, CHAIN_ID, MY_ADDRESS, PRIVATE_KEY as _CONFIG_KEY


class FailoverHTTPProvider(HTTPProvider):
    """HTTPProvider that rotates across multiple RPC endpoints.

    A single flaky node (timeout / connection reset / 5xx) is the top cause of
    the volume engine's 5-consecutive-failure breaker tripping. On a transport
    error we advance to the next endpoint and retry the SAME request, so the
    caller only sees an error if EVERY node fails. JSON-RPC errors (revert,
    nonce too low) come back as a normal response dict — those are real chain
    answers, identical on every node, so they pass straight through without
    failover. Sticks to the last good endpoint until it too fails."""

    def __init__(self, endpoint_uris, **kwargs):
        self._uris = list(endpoint_uris) or [None]
        self._idx = 0
        super().__init__(self._uris[0], **kwargs)

    def make_request(self, method, params):
        last_exc = None
        n = len(self._uris)
        for attempt in range(n):
            i = (self._idx + attempt) % n
            self.endpoint_uri = self._uris[i]
            try:
                resp = super().make_request(method, params)
                if i != self._idx:
                    print(f"[wallet] RPC failover → {self._uris[i]}")
                self._idx = i  # stick here until it fails
                return resp
            except Exception as e:  # transport-level only; RPC errors return a dict
                last_exc = e
                continue
        raise last_exc


class SomniaWallet:
    def __init__(self, private_key: str | None = None, address: str | None = None):
        # Optional overrides let a second wallet (e.g. the profit-lane wallet)
        # run alongside the default config wallet without touching globals.
        self.address     = address or MY_ADDRESS
        self.chain_id    = CHAIN_ID
        self.private_key = private_key or _CONFIG_KEY  # set via TESTNET/MAINNET_PRIVATE_KEY env var
        # Failover across the RPC pool; short per-request timeout so a hung
        # node rotates fast instead of blocking a whole trip into the breaker.
        self.w3          = Web3(FailoverHTTPProvider(SOMNIA_RPCS, request_kwargs={"timeout": 15}))
        # H3 fix: local nonce counter. Prevents multi-tx flows (approve → deposit → order)
        # from racing on `eth_getTransactionCount("pending")` when the RPC's pending pool
        # hasn't propagated between calls — that race silently drops the second tx.
        self._nonce: int | None = None
        self._nonce_lock = threading.Lock()

        if not self.private_key:
            print(f"[wallet] ⚠️  Wallet key not set — set {'MAINNET' if 'mainnet' in SOMNIA_RPC else 'TESTNET'}_PRIVATE_KEY")

    # ── Nonce management (H3) ─────────────────────────────────────────
    def reserve_nonce(self) -> int:
        """Return the next nonce to use AND increment the cached counter.
        Falls back to chain query on first use or after a reset."""
        with self._nonce_lock:
            if self._nonce is None:
                self._nonce = self.w3.eth.get_transaction_count(self.address, "pending")
            n = self._nonce
            self._nonce += 1
            return n

    def reset_nonce(self):
        """Force a fresh chain query on the next reserve_nonce() call.
        Use after a tx fails so we don't burn nonces on dropped txs."""
        with self._nonce_lock:
            self._nonce = None

    # ── Gas pricing (M3) ──────────────────────────────────────────────
    def _gas_fields(self) -> dict:
        """Returns EIP-1559 fields if the node supports them, else legacy gasPrice.
        EIP-1559 lets txs compete properly under congestion."""
        try:
            base_fee = self.w3.eth.get_block("latest").get("baseFeePerGas")
            if base_fee:
                priority = self.w3.eth.max_priority_fee
                return {
                    "maxFeePerGas":         int(base_fee * 2 + priority),  # generous; unused refunded
                    "maxPriorityFeePerGas": int(priority),
                }
        except Exception:
            pass
        return {"gasPrice": self.w3.eth.gas_price}

    # ── Send a pre-built unsigned tx dict returned by DreamDEX API ────
    def order_gas_limit(self, tx: dict, min_gas: int = 0) -> int:
        """Gas limit for an order / cancel tx, shared by the broadcast path and
        the eth_call sim so the sim can never pass a limit the broadcast would
        fail. The 5M floor clears the pool's InsufficientGasForPayout guard
        (0x782b2567): a taker IOC that sweeps depth spends ~1.7M reaching the
        payout check, and the guard then needs several M more of headroom —
        3M is not enough (DreamDEX docs §7a). gasUsed stays low (~1.7M), so the
        higher limit does not raise cost; it only has to pass the guard."""
        api_gas = int(tx.get("gasLimit", 300_000))
        floor = max(5_000_000, int(min_gas or tx.get("min_gas", 0)))
        return max(floor, int(api_gas * 2))

    def send_unsigned_tx(self, tx: dict, min_gas: int = 0) -> str:
        """
        Sign and broadcast a tx dict like:
          { "to": "0x...", "data": "0x...", "value": "0", "gasLimit": "250000" }
        Returns tx hash string.

        `min_gas` (or tx["min_gas"]) raises the gas floor further for calls that
        need even more headroom than the 5M order default.

        R2: auto-recovers from `nonce too low` once by re-syncing from chain.
        That happens when another process (docker exec, manual REPL) consumed
        nonces in parallel with the long-lived server wallet.
        """
        gas = self.order_gas_limit(tx, min_gas)

        def _build_and_send(n: int) -> str:
            tx_fields = {
                "to":      Web3.to_checksum_address(tx["to"]),
                "data":    tx.get("data", "0x"),
                "value":   int(tx.get("value", 0)),
                "gas":     gas,
                "nonce":   n,
                "chainId": self.chain_id,
                **self._gas_fields(),
            }
            signed_tx = Account.sign_transaction(tx_fields, self.private_key)
            sent = self.w3.eth.send_raw_transaction(signed_tx.raw_transaction)
            return sent.hex()

        nonce = self.reserve_nonce()
        try:
            return _build_and_send(nonce)
        except Exception as e:
            msg = str(e).lower()
            if "nonce too low" in msg or "0x04" in msg:
                # External nonce consumption — re-sync once and retry.
                print(f"[wallet] nonce drift detected (used={nonce}); resyncing and retrying once")
                self.reset_nonce()
                nonce2 = self.reserve_nonce()
                return _build_and_send(nonce2)
            # Other failures: burn nonce, force fresh sync next time.
            self.reset_nonce()
            raise

    def wait_for_receipt(self, tx_hash: str, timeout: int = 120) -> dict:
        """L2 fix: bumped 30→120s for mainnet congestion. A timeout here returns
        an error to the caller but the tx may still confirm — caller should
        save the hash and re-check next tick."""
        return self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=timeout)

    def sign_and_send(self, tx: dict) -> str:
        """Sign + broadcast a pre-built tx dict (from a contract .build_transaction()
        call). On nonce drift, retries once with a fresh nonce. Caller should
        ensure tx already contains nonce + gas fields."""
        try:
            signed = Account.sign_transaction(tx, self.private_key)
            sent = self.w3.eth.send_raw_transaction(signed.raw_transaction)
            return sent.hex()
        except Exception as e:
            msg = str(e).lower()
            if "nonce too low" in msg or "0x04" in msg:
                print(f"[wallet] sign_and_send nonce drift; resyncing and retrying once")
                self.reset_nonce()
                tx["nonce"] = self.reserve_nonce()
                signed = Account.sign_transaction(tx, self.private_key)
                sent = self.w3.eth.send_raw_transaction(signed.raw_transaction)
                return sent.hex()
            self.reset_nonce()
            raise

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
