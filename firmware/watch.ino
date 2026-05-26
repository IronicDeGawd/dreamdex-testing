#include <Arduino.h>
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <WiFiClientSecure.h>   // HTTPS for Cloudflare tunnel
#include <ArduinoJson.h>

// ── Board: ESP32-C3 SuperMini v1601 ──────────────────────
// Arduino IDE:
//   Tools → Board → ESP32 → "ESP32C3 Dev Module"
//   Tools → USB CDC On Boot → Enabled  (required for Serial monitor)
// Power:
//   No LiPo pad on C3. Wire TP4056 OUT+ → board 5V pin (NOT 3.3V).
//
// ── FIX LOG ──────────────────────────────────────────────
// v2 changes vs original:
//   [1] SDA moved GPIO8 → GPIO6. GPIO8 = onboard blue LED on C3 SuperMini
//       v1601. Driving I2C on GPIO8 caused constant LED flicker.
//   [2] handleButtons() rewritten. Original read all pins before debounce
//       check — caused SELECT drops under fast use. Now SELECT hold tracking
//       runs outside debounce so long-press is never missed.
//   [3] selectConsumed flag added. Prevents long-press AND short-press both
//       firing for a single physical button press.
//   [4] onShortPress() MENU_MANUAL fixed. Original incremented manualField
//       then immediately checked == 3, skipping the SEND confirmation screen.
//       Now checks first, then increments.
//   [5] adjustConfig() case 1 (max orders) clamped correctly.
//       agentMaxOrders = 0 means unlimited — DOWN from 0 stays at 0.

// ── Display ──────────────────────────────────────────────
#define SCREEN_W  128
#define SCREEN_H  64
#define OLED_ADDR 0x3C
#define SDA_PIN   6   // FIX [1]: was 8 — GPIO8 = blue LED on C3 SuperMini
#define SCL_PIN   9
Adafruit_SSD1306 display(SCREEN_W, SCREEN_H, &Wire, -1);

// ── Buttons (C3 SuperMini) ────────────────────────────────
// GPIO3/4/5 — safe, no boot-strapping conflicts.
// Active LOW — INPUT_PULLUP — no external resistors needed.
#define BTN_UP     3
#define BTN_DOWN   4
#define BTN_SELECT 5
#define LONG_PRESS_MS 600

// ── Backend ──────────────────────────────────────────────
// Production: agent is fronted by Cloudflare tunnel at this subdomain.
// CF terminates HTTPS on 443 and forwards to the backend container's :5001.
// HTTP fallback (commented) is the original LAN-only path; kept for local dev.
const char* BACKEND = "https://<TUNNEL_HOST>";
// const char* BACKEND = "http://192.168.1.5:5001";   // dev: laptop on same WiFi

// ── Known WiFi networks ───────────────────────────────────
// Copy wifi_secrets.example.h → wifi_secrets.h and fill in your SSIDs/passes.
#include "wifi_secrets.h"
const int NUM_NETS = sizeof(knownNets) / sizeof(knownNets[0]);

// ── Menus ────────────────────────────────────────────────
enum Menu {
  MENU_WIFI = 0, MENU_PRICES, MENU_AGENT,
  MENU_PORTFOLIO, MENU_LEADERBOARD,
  MENU_MANUAL, MENU_CONFIG,
  MENU_COUNT
};

// ── Global state ─────────────────────────────────────────
Menu          currentMenu   = MENU_PRICES;
unsigned long lastBtn       = 0;
// Per-endpoint timers — staggered so only one HTTP call runs per loop iteration.
// Keeps button responsiveness high (no single fetch blocks > one HTTP call's RTT).
unsigned long lastFetchPrices     = 0;
unsigned long lastFetchAgent      = 0;
unsigned long lastFetchPortfolio  = 0;
unsigned long lastFetchLeaderboard = 0;
const int     FETCH_PRICES_EVERY      = 10000;  // 10s — sparkline cadence
const int     FETCH_AGENT_EVERY       = 5000;   // 5s — most-watched
const int     FETCH_PORTFOLIO_EVERY   = 30000;  // 30s — slow-moving
const int     FETCH_LEADERBOARD_EVERY = 60000;  // 60s — slowest

// Cached data strings — populated by fetch*() functions
String priceWETH = "--", priceWBTC = "--", priceSOMI = "--";
String agentPaused = "false";
String agentLast   = "--";
String agentTxs    = "0";
int    agentOrdersDone = 0;
int    agentMaxOrders  = 100;
String portAgent = "--", portManual = "--", portPnL = "--";
float  portTotal = 0.0f;       // raw total_value from /portfolio (for PnL pct)
float  portWalletUsdso = 0.0f; // raw usdso_wallet (loose USDso)
float  portVaultsTotal = 0.0f; // raw sum of all pool vaults (locked USDso)
String lbRank = "--", lbTxs = "--", lbSignal = "--", lbGap = "--";
String lbLive = "false";

