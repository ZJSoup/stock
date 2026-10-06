"""Bot registry and process manager for the stock-bot dashboard.

Dashboard-managed bots are started via Popen in a new session so the whole
process group can be torn down on shutdown. External instances (started by
launchd / manually) are detected read-only via pgrep and can never be stopped
by the dashboard.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import signal
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_STATE_FILE = Path(__file__).resolve().parent / ".pids.json"

BOTS: dict[str, dict] = {
    "spy-steady": {
        "label": "SPY STEADY",
        "cmd": [
            sys.executable, "spy_multi_strategy.py",
            "--mode", "steady", "--capital-pct", "100", "--client-id", "2",
        ],
        "cwd": str(REPO_ROOT),
        "pattern": r"spy_multi_strategy.py.*--mode[ =]steady",
    },
    "spy-turbo": {
        "label": "SPY TURBO",
        "cmd": [
            sys.executable, "spy_multi_strategy.py",
            "--mode", "turbo", "--capital-pct", "100", "--client-id", "3",
        ],
        "cwd": str(REPO_ROOT),
        "pattern": r"spy_multi_strategy.py.*--mode[ =]turbo",
    },
    "momo": {
        "label": "MOMO",
        "cmd": [sys.executable, "-m", "momo_bot"],
        "cwd": str(REPO_ROOT),
        "pattern": r"momo_bot(\s|$)",
    },
}


def _now_iso() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _pgrep(pattern: str) -> list[int]:
    """Return PIDs whose full command line matches pattern (external procs)."""
    try:
        out = subprocess.run(
            ["pgrep", "-f", pattern],
            capture_output=True, text=True, timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    pids = []
    for line in out.stdout.split():
        try:
            pids.append(int(line))
        except ValueError:
            continue
    return pids


class BotManager:
    def __init__(self, state_file: str | Path | None = None):
        self.state_file = Path(state_file) if state_file else DEFAULT_STATE_FILE
        self._registry: dict[str, dict] = dict(BOTS)
        self._procs: dict[str, subprocess.Popen] = {}
        self._log_handles: dict[str, object] = {}

    # ------------------------------------------------------------------
    # registry / state helpers
    # ------------------------------------------------------------------
    def register_test_bot(self, bot_id: str, cmd: list[str], cwd: str) -> None:
        """Add an ad-hoc registry entry (test support)."""
        self._registry[bot_id] = {
            "label": bot_id,
            "cmd": list(cmd),
            "cwd": cwd,
            "pattern": None,
        }

    def mark_external(self, bot_id: str, pid: int) -> None:
        """Record an externally-started PID in state (test/future use)."""
        if bot_id not in self._registry:
            raise ValueError(f"unknown bot: {bot_id}")
        state = self._read_state()
        state[bot_id] = {
            "pid": int(pid),
            "managed_by": "external",
            "started_at": _now_iso(),
        }
        self._write_state(state)

    def _read_state(self) -> dict:
        try:
            with open(self.state_file, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else {}
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {}

    def _write_state(self, state: dict) -> None:
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.state_file.with_suffix(self.state_file.suffix + ".tmp")
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(state, fh, indent=2)
            tmp.replace(self.state_file)
        except OSError:
            pass

    def _update_state_entry(self, bot_id: str, entry: dict | None) -> None:
        state = self._read_state()
        if entry is None:
            state.pop(bot_id, None)
        else:
            state[bot_id] = entry
        self._write_state(state)

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------
    def status(self) -> dict[str, dict]:
        state = self._read_state()
        result: dict[str, dict] = {}
        own_pids = {p.pid for p in self._procs.values()}
        for bot_id, spec in self._registry.items():
            label = spec["label"]
            cmd = " ".join(spec["cmd"])
            base = {"label": label, "cmd": cmd}

            proc = self._procs.get(bot_id)
            if proc is not None:
                if proc.poll() is None:
                    result[bot_id] = {
                        **base, "running": True, "pid": proc.pid,
                        "managed_by": "dashboard",
                    }
                    continue
                # managed proc died; drop it
                self._procs.pop(bot_id, None)
                self._close_log(bot_id)
                self._update_state_entry(bot_id, None)

            pattern = spec.get("pattern")
            if pattern:
                external = [p for p in _pgrep(pattern) if p not in own_pids]
            else:
                external = []
            if external:
                result[bot_id] = {
                    **base, "running": True, "pid": external[0],
                    "managed_by": "external",
                }
            else:
                # stale state (dead pid / old managed entry) is not "running"
                stale = state.get(bot_id)
                pid = stale.get("pid") if stale else None
                managed_by = stale.get("managed_by") if stale else None
                if pid and _pid_alive(pid) and managed_by == "external":
                    result[bot_id] = {
                        **base, "running": True, "pid": pid,
                        "managed_by": "external",
                    }
                else:
                    result[bot_id] = {
                        **base, "running": False, "pid": None,
                        "managed_by": None,
                    }
        return result

    def start(self, bot_id: str) -> dict:
        spec = self._registry.get(bot_id)
        if spec is None:
            raise ValueError(f"unknown bot: {bot_id}")

        st = self.status().get(bot_id, {})
        if st.get("running"):
            raise ValueError(f"{bot_id} already running (pid {st.get('pid')})")

        log_path = f"/tmp/dashboard_{bot_id}.log"
        log_fh = open(log_path, "ab")
        try:
            proc = subprocess.Popen(
                spec["cmd"],
                cwd=spec["cwd"],
                stdout=log_fh,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        except (OSError, ValueError) as exc:
            log_fh.close()
            raise ValueError(f"failed to start {bot_id}: {exc}") from exc

        self._procs[bot_id] = proc
        self._log_handles[bot_id] = log_fh
        self._update_state_entry(bot_id, {
            "pid": proc.pid,
            "managed_by": "dashboard",
            "started_at": _now_iso(),
        })
        return self.status()[bot_id]

    def stop(self, bot_id: str) -> dict:
        proc = self._procs.get(bot_id)
        if proc is not None and proc.poll() is None:
            self._terminate(proc)
            self._procs.pop(bot_id, None)
            self._close_log(bot_id)
            self._update_state_entry(bot_id, None)
            return self.status()[bot_id]

        # no live in-memory handle: only a state-recorded managed pid may die
        state = self._read_state()
        entry = state.get(bot_id)
        pid = entry.get("pid") if entry else None
        if entry and entry.get("managed_by") == "dashboard" and _pid_alive(pid):
            try:
                os.killpg(os.getpgid(pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
            self._update_state_entry(bot_id, None)
            return self.status()[bot_id]

        raise ValueError("not managed by dashboard")

    def shutdown(self) -> None:
        """Terminate every dashboard-managed process (atexit hook)."""
        for bot_id in list(self._procs):
            proc = self._procs.get(bot_id)
            if proc is not None and proc.poll() is None:
                self._terminate(proc)
            self._procs.pop(bot_id, None)
            self._close_log(bot_id)
        # best effort state cleanup
        self._write_state({})

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------
    @staticmethod
    def _terminate(proc: subprocess.Popen) -> None:
        try:
            proc.terminate()
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                proc.kill()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        except (ProcessLookupError, PermissionError):
            pass

    def _close_log(self, bot_id: str) -> None:
        fh = self._log_handles.pop(bot_id, None)
        if fh is not None:
            try:
                fh.close()
            except OSError:
                pass
