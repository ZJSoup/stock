from __future__ import annotations

import dataclasses
import json
import os
import pathlib


@dataclasses.dataclass(frozen=True)
class Snapshot:
    date: str
    phase: str
    symbol: str | None
    con_id: int | None
    shares: int
    position_qty: int
    entry: float | None
    stop: float | None
    target: float | None
    realized_pnl: float
    peak_pnl: float
    reduced: bool
    done: bool
    grade: str | None = None
    start_equity: float = 0.0


def _path(data_dir: pathlib.Path, date: str) -> pathlib.Path:
    return data_dir / f"state-{date}.json"


def save_snapshot(data_dir: pathlib.Path, snap: Snapshot) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    final = _path(data_dir, snap.date)
    tmp = final.with_suffix(".json.tmp")
    with open(tmp, "w") as fh:
        json.dump(dataclasses.asdict(snap), fh)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, final)


def load_snapshot(data_dir: pathlib.Path, date: str) -> Snapshot | None:
    path = _path(data_dir, date)
    if not path.exists():
        return None
    with open(path) as fh:
        return Snapshot(**json.load(fh))


def clear_snapshot(data_dir: pathlib.Path, date: str) -> None:
    path = _path(data_dir, date)
    if path.exists():
        path.unlink()
