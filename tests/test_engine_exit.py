import datetime as dt
import pathlib
import tests.test_signals as ts
from momo_bot.config import load_config
from momo_bot.engine import (
    initial_state, handle, Account, ScanResults, Bars, Ticks, Fill,
    Exit, ReplaceStop, LockDay, Journal,
)
from momo_bot.pillars import Candidate
from momo_bot.signals import Tick

def settings():
    return load_config(pathlib.Path(__file__).parents[1] / "config.example.toml")

def candidate():
    return Candidate("ZTG", 123, last=5.78, prev_close=4.0, change_pct=0.445,
                     volume=5_000_000, avg_volume=100_000, rvol=50.0,
                     price=5.78, float_shares=8_000_000, has_news=False, grade="B")

def entered():
    s = initial_state("2026-09-29", settings())
    s, _ = handle(s, Account(2000, 2000))
    s, _ = handle(s, ScanResults([candidate()]))
    cross = ts.bar(8, 5.78, 6.05, 5.77, 6.02)
    s, _ = handle(s, Bars("ZTG", ts.BASING + [cross]))
    s, _ = handle(s, Fill("ZTG", 172, 6.0, "B"))
    return s

def test_target_halves_and_raises_stop():
    s = entered()
    target_bar = ts.bar(9, 6.0, 6.25, 5.99, 6.2)
    s, cmds = handle(s, Bars("ZTG", ts.BASING + [
        ts.bar(8, 5.78, 6.05, 5.77, 6.02), target_bar]))
    exits = [c for c in cmds if isinstance(c, Exit)]
    assert len(exits) == 1 and exits[0].qty == 86
    # breakeven stop is only attached after the half sell actually fills
    assert not any(isinstance(c, ReplaceStop) for c in cmds)
    s, cmds = handle(s, Fill("ZTG", 86, 6.2, "S"))
    assert any(isinstance(c, ReplaceStop) and c.stop_price == 6.0 for c in cmds)

def test_topping_tail_exits_remaining():
    s = entered()
    bars = ts.BASING + [ts.bar(8, 5.78, 6.05, 5.77, 6.02)]
    # halve at target first
    s, _ = handle(s, Bars("ZTG", bars + [ts.bar(9, 6.0, 6.25, 5.99, 6.2)]))
    s, _ = handle(s, Fill("ZTG", 86, 6.2, "S"))
    # topping tail on the next bar
    s, cmds = handle(s, Bars("ZTG", bars + [
        ts.bar(9, 6.0, 6.25, 5.99, 6.2),
        ts.bar(10, 6.2, 6.5, 6.15, 6.21)]))
    exits = [c for c in cmds if isinstance(c, Exit)]
    assert exits and exits[-1].qty == 86

def test_tape_pressure_exits():
    s = entered()
    now = dt.datetime(2026, 9, 29, 9, 31)
    ticks = [Tick(now, 6.0, 100, "B")] + [Tick(now, 5.99, 300, "S")]
    s, cmds = handle(s, Ticks("ZTG", ticks))
    assert any(isinstance(c, Exit) for c in cmds)

def test_stop_fill_locks_day():
    s = entered()
    s, cmds = handle(s, Fill("ZTG", 172, 5.89, "S"))
    assert s.done is True and s.phase == "DONE"
    assert any(isinstance(c, Journal) for c in cmds)
    assert any(isinstance(c, LockDay) for c in cmds)
