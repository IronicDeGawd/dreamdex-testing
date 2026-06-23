# R2 Archive — Reference Runbook

> Snapshot of the complete **Round 2** toolset (volume-maximizing taker-burst era).
> Round 3 is a fresh profit-making market-maker (`backend/agent_v3/`, see `../README.md`).
> Nothing here runs by default. Lift a file out and fix its imports if you need it.
> All paths below are relative to `backend/archive/`.

## Why this exists
R2 was scored on **raw volume**, so the playbook was: burn capital fast, spray IOC taker
orders, win on turnover. R3 is scored on **Effective Volume = Raw Volume × (1 + PnL%)** —
bleeding now discounts your score and a wipe = 0. The taker-burst engine below is therefore
the *wrong* tool for R3, but its primitives (direct-RPC broadcast, calldata building, gas tip,
fill-by-balance-delta detection, keepalive supervision) are reused by the new agent.

## What was KEPT in `backend/` (not archived) — the reusable core
- `trading/wallet.py` — key load, 5gwei priority tip (`_gas_fields`), raw-tx sign + broadcast.
- `trading/dreamdex.py` — `placeOrder` calldata build, vault deposit/withdraw, balance reads.
- `monitor/db.py`, `leaderboard.py`, `portfolio.py`, `prices.py` — SQLite + on-chain reads.
- `config.py` — addresses/decimals (being updated for R3 pools).

## Archived files — index

### The winning engine
- `aware_burst_vault.py` — **R2 winner.** Builds `placeOrder` calldata locally + direct-RPC
  broadcast. `BURST_CONFIRMED` unset = FAST (~10k vol/hr); =1 = slow API+verify (~3k/hr).
  Selector `0x4e978373`, orderType 2 (IOC). Reuse: direct-broadcast path, leg sizing.
- `aware_burst.py` — earlier non-vault burst variant. Has the DNS-crash robustness gap
  (no try/except around main-loop RPC → transient NameResolutionError kills it).
- `direct_burst.py` / `run_direct_burst.sh` — direct-RPC burst entry + launcher.
- `burst_decide.py`, `burst_autotune.sh` — leg-size / rate tuning helpers.

### Maker (most relevant to R3 — reuse heavily)
- `profit_maker.py` — **no-bleed maker.** `PROFIT_FUNDING=wallet` makes both legs draw the
  same balance location (fixes the vault-inventory wedge). Heartbeat `_wait()` keeps the 600s
  watchdog from false-killing a resting maker. Core logic ports into the R3 two-sided quoter.
- `reset_maker.sh`, `reset_maker_weth.sh`, `maker_keepalive.sh` — maker supervision.

### Keepalives & cycle control (cron-driven supervision)
- `aware_vault_keepalive.sh` (*/3), `aware_keepalive.sh`, `burst_keepalive.sh`,
  `gas_autokeep.sh` — restart-on-death crons. R3 reuses the supervise-by-volume idea
  (engine can be alive-but-stuck on a price-buffer hiccup).
- `cycle_phase.sh`, `switch_pair.sh` — mode/pair swapping between burst and maker.
- `stop_and_recover.sh`, `end_burst.sh`, `fix_burst.sh`, `fix_flatten.sh` — stop/recover ops.

### Gas & liquidation
- `buy_gas.py`, `gas_topup.sh` — USDso→SOMI swap to refuel gas (R3 wires this into the agent;
  note: SOMI native buy needs gas ≥ 5,000,000).
- `somi_drip.sh`, `sell_somi.py` — drip SOMI→USDso in small slices (mask as organic).
- `liquidate_to_usdso.py`, `auto_withdraw.py` — flatten positions / withdraw at round end.

### Balance & trade probes
- `audit_balances.py` — **true capital** = wallet `balanceOf` + vault `getWithdrawableBalance`
  + open orders. Has the full ABI. Deployed in R2 at local/server/container.
- `probe_realbal.py`, `probe_funding.py`, `probe_trades.py`, `probe2.py`,
  `deep_check.py`, `deep_watch.py`, `check_balance.py` — assorted on-chain inspectors.

### Old service scaffold
- `agent_r2/` — the R2 agent package (agent.py state machine, brain.py GPT-4o-mini logic,
  state.py, strategy.py). Superseded by the R3 agent.
- `server.py` — R2 Flask API (SIWE auth, /api/order, /api/status).
- `main.py` — R2 entrypoint (spawned agent loop + leaderboard monitor).

### Sweeps & smoke tests
- `sweep_b_to_h.py`, `sweep_opponents.py` — multi-wallet fund sweeps (R2 wallet B→H).
- `smoke_live_order.py`, `smoke_testnet.py`, `test_connectivity.py` — connectivity/order smoke tests.

## How to lift a file back
1. `git mv backend/archive/<file> backend/<file>` (or copy if you only want reference).
2. Fix imports — archived scripts expect `config`, `trading.*`, `monitor.*` at `backend/` root
   (they still live there), so most imports work once the file sits back in `backend/`.
3. Re-check addresses against R3 (`context/plan/round3-rules.md` docs-delta) before trading.
