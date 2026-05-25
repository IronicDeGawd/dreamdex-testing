#include <Arduino.h>
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>

// ── Board: ESP32-C3 SuperMini v1601 ──────────────────────
// (migrated from XIAO ESP32-S3 — Aug 2024 hardware change)
// Arduino IDE:
//   Tools → Board → ESP32 → "ESP32C3 Dev Module"
//   Tools → USB CDC On Boot → Enabled  (required for Serial monitor)
// Power:
//   The C3 SuperMini has NO dedicated LiPo pad. Wire TP4056 OUT+ to the
//   board's 5V pin (NOT 3.3V) — onboard regulator handles step-down.

// ── Display ──────────────────────────────────────────────
#define SCREEN_W  128
#define SCREEN_H  64
#define OLED_ADDR 0x3C
#define SDA_PIN   8   // C3: GPIO8 — same as XIAO
#define SCL_PIN   9   // C3: GPIO9 — same as XIAO
Adafruit_SSD1306 display(SCREEN_W, SCREEN_H, &Wire, -1);

// ── Buttons (C3 SuperMini pinout) ─────────────────────────
// XIAO used 1/2/3; C3 boot strapping conflicts with GPIO2/8/9 — moved up.
#define BTN_UP     3   // was 1 on XIAO
#define BTN_DOWN   4   // was 2 on XIAO
#define BTN_SELECT 5   // was 3 on XIAO
#define LONG_PRESS_MS 600

// ── Backend ──────────────────────────────────────────────
// Laptop IP on en0 (Wi-Fi). Flask runs on 5001 (avoids macOS AirPlay :5000).
// If laptop changes networks or DHCP reassigns, re-check with `ifconfig en0`.
const char* BACKEND = "http://192.168.1.5:5001";

// ── Known WiFi networks ───────────────────────────────────
// Real credentials live in firmware/wifi_secrets.h (gitignored).
// Copy wifi_secrets.example.h to wifi_secrets.h and fill in your networks.
#include "wifi_secrets.h"
const int NUM_NETS = sizeof(knownNets) / sizeof(knownNets[0]);

// ── Menus ────────────────────────────────────────────────
enum Menu { 
  MENU_WIFI=0, MENU_PRICES, MENU_AGENT, 
  MENU_PORTFOLIO, MENU_LEADERBOARD, 
  MENU_MANUAL, MENU_CONFIG,
  MENU_COUNT
};
const char* menuNames[] = {
  "WiFi", "Prices", "Agent",
  "Portfolio", "Leaderboard",
  "Manual Trade", "Config"
};

// ── State ────────────────────────────────────────────────
Menu    currentMenu    = MENU_PRICES;
int     menuScroll     = 0;
bool    inSubMenu      = false;
String  statusMsg      = "";
unsigned long lastFetch= 0;
unsigned long lastBtn  = 0;
const int FETCH_EVERY  = 10000; // fetch every 10s

// Data cache
String priceWETH="--", priceWBTC="--", priceSOMI="--";
String agentStatus="--", agentLast="--", agentTxs="--";
String agentPaused="NO";
int    agentMaxOrders = 100;   // mirror of server-side cap
int    agentOrdersDone = 0;
String portAgent="--", portManual="--", portPnL="--";
String lbRank="--", lbTxs="--", lbSignal="--", lbGap="--";

// Manual trade state (testnet: 3 pairs — WETH, WBTC, SOMI)
int    manualPairIdx  = 0;  // 0=WETH 1=WBTC 2=SOMI
int    manualSideIdx  = 0;  // 0=BUY  1=SELL
float  manualAmt      = 1.0;
int    manualField    = 0;  // which field is selected (0=pair,1=side,2=amt,3=send)
const char* manualPairs[] = {"WETH","WBTC","SOMI"};
const char* manualSides[] = {"BUY","SELL"};

