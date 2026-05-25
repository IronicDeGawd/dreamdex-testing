// wifi_secrets.example.h — committed placeholder. Copy to wifi_secrets.h
// and fill in your real SSIDs + passwords. wifi_secrets.h is gitignored.
#pragma once

struct WiFiNet { const char* ssid; const char* pass; };

static WiFiNet knownNets[] = {
  {"YOUR_SSID_HERE",        "YOUR_PASSWORD_HERE"},
  {"YOUR_BACKUP_SSID_HERE", "YOUR_BACKUP_PASSWORD_HERE"},
};
