// wifi_secrets.example.h — committed placeholder. Copy to wifi_secrets.h
// and fill in your real SSIDs + passwords. wifi_secrets.h is gitignored.
#pragma once

struct WiFiNet { const char* ssid; const char* pass; };

static WiFiNet knownNets[] = {
  {"YOUR_SSID_HERE",        "YOUR_PASSWORD_HERE"},
  {"YOUR_BACKUP_SSID_HERE", "YOUR_BACKUP_PASSWORD_HERE"},
};

// Shared-secret API key. MUST match backend's FLASK_API_KEY env var.
// Mainnet backend refuses /manual + /agent/* + /vault/* without this header.
// Generate a random string e.g. `openssl rand -hex 16` and use the same value
// in both places. Comment out (or leave undefined) for testnet dev mode.
#define API_KEY "REPLACE_WITH_RANDOM_HEX_STRING"