// Config state
int    cfgSpeedIdx   = 1;  // 0=slow 1=normal 2=fast 3=max
const char* speeds[] = {"SLOW","NORMAL","FAST","MAX"};
int    cfgField      = 0;  // 0=speed, 1=max_orders, 2=pause toggle
const int MAX_ORDERS_STEP = 10;

// WiFi scan state
int    wifiScroll   = 0;
int    wifiCount    = 0;
String wifiSSIDs[10];
int    wifiRSSIs[10];
bool   wifiKnown[10];
bool   wifiScanned  = false;

// ── Setup ─────────────────────────────────────────────────
void setup() {
  Serial.begin(115200);

  // Buttons
  pinMode(BTN_UP,     INPUT_PULLUP);
  pinMode(BTN_DOWN,   INPUT_PULLUP);
  pinMode(BTN_SELECT, INPUT_PULLUP);

  // OLED
  Wire.begin(SDA_PIN, SCL_PIN);
  if (!display.begin(SSD1306_SWITCHCAPVCC, OLED_ADDR)) {
    Serial.println("SSD1306 not found");
    while(true);
  }
  display.setTextColor(SSD1306_WHITE);
  display.clearDisplay();
  showSplash();

  // WiFi
  currentMenu = MENU_WIFI;
  scanWiFi();
}

// ── Loop ──────────────────────────────────────────────────
void loop() {
  handleButtons();

  // Fetch data every 10s (not on WiFi menu)
  if (currentMenu != MENU_WIFI && 
      millis() - lastFetch > FETCH_EVERY) {
    fetchData();
    lastFetch = millis();
  }

  drawMenu();
  delay(50);
}

// ══════════════════════════════════════════════════════════
// DISPLAY FUNCTIONS
// ══════════════════════════════════════════════════════════

void showSplash() {
  display.clearDisplay();
  display.setTextSize(2);
  display.setCursor(10, 8);
  display.print("DreamDEX");
  display.setTextSize(1);
  display.setCursor(25, 30);
  display.print("Trading Watch");
  display.setCursor(20, 45);
  display.print("0xF4c8...2905");
  display.display();
  delay(2000);
}

void drawMenu() {
  display.clearDisplay();
  switch(currentMenu) {
    case MENU_WIFI:         drawWiFi();        break;
    case MENU_PRICES:       drawPrices();      break;
    case MENU_AGENT:        drawAgent();       break;
    case MENU_PORTFOLIO:    drawPortfolio();   break;
    case MENU_LEADERBOARD:  drawLeaderboard(); break;
    case MENU_MANUAL:       drawManual();      break;
    case MENU_CONFIG:       drawConfig();      break;
  }
  display.display();
}

// ── Header helper ─────────────────────────────────────────
void header(const char* title) {
  display.setTextSize(1);
  display.setCursor(0, 0);
  display.print(title);
  display.drawLine(0, 9, 127, 9, SSD1306_WHITE);
}

// ── Nav dots ──────────────────────────────────────────────
void navDots() {
  // Small dots at bottom showing menu position
  int totalMenus = MENU_COUNT;
  int dotSpacing = 128 / totalMenus;
  for (int i = 0; i < totalMenus; i++) {
    int x = i * dotSpacing + dotSpacing/2;
    if (i == (int)currentMenu)
      display.fillCircle(x, 62, 2, SSD1306_WHITE);
    else
      display.drawCircle(x, 62, 1, SSD1306_WHITE);
  }
}

// ── WiFi Menu ─────────────────────────────────────────────
void drawWiFi() {
  header("< WiFi Select >");
  if (!wifiScanned) {
    display.setCursor(20, 28);
    display.print("Scanning...");
    return;
  }
  if (WiFi.status() == WL_CONNECTED) {
    display.setCursor(0, 12);
    display.print("Connected:");
    display.setCursor(0, 22);
    display.print(WiFi.SSID().substring(0,18));
    display.setCursor(0, 34);
    display.print("IP: ");
    display.print(WiFi.localIP());
    display.setCursor(0, 46);
    display.print("[SELECT] change WiFi");
    return;
  }
  // Show scan results
  for (int i = 0; i < min(wifiCount, 4); i++) {
    int idx = (wifiScroll + i) % wifiCount;
    int y = 12 + i * 12;
    // Cursor arrow
    if (i == 0) display.print(">");
    display.setCursor(8, y);
    // Known indicator
    if (wifiKnown[idx]) display.print("*");
    display.print(wifiSSIDs[idx].substring(0, 13));
    // Signal strength
    display.setCursor(100, y);
    display.print(wifiRSSIs[idx]);
  }
  display.setCursor(0, 56);
  display.print("*=known  SELECT=conn");
}