// ── Sparkline ring buffers ────────────────────────────────
// 24 samples × 3 pairs = 72 floats = 288 bytes — cheap on the C3.
// FETCH_EVERY is 10s, so 24 samples covers the last 4 minutes of mid prices.
constexpr int SPARK_LEN = 24;
struct PriceHist { float vals[SPARK_LEN]; int count; };
PriceHist histWETH = {{0}, 0};
PriceHist histWBTC = {{0}, 0};
PriceHist histSOMI = {{0}, 0};

void pushHist(PriceHist& h, float v) {
  if (v <= 0.0f) return;   // ignore 0/missing
  if (h.count < SPARK_LEN) {
    h.vals[h.count++] = v;
  } else {
    // shift left, drop oldest
    for (int i = 0; i < SPARK_LEN - 1; i++) h.vals[i] = h.vals[i + 1];
    h.vals[SPARK_LEN - 1] = v;
  }
}

// Manual trade state
int         manualPairIdx = 0;   // 0=WETH 1=WBTC 2=SOMI
int         manualSideIdx = 0;   // 0=BUY  1=SELL
float       manualAmt     = 1.0;
int         manualField   = 0;   // 0=pair 1=side 2=amount 3=SEND
const char* manualPairs[] = { "WETH", "WBTC", "SOMI" };
const char* manualSides[] = { "BUY", "SELL" };

// Config state
int         cfgSpeedIdx   = 1;   // 0=slow 1=normal 2=fast 3=max
int         cfgField      = 0;   // 0=speed 1=max_orders 2=pause
const char* speeds[]      = { "SLOW", "NORMAL", "FAST", "MAX" };
const int   MAX_ORDERS_STEP = 10;

// WiFi scan state
int    wifiScroll = 0;
int    wifiCount  = 0;
String wifiSSIDs[10];
int    wifiRSSIs[10];
bool   wifiKnown[10];
bool   wifiScanned = false;

// Button state — FIX [2][3]
unsigned long btnSelectDown  = 0;
bool          btnSelectHeld  = false;
bool          selectConsumed = false;  // FIX [3]: prevents double-fire

// ─────────────────────────────────────────────────────────
// SETUP
// ─────────────────────────────────────────────────────────
void setup() {
  Serial.begin(115200);
  delay(200);
  Serial.println("[boot] DreamDEX Watch v2 starting");

  pinMode(BTN_UP,     INPUT_PULLUP);
  pinMode(BTN_DOWN,   INPUT_PULLUP);
  pinMode(BTN_SELECT, INPUT_PULLUP);

  // FIX [1]: SDA on GPIO6, not GPIO8
  Wire.begin(SDA_PIN, SCL_PIN);
  if (!display.begin(SSD1306_SWITCHCAPVCC, OLED_ADDR)) {
    Serial.println("[oled] SSD1306 not found at 0x3C — try 0x3D");
    while (true) delay(1000);
  }
  display.setTextColor(SSD1306_WHITE);
  display.clearDisplay();
  showSplash();

  currentMenu = MENU_WIFI;
  scanWiFi();
}

// ─────────────────────────────────────────────────────────
// LOOP
// ─────────────────────────────────────────────────────────
void loop() {
  handleButtons();

  // Watchdog: if WiFi drops and we're NOT already on the WiFi screen, jump
  // there so the user can pick a new network. Re-scan first so the SSID list
  // is fresh. Skips the long-press SELECT escape requirement for this state.
  static bool wasConnected = false;
  bool connectedNow = (WiFi.status() == WL_CONNECTED);
  if (wasConnected && !connectedNow && currentMenu != MENU_WIFI) {
    Serial.println("[wifi] connection lost → returning to WiFi menu");
    currentMenu = MENU_WIFI;
    scanWiFi();   // refresh list
  }
  wasConnected = connectedNow;

  // Staggered fetch — at most ONE HTTP call per loop iteration. Keeps button
  // latency bounded by a single HTTP RTT instead of the sum of all 4 fetches.
  // Skip entirely when on the WiFi screen (we don't show fetched data there).
  if (currentMenu != MENU_WIFI && WiFi.status() == WL_CONNECTED) {
    unsigned long now = millis();
    if (now - lastFetchAgent > FETCH_AGENT_EVERY) {
      fetchAgent();
      lastFetchAgent = millis();
    } else if (now - lastFetchPrices > FETCH_PRICES_EVERY) {
      fetchPrices();
      lastFetchPrices = millis();
    } else if (now - lastFetchPortfolio > FETCH_PORTFOLIO_EVERY) {
      fetchPortfolio();
      lastFetchPortfolio = millis();
    } else if (now - lastFetchLeaderboard > FETCH_LEADERBOARD_EVERY) {
      fetchLeaderboard();
      lastFetchLeaderboard = millis();
    }
  }

  drawMenu();
  delay(50);
}

