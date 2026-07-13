# backend/control/engine_manager.py
"""
Launches, stops, and inspects a single trading-engine run.

The dashboard drives runs through this class instead of the operator typing
`./cheap.sh ...` / `./direct_burst.sh ...` by hand. To keep behaviour identical
to those launchers, we build the SAME `docker compose run` command with the SAME
env vars they set — just adding an explicit `--name` so the container can be
found again for `/status` and `/stop`.

Single-engine rule: only one run at a time (both engines share one wallet, so
concurrent runs collide on the nonce). `launch()` refuses while one is live.

MOCK mode (`CONTROL_MOCK=1`): instead of docker, run `mock_engine.py` as a plain
subprocess. It prints the exact same `tot=$<vol>` log lines the real engines do,
so the whole status/log/stop path is exercised locally without keys or Docker.
"""
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

# ── Config (env-overridable) ──────────────────────────────────────────────
MOCK        = os.environ.get("CONTROL_MOCK", "0") == "1"
BACKEND_DIR = Path(os.environ.get("CONTROL_BACKEND_DIR",
                                  str(Path(__file__).resolve().parent.parent)))
STATE_DIR   = Path(os.environ.get("CONTROL_STATE_DIR", str(BACKEND_DIR / "control" / "state")))
LOG_DIR     = STATE_DIR / "logs"
STATE_FILE  = STATE_DIR / "engine.json"
AUDIT_FILE  = STATE_DIR / "audit.log"
RUNS_FILE   = STATE_DIR / "runs.jsonl"   # one record per finished run: baseline, final, pnl, gas
CONTAINER   = os.environ.get("CONTROL_CONTAINER_NAME", "dreamdex_engine")
COMPOSE_SVC = os.environ.get("CONTROL_COMPOSE_SERVICE", "agent")

# Cumulative this-run volume is `tot=$<num>` in BOTH engines' per-leg log line.
_VOL_RE  = re.compile(r"tot=\$([0-9]+(?:\.[0-9]+)?)")
# Steady engine also prints a rolling cost `roll $<num>/1k`.
_ROLL_RE = re.compile(r"roll \$([0-9]+(?:\.[0-9]+)?)/1k")
# Per-run P&L, parsed from the engines' own logs. The maker's printed networth
# includes funds locked in its resting orders — invisible to any balance read,
# so its own figures are the authoritative ones while it runs.
_HB_RE       = re.compile(r"hb networth=\$([0-9]+(?:\.[0-9]+)?) \(([+-][0-9]+(?:\.[0-9]+)?)\)")
_START_MK_RE = re.compile(r"START networth=\$([0-9]+(?:\.[0-9]+)?) SOMI=([0-9]+(?:\.[0-9]+)?)")
_START_ST_RE = re.compile(r"START USDso=([0-9]+(?:\.[0-9]+)?) .*SOMI=([0-9]+(?:\.[0-9]+)?)")
_BLEED_ST_RE = re.compile(r"\(bleed \$(-?[0-9]+(?:\.[0-9]+)?)\)")
_USDSO_RE    = re.compile(r"USDso=([0-9]+(?:\.[0-9]+)?)")
_SOMI_RE     = re.compile(r"(?:SOMI|somi)=([0-9]+(?:\.[0-9]+)?)")
_STOP_MK_RE  = re.compile(r"networth=\$([0-9]+(?:\.[0-9]+)?) bleed=\$([+-][0-9]+(?:\.[0-9]+)?) "
                          r"gas=(-?[0-9]+(?:\.[0-9]+)?) SOMI")

MODES = ("steady", "fast", "maker")


class EngineError(Exception):
    """Raised for caller-fixable problems (already running, bad mode, etc.)."""