// ── Prices Menu ────────────────────────────────────────────
void drawPrices() {
  header("< Prices >");
  display.setTextSize(1);
  display.setCursor(0,  14); display.print("WETH  $"); display.print(priceWETH);
  display.setCursor(0,  26); display.print("WBTC  $"); display.print(priceWBTC);
  display.setCursor(0,  38); display.print("SOMI  $"); display.print(priceSOMI);
  // Agent pause/play hint right on home screen
  display.setCursor(0, 50);
  display.print(agentPaused=="true" ? "[Agent: PAUSED]" : "[Agent: PLAYING]");
  navDots();
}

// ── Agent Menu ─────────────────────────────────────────────
void drawAgent() {
  header("< Agent >");
  // Big play/pause indicator
  display.setTextSize(2);
  display.setCursor(0, 12);
  display.print(agentPaused=="true" ? "|| PAUSED" : "> PLAY");
  display.setTextSize(1);
  // Last decision
  display.setCursor(0, 32);
  display.print(agentLast.substring(0, 21));
  // Orders progress bar: N / MAX
  display.setCursor(0, 44);
  display.print("Orders: ");
  display.print(agentOrdersDone);
  display.print("/");
  if (agentMaxOrders == 0) display.print("inf");
  else                     display.print(agentMaxOrders);
  // Progress bar
  if (agentMaxOrders > 0) {
    int barW = 120;
    int filled = (int)(((float)agentOrdersDone / agentMaxOrders) * barW);
    if (filled > barW) filled = barW;
    display.drawRect(0, 54, barW, 5, SSD1306_WHITE);
    display.fillRect(0, 54, filled, 5, SSD1306_WHITE);
  }
  // No navDots — bar is at the bottom now
}

// ── Portfolio Menu ─────────────────────────────────────────
void drawPortfolio() {
  header("< Portfolio >");
  display.setCursor(0, 12);
  display.print("Agent:  $"); display.print(portAgent);
  display.setCursor(0, 24);
  display.print("Manual: $"); display.print(portManual);
  display.setCursor(0, 36);
  display.print("P&L:    $"); display.print(portPnL);
  navDots();
}

// ── Leaderboard Menu ──────────────────────────────────────
String lbLive = "false";   // "true" once Vercel deploy is up

void drawLeaderboard() {
  header("< Leaderboard >");
  if (lbLive != "true") {
    // Mainnet leaderboard isn't deployed yet — show a holding screen
    // instead of "#?" which looks like a parsing bug.
    display.setCursor(0, 16);
    display.print("Mainnet board");
    display.setCursor(0, 26);
    display.print("not live yet.");
    display.setCursor(0, 40);
    display.print("Tracks wallet:");
    display.setCursor(0, 50);
    display.print("0xF4c8...2905");
    return;
  }
  display.setTextSize(2);
  display.setCursor(30, 14);
  display.print("#"); display.print(lbRank);
  display.setTextSize(1);
  display.setCursor(0, 34);
  display.print("Txs: "); display.print(lbTxs);
  display.setCursor(0, 44);
  display.print("Gap: "); display.print(lbGap);
  display.print(" | ");   display.print(lbSignal);
  navDots();
}