// ═════════════════════════════════════════════════════════
// DISPLAY
// ═════════════════════════════════════════════════════════

void showSplash() {
  display.clearDisplay();
  display.setTextSize(2);
  display.setCursor(10, 8);
  display.print("DreamDEX");
  display.setTextSize(1);
  display.setCursor(20, 30);
  display.print("Trading Watch v2");
  display.setCursor(20, 44);
  display.print("0xF4c8...2905");
  display.display();
  delay(2000);
}

void drawMenu() {
  display.clearDisplay();
  switch (currentMenu) {
    case MENU_WIFI:        drawWiFi();        break;
    case MENU_PRICES:      drawPrices();      break;
    case MENU_AGENT:       drawAgent();       break;
    case MENU_PORTFOLIO:   drawPortfolio();   break;
    case MENU_LEADERBOARD: drawLeaderboard(); break;
    case MENU_MANUAL:      drawManual();      break;
    case MENU_CONFIG:      drawConfig();      break;
    default: break;
  }
  display.display();
}

// ── Shared helpers ────────────────────────────────────────

void header(const char* title) {
  display.setTextSize(1);
  display.setCursor(0, 0);
  display.print(title);
  display.drawLine(0, 9, 127, 9, SSD1306_WHITE);
}

// Render a sparkline at (x, y) of size (w, h) from a PriceHist ring buffer.
// Normalises values to [0, h-1] by the visible window's min/max so even small
// percentage moves show up. With <2 samples, draws a horizontal flat line.
void drawSparkline(int x, int y, int w, int h, const PriceHist& hist) {
  const int n = hist.count;
  if (n < 2) {
    // Flat line — nothing meaningful to chart yet.
    display.drawLine(x, y + h / 2, x + w - 1, y + h / 2, SSD1306_WHITE);
    return;
  }
  // Find min/max
  float lo = hist.vals[0], hi = hist.vals[0];
  for (int i = 1; i < n; i++) {
    if (hist.vals[i] < lo) lo = hist.vals[i];
    if (hist.vals[i] > hi) hi = hist.vals[i];
  }
  float range = hi - lo;
  if (range < 1e-9f) range = 1.0f;   // flat → middle line
  // Map each sample to (px, py)
  int prevPx = x, prevPy = y + h - 1 - (int)((hist.vals[0] - lo) / range * (h - 1));
  for (int i = 1; i < n; i++) {
    int px = x + (int)((float)i / (float)(n - 1) * (w - 1));
    int py = y + h - 1 - (int)((hist.vals[i] - lo) / range * (h - 1));
    display.drawLine(prevPx, prevPy, px, py, SSD1306_WHITE);
    prevPx = px;
    prevPy = py;
  }
}

void navDots() {
  // Hide MENU_WIFI's dot when connected — it's no longer reachable via UP/DOWN
  // (only re-appears on disconnect).
  bool hideWifi = (WiFi.status() == WL_CONNECTED);
  int visible = hideWifi ? (MENU_COUNT - 1) : MENU_COUNT;
  int sp = 128 / visible;
  int slot = 0;
  for (int i = 0; i < MENU_COUNT; i++) {
    if (hideWifi && i == (int)MENU_WIFI) continue;
    int x = slot * sp + sp / 2;
    if (i == (int)currentMenu)
      display.fillCircle(x, 62, 2, SSD1306_WHITE);
    else
      display.drawCircle(x, 62, 1, SSD1306_WHITE);
    slot++;
  }
}

// ── WiFi ──────────────────────────────────────────────────

void drawWiFi() {
  header("< WiFi Select >");
  if (!wifiScanned) {
    display.setCursor(20, 28);
    display.print("Scanning...");
    return;
  }
  if (WiFi.status() == WL_CONNECTED) {
    display.setCursor(0, 12); display.print("Connected:");
    display.setCursor(0, 22); display.print(WiFi.SSID().substring(0, 18));
    display.setCursor(0, 34); display.print("IP: ");
    display.print(WiFi.localIP());
    display.setCursor(0, 46); display.print("[SELECT] change WiFi");
    return;
  }
  for (int i = 0; i < min(wifiCount, 4); i++) {
    int idx = (wifiScroll + i) % wifiCount;
    int y   = 12 + i * 12;
    if (i == 0) { display.setCursor(0, y); display.print(">"); }
    display.setCursor(8, y);
    if (wifiKnown[idx]) display.print("*");
    display.print(wifiSSIDs[idx].substring(0, 13));
    display.setCursor(100, y);
    display.print(wifiRSSIs[idx]);
  }
  display.setCursor(0, 56);
  display.print("*=known  SELECT=conn");
}

