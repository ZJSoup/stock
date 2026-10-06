import pathlib
import tests.test_signals as ts
from momo_bot.config import load_config
from momo_bot import engine as eng
from momo_bot.pillars import Candidate

def settings():
    return load_config(pathlib.Path(__file__).parents[1] / "config.example.toml")

def candidate():
    return Candidate("ZTG", 123, 5.78, 4.0, 0.445, 5_000_000, 100_000, 50.0,
                     5.78, 8_000_000, False, "B")

def test_full_day_win_then_lock(tmp_path):
    s = eng.initial_state("2026-09-29", settings())
    s, _ = eng.handle(s, eng.Account(2000, 2000))
    s, _ = eng.handle(s, eng.ScanResults([candidate()]))

    # pullback forms, crossing candle enters, fill
    cross = ts.bar(8, 5.78, 6.05, 5.77, 6.02)
    s, _ = eng.handle(s, eng.Bars("ZTG", ts.BASING + [cross]))
    entry_cmds = []
    s, entry_cmds = eng.handle(s, eng.Fill("ZTG", 172, 6.0, "B"))
    assert any(isinstance(c, eng.AttachStop) for c in entry_cmds)

    # target reached: halve, fill the partial
    s, _ = eng.handle(s, eng.Bars("ZTG", ts.BASING + [
        cross, ts.bar(9, 6.0, 6.25, 5.99, 6.2)]))
    s, _ = eng.handle(s, eng.Fill("ZTG", 86, 6.2, "S"))

    # topping tail: remaining position exits, locks the day
    s, _ = eng.handle(s, eng.Bars("ZTG", ts.BASING + [
        cross, ts.bar(9, 6.0, 6.25, 5.99, 6.2),
        ts.bar(10, 6.2, 6.5, 6.15, 6.21)]))
    s, _ = eng.handle(s, eng.Fill("ZTG", 86, 6.2, "S"))

    assert s.done is True
    assert s.position_qty == 0
    assert s.realized_pnl > 0
    # DONE rejects new scan events with Noop
    _, cmds = eng.handle(s, eng.ScanResults([]))
    assert isinstance(cmds[0], eng.Noop)