// ── Manual Trade Menu ─────────────────────────────────────
void drawManual() {
  header("< Manual Trade >");

  // Pair row
  display.setCursor(0, 12);
  if (manualField == 0) display.print(">");
  display.setCursor(8,12);
  display.print("Pair: ");
  display.print(manualPairs[manualPairIdx]);

  // Side row
  display.setCursor(0, 24);
  if (manualField == 1) display.print(">");
  display.setCursor(8, 24);
  display.print("Side: ");
  display.print(manualSides[manualSideIdx]);

  // Amount row
  display.setCursor(0, 36);
  if (manualField == 2) display.print(">");
  display.setCursor(8, 36);
  display.print("Amt:  $");
  display.print(manualAmt, 2);

  // Send button
  display.setCursor(0, 50);
  if (manualField == 3) {
    display.print("[>> SEND TRADE <<]");
  } else {
    display.print("  [SEL to choose]");
  }
}

// ── Config Menu ───────────────────────────────────────────
void drawConfig() {
  header("< Config >");
  // Field 0: speed
  display.setCursor(0, 12);
  display.print(cfgField == 0 ? ">" : " ");
  display.print(" Speed: ");
  display.print(speeds[cfgSpeedIdx]);

  // Field 1: max orders
  display.setCursor(0, 24);
  display.print(cfgField == 1 ? ">" : " ");
  display.print(" Max Ord: ");
  if (agentMaxOrders == 0) display.print("inf");
  else                     display.print(agentMaxOrders);

  // Field 2: play/pause
  display.setCursor(0, 36);
  display.print(cfgField == 2 ? ">" : " ");
  display.print(" Agent: ");
  display.print(agentPaused=="true" ? "PAUSED" : "PLAY");

  // Hint
  display.setCursor(0, 52);
  display.print("SEL=cycle UP/DN=edit");
}

// ══════════════════════════════════════════════════════════
// BUTTON HANDLING
// ══════════════════════════════════════════════════════════

unsigned long btnSelectDown = 0;
bool          btnSelectHeld = false;

void handleButtons() {
  bool up  = (digitalRead(BTN_UP)   == LOW);
  bool dn  = (digitalRead(BTN_DOWN) == LOW);
  bool sel = (digitalRead(BTN_SELECT)== LOW);

  // Debounce
  if (millis() - lastBtn < 200) return;

  // Long press detection on SELECT = go back to prev menu
  if (sel) {
    if (btnSelectDown == 0) btnSelectDown = millis();
    if (!btnSelectHeld && millis() - btnSelectDown > LONG_PRESS_MS) {
      btnSelectHeld = true;
      onLongPress();
      lastBtn = millis();
    }
    return;
  } else {
    if (btnSelectDown > 0 && !btnSelectHeld) {
      onShortPress();
      lastBtn = millis();
    }
    btnSelectDown = 0;
    btnSelectHeld = false;
  }

  if (up) { onUp(); lastBtn = millis(); }
  if (dn) { onDown(); lastBtn = millis(); }
}

void onUp() {
  switch(currentMenu) {
    case MENU_WIFI:
      wifiScroll = (wifiScroll - 1 + wifiCount) % max(wifiCount,1);
      break;
    case MENU_PRICES:
    case MENU_AGENT:
    case MENU_PORTFOLIO:
    case MENU_LEADERBOARD:
      // Cycle to previous menu
      currentMenu = (Menu)(((int)currentMenu - 1 + MENU_COUNT) % MENU_COUNT);
      break;
    case MENU_MANUAL:
      adjustManual(-1);
      break;
    case MENU_CONFIG:
      adjustConfig(-1);
      break;
  }
}

void onDown() {
  switch(currentMenu) {
    case MENU_WIFI:
      wifiScroll = (wifiScroll + 1) % max(wifiCount, 1);
      break;
    case MENU_PRICES:
    case MENU_AGENT:
    case MENU_PORTFOLIO:
    case MENU_LEADERBOARD:
      currentMenu = (Menu)(((int)currentMenu + 1) % MENU_COUNT);
      break;
    case MENU_MANUAL:
      adjustManual(1);
      break;
    case MENU_CONFIG:
      adjustConfig(1);
      break;
  }
}