// ── Prices ────────────────────────────────────────────────

void drawPrices() {
  header("< Prices >");
  display.setTextSize(1);
  // Label + price (left side), sparkline (right side, 50px wide × 9px tall).
  // Each row uses ~12px vertical pitch. y is the text baseline.
  // Sparklines span x=78..127 (50 px wide).
  const int SPARK_X = 78, SPARK_W = 50, SPARK_H = 9;

  display.setCursor(0, 14); display.print("WETH $"); display.print(priceWETH);
  drawSparkline(SPARK_X, 13, SPARK_W, SPARK_H, histWETH);

  display.setCursor(0, 26); display.print("WBTC $"); display.print(priceWBTC);
  drawSparkline(SPARK_X, 25, SPARK_W, SPARK_H, histWBTC);

  display.setCursor(0, 38); display.print("SOMI $"); display.print(priceSOMI);
  drawSparkline(SPARK_X, 37, SPARK_W, SPARK_H, histSOMI);

  display.setCursor(0, 50);
  display.print(agentPaused == "true" ? "[Agent: PAUSED]" : "[Agent: PLAY]");
  navDots();
}

// ── Agent ─────────────────────────────────────────────────

void drawAgent() {
  header("< Agent >");
  display.setTextSize(2);
  display.setCursor(0, 12);
  display.print(agentPaused == "true" ? "|| PAUSED" : "> PLAY");
  display.setTextSize(1);
  display.setCursor(0, 32);
  display.print(agentLast.substring(0, 21));
  display.setCursor(0, 44);
  display.print("Orders: ");
  display.print(agentOrdersDone);
  display.print("/");
  if (agentMaxOrders == 0) display.print("inf");
  else                     display.print(agentMaxOrders);
  // Progress bar
  if (agentMaxOrders > 0) {
    int barW   = 120;
    int filled = (int)(((float)agentOrdersDone / agentMaxOrders) * barW);
    filled = constrain(filled, 0, barW);
    display.drawRect(0, 54, barW, 5, SSD1306_WHITE);
    display.fillRect(0, 54, filled, 5, SSD1306_WHITE);
  }
}

// ── Portfolio ─────────────────────────────────────────────

void drawPortfolio() {
  header("< Portfolio >");
  // Big total at the top — most-glanced number on this screen.
  display.setTextSize(2);
  display.setCursor(0, 12);
  display.print("$");
  display.print(portTotal, 2);
  display.setTextSize(1);

  // PnL with arrow and percent
  float pnl = portTotal - 50.0f;
  float pctPnl = (pnl / 50.0f) * 100.0f;
  display.setCursor(0, 32);
  display.print("PnL ");
  display.print(pnl >= 0 ? "+" : "");
  display.print(pnl, 2);
  display.print(" (");
  display.print(pctPnl, 1);
  display.print("%)");

  // Breakdown: wallet (loose) + vaults (locked in dreamDEX pools)
  display.setCursor(0, 42);
  display.print("wallet $"); display.print(portWalletUsdso, 2);
  display.setCursor(0, 52);
  display.print("vaults $"); display.print(portVaultsTotal, 2);
  display.print(" tx "); display.print(agentTxs);
}

// ── Leaderboard ───────────────────────────────────────────

void drawLeaderboard() {
  header("< Leaderboard >");
  if (lbLive != "true") {
    display.setCursor(0, 16); display.print("Mainnet board");
    display.setCursor(0, 26); display.print("not live yet.");
    display.setCursor(0, 40); display.print("Wallet tracked:");
    display.setCursor(0, 50); display.print("0xF4c8...2905");
    return;
  }
  display.setTextSize(2);
  display.setCursor(30, 14);
  display.print("#"); display.print(lbRank);
  display.setTextSize(1);
  display.setCursor(0, 34); display.print("Txs: "); display.print(lbTxs);
  display.setCursor(0, 44);
  display.print("Gap: "); display.print(lbGap);
  display.print(" | ");   display.print(lbSignal);
  navDots();
}

// ── Manual Trade ──────────────────────────────────────────