class EngineManager:
    def __init__(self):
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        LOG_DIR.mkdir(parents=True, exist_ok=True)

    # ── state file ────────────────────────────────────────────────────────
    def _read_state(self) -> dict:
        try:
            return json.loads(STATE_FILE.read_text())
        except (FileNotFoundError, ValueError):
            return {}

    def _write_state(self, state: dict) -> None:
        tmp = STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2))
        tmp.replace(STATE_FILE)

    # ── liveness ──────────────────────────────────────────────────────────
    def _proc_alive(self, pid) -> bool:
        if not pid:
            return False
        # A self-exited mock engine lingers as a zombie (we never wait() on it)
        # and still answers signal 0 — reap it so the run reconciles to stopped.
        try:
            done, _ = os.waitpid(int(pid), os.WNOHANG)
            return done == 0
        except ChildProcessError:
            pass   # not our child (control restarted) — fall through to signal 0
        except (OSError, ValueError):
            return False
        try:
            os.kill(int(pid), 0)
            return True
        except (OSError, ValueError):
            return False

    def _container_alive(self) -> bool:
        # Use Status, not Running: a container still booting reports Running=false
        # while Status=created, and treating that as dead let the watchdog rm+relaunch
        # a healthy engine mid-startup. A truly dead run is --rm'd, so inspect fails.
        try:
            out = subprocess.run(
                ["docker", "inspect", "-f", "{{.State.Status}}", CONTAINER],
                capture_output=True, text=True, timeout=10,
            )
            if out.returncode != 0:
                return False
            return out.stdout.strip() in ("running", "created", "restarting", "paused")
        except (OSError, subprocess.SubprocessError):
            return False

    def is_running(self) -> bool:
        st = self._read_state()
        if not st.get("running"):
            return False
        alive = self._proc_alive(st.get("pid")) if MOCK else self._container_alive()
        if not alive:
            # Engine exited on its own (hit target / errored). Reconcile.
            st["running"] = False
            st["ended_at"] = st.get("ended_at") or time.time()
            st["end_reason"] = st.get("end_reason") or "exited"
            self._write_state(st)
        return alive

    # ── launch ────────────────────────────────────────────────────────────
    def _steady_env(self, p: dict) -> dict:
        # Mirrors cheap.sh. Pair + spread gate are tunable (liquidity shifted
        # after the public launch — WBTC:USDso is now the tightest book).
        return {
            "CLIMB_PAIRS":           str(p.get("pair", "WETH:USDso")),  # comma list = auto-rotate to cheapest
            "CLIMB_TARGET_VOLUME":   str(p["target"]),
            "CLIMB_LEG_USD":         str(p["leg"]),
            "CLIMB_SLIP_PCT":        str(p.get("slip", 0.003)),
            "CLIMB_MAX_GAS_SOMI":    "120",
            "CLIMB_SOMI_FLOOR":      "4",
            "CLIMB_MAX_USDSO_BLEED": str(p.get("bleed_cap", 40)),
            "CLIMB_MAX_ITERS":       "40000",
            "CLIMB_PAUSE_S":         "0",
            "CLIMB_PREAPPROVE":      "1",
            "CLIMB_SPREAD_GATE_PCT": str(p.get("spread_gate", 0.05)),
            "CLIMB_COST_CEIL_PER_1K": str(p.get("cost_ceil", 0.15)),
            "CLIMB_PAUSE_EXP_S":     "45",
            # Wide window: realized cost is a safety breaker now, not the throttle.
            "CLIMB_COST_WINDOW":     "50",
            # Arena weekly cap (0 = off). Boosts travel via data/boosts.json, not env.
            "CLIMB_WEEKLY_TARGET":   str(p.get("weekly_target", 0)),
        }

    def _fast_env(self, p: dict) -> dict:
        # Mirrors direct_burst.sh.
        return {
            "DP_PAIR":             str(p.get("pair", "WETH:USDso")),
            "DP_TARGET":           str(p["target"]),
            "DP_LEG_USD":          str(p["leg"]),
            "DP_SLIP":             str(p.get("slip", 0.004)),
            "DP_SPREAD_GATE_PCT":  str(p.get("spread_gate", 0.15)),
            "DP_SETTLE_S":         "1.5",
            "DP_SOMI_FLOOR":       "3",
            "DP_PAUSE_S":          "8",
            "DP_MAX_NOFILL":       "6",
        }

    def _maker_env(self, p: dict) -> dict:
        # Two-sided PostOnly maker (maker_v2.py). target=0 means run until
        # stopped; inv_floor=0 unwinds fully (R4-friendly — its scoring counts
        # free USDso), 0.3 is the Arena fair-play default.
        return {
            "MAKER2_PAIRS":          str(p.get("pair", "WETH:USDso")),
            "MAKER2_TARGET_VOLUME":  str(p.get("target", 0)),
            "MAKER2_LEG_USD":        str(p["leg"]),
            "MAKER2_MAX_INV_USD":    str(p.get("cap", 40)),
            "MAKER2_MAX_BLEED":      str(p.get("bleed_cap", 3)),
            "MAKER2_INV_FLOOR_PCT":  str(p.get("inv_floor", 0.0)),
            "MAKER2_RESERVE_USD":    "2",
            "MAKER2_SOMI_FLOOR":     "3",
        }

    def _build_command(self, mode: str, params: dict):
        """Returns (argv, engine_env) for the chosen mode."""
        if mode == "steady":
            engine_env = self._steady_env(params)
            script = "volume_climb.py"
        elif mode == "maker":
            engine_env = self._maker_env(params)
            script = "maker_v2.py"
        else:
            engine_env = self._fast_env(params)
            script = "direct_burst.py"

        if MOCK:
            argv = [sys.executable, str(BACKEND_DIR / "control" / "mock_engine.py"), mode]
            return argv, engine_env

        argv = ["docker", "compose", "run", "--rm", "--no-deps", "-T",
                "--name", CONTAINER]
        for k, v in engine_env.items():
            argv += ["-e", f"{k}={v}"]
        argv += [COMPOSE_SVC, "python3", script]
        return argv, engine_env

    def launch(self, mode: str, params: dict, baseline: dict | None = None) -> dict:
        if mode not in MODES:
            raise EngineError(f"unknown mode {mode!r} (want one of {MODES})")
        if "target" not in params or "leg" not in params:
            raise EngineError("target and leg are required")
        if self.is_running():
            raise EngineError("an engine is already running — stop it first")

        # Clear any leftover container from a crashed run so --name is free.
        if not MOCK:
            subprocess.run(["docker", "rm", "-f", CONTAINER],
                           capture_output=True, text=True)

        argv, engine_env = self._build_command(mode, params)
        ts = int(time.time())
        log_path = LOG_DIR / f"{mode}-{ts}.log"
        logf = open(log_path, "wb")

        proc_env = dict(os.environ)
        proc_env.update(engine_env)
        proc = subprocess.Popen(
            argv, cwd=str(BACKEND_DIR),
            stdout=logf, stderr=subprocess.STDOUT,
            start_new_session=True, env=proc_env,
        )

        state = {
            "running":    True,
            # Watchdog may relaunch this run if it dies on its own (crash, network,
            # breaker). A deliberate dashboard stop clears the flag.
            "autorestart": True,
            "mode":       mode,
            "params":     params,
            "pid":        proc.pid,
            "container":  None if MOCK else CONTAINER,
            "log_path":   str(log_path),
            "started_at": time.time(),
            "mock":       MOCK,
            # Capital+gas snapshot taken by the caller right before launch (wallet
            # is flat then, so plain balance reads are exact). None if the read failed.
            "baseline":   baseline,
        }
        self._write_state(state)
        self.audit("launch", {"mode": mode, **params})
        return state

    # ── stop ──────────────────────────────────────────────────────────────
    def stop(self, reason: str = "manual") -> dict:
        st = self._read_state()
        if not st:
            raise EngineError("no run on record")
        was_running = self.is_running()

        if MOCK:
            pid = st.get("pid")
            if pid and self._proc_alive(pid):
                try:
                    os.killpg(os.getpgid(int(pid)), signal.SIGTERM)
                except (OSError, ValueError):
                    pass
        else:
            # docker stop sends SIGTERM (the engine's handler flattens, then exits),
            # then SIGKILL after the grace period. VERIFY it actually died — a
            # discarded failure would mark us "stopped" while the engine keeps
            # trading and the post-stop flatten fights it for the nonce.
            r = subprocess.run(["docker", "stop", "-t", "20", CONTAINER],
                               capture_output=True, text=True, timeout=45)
            if self._container_alive():
                subprocess.run(["docker", "kill", CONTAINER],
                               capture_output=True, text=True, timeout=20)
            if self._container_alive():
                raise EngineError(
                    f"docker stop did not kill {CONTAINER} — still alive "
                    f"({(r.stderr or '').strip()[:120]}); NOT flattening (nonce risk)")

        st["running"] = False
        st["ended_at"] = time.time()
        st["end_reason"] = reason
        # stop() is only ever called by the /stop endpoint — a deliberate stop.
        # Clear the flag so the watchdog never resurrects a run you killed.
        st["autorestart"] = False
        self._write_state(st)
        self.audit("stop", {"reason": reason, "was_running": was_running})
        return st

    def clean_stop_reason(self) -> str | None:
        """The engine prints '=== STOP: <reason> ===' when it stops ITSELF for a real
        reason (target reached, bleed/gas cap, trade-failure breaker) and '...ABORT'
        on a startup refusal. Either means: do not resurrect it. Returns the reason,
        or None when the process vanished with no such line (crash / host / RPC kill),
        which is the only case the watchdog restarts."""
        st = self._read_state()
        for line in reversed(self._tail(st.get("log_path"), 80)):
            if "=== STOP:" in line:
                return line.split("=== STOP:")[1].replace("===", "").strip() or "self-stop"
            if "ABORT" in line:
                return "startup abort"
        return None

    # ── status ────────────────────────────────────────────────────────────
    def _tail(self, path: str, n: int) -> list[str]:
        try:
            with open(path, "r", errors="replace") as fh:
                return fh.read().splitlines()[-n:]
        except (OSError, TypeError):
            return []

    def _head(self, path: str, n: int) -> list[str]:
        # The engines print their START balance line within the first few lines;
        # on a long run _tail(400) never sees it.
        try:
            with open(path, "r", errors="replace") as fh:
                return [line.rstrip("\n") for line, _ in zip(fh, range(n))]
        except (OSError, TypeError):
            return []

    def status(self) -> dict:
        st = self._read_state()
        running = self.is_running()
        st = self._read_state()  # re-read: is_running() may have reconciled it
        out = {
            "running":  running,
            "mode":     st.get("mode"),
            "params":   st.get("params", {}),
            "mock":     st.get("mock", MOCK),
            "started_at": st.get("started_at"),
            "ended_at":   st.get("ended_at"),
            "end_reason": st.get("end_reason"),
            "volume":   0.0,
            "cost_per_1k": None,
            "uptime_s": None,
        }
        if st.get("started_at"):
            end = st.get("ended_at") if not running else time.time()
            out["uptime_s"] = round((end or time.time()) - st["started_at"], 1)

        lines = self._tail(st.get("log_path"), 400)
        for line in reversed(lines):
            m = _VOL_RE.search(line)
            if m:
                out["volume"] = float(m.group(1))
                r = _ROLL_RE.search(line)
                if r:
                    out["cost_per_1k"] = float(r.group(1))
                break
        out.update(self._run_pnl(st, lines))
        return out

    def _run_pnl(self, st: dict, tail: list[str]) -> dict:
        """Per-run baseline + live P&L/gas. Baseline comes from the launch-time
        snapshot in state, or (for runs launched before snapshots existed) from
        the engine's own START log line."""
        mode = st.get("mode")
        baseline = st.get("baseline")
        if not baseline:
            for line in self._head(st.get("log_path"), 40):
                m = _START_MK_RE.search(line) or _START_ST_RE.search(line)
                if m:
                    baseline = {"source": "log", "networth": float(m.group(1)),
                                "somi": float(m.group(2))}
                    break
        out = {"baseline": baseline, "networth_now": None, "run_pnl": None,
               "gas_used_somi": None, "final": st.get("final")}
        for line in reversed(tail):
            if mode == "maker":
                m = _STOP_MK_RE.search(line)
                if m:   # STOP summary: bleed = start − now, gas already a delta
                    out["networth_now"] = float(m.group(1))
                    out["run_pnl"] = -float(m.group(2))
                    out["gas_used_somi"] = float(m.group(3))
                    break
                m = _HB_RE.search(line)
                if m:   # heartbeat prints the signed delta vs its own start
                    out["networth_now"] = float(m.group(1))
                    out["run_pnl"] = float(m.group(2))
                    break
            elif mode == "steady":
                m = _BLEED_ST_RE.search(line)
                if m:
                    out["run_pnl"] = round(-float(m.group(1)), 4)
                    s = _SOMI_RE.search(line)
                    if s and baseline and baseline.get("somi") is not None:
                        out["gas_used_somi"] = round(baseline["somi"] - float(s.group(1)), 4)
                    break
            elif mode == "fast":
                # Fast trip line has no bleed field — needs the launch snapshot.
                m = _USDSO_RE.search(line)
                if m and baseline and baseline.get("usdso") is not None:
                    out["run_pnl"] = round(float(m.group(1)) - baseline["usdso"], 4)
                    s = _SOMI_RE.search(line)
                    if s and baseline.get("somi") is not None:
                        out["gas_used_somi"] = round(baseline["somi"] - float(s.group(1)), 4)
                    break
        # A recorded final verdict (post-flatten chain read) beats log parses.
        fin = st.get("final")
        if fin:
            for src, dst in (("pnl", "run_pnl"), ("gas_somi", "gas_used_somi"),
                             ("networth", "networth_now")):
                if fin.get(src) is not None:
                    out[dst] = fin[src]
        return out

    def finalize(self, final: dict) -> dict:
        """Record the end-of-run verdict once (idempotent — an explicit /stop and
        the /status self-stop observer can both call this; first one wins) and
        append the run's full record to runs.jsonl."""
        st = self._read_state()
        if not st or st.get("final") or not st.get("started_at"):
            return st
        st["final"] = final
        self._write_state(st)
        vol = None
        for line in reversed(self._tail(st.get("log_path"), 400)):
            m = _VOL_RE.search(line)
            if m:
                vol = float(m.group(1))
                break
        rec = {"started_at": st.get("started_at"), "ended_at": st.get("ended_at"),
               "mode": st.get("mode"), "params": st.get("params"),
               "end_reason": st.get("end_reason"), "volume": vol,
               "baseline": st.get("baseline"), "final": final,
               "pnl": final.get("pnl"), "gas_somi": final.get("gas_somi")}
        try:
            with open(RUNS_FILE, "a") as fh:
                fh.write(json.dumps(rec) + "\n")
        except OSError:
            pass
        return st

    def logs(self, n: int = 80) -> dict:
        st = self._read_state()
        return {"log_path": st.get("log_path"), "lines": self._tail(st.get("log_path"), n)}

    # ── audit log ─────────────────────────────────────────────────────────
    def audit(self, action: str, detail: dict) -> None:
        rec = {"ts": time.time(), "action": action, "detail": detail}
        with open(AUDIT_FILE, "a") as fh:
            fh.write(json.dumps(rec) + "\n")

    def read_audit(self, n: int = 50) -> list[dict]:
        try:
            lines = AUDIT_FILE.read_text().splitlines()[-n:]
        except FileNotFoundError:
            return []
        out = []
        for ln in lines:
            try:
                out.append(json.loads(ln))
            except ValueError:
                continue
        return out