void onShortPress() {
  switch(currentMenu) {
    case MENU_WIFI:
      connectToSelected();
      break;
    case MENU_AGENT:
      postToggleAgent();
      break;
    case MENU_MANUAL:
      manualField = (manualField + 1) % 4;
      if (manualField == 0 && /* just wrapped */ true) {
        // Send when field rolls back to 0 after SEND
      }
      if (manualField == 3) {
        // On SEND field, select triggers trade
        sendManualTrade();
        manualField = 0;
      }
      break;
    case MENU_CONFIG:
      // Cycle the focused field; on the pause field SEL toggles agent state.
      if (cfgField == 2) {
        postToggleAgent();
      }
      cfgField = (cfgField + 1) % 3;
      break;
    default:
      break;
  }
}

void onLongPress() {
  // Long press = go back / home
  if (currentMenu == MENU_MANUAL) {
    manualField = 0;
  }
  currentMenu = MENU_PRICES; // home
}

void adjustManual(int dir) {
  switch(manualField) {
    case 0: // pair
      manualPairIdx = (manualPairIdx + dir + 3) % 3;
      break;
    case 1: // side
      manualSideIdx = (manualSideIdx + dir + 2) % 2;
      break;
    case 2: // amount
      manualAmt += dir * 0.5;
      if (manualAmt < 0.1) manualAmt = 0.1;
      if (manualAmt > 10)  manualAmt = 10;
      break;
  }
}

void adjustConfig(int dir) {
  switch(cfgField) {
    case 0: // speed
      cfgSpeedIdx = (cfgSpeedIdx + dir + 4) % 4;
      postSpeed();
      break;
    case 1: // max orders, step of 10. -10 from minimum wraps to "inf" (0).
      agentMaxOrders += dir * MAX_ORDERS_STEP;
      if (agentMaxOrders < 0) agentMaxOrders = 0;        // 0 = unlimited
      if (agentMaxOrders > 1000) agentMaxOrders = 1000;
      postMaxOrders();
      break;
    case 2: // pause/play — UP or DOWN both toggle
      postToggleAgent();
      break;
  }
}

// ══════════════════════════════════════════════════════════
// WIFI
// ══════════════════════════════════════════════════════════

void scanWiFi() {
  display.clearDisplay();
  display.setCursor(15,25);
  display.setTextSize(1);
  display.print("Scanning WiFi...");
  display.display();

  wifiCount = WiFi.scanNetworks();
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
}

void connectToSelected() {
  int idx = wifiScroll % wifiCount;
  if (!wifiKnown[idx]) {
    statusMsg = "Unknown network";
    return;
  }
  String ssid = wifiSSIDs[idx];
  const char* pass = "";
  for (int k = 0; k < NUM_NETS; k++) {
    if (ssid == knownNets[k].ssid) {
      pass = knownNets[k].pass;
      break;
    }
  }
  display.clearDisplay();
  display.setCursor(0,20);
  display.print("Connecting...");
  display.print(ssid);
  display.display();

  WiFi.begin(ssid.c_str(), pass);
  int tries = 0;
  while (WiFi.status() != WL_CONNECTED && tries < 20) {
    delay(500); tries++;
  }
  if (WiFi.status() == WL_CONNECTED) {
    currentMenu = MENU_PRICES;
    fetchData();
  }
}

// ══════════════════════════════════════════════════════════
// DATA FETCHING
// ══════════════════════════════════════════════════════════

String httpGet(String path) {
  if (WiFi.status() != WL_CONNECTED) return "{}";
  HTTPClient http;
  http.begin(String(BACKEND) + path);
  http.setTimeout(5000);
  int code = http.GET();
  String body = (code == 200) ? http.getString() : "{}";
  http.end();
  return body;
}