void drawManual() {
  header("< Manual Trade >");
  display.setCursor(0, 12);
  display.print(manualField == 0 ? ">" : " ");
  display.setCursor(8, 12);
  display.print("Pair: "); display.print(manualPairs[manualPairIdx]);

  display.setCursor(0, 24);
  display.print(manualField == 1 ? ">" : " ");
  display.setCursor(8, 24);
  display.print("Side: "); display.print(manualSides[manualSideIdx]);

  display.setCursor(0, 36);
  display.print(manualField == 2 ? ">" : " ");
  display.setCursor(8, 36);
  display.print("Amt:  $"); display.print(manualAmt, 2);

  display.setCursor(0, 50);
  if (manualField == 3) {
    // SEND confirmation. UP/DOWN cancel back to field 0; SEL fires the trade;
    // hold SEL returns home (cancels too).
    display.print("SEL:send UP/DN:cancel");
  } else {
    display.print("SEL:next  HOLD:home");
  }
}

// ── Config ────────────────────────────────────────────────

void drawConfig() {
  header("< Config >");
  display.setCursor(0, 12);
  display.print(cfgField == 0 ? ">" : " ");
  display.print(" Speed: "); display.print(speeds[cfgSpeedIdx]);

  display.setCursor(0, 24);
  display.print(cfgField == 1 ? ">" : " ");
  display.print(" MaxOrd: ");
  if (agentMaxOrders == 0) display.print("inf");
  else                     display.print(agentMaxOrders);

  display.setCursor(0, 36);
  display.print(cfgField == 2 ? ">" : " ");
  display.print(" Agent: ");
  display.print(agentPaused == "true" ? "PAUSED" : "PLAY");

  display.setCursor(0, 52);
  display.print("SEL:next UP/DN HOLD:home");
}

// ═════════════════════════════════════════════════════════
// BUTTON HANDLING  — FIX [2][3]
// ═════════════════════════════════════════════════════════
//
// Design:
//  • SELECT hold tracking runs every loop iteration, outside debounce.
//    Original code returned early before updating btnSelectDown, causing
//    long-press to be missed when debounce was active.
//  • selectConsumed flag prevents both long-press AND short-press firing
//    for one physical press.
//  • UP / DOWN remain debounced independently — SELECT does not reset their
//    debounce timer, so rapid UP → SELECT sequences register correctly.

void handleButtons() {
  bool sel = (digitalRead(BTN_SELECT) == LOW);

  // ── SELECT: track outside debounce so hold is never missed ──
  if (sel) {
    if (btnSelectDown == 0) btnSelectDown = millis();
    if (!btnSelectHeld && !selectConsumed &&
        millis() - btnSelectDown > LONG_PRESS_MS) {
      btnSelectHeld  = true;
      selectConsumed = true;
      onLongPress();
      lastBtn = millis();
    }
    return;  // while held, do nothing else
  } else {
    // SELECT just released
    if (btnSelectDown > 0) {
      if (!btnSelectHeld && !selectConsumed) {
        // Short press — apply debounce only here
        if (millis() - lastBtn >= 200) {
          onShortPress();
          lastBtn = millis();
        }
      }
      btnSelectDown  = 0;
      btnSelectHeld  = false;
      selectConsumed = false;
    }
  }

  // ── UP / DOWN: standard debounce ────────────────────────
  if (millis() - lastBtn < 200) return;

  bool up = (digitalRead(BTN_UP)   == LOW);
  bool dn = (digitalRead(BTN_DOWN) == LOW);

  if (up) { onUp();   lastBtn = millis(); return; }
  if (dn) { onDown(); lastBtn = millis(); return; }
}

// ── Button actions ────────────────────────────────────────

// Cycle to the next/prev menu while skipping MENU_WIFI when already connected.
// When disconnected MENU_WIFI is the boot/active screen so it's always shown.
Menu cycleMenu(Menu cur, int dir) {
  bool connected = (WiFi.status() == WL_CONNECTED);
  Menu next = cur;
  // At most MENU_COUNT iterations — guarantees termination even in weird states.
  for (int i = 0; i < MENU_COUNT; i++) {
    next = (Menu)(((int)next + dir + MENU_COUNT) % MENU_COUNT);
    if (connected && next == MENU_WIFI) continue;   // skip
    return next;
  }
  return cur;   // fallback, shouldn't happen
}

void onUp() {
  switch (currentMenu) {
    case MENU_WIFI:
      // Disconnected: UP scrolls the SSID list.
      // Connected: shouldn't be reachable since we hide MENU_WIFI from the
      // cycle, but if we land here defensively, exit to the previous menu.
      if (WiFi.status() == WL_CONNECTED) {
        currentMenu = cycleMenu(currentMenu, -1);
      } else {
        wifiScroll = (wifiScroll - 1 + max(wifiCount, 1)) % max(wifiCount, 1);
      }
      break;
    case MENU_PRICES:
    case MENU_AGENT:
    case MENU_PORTFOLIO:
    case MENU_LEADERBOARD:
      currentMenu = cycleMenu(currentMenu, -1);
      break;
    case MENU_MANUAL:
      adjustManual(-1);
      break;
    case MENU_CONFIG:
      adjustConfig(-1);
      break;
    default: break;
  }
}

