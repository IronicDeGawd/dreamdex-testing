// button_check.ino — buttons + OLED combined bring-up.
// Verifies BTN_UP (GPIO3), BTN_DOWN (GPIO4), BTN_SELECT (GPIO5) on the
// ESP32-C3 SuperMini against the SSD1306 OLED on GPIO8/9.
// No WiFi, no HTTP — if anything fails here it's hardware or wiring.
//
// Wiring (each button to GND, with INPUT_PULLUP — no external resistors):
//   GPIO3 → BTN_UP     → GND
//   GPIO4 → BTN_DOWN   → GND
//   GPIO5 → BTN_SELECT → GND
//
// What it shows:
//   Row 1  state of UP/DOWN/SELECT (filled = pressed)
//   Row 2  press counters
//   Row 3  last event (PRESS / RELEASE / LONG)
//   Row 4  a "long-press" status bar that fills as you hold SELECT
//
// What to verify:
//   1. UP/DOWN/SELECT each register independently
//   2. No phantom presses (counter doesn't tick when nothing is touched)
//   3. Long-press fires once at the 600ms mark on SELECT
//   4. Serial Monitor shows matching log lines

#include <Arduino.h>
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>

#define SCREEN_W  128
#define SCREEN_H  64
#define OLED_ADDR 0x3C
#define SDA_PIN   8
#define SCL_PIN   9

#define BTN_UP        3
#define BTN_DOWN      4
#define BTN_SELECT    5
#define LONG_PRESS_MS 600
#define DEBOUNCE_MS   25

Adafruit_SSD1306 display(SCREEN_W, SCREEN_H, &Wire, -1);

struct Btn {
  uint8_t  pin;
  const char* name;
  bool     state;           // true = pressed (active low)
  bool     stable;          // debounced
  uint32_t lastChangeMs;
  uint32_t pressedAtMs;
  uint32_t pressCount;
  uint32_t longCount;
  bool     longFired;
};

Btn buttons[3] = {
  { BTN_UP,     "UP",  false, false, 0, 0, 0, 0, false },
  { BTN_DOWN,   "DN",  false, false, 0, 0, 0, 0, false },
  { BTN_SELECT, "SEL", false, false, 0, 0, 0, 0, false },
};

String lastEvent = "(waiting)";
uint32_t lastEventAt = 0;

void setup() {
  Serial.begin(115200);
  delay(300);
  Serial.println("\n=== button_check ===");

  for (auto &b : buttons) {
    pinMode(b.pin, INPUT_PULLUP);
    Serial.printf("  %s on GPIO%u\n", b.name, b.pin);
  }

  Wire.begin(SDA_PIN, SCL_PIN);
  if (!display.begin(SSD1306_SWITCHCAPVCC, OLED_ADDR)) {
    Serial.println("[oled] init FAILED — run screen_check first");
    while (true) delay(1000);
  }
  display.setTextColor(SSD1306_WHITE);
  display.clearDisplay();
  display.setTextSize(1);
  display.setCursor(0, 0);  display.print("button_check");
  display.setCursor(0, 12); display.print("UP=GPIO3");
  display.setCursor(0, 22); display.print("DN=GPIO4");
  display.setCursor(0, 32); display.print("SEL=GPIO5");
  display.setCursor(0, 48); display.print("press any button");
  display.display();
  delay(1200);
}

void pollButton(Btn &b, uint32_t now) {
  bool raw = (digitalRead(b.pin) == LOW);  // active low

  // Debounce: only accept a new state after DEBOUNCE_MS of stability
  if (raw != b.state) {
    b.state = raw;
    b.lastChangeMs = now;
  }
  if (now - b.lastChangeMs >= DEBOUNCE_MS && b.stable != b.state) {
    b.stable = b.state;
    if (b.stable) {
      // press edge
      b.pressedAtMs = now;
      b.longFired   = false;
      b.pressCount++;
      lastEvent = String(b.name) + " PRESS";
      lastEventAt = now;
      Serial.printf("[%lu] %s PRESS  (count=%lu)\n", now, b.name, b.pressCount);
    } else {
      // release edge
      uint32_t held = now - b.pressedAtMs;
      lastEvent = String(b.name) + " REL " + held + "ms";
      lastEventAt = now;
      Serial.printf("[%lu] %s RELEASE held=%lums\n", now, b.name, held);
    }
  }

  // Long-press detection — fire once while held
  if (b.stable && !b.longFired && (now - b.pressedAtMs) >= LONG_PRESS_MS) {
    b.longFired = true;
    b.longCount++;
    lastEvent = String(b.name) + " LONG";
    lastEventAt = now;
    Serial.printf("[%lu] %s LONG-PRESS (count=%lu)\n", now, b.name, b.longCount);
  }
}

void loop() {
  uint32_t now = millis();
  for (auto &b : buttons) pollButton(b, now);

  display.clearDisplay();

  // Row 1: live button state — filled square = pressed
  display.setTextSize(1);
  int x = 0;
  for (auto &b : buttons) {
    display.drawRect(x, 0, 38, 14, SSD1306_WHITE);
    if (b.stable) display.fillRect(x + 2, 2, 34, 10, SSD1306_WHITE);
    display.setCursor(x + 6, 3);
    display.setTextColor(b.stable ? SSD1306_BLACK : SSD1306_WHITE);
    display.print(b.name);
    x += 44;
  }
  display.setTextColor(SSD1306_WHITE);

  // Row 2: press counts
  display.setCursor(0, 18);
  display.printf("U:%lu  D:%lu  S:%lu",
                 buttons[0].pressCount, buttons[1].pressCount, buttons[2].pressCount);

  // Row 3: long-press counts
  display.setCursor(0, 28);
  display.printf("long S:%lu  D:%lu  U:%lu",
                 buttons[2].longCount, buttons[1].longCount, buttons[0].longCount);

  // Row 4: last event (truncated)
  display.setCursor(0, 40);
  display.print("evt:");
  display.print(lastEvent.substring(0, 18));

  // Row 5: SELECT hold bar — fills as you approach 600ms
  if (buttons[2].stable) {
    uint32_t held = now - buttons[2].pressedAtMs;
    int barW = 120;
    int fill = (int)(((float)min(held, (uint32_t)LONG_PRESS_MS) / LONG_PRESS_MS) * barW);
    display.drawRect(0, 54, barW, 8, SSD1306_WHITE);
    display.fillRect(0, 54, fill, 8, SSD1306_WHITE);
  } else {
    display.setCursor(0, 54);
    display.print("hold SEL for long");
  }

  display.display();
  delay(20);  // tight loop — debounce/longpress timing depends on it
}
