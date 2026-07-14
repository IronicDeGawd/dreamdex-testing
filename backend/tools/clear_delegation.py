#!/usr/bin/env python3
"""Clear a wallet's EIP-7702 delegation (authorize the zero address).

Run this to return a wallet to a plain EOA after atomic-mode trading. The
self-call guard already makes a delegated wallet safe to leave as-is, but
clearing is the clean end state. Uses config's wallet unless overridden.

Usage:
    DREAMDEX_ENV=mainnet python3 tools/clear_delegation.py
    ATOM_PRIVATE_KEY=0x... ATOM_ADDRESS=0x... python3 tools/clear_delegation.py
"""
import os, re, sys
sys.path.insert(0, "/app")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from trading.wallet import SomniaWallet

key = os.environ.get("ATOM_PRIVATE_KEY")
addr = os.environ.get("ATOM_ADDRESS")
if key and not key.startswith("0x"):
    key = "0x" + re.search(r"[0-9a-fA-F]{64}", key).group(0)
w = SomniaWallet(private_key=key, address=addr)

before = w.delegation_target()
print(f"wallet {w.address} delegated to: {before or '(none)'}")
if not before:
    print("already clear — nothing to do"); sys.exit(0)
h, r = w.clear_delegation()
print(f"clear tx {h} status={r.status}")
print(f"delegated to now: {w.delegation_target() or '(none)'}")