void onDown() {
  switch (currentMenu) {
    case MENU_WIFI:
      if (WiFi.status() == WL_CONNECTED) {
        currentMenu = cycleMenu(currentMenu, +1);
      } else {
        wifiScroll = (wifiScroll + 1) % max(wifiCount, 1);
      }
      break;
    case MENU_PRICES:
    case MENU_AGENT:
    case MENU_PORTFOLIO:
    case MENU_LEADERBOARD:
      currentMenu = cycleMenu(currentMenu, +1);
      break;
    case MENU_MANUAL:
      adjustManual(1);
      break;
    case MENU_CONFIG:
      adjustConfig(1);
      break;
    default: break;
  }
}

void onShortPress() {
  switch (currentMenu) {
    case MENU_WIFI:
      connectToSelected();
      break;

    case MENU_AGENT:
      postToggleAgent();
      break;

    case MENU_MANUAL:
      // FIX [4]: check BEFORE incrementing so field 3 = visible SEND screen
      if (manualField == 3) {
        sendManualTrade();
        manualField = 0;
      } else {
        manualField++;
      }
      break;

    case MENU_CONFIG:
      // On the pause field, SEL toggles agent immediately before cycling
      if (cfgField == 2) postToggleAgent();
      cfgField = (cfgField + 1) % 3;
      break;

    default:
      break;
  }
}

void onLongPress() {
  // Always returns to home (Prices), resets sub-menu state
  manualField = 0;
  cfgField    = 0;
  currentMenu = MENU_PRICES;
  Serial.println("[btn] long press → home");
}

// ── Value adjusters ───────────────────────────────────────

void adjustManual(int dir) {
  switch (manualField) {
    case 0:
      manualPairIdx = (manualPairIdx + dir + 3) % 3;
      break;
    case 1:
      manualSideIdx = (manualSideIdx + dir + 2) % 2;
      break;
    case 2:
      manualAmt = constrain(manualAmt + dir * 0.5f, 0.1f, 10.0f);
      break;
    case 3:
      // SEND confirmation screen — UP/DOWN cancels back to field 0 (Pair).
      // Without this the only way out of the SEND screen was long-press
      // (which goes all the way home), which felt accidental.
      manualField = 0;
      break;
    default: break;
  }
}

void adjustConfig(int dir) {
  switch (cfgField) {
    case 0:  // speed
      cfgSpeedIdx = (cfgSpeedIdx + dir + 4) % 4;
      postSpeed();
      break;
    case 1:  // max orders — FIX [5]: clamp correctly, 0 = unlimited
      agentMaxOrders = constrain(agentMaxOrders + dir * MAX_ORDERS_STEP, 0, 1000);
      postMaxOrders();
      break;
    case 2:  // pause/play — UP or DOWN both toggle
      postToggleAgent();
      break;
    default: break;
  }
}

// ═════════════════════════════════════════════════════════
// WIFI
// ═════════════════════════════════════════════════════════

void scanWiFi() {
  display.clearDisplay();
  display.setTextSize(1);
  display.setCursor(15, 25);
  display.print("Scanning WiFi...");
  display.display();

  // Tear down prior state — prevents "sta is connecting, cannot set config"
  WiFi.disconnect(true, true);
  WiFi.mode(WIFI_STA);
  delay(150);

  wifiCount  = WiFi.scanNetworks();
  wifiScanned = true;

  for (int i = 0; i < min(wifiCount, 10); i++) {
    wifiSSIDs[i] = WiFi.SSID(i);
    wifiRSSIs[i] = WiFi.RSSI(i);
    wifiKnown[i] = false;
    for (int k = 0; k < NUM_NETS; k++) {
      if (wifiSSIDs[i] == knownNets[k].ssid) {
        wifiKnown[i] = true;
        break;
      }
    }
  }
  WiFi.scanDelete();  // free scan buffer
}

