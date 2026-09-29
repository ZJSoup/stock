import datetime as dt
from momo_bot.signals import (
    Bar, detect_impulse, find_pullback, is_basing, is_crossing_candle,
)

def bar(i, o, h, l, c, v=1000):
    return Bar(dt.datetime(2026, 9, 29, 9, 0) + dt.timedelta(minutes=i), o, h, l, c, v)

# 5-bar rally: 5.00 -> 6.00 (+20%), then pullback to 5.75, then reversal bar
RALLY = [
    bar(0, 5.00, 5.10, 4.98, 5.08),
    bar(1, 5.08, 5.30, 5.05, 5.28),
    bar(2, 5.28, 5.55, 5.25, 5.52),
    bar(3, 5.52, 5.80, 5.50, 5.76),
    bar(4, 5.76, 6.02, 5.74, 6.00),
]
PULLBACK = RALLY + [
    bar(5, 6.00, 6.00, 5.80, 5.82, 900),   # red bar, high 6.00
    bar(6, 5.82, 5.84, 5.75, 5.77, 800),   # low 5.75, red
]
BASING = PULLBACK + [
    bar(7, 5.77, 5.79, 5.749, 5.78, 700),  # second low within 0.003 of 5.75
]

def test_detect_impulse():
    imp = detect_impulse(RALLY, lookback=5, min_pct=0.15)
    assert imp is not None
    assert imp.start_idx == 0 and imp.end_idx == 4
    assert round(imp.gain_pct, 2) == 0.20
    assert imp.low == 4.98 and imp.high == 6.02

def test_detect_impulse_none_when_flat():
    flat = [bar(i, 5.0, 5.02, 4.99, 5.01) for i in range(5)]
    assert detect_impulse(flat, lookback=5, min_pct=0.15) is None

def test_find_pullback():
    imp = detect_impulse(BASING, lookback=5, min_pct=0.15)
    pb = find_pullback(BASING, imp, retrace_min=0.25, retrace_max=0.70)
    assert pb is not None
    assert pb.low == 5.75
    assert pb.breakout_level == 6.00
    # retraced 0.25 of the 1.04 leg (6.02 high ... leg measured close-to-close 5.00->6.00)
    assert 0.20 <= pb.depth_pct <= 0.30

def test_pullback_too_deep_is_rejected():
    imp = detect_impulse(RALLY, lookback=5, min_pct=0.15)
    broken = RALLY + [bar(5, 6.0, 6.0, 5.2, 5.25)]
    assert find_pullback(broken, imp, 0.25, 0.70) is None

def test_basing_requires_two_close_lows():
    imp = detect_impulse(BASING, lookback=5, min_pct=0.15)
    pb = find_pullback(BASING, imp, 0.25, 0.70)
    assert is_basing(BASING, pb, low_tol=0.003) is True
    assert is_basing(PULLBACK, pb, low_tol=0.003) is False

def test_crossing_candle():
    cross = bar(8, 5.78, 6.05, 5.77, 6.02)
    assert is_crossing_candle(cross, breakout_level=6.00) is True
    assert is_crossing_candle(bar(8, 5.78, 5.99, 5.77, 5.95), 6.00) is False
