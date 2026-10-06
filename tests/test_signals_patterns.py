import datetime as dt
import pytest
from momo_bot.signals import Bar, Tick, is_topping_tail, is_doji, volume_divergence, sell_pressure_ratio, risk_reward, has_black_history

def bar(o, h, l, c, v=1000):
    return Bar(dt.datetime(2026, 9, 29, 9, 30), o, h, l, c, v)

def series(specs):
    return [bar(*s) for s in specs]

def tick(price, size, side):
    return Tick(dt.datetime(2026, 9, 29, 9, 30), price, size, side)

def test_topping_tail():
    assert is_topping_tail(bar(6.00, 6.40, 5.98, 6.02)) is True   # long upper wick
    assert is_topping_tail(bar(6.00, 6.05, 5.95, 6.04)) is False

def test_doji():
    assert is_doji(bar(6.00, 6.02, 5.98, 6.001)) is True
    assert is_doji(bar(6.00, 6.20, 5.98, 6.18)) is False

def test_volume_divergence():
    bars = [
        bar(5.8, 5.9, 5.75, 5.88, v=3000),
        bar(5.9, 6.0, 5.88, 5.98, v=2000),
        bar(5.98, 6.10, 5.97, 6.08, v=1000),  # higher high, volumes 3000->1000 (-67%)
    ]
    assert volume_divergence(bars, n=3, decline=0.30) is True

def test_no_divergence_when_volume_expands():
    bars = [
        bar(5.8, 5.9, 5.75, 5.88, v=1000),
        bar(5.9, 6.0, 5.88, 5.98, v=2000),
        bar(5.98, 6.10, 5.97, 6.08, v=3000),
    ]
    assert volume_divergence(bars, 3, 0.30) is False

def test_sell_pressure_ratio():
    ticks = [tick(6.0, 100, "B"), tick(5.99, 300, "S")]
    ratio = sell_pressure_ratio(ticks)
    assert ratio == 0.75

def test_risk_reward():
    assert risk_reward(entry=6.0, stop=5.9, target=6.2) == pytest.approx(2.0)


# --- daily black-history filter (Ross's "dirty daily" rejection) -----------

def test_clean_history_passes():
    # steady uptrend near highs, no pump-and-dump, no distribution
    bars = series([
        (9.6, 9.8, 9.5, 9.7, 1000),
        (9.7, 10.0, 9.6, 9.9, 1000),
        (9.9, 10.2, 9.8, 10.1, 1000),
    ])
    assert has_black_history(bars) is False

def test_pump_then_dump_is_dirty():
    # JAGX-style: $2 base, rocketed to a $60 peak, collapsed back to $5
    bars = series([
        (2.0, 2.1, 1.95, 2.0, 1000),
        (2.0, 2.1, 1.95, 2.0, 1000),
        (2.0, 10.0, 2.0, 9.0, 2000),
        (9.0, 30.0, 8.5, 28.0, 3000),
        (28.0, 60.0, 27.0, 55.0, 4000),   # 60 peak
        (55.0, 55.0, 30.0, 32.0, 5000),
        (32.0, 33.0, 12.0, 14.0, 4000),
        (14.0, 15.0, 4.5, 5.0, 3000),     # close 5, far under peak
    ])
    assert has_black_history(bars) is True

def test_pump_without_dump_stays_clean():
    # strong run that is STILL holding near the high -> momentum, not dirty
    bars = series([
        (2.0, 2.1, 1.95, 2.0, 1000),
        (2.0, 10.0, 2.0, 9.0, 2000),
        (9.0, 30.0, 8.5, 28.0, 3000),
        (28.0, 60.0, 27.0, 55.0, 4000),
        (55.0, 59.0, 54.0, 57.0, 4000),
        (57.0, 60.0, 56.0, 58.0, 4000),   # close 58: giveback only ~3%
    ])
    assert has_black_history(bars) is False

def test_high_volume_distribution_is_dirty():
    # IPDN/VIVK-style: 3 heavy red bars, ~2x avg volume, -30% drop
    flat = [(10.0, 10.1, 9.9, 10.0, 1000)] * 17
    heavy_red = [
        (10.0, 10.0, 9.0, 9.2, 3000),
        (9.2, 9.3, 8.0, 8.2, 3000),
        (8.2, 8.3, 6.8, 7.0, 3000),
    ]
    assert has_black_history(series(flat + heavy_red)) is True

def test_red_bars_on_low_volume_stay_clean():
    # declining but on BELOW-normal volume -> no distribution, no pump
    flat = [(10.0, 10.1, 9.9, 10.0, 1000)] * 17
    light_red = [
        (10.0, 10.0, 9.0, 9.2, 500),
        (9.2, 9.3, 8.0, 8.2, 500),
        (8.2, 8.3, 6.8, 7.0, 500),
    ]
    assert has_black_history(series(flat + light_red)) is False

def test_black_history_empty_and_short():
    assert has_black_history([]) is False
    assert has_black_history(series([(2, 2.1, 1.95, 2.0)])) is False