void connectToSelected() {
  if (wifiCount == 0) return;
  int idx = wifiScroll % wifiCount;

  if (!wifiKnown[idx]) {
    display.clearDisplay();
    display.setCursor(10, 28);
    display.print("Unknown network");
    display.display();
    delay(1500);
    return;
  }

  String      ssid = wifiSSIDs[idx];
  const char* pass = "";
  for (int k = 0; k < NUM_NETS; k++) {
    if (ssid == knownNets[k].ssid) { pass = knownNets[k].pass; break; }
  }

  display.clearDisplay();
  display.setCursor(0, 12); display.print("Connecting to:");
  display.setCursor(0, 22); display.print(ssid.substring(0, 20));
  display.display();

  WiFi.disconnect(true, true);
  WiFi.mode(WIFI_STA);
  delay(150);
  WiFi.begin(ssid.c_str(), pass);

  int tries = 0;
  while (WiFi.status() != WL_CONNECTED && tries < 30) {
    delay(500);
    tries++;
    display.setCursor(tries * 4, 38);
    display.print(".");
    display.display();
  }

  if (WiFi.status() == WL_CONNECTED) {
    Serial.printf("[wifi] connected  ssid=%s  ip=%s  rssi=%d\n",
                  ssid.c_str(),
                  WiFi.localIP().toString().c_str(),
                  WiFi.RSSI());
    currentMenu = MENU_PRICES;
    fetchData();
    lastFetch = millis();
  } else {
    Serial.printf("[wifi] FAILED  status=%d\n", WiFi.status());
    display.clearDisplay();
    display.setCursor(0, 14); display.print("Connect FAILED");
    display.setCursor(0, 26); display.print("status="); display.print(WiFi.status());
    display.setCursor(0, 38); display.print("1=no ssid 4=fail");
    display.setCursor(0, 50); display.print("SELECT to retry");
    display.display();
    delay(2500);
    WiFi.disconnect(true, true);
    scanWiFi();
  }
}

// ═════════════════════════════════════════════════════════
// HTTP HELPERS
// ═════════════════════════════════════════════════════════

// True if BACKEND starts with "https://"
static inline bool backendIsHttps() {
  return String(BACKEND).startsWith("https://");
}

// Initialise an HTTPClient with optional TLS. Caller must `http.end()`.
// CF tunnel uses a public CA (Cloudflare Inc ECC CA-3 etc) — we accept any
// valid CA without pinning since X-API-Key is what gates writes. For a
// production prod-grade prod we'd pin the leaf cert via setCACert().
static WiFiClientSecure _tlsClient;   // reused — keepalive TCP cheaper than reconnect

bool httpBegin(HTTPClient& http, String url) {
  if (backendIsHttps()) {
    _tlsClient.setInsecure();    // skip cert verify; X-API-Key handles auth
    return http.begin(_tlsClient, url);
  } else {
    return http.begin(url);
  }
}

// HTTP timeouts kept short so a hung backend can't pin the loop for long.
// CF tunnel + container backend typical p50: 100-300ms. p99 spikes to 2s.
// 4s gives 10x headroom over p99 without making slow backends invisible.
constexpr int HTTP_TIMEOUT_MS = 4000;

String httpGet(String path) {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.printf("[http] GET %s skipped — no WiFi\n", path.c_str());
    return "{}";
  }
  HTTPClient http;
  if (!httpBegin(http, String(BACKEND) + path)) {
    Serial.printf("[http] GET %s begin() failed\n", path.c_str());
    return "{}";
  }
  http.setTimeout(HTTP_TIMEOUT_MS);
  int    code = http.GET();
  String body = (code == 200) ? http.getString() : "{}";
  http.end();
  if (code != 200)
    Serial.printf("[http] GET %s → %d\n", path.c_str(), code);
  // Service buttons immediately so a sequence of fetches doesn't queue presses.
  handleButtons();
  return body;
}

void httpPost(String path, String payload) {
  if (WiFi.status() != WL_CONNECTED) return;
  HTTPClient http;
  if (!httpBegin(http, String(BACKEND) + path)) {
    Serial.printf("[http] POST %s begin() failed\n", path.c_str());
    return;
  }
  http.addHeader("Content-Type", "application/json");
  // Shared-secret auth (backend refuses mutating requests without it). API_KEY
  // comes from wifi_secrets.h — copy wifi_secrets.example.h and set API_KEY to
  // match the backend's FLASK_API_KEY env var.
  #ifdef API_KEY
    http.addHeader("X-API-Key", API_KEY);
  #endif
  http.setTimeout(HTTP_TIMEOUT_MS);
  int code = http.POST(payload);
  http.end();
  if (code < 0)
    Serial.printf("[http] POST %s → %d\n", path.c_str(), code);
  else if (code == 401)
    Serial.printf("[http] POST %s → 401 (missing/bad API_KEY)\n", path.c_str());
  handleButtons();
}

// ═════════════════════════════════════════════════════════
// DATA FETCH
// ═════════════════════════════════════════════════════════

// Run all fetches sequentially — used once on initial WiFi connect.
// Steady-state polling is staggered via the per-endpoint timers in loop().
void fetchData() {
  fetchPrices();
  fetchAgent();
  fetchPortfolio();
  fetchLeaderboard();
}

