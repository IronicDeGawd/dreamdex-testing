# DreamDEX Round 4 — Builder Feedback

Feedback on the **DreamDEX protocol** from building and running an autonomous
trading agent through Round 4 of the volume contest on Somnia mainnet. Focused on
the exchange itself — mechanics, execution, and docs.

## The round in one paragraph

Reached **1,501,818 USDso** of volume. The engineering focus was an **atomic
(EIP-7702) taker** that places a buy and a sell in a single transaction, plus
**depth-aware leg sizing** that fits each round-trip to the top-of-book depth. With
zero protocol fees, the only costs are the **spread crossed** (the toll) and **gas**
— the toll is ~fixed per $1k of volume, gas is ~fixed per transaction. So the
efficient move is fewer, larger transactions on the deepest book. On a deep book the
atomic engine sustained **~$130k/hr** of volume at close to the spread floor.

## What worked well

- **EIP-7702 atomic execution** is the standout. Bundling buy+sell into one
  transaction removed leftover-inventory risk entirely and roughly **tripled gas
  efficiency** versus two separate transactions. A great primitive to have on an L1
  CLOB — it makes round-trip strategies both cheaper and safer.
- **Zero protocol fees** make the cost model clean and easy to reason about: spread
  plus gas, nothing hidden. It rewards good execution rather than fee arbitrage.
- **Deep books absorbed large legs** with negligible price impact — $198 legs on the
  deep pair moved at ~the quoted spread.

## Protocol notes & suggestions

### 1. Collateral is reserved at the limit price, not mid

A buy reserves collateral sized at the **limit** price (above mid by the slip), so a
leg whose notional equals the free balance pre-reverts on the very first order. We
cap legs at ~0.95× free balance to avoid it. This is defensible behavior, but it
isn't obvious from the docs — a one-line note ("orders reserve collateral at the
limit price, not mid") would save integrators a debugging session.

### 2. Per-pair book depth varies wildly

Top-of-book depth on one pair was ~$8k while another pair's bid side was ~$100 at the
same instant. Depth-aware sizing handles this, but a naive fixed-leg taker will
silently walk the thin book and pay far more than the quoted spread. Not a bug —
just worth surfacing that per-pair depth is highly uneven, so integrators size to
the touch rather than to a fixed notional.

### 3. Endpoint availability

During the round, every public Somnia RPC endpoint we use went unreachable at the
same time for a stretch (our failover cycled through all of them and exhausted). Our
engine handled it — it self-stops cleanly and flattens — but a synchronized outage
is a real availability risk for anything automated against the exchange. Documented
rate limits and at least one more independent endpoint would reduce the blast radius.

## Carry-over from Round 1 findings

The protocol findings filed in Round 1 (`reports/FINDINGS.md`) still make useful
regression targets — a few were confirmed and fixed by the team. The pre-trade
`eth_call` false-negative rate and the `OrderPlaced filled=0` event semantics are
the two most worth double-checking still hold after recent changes.
