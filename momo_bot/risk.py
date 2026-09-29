from __future__ import annotations

import datetime as dt


def daily_loss_hit(start_equity: float, total_pnl: float, daily_loss_pct: float) -> bool:
    return total_pnl <= -(start_equity * daily_loss_pct)


def giveback_hit(total_pnl: float, peak_pnl: float, giveback_pct: float) -> bool:
    if peak_pnl <= 0:
        return False
    return total_pnl <= peak_pnl * (1 - giveback_pct)


def in_trading_window(now: dt.datetime, start: dt.time, end: dt.time) -> bool:
    t = now.time()
    return start <= t <= end


def past_window(now: dt.datetime, end: dt.time) -> bool:
    return now.time() > end