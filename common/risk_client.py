"""Fail-closed client for the dashboard risk-budget endpoint.

Any failure to reach/understand the dashboard is treated as a hard block
on new entries.
"""

import json
import urllib.error
import urllib.request


class RiskBudgetClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8765", timeout: float = 3.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _snapshot(self) -> dict | None:
        try:
            req = urllib.request.Request(f"{self.base_url}/api/risk-budget")
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                if resp.status != 200:
                    return None
                return json.loads(resp.read().decode())
        except (urllib.error.URLError, OSError, ValueError, TimeoutError):
            return None

    def can_open(self, strategy_id: str) -> tuple[bool, str]:
        snap = self._snapshot()
        if snap is None:
            return False, "risk budget dashboard unreachable"
        try:
            if snap["account"]["halt"]:
                return False, "account halt is active"
            strategy = snap["strategies"][strategy_id]
            if not strategy["can_open"]:
                return False, f"risk budget exceeded for {strategy_id}"
        except (KeyError, TypeError):
            return False, "malformed risk budget response"
        return True, ""

    def is_halted(self) -> bool:
        snap = self._snapshot()
        if snap is None:
            return True
        try:
            return bool(snap["account"]["halt"])
        except (KeyError, TypeError):
            return True
