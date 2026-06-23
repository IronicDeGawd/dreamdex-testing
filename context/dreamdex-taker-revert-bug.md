# Bug report — `placeTakerOrderWithoutVault` reverts unconditionally

> For the DreamDEX devs. Prepared 2026-06-16. All artifacts below are real and
> replayable. Caller is our contest wallet (trader-9).

## Summary
Every call to **`placeTakerOrderWithoutVault(bool,uint64,uint256,uint256,uint64,uint8,uint8,address,uint96)`**
on the WETH:USDso pool now **reverts with empty return data** (`execution
reverted`, `0x` — a bare `require`/revert with no reason string; on-chain
`status=0`). This method worked earlier in the contest (we placed 100k+ orders
through it) and began reverting universally on 2026-06-16. The vault-based
`placeOrder` path is unaffected and still works.

## Environment
| | |
|---|---|
| Chain | Somnia mainnet, chainId **5031** |
| Pool (to) | `0xa936da11B57b50A344e1293AAaE5232885ea2bDE` (WETH:USDso) |
| Base / Quote | WETH / USDso |
| Caller (from) | `0xF4c825F3C2970153d78B407CF190861dd4E2b905` (trader-9) |
| Observed at | ~block 335,436,430 – 335,446,376, 2026-06-16 |

## Real on-chain reverted transaction (for backend tracing)
| | |
|---|---|
| **tx hash** | **`0xad98faecca7e9ee682d184c8a8874b5d6296b3b53b370e49770c209601114edd`** |
| status | **0 (reverted)** |
| block | 335,446,376 |
| gasUsed | **68,754** (of 2,000,000 limit) |
| from | `0xF4c825F3C2970153d78B407CF190861dd4E2b905` |
| to | `0xa936da11B57b50A344e1293AAaE5232885ea2bDE` |
| price (raw) | 1795510000000000000000 |
| qty (raw) | 1000000000000000 (0.001 WETH, = minQty) |

The very low gasUsed (~69k) means the revert happens **early** in the function —
consistent with a top-level `require`/gate rather than a deep matching failure.

## Replayable `eth_call` (no gas)
- **selector:** `0x1c792779`
- **args:** `isBid=true, userData=0, price=1794040000000000000000, quantity=1500000000000000, expireTimestampNs=1781672128000000000, orderType=2, selfMatchingOption=1, builder=0x0, builderFeeBpsTimes1k=0`
- **result:** `('execution reverted', '0x')`
- **calldata:**
```
0x1c79277900000000000000000000000000000000000000000000000000000000000000010000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000061414e72c4c9cc00000000000000000000000000000000000000000000000000000005543df729c00000000000000000000000000000000000000000000000000018b9c55c57a780000000000000000000000000000000000000000000000000000000000000000002000000000000000000000000000000000000000000000000000000000000000100000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000
```

## Preconditions ruled out (all satisfied — none is the cause)
- **Funds:** wallet held USDso ≈ $10, native SOMI ≈ 9.5, WETH 0. Even min-size (0.001) reverts.
- **Allowance:** USDso and WETH allowance to the pool both max (`1.157e59`).
- **Book crossable:** live bid/ask present; ask ≈ 1793.54 with depth 0.0518 WETH (~30× test size); our buy price is above the ask, so it should cross.
- **Gas:** ample; it reverts even in pure `eth_call`.

## Combinations tried — all revert with identical empty `0x`
- `orderType ∈ {0,1,2,3}` × `selfMatchingOption ∈ {0,1}`
- `quantity ∈ {0.001 (minQty), 0.0015, 0.005, 0.0055}`
- `slippage ∈ {50 ticks ($0.50), 200 ticks ($2.00)}`
- BUY side (SELL untestable from a flat WETH balance)

## Evidence it is method-specific (not pool-wide, not account-specific)
- **t3** (`0x8f0A24AE910D4B89C4422b6884d71739DBC1ec86`) — the only other trader on
  `placeTakerOrderWithoutVault` — is **frozen** (volume stuck at 1,065,462).
- **t2** (`0x43876c4668Ac0207F000C387eAf1eC8884f26BC7`) and t4, who use the
  **vault-based `placeOrder`** (selector `0x4e978373`), are **still trading**.
- The **REST API path works for us**: `POST /v0/markets/WETH:USDso/orders` with
  `orderType=immediateOrCancel` / `postOnly` builds a `placeOrder` tx that
  succeeds — we deposited to the vault and rested/filled an order the same hour.

## Question for the devs
Was `placeTakerOrderWithoutVault` intentionally disabled/gated on this pool
(similar to the earlier stablecoin-pair change)? If yes, please confirm the
intended replacement is `placeOrder`. If it should still work, the tx hash and
calldata above reproduce the revert directly.

## Our mitigation (already done, for context)
Switched our bot off `placeTakerOrderWithoutVault` onto the working
`placeOrder` path (no-bleed maker live now; a taker variant via the API IOC path
is prepped).