void fetchPrices() {
  String body = httpGet("/prices");
  StaticJsonDocument<1024> doc;
  if (deserializeJson(doc, body)) return;
  float mWETH = doc["WETH:USDso"]["mid"];
  float mWBTC = doc["WBTC:USDso"]["mid"];
  float mSOMI = doc["SOMI:USDso"]["mid"];
  priceWETH = String(mWETH, 2);
  priceWBTC = String(mWBTC, 0);
  priceSOMI = String(mSOMI, 5);
  pushHist(histWETH, mWETH);
  pushHist(histWBTC, mWBTC);
  pushHist(histSOMI, mSOMI);
}

void fetchAgent() {
  String body = httpGet("/agent");
  StaticJsonDocument<512> doc;
  if (deserializeJson(doc, body)) return;
  agentPaused    = doc["paused"] ? "true" : "false";
  agentTxs       = String((int)doc["state"]["tx_count"]);
  agentOrdersDone = doc["orders_done"] | (int)doc["state"]["tx_count"];
  agentMaxOrders  = doc["max_orders"]  | 0;
  String action  = doc["last_decision"]["action"] | "hold";
  String pair    = doc["last_decision"]["pair"]   | "-";
  String reason  = doc["last_decision"]["reason"] | "-";
  agentLast = action + " " + pair.substring(0, 4) + ": " + reason;
}

void fetchPortfolio() {
  String body = httpGet("/portfolio");
  StaticJsonDocument<512> doc;
  if (deserializeJson(doc, body)) return;
  portAgent  = String((float)doc["agent_balance"],  2);
  portManual = String((float)doc["manual_balance"], 2);
  portTotal  = (float)doc["total_value"];
  portWalletUsdso = (float)doc["usdso_wallet"];
  // Sum across all pool vaults
  portVaultsTotal = 0.0f;
  JsonObject vaults = doc["usdso_vaults"].as<JsonObject>();
  if (!vaults.isNull()) {
    for (JsonPair kv : vaults) {
      portVaultsTotal += (float)kv.value();
    }
  }
  float pnl  = portTotal - 50.0f;
  portPnL    = (pnl >= 0.0f ? "+" : "") + String(pnl, 2);
}

void fetchLeaderboard() {
  String body = httpGet("/leaderboard");
  StaticJsonDocument<512> doc;
  if (deserializeJson(doc, body)) return;
  lbLive = doc["live"] ? "true" : "false";
  if (doc["my_rank"].is<int>())
    lbRank = String((int)doc["my_rank"]);
  else
    lbRank = String(doc["my_rank"] | "?");
  lbTxs    = String((int)doc["my_tx"]);
  lbGap    = String((int)doc["gap"]);
  lbSignal = String(doc["signal"] | "--");
  if      (lbSignal == "ACCELERATE") lbSignal = "ACCEL";
  else if (lbSignal == "MAINTAIN")   lbSignal = "OK";
  else if (lbSignal == "SLOW DOWN")  lbSignal = "SLOW";
  else if (lbSignal == "MAX SPEED")  lbSignal = "MAX";
}

// ═════════════════════════════════════════════════════════
// TRADE ACTIONS
// ═════════════════════════════════════════════════════════

void sendManualTrade() {
  const char* pairsFull[] = { "WETH:USDso", "WBTC:USDso", "SOMI:USDso" };
  // Arduino String::toLowerCase() mutates in place — build separately
  String sideLower = manualSides[manualSideIdx];
  sideLower.toLowerCase();
  String payload =
    "{\"pair\":\"" + String(pairsFull[manualPairIdx]) +
    "\",\"side\":\"" + sideLower +
    "\",\"amount_usdso\":" + String(manualAmt, 2) + "}";

  display.clearDisplay();
  display.setCursor(0, 14); display.print("Sending trade...");
  display.setCursor(0, 26); display.print(manualPairs[manualPairIdx]);
  display.print(" "); display.print(manualSides[manualSideIdx]);
  display.setCursor(0, 38); display.print("$"); display.print(manualAmt, 2);
  display.display();

  httpPost("/manual", payload);
  delay(1000);

  // Refresh portfolio immediately after trade
  fetchPortfolio();
}

void postToggleAgent() {
  httpPost("/agent/toggle", "{}");
  delay(300);
  fetchAgent();
}

void postSpeed() {
  // Build lowercase speed string manually (toLowerCase mutates)
  String s = speeds[cfgSpeedIdx];
  s.toLowerCase();
  httpPost("/agent/speed", "{\"speed\":\"" + s + "\"}");
}

void postMaxOrders() {
  httpPost("/agent/max_orders",
           "{\"max_orders\":" + String(agentMaxOrders) + "}");
}
