import datetime as dt
from momo_bot.config import load_config
from momo_bot.engine import (
    initial_state, handle, Clock, Account, ScanResults, Bars, Fill,
    Enter, AttachStop, LockDay, Subscribe,
)
from momo_bot.pillars import Candidate
from momo_bot.signals import Bar
from tests.test_signals import RALLY, PULLBACK, BASING
import tests.test_signals as ts

SETTINGS = None

def settings():
    import pathlib
    return load_config(pathlib.Path(__file__).parents[1] / "config.example.toml")

def candidate():
    return Candidate("ZTG", 123, last=5.78, prev_close=4.0, change_pct=0.445,
                     volume=5_000_000, avg_volume=100_000, rvol=50.0,
                     price=5.78, float_shares=8_000_000, has_news=False, grade="B")

def run(state, event):
    return handle(state, event)

def test_scan_selects_candidate():
    s = initial_state("2026-09-29", settings())
    s, _ = run(s, Account(equity=2000, settled_cash=2000))
    s, cmds = run(s, ScanResults(candidates=[candidate()]))
    assert s.symbol == "ZTG"
    assert any(isinstance(c, Subscribe) for c in cmds)

def test_pullback_then_cross_emits_entry():
    s = initial_state("2026-09-29", settings())
    s, _ = run(s, Account(2000, 2000))
    s, _ = run(s, ScanResults([candidate()]))
    s, _ = run(s, Bars("ZTG", BASING))
    cross = ts.bar(8, 5.78, 6.05, 5.77, 6.02)
    s, cmds = run(s, Bars("ZTG", BASING + [cross]))
    enters = [c for c in cmds if isinstance(c, Enter)]
    assert len(enters) == 1
    # B-grade factor 0.5: cash cap 345 shares (2000/5.78) -> 172
    assert enters[0].qty >= 100

def test_entry_fill_attaches_stop():
    s = initial_state("2026-09-29", settings())
    s, _ = run(s, Account(2000, 2000))
    s, _ = run(s, ScanResults([candidate()]))
    cross = ts.bar(8, 5.78, 6.05, 5.77, 6.02)
    s, _ = run(s, Bars("ZTG", BASING + [cross]))
    s, cmds = run(s, Fill("ZTG", qty=172, price=6.0, side="B"))
    assert s.phase == "IN_POSITION"
    stops = [c for c in cmds if isinstance(c, AttachStop)]
    assert len(stops) == 1 and stops[0].stop_price < 6.0

def test_lock_after_window():
    s = initial_state("2026-09-29", settings())
    s, _ = run(s, Account(2000, 2000))
    s, cmds = run(s, Clock(dt.datetime(2026, 9, 29, 10, 1)))
    assert s.done is True
    assert any(isinstance(c, LockDay) for c in cmds)
