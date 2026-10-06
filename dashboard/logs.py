"""Tail known bot log files (read-only).

Names come from a fixed allowlist — callers can never point this at an
arbitrary filesystem path.
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

# name -> list of path templates; {today} is substituted with YYYYMMDD
LOG_SOURCES: dict[str, list[str]] = {
    "spy-steady": [
        "/tmp/dashboard_spy-steady.log",
        "/tmp/spy_steady_{today}.out",
    ],
    "spy-turbo": ["/tmp/dashboard_spy-turbo.log"],
    "momo": ["/tmp/dashboard_momo.log"],
    "spy-trader": ["/Users/zijunt/spy_trader.log"],
}


def _resolve(template: str) -> Path:
    today = _dt.datetime.now().strftime("%Y%m%d")
    return Path(template.format(today=today))


def _read_tail(path: Path, lines: int) -> list[str]:
    """Read the last `lines` lines of a file; binary-safe."""
    try:
        data = path.read_bytes()
    except OSError:
        return []
    text = data.decode("utf-8", errors="replace")
    return text.splitlines()[-lines:]


def tail_log(name: str, lines: int = 200) -> dict:
    if name not in LOG_SOURCES:
        raise ValueError(f"unknown log source: {name}")
    if lines <= 0:
        raise ValueError("lines must be positive")

    paths = [_resolve(t) for t in LOG_SOURCES[name]]
    combined: list[str] = []
    found_path: str | None = None
    remaining = lines
    for path in paths:
        if not path.exists():
            continue
        if found_path is None:
            found_path = str(path)
        chunk = _read_tail(path, remaining)
        combined.extend(chunk)
        remaining -= len(chunk)
        if remaining <= 0:
            break

    return {"name": name, "lines": combined[-lines:], "path": found_path}