void httpPost(String path, String payload) {
  if (WiFi.status() != WL_CONNECTED) return;
  HTTPClient http;
  http.begin(String(BACKEND) + path);
  http.addHeader("Content-Type", "application/json");
  http.setTimeout(5000);
  http.POST(payload);
  http.end();
}

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
  // Testnet only has 3 pairs (WETH/WBTC/SOMI). USDC.e pulled — was always "--".
  priceWETH = String((float)doc["WETH:USDso"]["mid"], 2);
  priceWBTC = String((float)doc["WBTC:USDso"]["mid"], 0);
  priceSOMI = String((float)doc["SOMI:USDso"]["mid"], 5);
}

void fetchAgent() {
  String body = httpGet("/agent");
  StaticJsonDocument<512> doc;
  if (deserializeJson(doc, body)) return;
  agentPaused = doc["paused"] ? "true" : "false";
  agentTxs    = String((int)doc["state"]["tx_count"]);
  agentOrdersDone = doc["orders_done"] | (int)doc["state"]["tx_count"];
  agentMaxOrders  = doc["max_orders"]  | 0;
  String action = doc["last_decision"]["action"] | "hold";
  String pair   = doc["last_decision"]["pair"]   | "-";
  String reason = doc["last_decision"]["reason"] | "-";
  agentLast = action + " " + pair.substring(0,4) + ": " + reason;
}

void fetchPortfolio() {
  String body = httpGet("/portfolio");
  StaticJsonDocument<512> doc;
  if (deserializeJson(doc, body)) return;
  portAgent  = String((float)doc["agent_balance"],  2);
  portManual = String((float)doc["manual_balance"], 2);
  float pnl  = (float)doc["total_value"] - 50.0;
  portPnL    = (pnl >= 0 ? "+" : "") + String(pnl, 2);
}

void fetchLeaderboard() {
  String body = httpGet("/leaderboard");
  StaticJsonDocument<512> doc;
  if (deserializeJson(doc, body)) return;
  lbLive   = doc["live"] ? "true" : "false";
  // my_rank can be int or "?" string — coerce safely.
  // NB: ArduinoJson's `|` default operator must be applied BEFORE the C cast,
  // otherwise `|` becomes bitwise OR on a const char* and fails to compile.
  if (doc["my_rank"].is<int>()) lbRank = String((int)doc["my_rank"]);
  else                          lbRank = String((const char*)(doc["my_rank"] | "?"));
  lbTxs    = String((int)doc["my_tx"]);
  lbGap    = String((int)doc["gap"]);
  lbSignal = String(doc["signal"] | "--");
  // Truncate signal for display
  if (lbSignal == "ACCELERATE")  lbSignal = "ACCEL";
  if (lbSignal == "MAINTAIN")    lbSignal = "OK";
  if (lbSignal == "SLOW DOWN")   lbSignal = "SLOW";
  if (lbSignal == "MAX SPEED")   lbSignal = "MAX";
}

// ══════════════════════════════════════════════════════════
// ACTIONS
// ══════════════════════════════════════════════════════════

void sendManualTrade() {
  String pairs[] = {"WETH:USDso","WBTC:USDso","SOMI:USDso"};
  // Arduino String::toLowerCase() returns void (mutates in place), so it
  // can't be inlined into a concat — build the lowercase side separately.
  String sideLower = manualSides[manualSideIdx];
  sideLower.toLowerCase();
  String payload = "{\"pair\":\"" + pairs[manualPairIdx] +
                   "\",\"side\":\"" + sideLower +
                   "\",\"amount_usdso\":" + String(manualAmt, 2) + "}";
  display.clearDisplay();
  display.setCursor(20, 24);
  display.print("Sending trade...");
  display.display();
  httpPost("/manual", payload);
  delay(1000);
}

void postToggleAgent() {
  httpPost("/agent/toggle", "{}");
  delay(300);
  fetchAgent();
}

void postSpeed() {
  String payload = "{\"speed\":\"" + String(speeds[cfgSpeedIdx]) + "\"}";
  // lowercase it
  payload.toLowerCase();
  httpPost("/agent/speed", payload);
}

void postMaxOrders() {
  String payload = "{\"max_orders\":" + String(agentMaxOrders) + "}";
  httpPost("/agent/max_orders", payload);
}
