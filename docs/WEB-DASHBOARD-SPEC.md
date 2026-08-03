# Web Dashboard Spec — DreamDEX Agent Control Panel

> Single-page HTML/CSS/JS app to monitor + control the DreamDEX trading agent.
> **No build step.** One `.html` file that talks directly to the backend.
> **Design language:** minimalist, functional, no neon/glow/gradients. Think
> Bloomberg terminal meets monospace. Black-on-white or white-on-near-black.
> System fonts only (or one humanist sans + one mono). Tables and
> well-spaced rows over flashy cards.

---

## Backend

**Base URL:** `https://<TUNNEL_HOST>`

**Auth:** all `POST` endpoints (mutations) require the header

```
X-API-Key: <FLASK_API_KEY from backend .env>
```

**GET endpoints are public** (read-only). No CORS issues — backend serves any origin.

Store the API key in `localStorage` after a one-time prompt on first load. **Never hardcode it in the HTML.**

---

## Endpoint reference

### `GET /prices` (no auth)

Live top-of-book mid/bid/ask for the 4 (mainnet) or 3 (testnet) trading pairs.

```jsonc
{
  "WETH:USDso":   { "mid": 2131.76, "bid": 2131.55, "ask": 2131.98, "spread": 0.0205 },
  "WBTC:USDso":   { "mid": 76922.5, "bid": 76907.0, "ask": 76938.0, "spread": 0.0405 },
  "SOMI:USDso":   { "mid": 0.16445,  "bid": 0.16440, "ask": 0.16450, "spread": 0.0608 },
  "USDC.e:USDso": { "mid": 1.00001,  "bid": 1.0,     "ask": 1.00002, "spread": 0.0002 }   // mainnet only
}
```

- `spread` is a percentage (e.g. `0.0205` = 0.02%).
- `USDC.e` only appears on mainnet.
- Poll **every 5–10 seconds**.

### `GET /agent` (no auth)

Full snapshot of agent runtime state.

```jsonc
{
  "running":     true,
  "paused":      false,
  "loop_secs":   120,          // 600=slow, 300=normal, 120=fast, 45=max
  "max_orders":  100,          // 0 = unlimited
  "orders_done":   3,
  "orders_remaining": 97,      // null when max_orders=0
  "last_decision": {
    "action":     "buy",       // "buy" | "sell" | "hold"
    "pair":       "SOMI:USDso",
    "amount_usdso": 0.5,
    "order_type": "market",
    "limit_price": null,
    "reason":     "Small trade for volume generation",
    "confidence": 80,          // 0..100
    "time":       "10:22:29"
  },
  "state": {
    "tx_count":       3,
    "usdso_balance":  29.5,    // LOCAL estimate, not safety-authoritative
    "open_positions": 1
  }
}
```

- Poll **every 5 seconds**.
- `last_decision.reason == "capital floor hit"` → wallet USDso < $22 → trigger the BAL LOW state in the UI.

### `GET /portfolio` (no auth)

On-chain balance breakdown (the **authoritative** source of truth for safety).

```jsonc
{
  "agent_balance":   50.0,        // total USDso (wallet + all vaults)
  "manual_balance":  20.0,        // static $20 reservation for manual trades
  "total_value":     70.0,
  "usdso_wallet":    50.0,        // loose USDso in the EOA
  "usdso_vaults": {
    "WETH:USDso":   0.0,
    "WBTC:USDso":   0.0,
    "SOMI:USDso":   0.0,
    "USDC.e:USDso": 0.0           // mainnet only
  },
  "last_refresh":    1779790954.24   // unix seconds — staleness check
}
```

- Poll **every 30 seconds**.
- `total_value - 50` = PnL vs the $50 contest seed.
- If `now() - last_refresh > 120` show a "stale" warning badge.

### `GET /leaderboard` (no auth)

The agent's contest standing. **May 404 / return placeholder until the contest leaderboard is live.**

```jsonc
{
  "my_rank":   12,
  "total":     50,
  "my_tx":     17,
  "third_tx":  31,
  "gap":       14,                // txs behind #3
  "signal":    "MAINTAIN"         // "CHASE" | "MAINTAIN" | "PROTECT"
}
```

- Poll **every 60 seconds**.
- If the response shape is empty / 404, render "Leaderboard not live yet".

---

### `POST /agent/toggle` (auth required)

