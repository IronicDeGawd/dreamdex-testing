// screen_check.ino — minimal OLED bring-up for ESP32-C3 SuperMini
// Verifies: I2C wiring on GPIO8/9, SSD1306 at 0x3C, display draw works.
// Flash this BEFORE watch.ino to isolate display issues from WiFi/HTTP.
//
// Arduino IDE:
//   Tools → Board       → ESP32 → "ESP32C3 Dev Module"
//   Tools → USB CDC On Boot → Enabled   (so Serial shows up over USB-C)
//   Tools → Port        → /dev/cu.usbmodem*  (or COMx on Windows)
//
// Wiring (ESP32-C3 SuperMini → SSD1306):
//   3.3   → VCC
//   GND   → GND
//   GPIO8 → SDA
//   GPIO9 → SCL
//
// What you should see:
//   1. Splash: "Screen Check" / "ESP32-C3"
//   2. Animated counter that ticks every 500ms
//   3. A square that bounces left↔right across the bottom
//   4. Serial Monitor (115200) prints "tick N" each frame
//
// If screen is BLANK:
//   - SDA/SCL swapped?  Try swapping GPIO8 and GPIO9 wires.
//   - Wrong I2C address? Some SSD1306 boards are 0x3D, not 0x3C — try the
//     I2C_SCAN block (uncomment SCAN_ONLY below) to confirm.
//   - Bad solder joint on VCC/GND? Measure 3.3V across VCC↔GND with a meter.

#include <Arduino.h>
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>

#define SCREEN_W  128
#define SCREEN_H  64
#define OLED_ADDR 0x3C   // try 0x3D if 0x3C shows nothing
#define SDA_PIN   8
#define SCL_PIN   9

// Set to 1 to skip drawing and only do an I2C bus scan, then halt.
#define SCAN_ONLY 0

Adafruit_SSD1306 display(SCREEN_W, SCREEN_H, &Wire, -1);

void i2cScan() {
  Serial.println("[i2c] scanning bus...");
  uint8_t found = 0;
  for (uint8_t addr = 1; addr < 127; addr++) {
    Wire.beginTransmission(addr);
    if (Wire.endTransmission() == 0) {
      Serial.printf("[i2c]   device @ 0x%02X\n", addr);
      found++;
    }
  }
  Serial.printf("[i2c] done — %u device(s)\n", found);
}

void setup() {
  Serial.begin(115200);
  delay(300);
  Serial.println();
  Serial.println("=== screen_check (ESP32-C3 SuperMini) ===");
  Serial.printf("SDA=GPIO%d  SCL=GPIO%d  expected OLED @ 0x%02X\n",
                SDA_PIN, SCL_PIN, OLED_ADDR);

  Wire.begin(SDA_PIN, SCL_PIN);
  i2cScan();

#if SCAN_ONLY
  Serial.println("[scan-only] halting. Set SCAN_ONLY 0 to draw.");
  while (true) delay(1000);
#endif

  if (!display.begin(SSD1306_SWITCHCAPVCC, OLED_ADDR)) {
    Serial.println("[oled] SSD1306 begin() FAILED");
    Serial.println("[oled] check wiring or try OLED_ADDR = 0x3D");
    while (true) delay(1000);
  }
  Serial.println("[oled] init OK");

  display.clearDisplay();
  display.setTextColor(SSD1306_WHITE);

  // Splash
  display.setTextSize(2);
  display.setCursor(0, 4);
  display.print("Screen");
  display.setCursor(0, 22);
  display.print("Check");
  display.setTextSize(1);
  display.setCursor(0, 48);
  display.print("ESP32-C3 SuperMini");
  display.setCursor(0, 56);
  display.print("OLED 0x");
  display.print(OLED_ADDR, HEX);
  display.print(" OK");
  display.display();
  delay(1500);
}

uint32_t tick = 0;

void loop() {
  display.clearDisplay();

  // Header
  display.setTextSize(1);
  display.setCursor(0, 0);
  display.print("OLED OK  GPIO8/9");
  display.drawLine(0, 9, 127, 9, SSD1306_WHITE);

  // Tick counter (big)
  display.setTextSize(2);
  display.setCursor(0, 16);
  display.print("tick:");
  display.setCursor(0, 36);
  display.print(tick);

  // Bouncing square as motion proof
  int span = SCREEN_W - 10;
  int phase = tick % (span * 2);
  int x = (phase < span) ? phase : (span * 2 - phase);
  display.fillRect(x, 56, 8, 8, SSD1306_WHITE);

  // Frame border so a dead pixel column is obvious
  display.drawRect(0, 10, SCREEN_W, SCREEN_H - 10, SSD1306_WHITE);

  display.display();
  Serial.printf("tick %lu\n", (unsigned long)tick);
  tick++;
  delay(500);
}
