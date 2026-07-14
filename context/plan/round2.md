# Round 2 Plan — DreamDEX Contest (max volume, bounded drain)

> Created 2026-06-08. Contest restarted; $100 USDso funded, volume is the sole metric,
> +25 USDso per 500k volume. Capital refunded at end.

## Goal & economics
- **Volume is the only scored metric.** +25 USDso per 500k volume milestone.
- Capital refunded at end → real goal: survive without hitting floor, push max volume,
  let bonuses extend runway.
- **Round 1's fatal inefficiency:** ~$3 legs → 63k txs → ~$22 gas for 205k volume.
  **Round 2 fix: big legs (~$40) → ~25× fewer txs → far less gas per unit volume.**
  Volume-per-tx = leg size; depth isn't the limit (MM seeds deep), capital is.

## Trading pair — USDC.e:USDso (decided 2026-06-08, live spread check)
| Pair | Spread | Top depth | Verdict |
|------|-------:|-----------|---------|
| **USDC.e:USDso** | **0.0200%** | $115 / $84 | ✅ tightest + deepest + stable |
| WBTC:USDso | 0.0202% | empty | unusable |
| WETH:USDso | 0.0208% | $84 / $67 | thin for $40 legs |
| SOMI:USDso | 0.0882% | deep | 4.4× wider spread |

USDC.e: lowest spread, deepest top-of-book ($40 legs fill in one level), stablecoin
(near-zero slippage between legs), 99.5% fill rate in round-1 data.

## Architecture — 3 lanes
| Wallet | Capital | Role | Leaderboard? |
|--------|---------|------|:---:|
| **H** — hot/leaderboard `0xF4c8…2b905` | $60 USDso + 23.6 SOMI gas | Volume burst, ~$40 legs | ✅ |
| **P** — profit (fresh key, server-only) | $40 USDso + ~1.5 SOMI gas | Maker spread-capture, never bleeds | ❌ |

Hot wallet key `0x40db…4f3f` is **SAFE** — local transcripts only, never public. No rotation needed.

### Lane 1 — Volume burst (Wallet H, $60)
- `direct_burst.py`, wallet-funded IOC, **~$40 legs** on USDC.e:USDso.
- skip-sim, 2M gas, 3-tick slippage. Clean keepalive cron (restart-if-dead).
- **No locked reserve** — the $40 in P is the de-facto reserve (can't bleed).
- **Safety floor:** burst halts if H's USDso < ~$15 (catches round-1 floor-breach bug class).
- **Drain-rate alarm:** pause + alert if H falls faster than expected $/hour.

### Lane 2 — Profit (Wallet P, $40) — NEVER bleeds
Structurally incapable of a losing trade:
1. **Maker-only.** PostOnly limit (`normalOrder`). Reject any IOC/market in code.
2. **Never cross spread.** BUY ≤ best bid, SELL ≥ best ask. Never lift ask / hit bid.
3. **Cost-basis ledger.** SELL must be ≥ cost × (1 + min margin). No profitable exit → HOLD forever. No stop-loss ever.
4. **Orders may sit indefinitely** — acceptable. Cancel/repost only to follow book on same side at no-worse price.
5. **Capital firewall.** P never pulls from H; bounded to its $40.
6. **Pair:** ERC20-base (USDC.e or WETH), NOT native SOMI (native maker SELL stuck — vault native withdraw reverts `0x734b5f70`).
7. **End-game:** sweep P's USDso (principal + profit) → H, burst into extra volume.

**Honest expectation:** round-1 maker experiment got ZERO fills behind the MM. Likely P sits
mostly idle = $40 stays safe; occasional spread capture on market moves. Never bleeds.

## Execution phases
1. **Phase 1 — wallets:** generate Wallet P fresh key on server; split H=$60 / P=$40; send ~1.5 SOMI gas to P.
2. **Phase 2 — volume burst:** configure direct_burst for USDC.e:USDso, $40 legs; launch + keepalive cron.
3. **Phase 3 — profit bot:** maker-only bot on P with all no-bleed guards; launch.
4. **Phase 4 — monitor:** dashboard, drain alarm, gas watch, milestone tracking.
5. **Phase 5 — endgame:** sweep P → H, final burst, teardown.

## Bonus mechanics (confirmed 2026-06-08)
- **+25 USDso claimable on-demand at each 500k volume milestone** (during the run) → reinvestable flywheel.
- Action: track cumulative volume; as each 500k is crossed, ping emrey to claim +25, then funnel it into Wallet H for bigger legs / longer runway.
- Wallet P address: `0x75B649620c93D7b405018872D709f1fDd1cbBC0F` (fresh, server-only key).

## Round-1 gotchas still in force
- Wallet-funded IOC only (vault-funded never fills on mainnet).
- `expireTimestampNs` must be future ns, never 0 (silently rejected).
- USDC.e = 6 decimals, minQty $1.
- `selfMatchingOption = 1` (CancelMaker) so own resting orders don't abort the IOC.
- Native SOMI vault sentinel `0x28f34De…`, not address(0) — but we're avoiding native SOMI anyway.
