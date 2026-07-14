# Plan — R4 direct improvements (separate from Algo Arena plan)

> Status: APPROVED 2026-07-12 — Batch 1 implementation started immediately.
> Constraint: engine is LIVE (pid 2276892, rank #2/9, ~2 days to 500k target).
> Engine-side code is baked into the docker image → any engine change forces a
> stop/rebuild/relaunch of a healthy run. Control-side code is host-run → safe anytime.
> Rule (learned 2026-06-24): check `agent_state`/engine running-state BEFORE any redeploy.

## Batch 1 — control-side, zero risk to the live engine (do first)
1. **24h-idle keepalive cron** (rule-11 DQ guard): control-side cron checks last-trade age
   via /status; if idle >20h, fire one tiny `/trade` (KEEPALIVE_LEG_USD=1 pattern from R3).
   Matters most AFTER the 500k self-stop, which is exactly when nobody is watching.
2. **Dashboard pair/leg selector**: Launch panel gets pair-list, leg, spread_gate,
   cost_ceil inputs wired to the existing /launch params (today only the API takes them).
   `static/index.html` + trivial `control/app.py` passthrough.
3. **Security MEDIUMs**: bind uvicorn to 127.0.0.1 (dashboard already fronted by the
   `dreamdex.ironyaditya.xyz` proxy — verify proxy talks to localhost first, else keep
   0.0.0.0 and firewall the raw port); Cloudflare Access on the hostname. No engine impact.

## Batch 2 — engine-side, deploy at the next NATURAL stop (500k target ≈ 2 days)
4. ✅ **SIGTERM handler completeness check** (2026-07-12): `_on_term` verified present in
   BOTH the server's volume_climb.py and the RUNNING container (/app/volume_climb.py,
   image built 2026-07-12). No change needed.
5. ✅ CODE SHIPPED, BAKE PENDING (2026-07-12) — **weekly-window + boost-aware rotation**
   (commits 23514b0 engine, 3be280d control). Engine: pair_boosts() reads data/boosts.json
   every ~60s (mounted ./data), rotation ranks by eff÷boost, gates scale by boost, Monday-
   UTC week counter + optional CLIMB_WEEKLY_TARGET (idles till Monday, never stops).
   Control: GET/POST /boosts (live on server), /launch weekly_target param (live).
   volume_climb.py is rsynced to the server but NOT in the image — **at the 500k stop run
   `docker compose build agent` once** and the engine is Arena-ready. No boosts file =
   behavior identical to today, so the bake is safe whenever the stop happens.
6. **(Optional) joint (pair,leg) toll optimizer**: measured saving ~$0.02/1k — only worth it
   if R4 continues past 500k; otherwise fold into Arena Phase 3. NOT DONE (deliberate).

## Explicitly NOT doing for R4
- Direct-RPC execution path in volume_climb (the +$0.044/1k overhead attack): high-risk
  change on a live engine 2 days from target; `direct_burst.py` already exists if deadline
  pressure emerges. Fold into Arena work if week-1 data justifies it.

## Order of operations
1. Ship Batch 1 now (no engine contact). Verify each on the live dashboard.
2. When the engine self-stops at 500k (or user /stops it): verify flat, then rebuild once
   with Batch 2 items, relaunch only if R4 still needs volume — else leave stopped and
   carry the image into Arena week 1.
