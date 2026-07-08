#!/usr/bin/env python3
# backend/control/mock_engine.py
"""
Fake trading engine for LOCAL dashboard testing (CONTROL_MOCK=1).

Prints the SAME per-leg log line the real engines emit — the crucial part is
`tot=$<cumulative-volume>`, which EngineManager.status() parses. This lets the
whole launch → status → logs → stop flow be exercised without Docker, an RPC, or
a wallet key. It reads the same env vars the real engines do so the numbers move
in a believable way, then exits when it "reaches target".
"""
import os
import sys
import time

mode = sys.argv[1] if len(sys.argv) > 1 else "steady"

if mode == "steady":
    target = float(os.environ.get("CLIMB_TARGET_VOLUME", 100000))
    leg    = float(os.environ.get("CLIMB_LEG_USD", 50))
    print(f"[start] MOCK steady leg=${leg} target=${target}", flush=True)
else:
    target = float(os.environ.get("DP_TARGET", 100000))
    leg    = float(os.environ.get("DP_LEG_USD", 25))
    print(f"[start] MOCK fast leg=${leg} target=${target}", flush=True)

vol = 0.0
trips = 0
usdso = 200.0
somi = 30.0
per_trip = leg * 2  # both legs count

while vol < target:
    trips += 1
    vol += per_trip
    usdso -= 0.01  # tiny mock bleed
    if mode == "steady":
        cost = 0.11
        print(f"[{trips}] trip {trips}: vol+=${per_trip:.2f} tot=${vol:.2f} "
              f"USDso={usdso:.4f}(bleed $0.0100) SOMI={somi:.4f} | "
              f"cost ${cost:.3f}/1k roll ${cost:.3f}/1k", flush=True)
    else:
        print(f"[{trips}] vol+=${per_trip:.2f} tot=${vol:.2f} "
              f"USDso={usdso:.2f} somi={somi:.2f}", flush=True)
    time.sleep(float(os.environ.get("CONTROL_MOCK_LEG_S", 1.0)))

print(f"[done] trips={trips} vol=${vol:.2f}", flush=True)
