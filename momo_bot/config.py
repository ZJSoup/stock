from __future__ import annotations

import datetime as dt
import os
import pathlib
import tomllib
from dataclasses import dataclass, replace


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    client_id: int
    mode: str
    risk_pct: float
    daily_loss_pct: float
    giveback_pct: float
    window_start: dt.time
    window_end: dt.time
    impulse_lookback: int
    impulse_min_pct: float
    retrace_min: float
    retrace_max: float
    basing_low_tol: float
    sell_pressure_pct: float
    divergence_bars: int
    divergence_decline: float
    entry_rr: float


def default_config_path() -> pathlib.Path:
    env = os.environ.get("MOMO_CONFIG")
    if env:
        return pathlib.Path(env)
    return pathlib.Path.home() / ".config" / "stock-momo" / "config.toml"


def _parse_time(value: str) -> dt.time:
    hh, mm = value.split(":")
    return dt.time(int(hh), int(mm))


def load_config(path: pathlib.Path | None = None) -> Settings:
    path = path or default_config_path()
    with open(path, "rb") as fh:
        data = tomllib.load(fh)

    risk = data["risk"]
    window = data["window"]
    sig = data["signals"]
    s = Settings(
        host=data["host"],
        port=int(data["port"]),
        client_id=int(data["client_id"]),
        mode=str(data["mode"]),
        risk_pct=float(risk["risk_pct"]),
        daily_loss_pct=float(risk["daily_loss_pct"]),
        giveback_pct=float(risk["giveback_pct"]),
        window_start=_parse_time(window["start"]),
        window_end=_parse_time(window["end"]),
        impulse_lookback=int(sig["impulse_lookback"]),
        impulse_min_pct=float(sig["impulse_min_pct"]),
        retrace_min=float(sig["retrace_min"]),
        retrace_max=float(sig["retrace_max"]),
        basing_low_tol=float(sig["basing_low_tol"]),
        sell_pressure_pct=float(sig["sell_pressure_pct"]),
        divergence_bars=int(sig["divergence_bars"]),
        divergence_decline=float(sig["divergence_decline"]),
        entry_rr=float(sig["entry_rr"]),
    )

    env_port = os.environ.get("MOMO_PORT")
    if env_port:
        s = replace(s, port=int(env_port))
    env_mode = os.environ.get("MOMO_MODE")
    if env_mode:
        s = replace(s, mode=env_mode)
    return s
