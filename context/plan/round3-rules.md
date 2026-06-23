# Round 3 Rules — DreamDEX Dev Traders Program (captured 2026-06-24)

Source: https://somniachain.notion.site/DreamDEX-Dev-Traders-Program-Details-for-Devs-388b7df18a6b8041835bc621c74f98c0

## Format
- 14 days (2 weeks), equal starting capital, one leaderboard, ranked by volume.
- Alpha stress-test of the DreamDEX CLOB API/WebSocket/contracts.

## Starting conditions
- **$150 USDso** starting capital. **No top-ups or transfers** into the registered wallet — ever.
- **50 SOMI** for gas. To get more gas you must **convert your own USDso → SOMI**. This is the only gas you get.
- New wallet required (zero TX). Register before Day 1. Unregistered-wallet trades count for nothing.

## Scoring — THE KEY CHANGE
- Leaderboard shows: P&L, Total Volume (USDso), Tx count.
- **Ranking + milestones use PnL-weighted volume:**
  - **Effective Volume = Raw Volume × (1 + PnL%)**
  - PnL% is relative to 150 USDso start. Up 20% → ×1.2. Down 20% → ×0.8. **Wiped balance → 0.**
  - Goal explicitly: reward bots that trade *profitably*, not just turnover.

## Rules (verbatim intent)
1. Invite-only, one wallet/person, register before Day 1.
2. Equal $150 start, no top-ups/transfers.
3. Milestone rewards: **$25 per 500k USDso volume**.
4. Bots/automation allowed (scripts, CCXT, on-chain agents).
5. **No stablecoin pairs.** Eligible pairs: **BTC/USDso, ETH/USDso, SOMI/USDso**.
6. Gas: 50 SOMI given; convert own funds for more.
7. **Top 2** auto-qualify for next cohort + leaderboard rewards.
8. Must share trading bot repo at program end.
9. Team may remove participants for inappropriate behaviour.
10. Program can be cancelled anytime (rewards paid by performance to that day).
11. **>24h with no on-chain trading activity = automatic disqualification.**

## Resources
- Docs (NEW GitBook): https://metaversal.gitbook.io/dex/ld25g222WKDrLlJMcR41/
- Leaderboard (NEW): https://dreamdex-leaderboard-new.vercel.app/
- Contact: Anjali Singh / Emre (DevRel)

## Strategic implications (R2 playbook inversion)
- R2 = maximize raw volume, tolerate small bleed. R3 = volume × (1+PnL%) → **bleeding directly discounts volume; a wipe = 0.**
- "Hold and let rivals sprint dry" is now DANGEROUS: idle >24h = DQ. Must trade continuously for 14 days.
- Profit compounds: earn → bigger balance → bigger legs → more raw volume AND higher multiplier.
- Stablecoin-pair ban: any USDC.e-leg volume from R2 engine will NOT count. Must trade BTC/ETH/SOMI vs USDso.
- Must reserve some USDso for SOMI gas top-ups (no free gas refills).
- New goal = profitable market-making, not a capital-burning taker sprint.

## Docs delta (new GitBook https://docs.dreamdex.io/ld25g222WKDrLlJMcR41 — read 2026-06-24)

### 🌟 Maker yield (KEY PROFIT LEVER — new detail)
- Each resting order accrues `score = quantity × W × seconds`, where `W = exp(-(P_order − P_mid)² / 2σ²)` (Gaussian, W=1 at mid, decays with distance). σ not published → measure empirically.
- Payout: `total_yield × (your_score / total_score_all_makers)`. Yield source ≈ **3.3% APY** from Frax backing of USDso; protocol keeps nothing.
- Time-weighted in seconds; **auto-settled on-chain periodically — NO claim tx**.
- **No yield while the book is one-sided — must quote BOTH bid and ask.** IOC/FOK that never rest = excluded.
- `getMidpointEmaState()` on each pool returns the live EMA mid the algo uses → quote relative to it, not (bid+ask)/2.

### Pairs / pools (mainnet)
- WETH/USDso `0xa936da11B57b50A344e1293AAaE5232885ea2bDE`
- SOMI/USDso `0x035De7403eac6872787779CCA7CCF1b4CDb61379` (native SOMI base; sentinel `0x28f34DeFd2b4CB48d9eE6d89f2Be4Bc601694c00`)
- WBTC/USDso `0x25bfF6B7B5E2243424F38E75de7ab03C0522a5EA` (**NEW** — verify contest eligibility)
- USDC.e/USDso `0x47fD2f18426f67106DBaC82F6d21D446c5F2120b` (docs list live, but R3 rules ban stablecoin pairs → DON'T use for volume)
- Tokens: USDso `0x00000022dA000002656c64D9eA6011ea952D008A`, WETH `0x936Ab8C674bcb567CD5dEB85D8A216494704E9D8`
- SpotRouter `0x780672aDA90Ed7cf2C3E8B70DBa87A19d584c8B0` (multi-hop swapExactIn/Out — useful for USDso→SOMI gas refuel routing), SpotPoolRegistry `0xB601bc1099B040E4882089D94690F7C38AF4CCD2`

### Order types / mechanics
- orderType: 0=GTC(vault-only), 1=FOK, 2=IOC, 3=PostOnly(vault-only). Market-buy with wallet funding unsupported → use IOC limit.
- placeOrder selector `0x4e978373` confirmed. selfMatchingOption=1 (cancelMaker) confirmed. expireTimestampNs use `(now+3600)*1e9`.
- Fees: **0% maker / 0% taker** confirmed, all pairs.
- SOMI native buys need gas ≥ 5,000,000 (confirms §7a). ERC20 transfers gas 2,000,000.
- Gas sponsorship ("SOMI + stablecoin pairs") = docs TODO, UNCONFIRMED → measure SOMI burn empirically per pair.

### API
- HTTP `https://api.dreamdex.io/v0` (SIWE nonce→login→JWT). WS `wss://api.dreamdex.io/v0/ws/public` (no auth, ping 30s). Symbol format `WETH:USDso`.
- Docs now OFFICIALLY say the HTTP API alone can't place orders — client must broadcast its own tx → **confirms our direct-RPC engine is the right path.**
- Useful endpoints: `GET /v0/markets` (pairs+tick/lot/minQty), `GET /v0/orderbooks?symbols=&depth=`, `GET /v0/markets/{sym}/vault/balance?walletAddress=`.
- Official CCXT fork exists but JS-only (`github:somnia-chain/ccxt#add-dreamdex-exchange`) → not usable from our Python engine. No MCP/AGENTS.md.
- Re-test in R3: `getOwnOpenOrders` (docs say it returns data now; R2 it returned empty). New helpers: `getAutoPullRequirement()` (exact wallet spend pre-flight), `getMidpointEmaState()`.
