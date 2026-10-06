import datetime as dt
import pathlib
import tests.test_signals as ts
from momo_bot.config import load_config
from momo_bot.engine import (
    initial_state, handle, restore_state, Account, ScanResults, Bars, Ticks, Fill,
    Exit, ReplaceStop, LockDay, Journal,
)
from momo_bot.pillars import Candidate
from momo_bot.signals import Tick
from momo_bot.state import Snapshot

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
    assert any(isinstance(c, ReplaceStop) and c.stop_price == 6.0 and c.qty == 86
               for c in cmds)

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


def test_target_plus_topping_tail_single_exit():
    # A bar that both clears the 1R target AND is a topping tail must produce
    # exactly one Exit (full qty, reason "candle pattern") + CancelAll.
    # state.reduced must stay False (the half-exit path never ran).
    s = entered()
    assert s.target is not None  # sanity: entry set a target
    # Topping tail: open=6.2, high=6.5 (>= target 6.25), low=6.15, close=6.22
    # body=0.02, upper=0.28, rng=0.35 → upper>=2*body and upper/rng=0.8
    topping_target_bar = ts.bar(9, 6.2, 6.5, 6.15, 6.22)
    s, cmds = handle(s, Bars("ZTG", ts.BASING + [
        ts.bar(8, 5.78, 6.05, 5.77, 6.02), topping_target_bar]))
    exits = [c for c in cmds if isinstance(c, Exit)]
    assert len(exits) == 1
    assert exits[0].qty == 172  # full position, not half
    assert exits[0].reason == "candle pattern"
    assert s.reduced is False
    # _force_flatten emits CancelAll, not LockDay (the day isn't locked yet)
    assert not any(isinstance(c, LockDay) for c in cmds)


def test_restore_legacy_snapshot_daily_loss_gate():
    # Legacy snapshots have start_equity=0 (field default). Restoring one with
    # a broker position must NOT false-trigger daily loss on the first bar
    # with a small unrealized loss — the check is gated until an Account
    # event establishes real equity.
    snap = Snapshot(
        date="2026-09-29", phase="IN_POSITION", symbol="ZTG", con_id=123,
        grade="B", shares=172, position_qty=172, entry=6.0, stop=5.75,
        target=6.25, realized_pnl=0.0, peak_pnl=0.0, reduced=False, done=False,
        start_equity=0.0,  # legacy: field missing on disk, loads as default
    )
    s, mode = restore_state("2026-09-29", settings(), snap, 172, 6.0, False)
    assert mode == "normal"
    assert s.start_equity == 0.0
    assert s.phase == "IN_POSITION"
    # Small unrealized loss — 172 * (5.98 - 6.0) = -3.44. With start_equity=0
    # and daily_loss_pct=0.15, daily_loss_hit(0, -3.44, 0.15) would be True
    # if the gate were missing.
    loss_bar = ts.bar(9, 6.0, 6.02, 5.97, 5.98)
    s, cmds = handle(s, Bars("ZTG", [loss_bar]))
    assert s.done is False, "daily loss must NOT fire when start_equity is 0"
    assert not any(isinstance(c, LockDay) for c in cmds)
    # Now the runtime delivers a real Account event — start_equity established.
    s, _ = handle(s, Account(2000, 2000))
    assert s.start_equity == 2000
    # A big loss bar (e.g. down to 5.0 = -$172 = -8.6% of 2000, still <15%)
    # Won't lock at -8.6%. Need >15% loss: 2000 * 0.15 = $300 → price drop of
    # 300/172 ≈ 1.74 → entry 6.0 - 1.74 = 4.26. Use 4.0 to be well past it.
    big_loss_bar = ts.bar(10, 5.98, 6.0, 3.8, 4.0)
    s, cmds = handle(s, Bars("ZTG", [loss_bar, big_loss_bar]))
    assert s.done is True, "daily loss must fire once start_equity is known"
    assert any(isinstance(c, LockDay) for c in cmds)


def test_unclaimed_returns_readonly_mode():
    s, mode = restore_state("2026-09-29", settings(), None, 0, None, True)
    assert mode == "readonly"
    assert s.phase == "IDLE"


def test_others_positions_not_passed_normal_start():
    s, mode = restore_state("2026-09-29", settings(), None, 0, None, False)
    assert mode == "normal"


def test_own_position_no_snapshot_is_readonly_not_crash():
    s, mode = restore_state("2026-09-29", settings(), None, 100, 6.0, False)
    assert mode == "readonly"


def test_restore_entering_no_broker_position_resets():
    # Snapshot says ENTERING but the broker has no position: buy never filled.
    # restore_state should return a clean IDLE state carrying start_equity
    # forward — no phantom symbol/shares.
    snap = Snapshot(
        date="2026-09-29", phase="ENTERING", symbol="ZTG", con_id=123,
        grade="B", shares=172, position_qty=0, entry=None, stop=5.75,
        target=6.25, realized_pnl=0.0, peak_pnl=0.0, reduced=False, done=False,
        start_equity=2000.0,
    )
    s, mode = restore_state("2026-09-29", settings(), snap, 0, None, False)
    assert mode == "normal"
    assert s.phase == "IDLE"
    assert s.symbol is None
    assert s.shares == 0
    assert s.position_qty == 0
    assert s.start_equity == 2000.0