Flips pause state. Empty body.

```bash
curl -X POST -H "X-API-Key: <key>" -H "Content-Type: application/json" -d '{}' \
  https://<TUNNEL_HOST>/agent/toggle
# → { "status": "paused" } or { "status": "resumed" }
```

### `POST /agent/speed` (auth required)

```jsonc
// Request body:
{ "speed": "slow" | "normal" | "fast" | "max" }
// slow = 600s, normal = 300s, fast = 120s, max = 45s
// → { "ok": true, "speed": "fast" }
```

### `POST /agent/max_orders` (auth required)

```jsonc
{ "max_orders": 100 }   // integer ≥ 0. 0 = unlimited.
// → { "ok": true, "max_orders": 100 }
```

### `POST /manual` (auth required)

Fires a single manual trade. **Real money on mainnet.** Confirm in UI with a modal before sending.

```jsonc
// Request:
{
  "pair":         "WETH:USDso",   // any pair from /prices
  "side":         "buy" | "sell",
  "amount_usdso": 1.50            // dollar amount to spend (buy) or receive (sell)
}
// Response on success:
{
  "status":       "success",
  "tx_hash":      "0xabc…",
  "block":        "12345678",
  "vault_delta":  "quote -1.50e18, base +0"
}
// Response on failure (any of these — show the message verbatim):
{ "status": "silent_reject", "tx_hash": "0x…" }   // tx mined but pool rejected
{ "status": "would_revert",  "sim_raw": "0x…" }   // eth_call sim said no
{ "status": "reverted",      "tx_hash": "0x…" }
{ "status": "unverified",    "tx_hash": "0x…", "block": "…" }   // ambiguous
{ "status": "error",         "error":   "<message>" }
```

### `POST /vault/deposit` (auth required)

```jsonc
{
  "pair":   "WETH:USDso",
  "token":  "0x9c32...171",   // base or quote token address
  "amount": 5.0
}
// → { "status": "success", "tx_hash": "0x…" } or { "status": "error", "error": "…" }
```

### `POST /vault/withdraw` (auth required)

Same shape as deposit.

---

## UI layout (5 sections, single page, no router)

Render top → bottom in this order. **Sticky header** with title + connection status. **No tabs / no sidebar** — a contest dashboard wants everything visible.

### 1. Header

- App title: `dreamDEX Agent` (left)
- Connection dot (right): green = backend reachable + `running:true`, amber = paused, red = unreachable. Tooltip on hover with the actual error if red.
- API key status: small lock icon. Click → modal to update the stored API key.

### 2. Prices table

Plain table, monospace numeric column, 4 rows (or 3 on testnet):

```
PAIR          MID         BID         ASK         SPREAD%
WETH:USDso    2131.76     2131.55     2131.98     0.02
WBTC:USDso    76922.50    76907.00    76938.00    0.04
SOMI:USDso    0.16445     0.16440     0.16450     0.06
USDC.e:USDso  1.00001     1.00000     1.00002     0.00
```

Bonus (nice but optional): tiny inline 24-sample sparkline on the right edge of each row, drawn via SVG `<polyline>` from a ring-buffer the dashboard maintains in JS (push every poll). Same idea as the watch firmware.

### 3. Agent control card

Single horizontal row of controls + a single status line.

- **Big pause/play button** (one button — its label flips with state). Calls `POST /agent/toggle`.
- **Speed selector** — 4 radio-like buttons (SLOW / NORMAL / FAST / MAX). Calls `POST /agent/speed`.
- **Max orders** — number input + Save button. Calls `POST /agent/max_orders`. Show "∞" when value is 0.
- **Status line** below the buttons: `{action} {pair} • {confidence}% • {reason} • last @ {time}`.
- If `last_decision.reason` includes `"capital floor"`, replace the entire card body with a prominent **BAL LOW** banner showing:
  - `Wallet USDso: $X.XX`
  - `Need $X.XX more to reach the $22 floor`
  - `Top up the wallet and the agent will resume on its next tick.`

### 4. Portfolio card

3-column layout, no decoration:

| Total | PnL | Liquidity |
|---|---|---|
| `$70.00` big | `-$0.32 (-0.5%)` colored: red if negative, default (no styling) if positive — no green/glow | `$50.00 wallet / $0.00 in vaults` |

