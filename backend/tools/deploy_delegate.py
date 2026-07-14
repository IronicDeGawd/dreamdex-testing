#!/usr/bin/env python3
"""Deploy the RoundTrip7702 delegate contract, print its address.

The contract is stateless and permissionless to deploy — the security guard is
in the code (self-call only), not in who deploys it. Any funded wallet can
deploy; wallets then delegate to the resulting address.

Usage (inside the agent container or a venv with web3 + config on path):
    DREAMDEX_ENV=mainnet DEPLOY_KEY=0x<hex> python3 tools/deploy_delegate.py
    # or rely on config's PRIVATE_KEY (MAINNET_/TESTNET_PRIVATE_KEY env)
"""
import os, re, sys
sys.path.insert(0, "/app")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from web3 import Web3
from eth_account import Account
import config
from trading.delegate import ROUNDTRIP_INITCODE

key = os.environ.get("DEPLOY_KEY") or config.PRIVATE_KEY
if not key:
    sys.exit("no deploy key — set DEPLOY_KEY=0x... or config PRIVATE_KEY env")
if not key.startswith("0x"):
    key = "0x" + re.search(r"[0-9a-fA-F]{64}", key).group(0)

w3 = Web3(Web3.HTTPProvider(config.SOMNIA_RPC, request_kwargs={"timeout": 30}))
acct = Account.from_key(key)
print(f"deployer {acct.address} chain {config.CHAIN_ID} "
      f"gas {w3.eth.get_balance(acct.address)/1e18:.4f}")

gp = w3.eth.gas_price
# Somnia's gas accounting runs high (ERC20 ~2M, orders ~5M vs mainnet norms), so
# a contract deploy needs far more than a standard chain — 800k ran out of gas.
deploy_gas = int(os.environ.get("DEPLOY_GAS", "3000000"))
tx = {
    "from": acct.address, "nonce": w3.eth.get_transaction_count(acct.address, "pending"),
    "data": ROUNDTRIP_INITCODE, "value": 0, "gas": deploy_gas,
    "maxFeePerGas": int(gp * 2), "maxPriorityFeePerGas": 0, "chainId": config.CHAIN_ID,
}
h = w3.eth.send_raw_transaction(Account.sign_transaction(tx, key).raw_transaction)
print("deploy tx:", h.hex() if hasattr(h, "hex") else h)
r = w3.eth.wait_for_transaction_receipt(h, timeout=120)
if r.status != 1:
    sys.exit(f"deploy FAILED status={r.status}")
print(f"DELEGATE DEPLOYED: {r.contractAddress}  (gasUsed {r.gasUsed})")
print(f"code bytes: {len(w3.eth.get_code(r.contractAddress))}")
