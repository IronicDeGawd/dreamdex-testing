# Plan — Algo Arena Adaptation (vol modes v2 + profit maker v2 + hybrid)

> Status: APPROVED 2026-07-12 with decisions: **balanced hybrid** (maker 24/7 + taker
> top-up for raffle saturation + rank defense), **FRESH wallet** for the Arena (fund +
> gas + register before week-1 trades), R4 Batch-1 control-side work started same day.
> Source of rules: https://dreamdex.io/algo-arena (fetched 2026-07-12).
> R4 engine untouched by this plan (see context/plan/r4-improvements.md for that).

## 1. The new rules (verified from the page)

| Rule | Value |
|---|---|
| Duration | **8 weeks, July 13 → Sept 6, 2026** (week 1 starts TOMORROW) |
| Prize pool | $10,000 USDso total; **$1,250/week** = $1,000 leaderboard + $250 raffle |
| Score | **`Volume × Pair Boost × Challenges`** — NO PnL term |
| Week window | Monday 00:00 UTC → Sunday 23:59 UTC; leaderboard resets weekly |
| Pair boosts | 1.2×–1.5× on selected pairs, changes weekly, announced on dreamDEX/Somnia channels |
| Challenges | ad-hoc weekly objectives → extra points / volume credit (no examples published) |
| Raffle | 1 ticket per **$2,500** traded, **cap 100 tickets/wallet** → saturates at **$250k/week** |
| Raffle prizes | $100 / $60 / $40 / $30 / $20 for draws 1–5 |
| Leaderboard split | $1,000/week — per-rank split NOT published |
| Eligibility | wallet must be **registered before trades count** ("registration coming soon") |
| Banned | wash trading, self-dealing, manipulation → DQ without notice |
| NOT in rules | no PnL multiplier, no maker/taker distinction, no min balance, no top-up ban |

## 1a. Fair-play rules (Discord post, added 2026-07-13) — STRATEGY-CRITICAL

dreamDEX runs automated wash-trade detection in the Arena. Two flagged patterns
(+ unspecified "additional filters"); flagged accounts are reviewed and their
volume REMOVED:
1. **Short-window round-trips** — buying and selling nearly the same amount
   within 30 seconds. This is `volume_climb`'s exact signature.
2. **Near-flat cycles** — building a position and unwinding it almost
   completely back to zero, 3+ times. This catches round-trip churn at ANY
   speed — slowing down does NOT escape it; the flag is position SHAPE.

Consequences (supersede parts of the plan below):
- **Taker as a volume engine is DEAD for the Arena.** Mode A survives only for
  utility trades (maker inventory rebalancing, gas top-ups, challenge tasks) —
  a handful of non-cyclic trades, never a churn loop. The Mode C "taker top-up
  when behind pace" trigger is removed; if behind, widen maker caps/legs or
  quote more pairs instead.
- **The maker carries ALL volume** — and must not look like pattern 2 itself:
  maker_v2 now holds a standing inventory floor (MAKER2_INV_FLOOR_PCT, default
  30% of cap — the position oscillates in a band, never flushes to zero) and
  jitters quote sizes (MAKER2_LEG_JITTER_PCT) so buys/sells don't mirror.
  Stop-loss and shutdown still flatten fully (risk beats shaping).
- **Competitive upside:** every farmer rival running 30s round-trips gets their
  volume stripped. The effective leaderboard shrinks toward real traders —
  patient maker volume becomes a top-tier score. Expect week-1 farmer flow to
  feed our maker until the flags catch them.
- Re-verify enforcement from the week-1 leaderboard (do flagged accounts
  actually lose volume?) before loosening any of this.

## 2. What changes strategically vs R4

1. **PnL multiplier is GONE.** The R3/R4 "inventory is poison / end flat" doctrine no longer
   affects SCORE. Inventory is still a capital risk (we can lose real money), but not a
   score penalty. → The maker can hold small working inventory freely.
2. **Weekly resets** → 8 independent sprints, not one marathon. Consistency across 8 weeks
   beats one heroic week. Capital preservation matters MORE (must survive 8 weeks), score
   endurance matters LESS (each Monday is a clean slate).
3. **Pair boosts flip the rotation objective.** Old: pick the pair with the lowest effective
   toll. New: pick the pair with the lowest **toll per score point = toll ÷ boost**. A 1.5×
   boosted pair with 0.10/1k toll beats an unboosted pair at 0.08/1k.
4. **No top-up ban** stated → capital can be scaled. Bigger legs = more volume/hour AND more
   maker capital. (Confirm with organizers before relying on it — the R4 habit dies hard.)
5. **Public release = real organic flow.** In alpha, books were bots-only and maker fills were
   scarce. With real users, resting quotes get hit by genuine takers → spread capture pays
   AND every maker fill is contest volume. **Maker volume counts identically to taker volume.**
