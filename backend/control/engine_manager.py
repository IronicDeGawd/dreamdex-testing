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
CONTAINER   = os.environ.get("CONTROL_CONTAINER_NAME", "dreamdex_engine")
COMPOSE_SVC = os.environ.get("CONTROL_COMPOSE_SERVICE", "agent")

# Cumulative this-run volume is `tot=$<num>` in BOTH engines' per-leg log line.
_VOL_RE  = re.compile(r"tot=\$([0-9]+(?:\.[0-9]+)?)")
# Steady engine also prints a rolling cost `roll $<num>/1k`.
_ROLL_RE = re.compile(r"roll \$([0-9]+(?:\.[0-9]+)?)/1k")

MODES = ("steady", "fast")


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
        try:
            os.kill(int(pid), 0)
            return True
        except (OSError, ValueError):
            return False

    def _container_alive(self) -> bool:
        try:
            out = subprocess.run(
                ["docker", "inspect", "-f", "{{.State.Running}}", CONTAINER],
                capture_output=True, text=True, timeout=10,
            )
            return out.returncode == 0 and out.stdout.strip() == "true"
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
        # Mirrors cheap.sh.
        return {
            "CLIMB_TARGET_VOLUME":   str(p["target"]),
            "CLIMB_LEG_USD":         str(p["leg"]),
            "CLIMB_SLIP_PCT":        str(p.get("slip", 0.003)),
            "CLIMB_MAX_GAS_SOMI":    "120",
            "CLIMB_SOMI_FLOOR":      "4",
            "CLIMB_MAX_USDSO_BLEED": str(p.get("bleed_cap", 40)),
            "CLIMB_MAX_ITERS":       "40000",
            "CLIMB_PAUSE_S":         "0",
            "CLIMB_PREAPPROVE":      "1",
            "CLIMB_SPREAD_GATE_PCT": "0.05",
            "CLIMB_COST_CEIL_PER_1K": str(p.get("cost_ceil", 0.15)),
            "CLIMB_PAUSE_EXP_S":     "45",
            "CLIMB_COST_WINDOW":     "15",
        }

    def _fast_env(self, p: dict) -> dict:
        # Mirrors direct_burst.sh.
        return {
            "DP_TARGET":           str(p["target"]),
            "DP_LEG_USD":          str(p["leg"]),
            "DP_SLIP":             str(p.get("slip", 0.004)),
            "DP_SPREAD_GATE_PCT":  str(p.get("spread_gate", 0.15)),
            "DP_SETTLE_S":         "1.5",
            "DP_SOMI_FLOOR":       "3",
            "DP_PAUSE_S":          "8",
            "DP_MAX_NOFILL":       "6",
        }

    def _build_command(self, mode: str, params: dict):
        """Returns (argv, engine_env) for the chosen mode."""
        if mode == "steady":
            engine_env = self._steady_env(params)
            script = "volume_climb.py"
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

    def launch(self, mode: str, params: dict) -> dict:
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
            "mode":       mode,
            "params":     params,
            "pid":        proc.pid,
            "container":  None if MOCK else CONTAINER,
            "log_path":   str(log_path),
            "started_at": time.time(),
            "mock":       MOCK,
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
            subprocess.run(["docker", "stop", "-t", "15", CONTAINER],
                           capture_output=True, text=True, timeout=40)

        st["running"] = False
        st["ended_at"] = time.time()
        st["end_reason"] = reason
        self._write_state(st)
        self.audit("stop", {"reason": reason, "was_running": was_running})
        return st

    # ── status ────────────────────────────────────────────────────────────
    def _tail(self, path: str, n: int) -> list[str]:
        try:
            with open(path, "r", errors="replace") as fh:
                return fh.read().splitlines()[-n:]
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
        return out

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
