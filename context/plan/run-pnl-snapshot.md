# Per-Run Capital & Gas Snapshot → Run P&L / Bleed Readout

## Context
Every run currently reports volume and rolling cost, but not "did THIS run make or lose money, and what did gas cost?". The maker engine prints its own networth deltas to its log, the taker prints bleed — but nothing captures an authoritative baseline at launch or a final verdict at stop, and nothing shows it on the dashboard. Feature: at launch, snapshot exact capital (USDso wallet + vault + bags at mid) and gas (native SOMI); show live run P&L and gas used; at stop, record the final verdict per run to a history file.

**Hard constraint: a real-money maker run is LIVE right now (rank #1).** Zero engine-file changes (engine code is baked into the docker image; the running container must not be touched). Control server + dashboard only — host-run, safe to restart while the engine runs. A log-parse fallback (`START networth=$78.1742 SOMI=…` is already in the live log) makes the feature work for the in-flight run immediately.

## Files to modify
1. `backend/control/app.py`
2. `backend/control/engine_manager.py`
3. `backend/static/index.html`

Read-only references: `backend/maker_v2.py` (log formats, lines 310/370/454), `backend/volume_climb.py` (171/525), `backend/direct_burst.py` (224), `backend/control/mock_engine.py` (39-44).

## Step 1 — Snapshot helper (app.py)
- `LiveBackend.run_snapshot()` (after `balances()`, ~line 175): reuse `self.balances()`; `somi_px` = SOMI:USDso book mid (same pattern as `gas_topup()` line 231-235, `None` if book empty); value each bag > dust at its pair's book mid (pattern from `cohort()` lines 207-209). Returns:
  `{ts, source, usdso, vault, somi, somi_px, bags, bags_usd, networth}` where `networth = usdso + vault + bags_usd + somi*somi_px` (mirrors maker's own networth definition so the two are comparable; if `somi_px` is None, omit the gas term).
- `MockBackend.run_snapshot()`: static stub so mock mode exercises the whole flow.
- `/launch` (line ~453, after guards, before `engine.launch`): `baseline = backend.run_snapshot()` in try/except → `None` on failure (snapshot failure must NEVER block a launch); `baseline["source"]="launch"`. Pass `engine.launch(body.mode, params, baseline=baseline)`.
- `/autorestart` (line 518) also calls `engine.launch` directly → snapshot there too, `source="autorestart"` (comment: post-crash reads can miss order-locked funds; maker's new START line corrects it). `transition.sh` needs nothing — it POSTs /launch.

## Step 2 — engine_manager.py: store baseline, parse live P&L, finalize
- `launch(self, mode, params, baseline=None)`: add `"baseline": baseline` to the state dict (lines 207-219). Persists in engine.json → survives control restarts.
- New regexes next to `_VOL_RE` (line 39) — formats verified against source:
  ```python
  _HB_RE       = re.compile(r"hb networth=\$([0-9]+(?:\.[0-9]+)?) \(([+-][0-9]+(?:\.[0-9]+)?)\)")
  _START_MK_RE = re.compile(r"START networth=\$([0-9]+(?:\.[0-9]+)?) SOMI=([0-9]+(?:\.[0-9]+)?)")
  _START_ST_RE = re.compile(r"START USDso=([0-9]+(?:\.[0-9]+)?) .*SOMI=([0-9]+(?:\.[0-9]+)?)")
  _BLEED_ST_RE = re.compile(r"\(bleed \$(-?[0-9]+(?:\.[0-9]+)?)\)")
  _SOMI_RE     = re.compile(r"(?:SOMI|somi)=([0-9]+(?:\.[0-9]+)?)")          # steady/fast trip lines
  _STOP_MK_RE  = re.compile(r"networth=\$([0-9]+(?:\.[0-9]+)?) bleed=\$([+-][0-9]+(?:\.[0-9]+)?) gas=(-?[0-9]+(?:\.[0-9]+)?) SOMI")
  ```
- New `_head(path, n=40)` next to `_tail` (line 278) — START line lives in the log head; `_tail(400)` misses it on long runs.
- `status()` (line 285) new fields, all None-safe:
  - `baseline`: from state; if absent (current live run), synthesize from log head: maker → `_START_MK_RE` `{networth, somi, source:"log"}`; steady → `_START_ST_RE` `{usdso→networth-proxy, somi}`.
  - `networth_now` / `run_pnl`: maker → last `_HB_RE` in tail (`run_pnl` = engine-printed delta, authoritative — includes order-locked funds); if a `_STOP_MK_RE` line exists prefer it (`run_pnl = -bleed`). steady → last `_BLEED_ST_RE`, `run_pnl = -bleed`. fast → last trip `USDso=` minus `baseline["usdso"]` (needs control snapshot, else None).
  - `gas_used_somi`: steady/fast → baseline somi − last `_SOMI_RE`; maker → only from `_STOP_MK_RE` after end (None mid-run; dashboard covers live, Step 4).
  - `final`: pass through `state.get("final")`; when present its `pnl`/`gas_somi` override log-parsed values.
- New `finalize(final: dict)`: idempotent (no-op if `final` already in state); write into state + append `{started_at, ended_at, mode, params, end_reason, volume, baseline, final, pnl, gas_somi}` to `STATE_DIR/runs.jsonl` (same append pattern as `audit()`, line 321). engine_manager stays chain-free — app.py owns all RPC.

## Step 3 — app.py: finalize on /stop and on observed self-stop
- `/stop` (line 470): after existing flatten (wallet is flat → control read accurate), best-effort try/except:
  `final = backend.run_snapshot()`; baseline = state's baseline OR status()'s log-head fallback; `final["pnl"] = final.networth − baseline.networth`; `final["gas_somi"] = baseline.somi − final.somi`; `final["gas_usd"] = gas_somi*somi_px`; `engine.finalize(final)`. Never fail the stop response over bookkeeping.
- Self-stops (target hit / bleed cap / breaker) never hit /stop → in the `/status` endpoint handler (line 375): if `not running` and `started_at` set and no `final` and `ended_at` older than 30s → same finalize block. 30s guard keeps the 5s dashboard poller from racing an explicit /stop's flatten. Engines flatten themselves on self-stop, so the read is accurate. Runs once (finalize idempotent) — no per-poll RPC.

## Step 4 — Dashboard (index.html)
- Two rows in the Live Status panel after the Target row (~line 280), same `table-row` pattern: **Run P&L** (`id=s_pnl`) and **Gas used** (`id=s_gas`).
- `refreshStatus()` (574-589): `s_pnl` from `s.final?.pnl ?? s.run_pnl`, signed `$X.XXXX`, green ≥0 else red; `s_gas` from `s.final?.gas_somi ?? s.gas_used_somi` as `X.XXXX SOMI`; stash `window._runBaseline = s.baseline`, `window._runActive = s.running`.
- `refreshBalances()` (590+): if run active, baseline has somi, and server gave no gas figure → `s_gas = (baseline.somi − b.somi).toFixed(4)` — live maker gas from the existing 15s /balances poll, zero new RPC.

## Edge cases (accounted for)
- RPC failure at launch → baseline None, launch proceeds, START-line fallback covers display.
- Log missing → `_tail`/`_head` return `[]`, fields degrade to None ("—" in UI).
- Keepalive/topup pollution: none — both no-op/409 while an engine runs.
- Negative bleed (profit) → all regexes sign-tolerant; maker deltas are `{:+}`-formatted, steady bleed can be `$-0.0100`.
- Autorestart → fresh baseline per engine process; crashed segment already finalized to runs.jsonl by the /status hook. P&L is per-process, not per-campaign.
- Mock mode fully exercises flow (mock_engine prints steady-format lines incl. `(bleed $0.0100) SOMI=…`).

## Verification
1. **Local mock e2e**: `CONTROL_MOCK=1 …uvicorn control.app:app --port 8788` from `backend/`; launch steady small-target via dashboard → check `baseline` in `control/state/engine.json`, `run_pnl`/`gas_used_somi` in `/status`, both rows render; let it self-stop → confirm auto-finalize wrote `final` + a runs.jsonl line; repeat with explicit /stop.
2. **Regex spot-check**: run the six patterns against real lines copied from the live server log (maker hb/START) + volume_climb/direct_burst source formats.
3. **Deploy control-only** (live maker untouched): rsync `control/app.py`, `control/engine_manager.py`, `static/index.html`; restart uvicorn by **port-kill** (never `pkill -f control.app:app`); confirm dashboard shows Run P&L for the in-flight run via the START-line fallback (should match the latest `hb networth=… (±d)` log line) and live gas via the /balances delta.

## Post-approval housekeeping
- Save this plan to `context/plan/run-pnl-snapshot.md`.
- Branch: continue on `feature/maker-v2` (control-side feature, same workstream). One commit per logical unit (engine_manager, app.py, dashboard can be 1–2 commits).
