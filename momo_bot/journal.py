from __future__ import annotations

import csv
import pathlib

TRADE_COLS = ["date", "symbol", "grade", "shares", "entry", "exit", "pnl", "reason"]
TRADES_FILE = "journal.csv"
VIOLATIONS_FILE = "violations.csv"


def _append(path: pathlib.Path, fieldnames: list[str], row: dict) -> None:
    new = not path.exists()
    with open(path, "a", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        if new:
            writer.writeheader()
        writer.writerow(row)


def record_trade(data_dir: pathlib.Path, record: dict) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    _append(data_dir / TRADES_FILE, TRADE_COLS, record)


def record_violation(data_dir: pathlib.Path, reason: str, date: str) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / VIOLATIONS_FILE
    _append(path, ["date", "reason"], {"date": date, "reason": reason})


def _read_trades(data_dir: pathlib.Path) -> list[dict]:
    path = data_dir / TRADES_FILE
    if not path.exists():
        return []
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


def _max_drawdown(pnls: list[float]) -> float:
    """Worst peak-to-trough drop on the cumulative pnl curve, as a fraction
    of the peak cumulative equity."""
    peak = 0.0
    cum = 0.0
    worst = 0.0
    for p in pnls:
        cum += p
        peak = max(peak, cum)
        if peak > 0:
            worst = max(worst, (peak - cum) / peak)
    return worst


def summarize(data_dir: pathlib.Path) -> dict:
    rows = _read_trades(data_dir)
    pnls = [float(r["pnl"]) for r in rows]
    days = {r["date"] for r in rows}
    vpath = data_dir / VIOLATIONS_FILE
    violations = 0
    if vpath.exists():
        with open(vpath, newline="") as fh:
            violations = max(0, sum(1 for _ in csv.DictReader(fh)))
    wins = sum(1 for p in pnls if p > 0)
    stats = {
        "trading_days": len(days),
        "setups": len(rows),
        "net": sum(pnls),
        "win_rate": (wins / len(pnls)) if pnls else 0.0,
        "max_drawdown": _max_drawdown(pnls),
        "violations": violations,
    }
    stats["eligible"] = (
        stats["trading_days"] >= 20
        and stats["setups"] >= 10
        and stats["net"] > 0
        and stats["win_rate"] >= 0.60
        and stats["max_drawdown"] <= 0.15
        and stats["violations"] == 0
    )
    return stats
