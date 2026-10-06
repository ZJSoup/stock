"""Read/write the momo bot TOML config with validation and backup.

CONFIG_PATH is module-level on purpose: tests monkeypatch this name.
No third-party TOML writer is available, so serialization is a small
hand-rolled dumper with stable key order matching config.example.toml.
"""

from __future__ import annotations

import pathlib
import shutil
import tomllib

CONFIG_PATH = pathlib.Path.home() / ".config" / "stock-momo" / "config.toml"

TOP_LEVEL_ORDER = ["host", "port", "client_id", "mode"]
SECTION_ORDER: dict[str, list[str]] = {
    "risk": ["risk_pct", "daily_loss_pct", "giveback_pct"],
    "window": ["start", "end"],
    "signals": [
        "impulse_lookback", "impulse_min_pct", "retrace_min", "retrace_max",
        "basing_low_tol", "sell_pressure_pct", "divergence_bars",
        "divergence_decline", "entry_rr",
    ],
}

# type spec: "str" | "int" | "float"
_TOP_TYPES = {"host": "str", "port": "int", "client_id": "int", "mode": "str"}
_SECTION_TYPES: dict[str, dict[str, str]] = {
    "risk": {
        "risk_pct": "float", "daily_loss_pct": "float", "giveback_pct": "float",
    },
    "window": {"start": "str", "end": "str"},
    "signals": {
        "impulse_lookback": "int", "impulse_min_pct": "float",
        "retrace_min": "float", "retrace_max": "float",
        "basing_low_tol": "float", "sell_pressure_pct": "float",
        "divergence_bars": "int", "divergence_decline": "float",
        "entry_rr": "float",
    },
}

PAPER_PORTS = {4002, 7497}
LIVE_PORTS = {4001, 7496}


# ----------------------------------------------------------------------
# read
# ----------------------------------------------------------------------
def read_momo_config() -> dict:
    path = CONFIG_PATH
    if not path.exists():
        return {"path": str(path), "exists": False}
    try:
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"cannot parse {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"unexpected config shape in {path}")
    data["path"] = str(path)
    data["exists"] = True
    return data


# ----------------------------------------------------------------------
# validation
# ----------------------------------------------------------------------
def _coerce(value, kind: str, label: str):
    # bool is a subtype of int in Python — never accept it for numeric fields
    if isinstance(value, bool):
        raise ValueError(f"{label} must be {kind}, got bool")
    if kind == "str":
        if not isinstance(value, str) or not value:
            raise ValueError(f"{label} must be a non-empty string")
        return value
    if kind == "int":
        if isinstance(value, int):
            return value
        if isinstance(value, float) and value.is_integer():
            return int(value)
        raise ValueError(f"{label} must be an integer")
    if kind == "float":
        if isinstance(value, (int, float)):
            return float(value)
        raise ValueError(f"{label} must be numeric")
    raise ValueError(f"unknown type spec for {label}")


def _validate(data: dict) -> dict:
    mode = data.get("mode")
    if mode not in {"paper", "live"}:
        raise ValueError("mode must be 'paper' or 'live'")

    port = _coerce(data.get("port"), "int", "port")
    allowed = PAPER_PORTS if mode == "paper" else LIVE_PORTS
    if port not in allowed:
        raise ValueError(
            f"port {port} not allowed for mode={mode} "
            f"(allowed: {sorted(allowed)})"
        )

    clean = {"mode": mode, "port": port}
    for key in ("host", "client_id"):
        clean[key] = _coerce(data.get(key), _TOP_TYPES[key], key)

    for section, type_map in _SECTION_TYPES.items():
        raw = data.get(section)
        if not isinstance(raw, dict):
            raise ValueError(f"section [{section}] is required")
        clean[section] = {
            key: _coerce(raw.get(key), kind, f"{section}.{key}")
            for key, kind in type_map.items()
        }
    return clean


# ----------------------------------------------------------------------
# write
# ----------------------------------------------------------------------
def _dump_value(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    # string: basic TOML escaping
    escaped = (
        str(value)
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\t", "\\t")
    )
    return f'"{escaped}"'


def _dump_toml(data: dict) -> str:
    out: list[str] = []
    for key in TOP_LEVEL_ORDER:
        if key in data:
            out.append(f"{key} = {_dump_value(data[key])}")

    for section in ("risk", "window", "signals"):
        if section not in data:
            continue
        out.append("")
        out.append(f"[{section}]")
        values = data[section]
        for key in SECTION_ORDER[section]:
            if key in values:
                out.append(f"{key} = {_dump_value(values[key])}")
        # preserve any extra keys deterministically
        extras = sorted(k for k in values if k not in SECTION_ORDER[section])
        for key in extras:
            out.append(f"{key} = {_dump_value(values[key])}")

    # preserve extra top-level keys after the known sections
    extras = sorted(k for k in data if k not in TOP_LEVEL_ORDER
                    and k not in SECTION_ORDER)
    for key in extras:
        value = data[key]
        if isinstance(value, dict):
            out.append("")
            out.append(f"[{key}]")
            for sub_key in sorted(value):
                out.append(f"{sub_key} = {_dump_value(value[sub_key])}")
        else:
            out.insert(len(TOP_LEVEL_ORDER), f"{key} = {_dump_value(value)}")

    return "\n".join(out) + "\n"


def write_momo_config(data: dict) -> dict:
    if not isinstance(data, dict):
        raise ValueError("config body must be an object")
    clean = _validate(data)

    path = CONFIG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        backup = path.with_suffix(path.suffix + ".bak")
        shutil.copy2(path, backup)

    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(_dump_toml(clean))
    tmp.replace(path)

    return read_momo_config()
