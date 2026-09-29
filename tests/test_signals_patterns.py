import datetime as dt
import pytest
from momo_bot.signals import Bar, Tick, is_topping_tail, is_doji, volume_divergence, sell_pressure_ratio, risk_reward

def bar(o, h, l, c, v=1000):
    return Bar(dt.datetime(2026, 9, 29, 9, 30), o, h, l, c, v)

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
