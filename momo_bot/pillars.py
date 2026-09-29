from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Candidate:
    symbol: str
    con_id: int
    last: float
    prev_close: float
    change_pct: float
    volume: float
    avg_volume: float
    rvol: float
    price: float
    float_shares: float | None
    has_news: bool
    grade: str


def relative_volume(today_volume: float, avg_volume: float) -> float:
    if avg_volume <= 0:
        return 0.0
    return today_volume / avg_volume


def grade_pillars(
    change_pct: float,
    rvol: float,
    price: float,
    float_shares: float | None,
    has_news: bool,
) -> str | None:
    if change_pct < 0.10 or rvol < 5.0:
        return None
    if not (2.0 <= price <= 20.0):
        return None
    if float_shares is None:
        float_ok = False   # unknown float is treated conservatively (low confidence)
    else:
        float_ok = float_shares < 20_000_000
    if not float_ok:
        return None

    if not has_news:
        return "B"
    if change_pct >= 0.30 and 5.0 <= price <= 10.0 and float_shares < 10_000_000:
        return "A+"
    return "A"
