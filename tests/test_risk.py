import datetime as dt
from momo_bot.risk import daily_loss_hit, giveback_hit, in_trading_window, past_window

def test_daily_loss():
    assert daily_loss_hit(start_equity=2000, total_pnl=-300, daily_loss_pct=0.15) is True
    assert daily_loss_hit(2000, -200, 0.15) is False

def test_giveback():
    # peak +$400, now +$200 -> gave back 50%
    assert giveback_hit(total_pnl=200, peak_pnl=400, giveback_pct=0.50) is True
    assert giveback_hit(300, 400, 0.50) is False
    assert giveback_hit(-50, 400, 0.50) is True

def test_giveback_ignored_before_profit():
    assert giveback_hit(-100, 0, 0.50) is False

def test_window():
    now = dt.datetime(2026, 9, 29, 8, 0)
    assert in_trading_window(now, dt.time(7, 0), dt.time(10, 0)) is True
    assert past_window(dt.datetime(2026, 9, 29, 10, 1), dt.time(10, 0)) is True
    assert past_window(now, dt.time(10, 0)) is False

    # Boundary tests for exact time
    assert in_trading_window(dt.datetime(2026, 9, 29, 7, 0), dt.time(7, 0), dt.time(10, 0)) is True
    assert in_trading_window(dt.datetime(2026, 9, 29, 10, 0), dt.time(7, 0), dt.time(10, 0)) is True
    assert past_window(dt.datetime(2026, 9, 29, 10, 0), dt.time(10, 0)) is False