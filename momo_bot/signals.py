from __future__ import annotations

import datetime as dt
from dataclasses import dataclass


@dataclass(frozen=True)
class Bar:
    date: dt.datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class Tick:
    time: dt.datetime
    price: float
    size: float
    side: str  # "B" aggressor buy, "S" aggressor sell


@dataclass(frozen=True)
class Impulse:
    start_idx: int
    end_idx: int
    low: float
    high: float
    gain_pct: float


@dataclass(frozen=True)
class Pullback:
    start_idx: int
    end_idx: int
    low: float
    breakout_level: float
    depth_pct: float


def detect_impulse(bars: list[Bar], lookback: int, min_pct: float) -> Impulse | None:
    """Strong unidirectional leg of exactly `lookback` bars.

    The pivot high is searched backwards from the most recent bar, so the
    rally leg is still found after pullback bars have formed. The most
    recent qualifying window wins.
    """
    if len(bars) < lookback:
        return None
    for end in range(len(bars) - 1, lookback - 2, -1):
        window = bars[end - lookback + 1:end + 1]
        low = min(b.low for b in window)
        high = max(b.high for b in window)
        # directional move captured across the leg (open of first bar to close
        # of last); high-low wick range overstates it
        gain = (window[-1].close - window[0].open) / window[0].open
        # closes must march up: tolerance 0.5% for micro noise
        ordered = all(
            window[i].close >= window[i - 1].close * 0.995
            for i in range(1, len(window))
        )
        if gain >= min_pct and ordered:
            return Impulse(end - lookback + 1, end, low, high, gain)
    return None


def find_pullback(
    bars: list[Bar], impulse: Impulse, retrace_min: float, retrace_max: float
) -> Pullback | None:
    """First consolidation after the impulse: lower highs, capped retracement."""
    after = bars[impulse.end_idx + 1:]
    if len(after) < 2:
        return None
    leg = impulse.high - impulse.low
    pb_bars = []
    for b in after:
        if b.close >= impulse.high:  # broke out already, no pullback formed
            return None
        pb_bars.append(b)
        low = min(x.low for x in pb_bars)
        depth = (impulse.high - low) / leg if leg else 0
        if depth > retrace_max:
            return None
        if len(pb_bars) >= 2 and retrace_min <= depth:
            breakout_level = max(x.high for x in pb_bars)
            return Pullback(
                impulse.end_idx + 1,
                impulse.end_idx + len(pb_bars),
                low,
                breakout_level,
                depth,
            )
    return None


def is_basing(bars: list[Bar], pullback: Pullback, low_tol: float) -> bool:
    """Two distinct recent lows within `low_tol` (fraction) of each other."""
    lows = sorted({round(b.low, 4) for b in bars[pullback.start_idx:]})
    if len(lows) < 2:
        return False
    base = pullback.low
    return (lows[1] - lows[0]) / base <= low_tol


def is_crossing_candle(bar: Bar, breakout_level: float) -> bool:
    return bar.high > breakout_level


def _body(bar: Bar) -> float:
    return abs(bar.close - bar.open)


def _range(bar: Bar) -> float:
    return bar.high - bar.low


def is_topping_tail(bar: Bar) -> bool:
    body = _body(bar)
    upper = bar.high - max(bar.open, bar.close)
    rng = _range(bar)
    if rng <= 0:
        return False
    return upper >= 2 * body and upper / rng >= 0.4


def is_doji(bar: Bar) -> bool:
    rng = _range(bar)
    if rng <= 0:
        return True
    return _body(bar) / rng <= 0.10


def volume_divergence(bars: list[Bar], n: int, decline: float) -> bool:
    if len(bars) < n:
        return False
    window = bars[-n:]
    if window[-1].high <= max(b.high for b in window[:-1]):
        return False  # no new high, nothing to diverge from
    first_vol = window[0].volume
    if first_vol <= 0:
        return False
    shrink = (first_vol - window[-1].volume) / first_vol
    return shrink >= decline


def sell_pressure_ratio(ticks: list[Tick]) -> float:
    total = sum(t.size for t in ticks)
    if total <= 0:
        return 0.0
    sold = sum(t.size for t in ticks if t.side == "S")
    return sold / total


def risk_reward(entry: float, stop: float, target: float) -> float:
    risk = abs(entry - stop)
    if risk <= 0:
        return 0.0
    return abs(target - entry) / risk


def _pump_and_dump(bars: list[Bar], pump_pct: float, crash_pct: float) -> bool:
    """A recent peak that was itself pumped up from a much lower base, and
    which the latest close has already given back heavily. A name still
    holding its high is momentum, not a dump."""
    peak_idx = max(range(len(bars)), key=lambda i: bars[i].high)
    peak = bars[peak_idx].high
    if peak <= 0:
        return False
    pre_low = min(b.low for b in bars[:peak_idx + 1])
    run_up = peak / pre_low - 1 if pre_low > 0 else 0.0
    give_back = 1 - bars[-1].close / peak
    return run_up >= pump_pct and give_back >= crash_pct


def _high_volume_distribution(
    bars: list[Bar], n: int, vol_mult: float, drop_pct: float
) -> bool:
    """`n` consecutive red bars on clearly above-average volume with a
    meaningful cumulative decline — active distribution, not a quiet drift."""
    if len(bars) < n:
        return False
    tail = bars[-n:]
    if not all(b.close < b.open for b in tail):
        return False
    avg_vol = sum(b.volume for b in bars) / len(bars)
    tail_vol = sum(b.volume for b in tail) / n
    drop = 1 - tail[-1].close / tail[0].open
    return tail_vol >= vol_mult * avg_vol and drop >= drop_pct


def has_black_history(
    bars: list[Bar],
    lookback: int = 20,
    pump_pct: float = 0.75,
    crash_pct: float = 0.50,
    dist_n: int = 3,
    vol_mult: float = 1.5,
    dist_pct: float = 0.20,
) -> bool:
    """Daily-history veto (Ross's "dirty daily chart" rejection). True when
    the recent completed sessions show either a pump-and-dump (a peak pumped
    up >= `pump_pct` from its base and already given back >= `crash_pct`) or
    high-volume distribution (`dist_n` heavy red bars, >= `vol_mult` x average
    volume, down >= `dist_pct`). Tunable for the paper phase; deliberately
    conservative so clean momentum near highs is never vetoed."""
    if not bars:
        return False
    window = bars[-lookback:]
    return (
        _pump_and_dump(window, pump_pct, crash_pct)
        or _high_volume_distribution(window, dist_n, vol_mult, dist_pct)
    )
