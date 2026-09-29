from __future__ import annotations

GRADE_FACTORS: dict[str, float] = {"A+": 1.0, "A": 0.8, "B": 0.5}


def risk_shares(equity: float, risk_pct: float, entry: float, stop: float) -> int:
    risk_amount = equity * risk_pct
    distance = abs(entry - stop)
    if distance <= 0:
        return 0
    return int(risk_amount / distance)


def cash_shares(settled_cash: float, price: float) -> int:
    if price <= 0:
        return 0
    return int(settled_cash / price)


def calc_shares(
    equity: float,
    settled_cash: float,
    entry: float,
    stop: float,
    grade: str,
    risk_pct: float,
) -> int:
    by_risk = risk_shares(equity, risk_pct, entry, stop)
    by_cash = cash_shares(settled_cash, entry)
    factor = GRADE_FACTORS.get(grade, 0.0)
    return int(min(by_risk, by_cash) * factor)
