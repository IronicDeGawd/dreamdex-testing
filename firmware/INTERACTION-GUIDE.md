# Watch Interaction Guide

> Quick reference for every screen on the DreamDEX trading watch.
> Hardware: ESP32-C3 SuperMini + 0.96" SSD1306 OLED + 3 buttons.

---

## The three buttons

| Pin | Label | Function |
|---|---|---|
| GPIO 3 | **UP** | Navigate up / decrement |
| GPIO 4 | **DOWN** | Navigate down / increment |
| GPIO 5 | **SELECT** | Confirm / cycle field. **Hold ≥ 600 ms** = return to home (Prices). |

`HOLD: home` works from EVERY screen — it's the universal escape hatch.

---

## Menu cycle

When WiFi is **connected**, UP/DOWN on a status screen cycles through these 6 menus (WiFi is hidden):

```
PRICES ⇆ AGENT ⇆ PORTFOLIO ⇆ LEADERBOARD ⇆ MANUAL ⇆ CONFIG ⇆ (wrap to PRICES)
```

When WiFi is **disconnected**, WIFI appears at the start of the cycle and the watch auto-switches to it (so you can pick a network).

---

## Per-screen behaviour

### WIFI (only visible when disconnected)
| Button | Action |
|---|---|
| UP | Scroll SSID list ↑ |
| DOWN | Scroll SSID list ↓ |
| SELECT | Connect to highlighted SSID |
| HOLD SEL | (no escape — connect first) |

Once connected, this screen auto-disappears from the menu cycle. It re-appears automatically if the connection drops.

---

### PRICES (home screen)
Shows mid prices for WETH/WBTC/SOMI with a 24-sample sparkline next to each pair, plus agent status indicator.

| Button | Action |
|---|---|
| UP | → previous menu (CONFIG) |
| DOWN | → next menu (AGENT) |
| SELECT | (no action) |
| HOLD SEL | Stay on PRICES (already home) |

**Tip:** sparklines need 30+ seconds to populate. First sample = flat line by design.

---

### AGENT
Shows PLAY/PAUSED status, last decision, orders done / max orders, progress bar.

| Button | Action |
|---|---|
| UP | → PRICES |
| DOWN | → PORTFOLIO |
| SELECT | Toggle PLAY ↔ PAUSED |
| HOLD SEL | → home |

**Special view:** when wallet USDso < $22 the screen replaces the status with a `BAL LOW` warning showing exactly how much more USDso you need to start the agent.

---

### PORTFOLIO
Shows total value (big), PnL absolute + percent, wallet vs vaults breakdown, agent tx count.

| Button | Action |
|---|---|
| UP | → AGENT |
| DOWN | → LEADERBOARD |
| SELECT | (no action) |
| HOLD SEL | → home |

---

### LEADERBOARD
Shows your rank, total tx count, gap to #3, and current strategy signal (chase/maintain/hold).

| Button | Action |
|---|---|
| UP | → PORTFOLIO |
| DOWN | → MANUAL |
| SELECT | (no action) |
| HOLD SEL | → home |

If the contest leaderboard isn't deployed yet, this screen shows a placeholder.

---

### MANUAL (trade input — field cycle)
This screen has **4 fields** that you cycle through with SELECT:

| Field | What it controls | UP / DOWN | SELECT |
|---|---|---|---|
| 0 — Pair | WETH / WBTC / SOMI | change pair | → field 1 |
| 1 — Side | BUY / SELL | toggle side | → field 2 |
| 2 — Amount | $0.10 – $10.00 in $0.50 steps | adjust amount | → field 3 (SEND confirmation) |
| 3 — SEND | Confirmation screen | **CANCEL → back to field 0** | **Send the trade** then back to field 0 |

| At any field | Action |
|---|---|
| HOLD SEL | → home (cancels the in-progress trade) |

**The SEND screen is destructive** — pressing SELECT fires a real on-chain trade. UP or DOWN both cancel back to the pair-select. The bottom hint reminds you: `SEL:send UP/DN:cancel`.

---

### CONFIG (agent settings — field cycle)
Three fields:

| Field | What it controls | UP / DOWN | SELECT |
|---|---|---|---|
| 0 — Speed | SLOW (10 min) / NORMAL (5 min) / FAST (2 min) / MAX (45 s) | change | → field 1 |
| 1 — Max Orders | 0 (inf) – 1000 in steps of 10 | change | → field 2 |
| 2 — Agent | PLAY / PAUSED | toggle directly | toggle + cycle to field 0 |

| At any field | Action |
|---|---|
| HOLD SEL | → home |

The change is sent to the backend immediately — no separate "save" step.

---

## Indicators you'll see anywhere

- **`> PLAY`** big text on AGENT = agent is trading
- **`|| PAUSED`** big text on AGENT = agent held by user
- **`BAL LOW`** big text on AGENT = capital floor blocking trades; need to top up USDso
- **Bottom row of dots** = nav indicator; filled dot = current menu position. Hides WiFi dot when connected so the count matches the cycle.

---

## Network status hints

- If WiFi drops mid-session, the watch auto-jumps to the WIFI screen so you can re-select a network. Once reconnected, jumps back to PRICES automatically.
- If the backend tunnel is unreachable (CF down, agent stopped, etc.), the GET screens (PRICES, AGENT, PORTFOLIO, LEADERBOARD) keep showing the last-known values. POST actions (SELECT on AGENT/MANUAL/CONFIG) will silently fail; the console reports the error code over Serial.

---

## Cheatsheet

```
ANYWHERE     HOLD SELECT (600 ms) → home (PRICES)

PRICES       UP/DOWN: navigate menus
AGENT        UP/DOWN: navigate    SELECT: pause/play
PORTFOLIO    UP/DOWN: navigate
LEADERBOARD  UP/DOWN: navigate
MANUAL       UP/DOWN: edit field  SELECT: next field / send
CONFIG       UP/DOWN: edit field  SELECT: next field / toggle
WIFI         UP/DOWN: scroll SSID SELECT: connect
```