Below: a small horizontal stacked bar showing wallet vs each pool vault as proportions of `agent_balance`. Hover/tap a segment to see its USDso amount.

### 5. Manual trade card

Three inputs side-by-side, then a confirm button:

- **Pair**: dropdown of pair names from `/prices` keys
- **Side**: two-button toggle (BUY / SELL)
- **Amount**: number input, `0.10` step, label `USDso`

When the user clicks `Send Trade`:

1. **Confirmation modal** — restate `BUY 1.50 USDso of WETH:USDso ?`, two buttons: `Cancel` / `Confirm send`.
2. On confirm, `POST /manual` with the payload. While in-flight, disable the button and show `Sending…`.
3. On response, show a one-line result row at the top of an in-page log:
   - `success` → `[10:42:11] ✓ BUY WETH:USDso $1.50 — tx 0xabc... block 12345`
   - any failure → `[10:42:11] ✗ BUY WETH:USDso $1.50 — silent_reject (tx 0xabc...)`
4. The log persists in `localStorage` so refresh keeps history. Cap at last 50 entries.

### 6. (Optional) Vault management card

Only render when `usdso_vaults[any] > 0` or `usdso_wallet > 0` — i.e. when there's something to move.

- Pair dropdown
- Token: BASE or USDso radio
- Amount input
- Two buttons: `Deposit` / `Withdraw`

Same confirmation-modal + log-row pattern as Manual trade.

### 7. Footer

- Last poll timestamps per endpoint
- Backend URL
- API key status
- A small "Stop & wipe" link that opens a modal with the exact `ssh ...` teardown commands from `RUNBOOK.md` (don't try to execute over the web — just display so the user can copy).

---

## State machine notes

- **First load**: prompt for API key → save to `localStorage` → kick off all the polls in parallel.
- **API key missing or wrong**: any POST returns 401 → show a banner offering to update the key.
- **Backend unreachable** (network error / 5xx): retain last known data on screen, show red dot in header, retry with exponential backoff (1s, 2s, 4s, 8s, capped at 30s).
- **`paused` flips**: pause button visually swaps to play. No optimistic update — wait for the toggle response to flip the UI.
- **`orders_done == max_orders`**: progress bar fills, pause button becomes disabled-but-shown, status line shows "MAX ORDERS REACHED — raise the cap to continue".

---

## Polling cadence summary

| Endpoint | Interval | Reason |
|---|---|---|
| `/prices` | 5–10 s | Sparkline cadence |
| `/agent` | 5 s | Most-watched state |
| `/portfolio` | 30 s | Slow-moving |
| `/leaderboard` | 60 s | Slowest |

**At most one fetch in flight at a time per endpoint.** If a fetch is already pending, skip the next tick.

---

## Hard rules

1. **No build step.** Single `.html` file, vanilla JS, vanilla CSS, or one CDN link to a CSS reset. Lovable should output something deployable to GitHub Pages with no `npm install`.
2. **No flashy color.** No gradients, no glows, no neon. Black, white, one accent for warnings/errors (a muted red is fine). Default body font: system stack. Numeric columns: `font-family: ui-monospace, Menlo, Consolas, monospace`.
3. **API key never in the HTML.** Always read from `localStorage`. The lock icon in the header is the only way to set/update it.
4. **All mutating actions require a confirmation modal** when the amount exceeds $1, or for ANY mainnet operation (you can read the network from the backend URL — though for now just always confirm).
5. **Display the raw error message verbatim** on any failure. Don't soften "silent_reject" into "trade failed" — operators need to see the actual status string.
6. **Mobile-friendly.** Most-likely use is on a phone next to the watch. One column on narrow viewports, multi-column on ≥768 px.

---

## Hard-coded values you'll need

- **Backend URL**: `https://<TUNNEL_HOST>`
- **Mainnet wallet** (for display only): `0x0000000000000000000000000000000000000000`
- **Capital floor**: `$22 USDso` (the threshold below which the agent holds)
- **Initial seed**: `$50 USDso` (used for PnL calc: `total_value - 50`)

---

## What NOT to build

- No charts beyond inline sparklines. No Recharts/D3 — pure SVG inline.
- No animations beyond a single fade-in on first render. No spinners that pulse forever; use a single `Loading…` text.
- No "engagement" widgets. No dark/light mode toggle (just pick one). No fake AI nameplate.
- No background music, no sound effects.
