# DreamDEX Round 4 — Builder Feedback

Feedback from building and running an autonomous trading agent through Round 4 of
the DreamDEX volume contest on Somnia mainnet. Written for the DreamDEX team as
product and protocol feedback; ordered roughly by impact.

## Summary of the round

- Reached **1,501,818 USDso** of volume.
- The round's engineering focus was an **atomic (EIP-7702) taker** that places a
  buy and a sell in a single transaction, plus **depth-aware leg sizing** that fits
  each round-trip to the top-of-book depth.
- With zero protocol fees, the only costs are the **spread crossed** (the toll) and
  **gas**. The toll is ~fixed per $1k of volume; gas is ~fixed per transaction. So
  the winning move is fewer, larger transactions on the deepest book — which is
  exactly what the atomic + big-leg approach does. On a deep book it sustained
  ~$130k/hr of volume at close to the spread floor.

## Product / protocol feedback

### 1. Leaderboard PnL treats a stablecoin swap as a loss  (highest impact)

The leaderboard appears to compute `pnl = usdsoBalance − allocation`. That reads
**only** the USDso balance. The moment a trader swaps idle USDso into USDC.e (a
normal capital-preservation move at the end of a run), their displayed balance
collapses and their PnL shows a large "loss," even though the value never left the
wallet.

Consequences:
- A wound-down trader can look like they bled ~$150 when they actually hold ~$65 of
  value in USDC.e + inventory.
- Their computed `$/1k` efficiency is **overstated** (looks worse than reality),
  which makes the board misleading for anyone comparing efficiency.

Suggestion: score capital as **total wallet value** = USDso + USDC.e + base
inventory valued at mid (+ funds reserved in resting orders), not USDso alone.
The building blocks are already on-chain; it's a read change, not a rule change.

### 2. Volume snapshot cache lag

The leaderboard volume value went stale for minutes at a time while an engine was
demonstrably still trading (identical value across reads a minute apart, then a
jump). Fine for humans, but it makes automated "am I at target yet?" polling
unreliable — bots either over-run the target or stall. A `lastUpdated` timestamp on
each row, or a shorter cache TTL, would help.

### 3. Public RPC reliability

During the round, **all three** public Somnia RPC endpoints we use went unreachable
at the same time for a stretch (our failover cycled through every one and
exhausted). Our engine handled it (it self-stops cleanly and flattens), but a
single-provider or synchronized outage is a real availability risk for anything
automated. Documented rate limits and at least one more independent endpoint would
reduce the blast radius.

### 4. Collateral is locked at the limit price, not mid

A buy locks collateral sized at the **limit** price (above mid by the slip), so a
leg whose notional equals the free balance pre-reverts on the first order. We cap
legs at ~0.95× free balance to avoid it. This is defensible behavior, but it isn't
obvious from the docs — a one-line note ("orders reserve collateral at the limit
price, not mid") would save integrators a debugging session.

### 5. Book depth varies wildly between pairs

Top-of-book depth on one pair was ~$8k while another pair's bid side was ~$100 at
the same instant. Depth-aware sizing handles this, but a naive fixed-leg taker will
silently walk the thin book and pay far more than the quoted spread. Not a bug —
just worth surfacing that per-pair depth is highly uneven.

### What worked well

- **EIP-7702 atomic execution** was the standout: bundling buy+sell into one tx
  removed the leftover-inventory risk entirely and roughly tripled gas efficiency
  versus two separate transactions. Great primitive to have on an L1 CLOB.
- **Zero fees** make the cost model clean and easy to reason about — spread + gas,
  nothing hidden.
- The deep books (when deep) absorbed large legs with negligible price impact.

## Carry-over from earlier rounds

The protocol findings filed in Round 1 (`reports/FINDINGS.md`) still make useful
regression targets — a few were confirmed and fixed by the team; the pre-trade
`eth_call` false-negative rate and the `OrderPlaced filled=0` event semantics are
the ones most worth double-checking still hold after recent changes.