6. **Wash-trading optics.** Our taker round-trips cross the REAL book (real counterparty, real
   risk) — legitimate per the bot-kit's own CONTRIBUTING.md framing we used at Builder
   Session 1. But high-frequency two-way taker flow can pattern-match "volume farming" to a
   human reviewer. Maker volume is unimpeachable. → shift the volume MIX toward maker,
   randomize taker leg sizes/timing, and NEVER run maker+taker on the same pair at once
   (protocol selfMatchingOption=1 cancels self-crosses, but a same-wallet cross attempt still
   looks like self-dealing intent). Multi-wallet raffle farming = sybil = do NOT do it.

## 3. Target architecture — three modes, one shared core

All modes reuse the R4-hardened shared layer (`trading/wallet.py` + `trading/dreamdex.py`):
5M gas floor + honest sim, allowance check-first, RPC failover, true-capital reads.

```
                 control API (:8787)  ← weekly /boosts config, /launch per mode
                        │
        ┌───────────────┼────────────────┐
   Mode A: vol v2   Mode B: maker v2   Mode C: hybrid scheduler
   (taker, boost-   (PostOnly 2-sided  (maker 24/7 base-load;
    aware rotation)  spread capture)    taker top-up if behind pace)
```

### Mode A — `volume_climb` v2 (boost-aware taker)
- **Boost config:** `WEEKLY_BOOSTS` (JSON pair→multiplier), settable at runtime via a new
  control endpoint `POST /boosts` (no image rebuild — engine re-reads from a state file each
  rotation). Manual weekly entry from announcements; there is no boost API.
- **Rotation ranking:** rank pairs by `effective_toll ÷ boost` instead of raw effective toll.
  Keep the depth gate, implied-toll gate, and realized breaker exactly as-is.
- **Weekly window awareness:** engine tracks the Mon 00:00 UTC window; volume counter,
  target, and bleed budget are per-week. New params: `weekly_target`, `weekly_bleed_cap`.
- **Raffle pacing:** first-class target of **$250k/week** (100 tickets) when budget allows;
  after saturation, taker throttles to whatever leaderboard pace requires and lets maker
  carry the rest.
- **Anti-pattern hygiene:** jitter leg size ±20% and inter-trip sleep; cap trips/hour.

### Mode B — maker v2 (profit + volume) — modernize `agent_v3/`
Base asset: `agent_v3/maker.py` (PostOnly two-sided, no-bleed sell ≥ cost+margin, trend
guard, limit-protected stop-loss, per-pair inventory caps) — testnet-validated, ran on R3
mainnet. Work needed:
1. **Re-base on the shared layer:** verify maker paths go through `send_unsigned_tx`
   (5M floor) and the fixed approval logic; kill any duplicated old gas/approve code.
2. **True-capital + on-chain-authoritative inventory** (Inventory.sync_base already does
   ERC20; keep SOMI excluded or accept tracker risk — decide per measured spread).
3. **Re-measure the books post-public-launch** (this is the make-or-break input): capturable
   spread per pair, organic fill rate at the touch, adverse-selection cost. Alpha numbers
   (SOMI ~10bps, WETH 1.4bps) are stale. 24–48h of passive measurement in week 1.
4. **Score-aware quoting:** prefer boosted pairs when spread economics are comparable.
5. **Maker yield program:** re-verify it survived public launch (was: pool APY, score =
   qty × Gaussian-weight@EMA-mid × seconds, both sides resting; EMA selector `0x2d1590a0`,
   clamp to ±10% of book mid). If live, our at-touch two-sided quoting already sits on the
   yield peak — collect it for free, don't tighten quotes to chase it (the $5 whipsaw lesson).
6. **Strategist stays off** (`STRATEGIST_ENABLED=false`); Telegram monitor stays (alerts).
7. **PnL truth:** weekly report = on-chain net worth delta (wallet+vault+inventory@mid),
   never the fill tracker (R3 desync lesson).

### Wallet architecture — ONE owner + TWO operator keys (decided 2026-07-13)
User decision, superseding the earlier "maker wallet + taker wallet" idea:
**one funded, registered OWNER wallet** (capital in wallet/vault, key goes cold
after setup) and **two OPERATOR keys** — one for the maker, one for the taker —
placing orders on the owner's behalf via `placeOrderFor` (mechanism proven
on-chain during the bot-kit work, issue #7 / PR #8).

Why this wins:
1. **One leaderboard row** — all maker + taker volume aggregates on the owner.
2. **Protocol-enforced wash-safety** — same-account self-cross is impossible
   (`selfMatchingOption=1` CancelMaker cancels instead of filling); the
   two-wallet design only had policy-level protection against a DQ scenario.
3. **Independent nonces** — two operator EOAs = maker and taker can run
   CONCURRENTLY at last (the old one-at-a-time rule was a nonce constraint).
4. **Bots never hold funds** — operators can trade for the owner but not
   withdraw; a leaked bot key can bleed via bad trades at worst.

Pre-build gates (in order):
- **G1 — attribution test:** one tiny `placeOrderFor` trade; confirm the volume
  lands on the OWNER's leaderboard entry, not the operator's. If it credits the
  operator, this architecture is dead — fall back to two wallets. TEST FIRST.
- **G2 — operator approval flow:** owner's one-time on-chain operator grant per
  key; each operator EOA funded with its own SOMI gas.
- **G3 — direct-contract maker path:** operator orders bypass the REST
  order-builder → maker needs placeOrderFor calldata for PostOnly + operator
  cancel + fill detection keyed to the owner's open orders (prior art:
  direct_burst calldata builder + kit-fix branch).
- Pair-disjointness stays REQUIRED operationally: CancelMaker prevents the
  fill, but every taker cross on the maker's pair still cancels our resting
  quote (wasted gas + lost queue position). Maker quotes the boosted pair,
  taker runs elsewhere.

### Mode C — hybrid scheduler
- Maker runs 24/7 as base-load volume+profit. A small scheduler (control-side, host-run)
  compares **week-to-date volume vs pace** each hour: `pace = weekly_target ×
  elapsed_week_fraction`. If behind by >X% after Thursday, it launches taker top-up on the
  **boosted pair the maker is NOT quoting**; stops taker when back on pace.
- Hard rule enforced in engine_manager: maker and taker never share a pair concurrently.
- `engine_manager` today has a single-engine lock → extend to **named slots**
  (`maker`, `taker`) with the pair-disjointness check.

### Ops & dashboard
- **Weekly runbook:** Mon — read boost announcement → `POST /boosts` → set weekly_target;
  Sun 23:30 UTC — flatten maker inventory (optional; score doesn't care, capital does),
  snapshot; Wed — verify prize arrival.
- **Dashboard adds:** week countdown, week-to-date volume × boost = score, raffle tickets
  (vol/2500 capped 100), boost editor, maker PnL (on-chain), pace-vs-target bar.
- **Registration:** register the wallet the moment registration opens; trades before
  registration DON'T COUNT — this gates everything.

## 4. Economics sanity check
- Taker cost at current calibration: ~$0.056–0.15/1k → $250k/week costs **$14–$37/week**
  if done purely taker. Raffle EV alone can cover this only in a small cohort; leaderboard
  split unknown → decide weekly aggression from the cohort panel (already built).
- Maker at positive capture: volume is **negative cost** (paid to trade). Every $ of volume
  shifted from taker to maker improves both PnL and optics. Throughput is the open
  question — depends entirely on post-launch organic flow (measure in week 1).
- 8-week survival: weekly bleed budget ≈ capital ÷ 10 max, so one bad week can't end the run.

## 5. Phases (in order; each independently shippable)
- **Phase 0 — Arena-legal + boost-aware (small, ship before/during week 1):**
  registration; `WEEKLY_BOOSTS` config + `POST /boosts`; toll÷boost rotation ranking;
  weekly window counters in volume_climb. → We can compete in week 1 with the proven
  taker engine while the maker is rebuilt.
- **Phase 1 — Maker v2:** re-base agent_v3 maker on shared layer; passive book measurement
  (24–48h); testnet validation checklist (the 2026-06-24 checklist still applies); mainnet
  soft-launch with tiny caps (`MAKER_MAX_INV_USD=20`, leg $10–15); scale caps only after
  3 profitable days.
- **Phase 2 — Hybrid:** engine_manager named slots + pair-disjointness; pace scheduler;
  dashboard weekly panels.
- **Phase 3 — Tuning + challenges:** week-1 data → leg/pair/quote-width tuning; manual
  challenge playbook (control `/trade` covers ad-hoc tasks); joint (pair,leg) optimizer if
  still worth ~$0.02/1k.

## 6. Open questions (user decisions)
1. **Capital:** how much for the Arena? Reuse R4 wallet (~$101) or fund fresh/top up?
   (No top-up ban in the new rules — confirm with organizers.)
2. **Wallet:** reuse `0x703e…22F6` or a fresh one? Either must be registered first.
3. **Priority dial:** profit-first (maker-only, volume as byproduct, taker only for raffle
   saturation) vs rank-first (spend taker budget weekly for the $1,000 board)?
4. **R4 handoff:** R4 target 500k is ~2 days out — does R4 end before Arena week 1 matters,
   and is the same wallet allowed in both?

## 7. Risks
- **Registration not yet open** → watch announcements; nothing counts until registered.
- **Post-launch books unknown** → maker economics unproven until measured; Phase 1 gates on
  measurement, not hope.
- **Boost/challenge announcements are manual inputs** → missing a Monday announcement wastes
  a week's multiplier; add it to the weekly runbook + a Telegram reminder.
- **DQ discretion** ("without notice") → keep the volume mix maker-heavy and taker flow
  irregular; we also have the Builder-Session public record of arguing for honest volume.
