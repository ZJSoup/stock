from __future__ import annotations

import dataclasses
import datetime as dt
from dataclasses import dataclass, field

from . import risk
from .pillars import Candidate
from .signals import (
    Bar, Tick, detect_impulse, find_pullback, is_basing, is_crossing_candle,
    is_doji, is_topping_tail, volume_divergence, sell_pressure_ratio, risk_reward,
)
from .sizing import calc_shares


# ---- Events ---------------------------------------------------------------

@dataclass(frozen=True)
class Clock:
    now: dt.datetime

@dataclass(frozen=True)
class Account:
    equity: float
    settled_cash: float

@dataclass(frozen=True)
class ScanResults:
    candidates: list[Candidate]

@dataclass(frozen=True)
class Bars:
    symbol: str
    bars: list[Bar]

@dataclass(frozen=True)
class Ticks:
    symbol: str
    ticks: list[Tick]

@dataclass(frozen=True)
class Fill:
    symbol: str
    qty: int
    price: float
    side: str  # "B" or "S"

@dataclass(frozen=True)
class Halted:
    symbol: str
    halted: bool


# ---- Commands -------------------------------------------------------------

@dataclass(frozen=True)
class _Cmd:
    pass

@dataclass(frozen=True)
class Subscribe(_Cmd):
    symbol: str

@dataclass(frozen=True)
class Unsubscribe(_Cmd):
    symbol: str

@dataclass(frozen=True)
class Enter(_Cmd):
    symbol: str
    qty: int
    ref_price: float

@dataclass(frozen=True)
class AttachStop(_Cmd):
    symbol: str
    qty: int
    stop_price: float

@dataclass(frozen=True)
class ReplaceStop(_Cmd):
    symbol: str
    qty: int
    stop_price: float

@dataclass(frozen=True)
class Exit(_Cmd):
    symbol: str
    qty: int
    ref_price: float
    reason: str

@dataclass(frozen=True)
class CancelAll(_Cmd):
    symbol: str

@dataclass(frozen=True)
class LockDay(_Cmd):
    reason: str

@dataclass(frozen=True)
class Violation(_Cmd):
    reason: str

@dataclass(frozen=True)
class Journal(_Cmd):
    record: dict

@dataclass(frozen=True)
class Snapshot(_Cmd):
    pass

@dataclass(frozen=True)
class Noop(_Cmd):
    pass


# ---- State ----------------------------------------------------------------

@dataclass
class State:
    date: str
    settings: object
    phase: str = "IDLE"
    start_equity: float = 0.0
    equity: float = 0.0
    settled_cash: float = 0.0
    symbol: str | None = None
    con_id: int | None = None
    grade: str | None = None
    bars: dict[str, list[Bar]] = field(default_factory=dict)
    entry: float | None = None
    stop: float | None = None
    target: float | None = None
    shares: int = 0
    position_qty: int = 0
    realized_pnl: float = 0.0
    peak_pnl: float = 0.0
    reduced: bool = False
    done: bool = False


def initial_state(date: str, settings) -> State:
    return State(date=date, settings=settings)


# ---- Helpers --------------------------------------------------------------

def _last_price(state: State, symbol: str) -> float | None:
    series = state.bars.get(symbol)
    return series[-1].close if series else None


def _unrealized(state: State) -> float:
    if state.position_qty and state.entry is not None:
        last = _last_price(state, state.symbol)
        if last is not None:
            return (last - state.entry) * state.position_qty
    return 0.0


def _total_pnl(state: State) -> float:
    return state.realized_pnl + _unrealized(state)


def _lock(state: State, reason: str, cmds: list) -> None:
    state.done = True
    state.phase = "DONE"
    cmds.append(LockDay(reason))
    cmds.append(Snapshot())


def _maybe_hard_risk(state: State, cmds: list) -> bool:
    """Account-level circuit breakers. Returns True if the day was locked;
    callers must stop further evaluation for this event when it does."""
    total = _total_pnl(state)
    state.peak_pnl = max(state.peak_pnl, total)
    if risk.daily_loss_hit(state.start_equity, total, state.settings.daily_loss_pct):
        _force_flatten(state, cmds, "daily loss")
        _lock(state, "daily loss", cmds)
        return True
    if risk.giveback_hit(total, state.peak_pnl, state.settings.giveback_pct):
        _force_flatten(state, cmds, "giveback")
        _lock(state, "giveback", cmds)
        return True
    return False


def _force_flatten(state: State, cmds: list, reason: str) -> None:
    if state.position_qty > 0:
        cmds.append(Exit(state.symbol, state.position_qty,
                         _last_price(state, state.symbol) or state.entry, reason))
        cmds.append(CancelAll(state.symbol))


# ---- Event handlers -------------------------------------------------------

def _on_clock(state: State, event: Clock, cmds: list) -> None:
    s = state.settings
    if risk.past_window(event.now, s.window_end) and not state.done:
        if state.position_qty > 0:
            _force_flatten(state, cmds, "window end")
        _lock(state, "window end", cmds)


def _on_account(state: State, event: Account, cmds: list) -> None:
    if state.start_equity == 0.0:
        state.start_equity = event.equity
    state.equity = event.equity
    state.settled_cash = event.settled_cash
    if state.phase == "IDLE":
        state.phase = "SCANNING"
    if _maybe_hard_risk(state, cmds):
        return


def _on_scan(state: State, event: ScanResults, cmds: list) -> None:
    if state.phase != "SCANNING" or not event.candidates:
        return
    # Ross: trade the most obvious name; grade order A+ > A > B
    ranked = sorted(event.candidates, key=lambda c: {"A+": 0, "A": 1, "B": 2}[c.grade])
    pick = ranked[0]
    state.symbol = pick.symbol
    state.con_id = pick.con_id
    state.grade = pick.grade
    state.phase = "WATCH_PULLBACK"
    cmds.append(Subscribe(pick.symbol))
    cmds.append(Snapshot())


def _evaluate_entry(state: State, cmds: list) -> None:
    if state.phase != "WATCH_PULLBACK":
        return
    cfg = state.settings
    bars = state.bars.get(state.symbol, [])
    impulse = detect_impulse(bars, cfg.impulse_lookback, cfg.impulse_min_pct)
    if impulse is None:
        return
    pullback = find_pullback(bars, impulse, cfg.retrace_min, cfg.retrace_max)
    if pullback is None or not is_basing(bars, pullback, cfg.basing_low_tol):
        return
    latest = bars[-1]
    if not is_crossing_candle(latest, pullback.breakout_level):
        return
    # Buy at the breakout level (the crossing candle is already above it,
    # so this is a marketable limit that caps slippage).
    entry = pullback.breakout_level
    stop_price = pullback.low  # v1: exact pullback low, no extra buffer
    # Extension target one R above entry; Ross requires the setup to have
    # room for at least what is risked, so the R:R gate checks against it.
    target = entry + (entry - stop_price)
    rr = risk_reward(entry, stop_price, target)
    if rr < cfg.entry_rr:
        return
    qty = calc_shares(state.equity, state.settled_cash, entry, stop_price,
                      state.grade, cfg.risk_pct)
    if qty <= 0:
        return
    state.stop = stop_price
    state.target = target
    state.shares = qty
    state.phase = "ENTERING"
    cmds.append(Enter(state.symbol, qty, entry))
    cmds.append(Snapshot())


def _on_bars(state: State, event: Bars, cmds: list) -> None:
    if event.symbol != state.symbol:
        return
    state.bars[event.symbol] = event.bars
    if state.phase == "WATCH_PULLBACK":
        _evaluate_entry(state, cmds)
    elif state.phase == "IN_POSITION":
        _evaluate_exits(state, cmds)
    if _maybe_hard_risk(state, cmds):
        return


def _on_fill(state: State, event: Fill, cmds: list) -> None:
    if event.side == "B":
        state.position_qty += event.qty
        if state.entry is None:
            state.entry = event.price
        state.phase = "IN_POSITION"
        cmds.append(AttachStop(state.symbol, state.position_qty, state.stop))
    else:
        pnl = (event.price - state.entry) * event.qty
        state.realized_pnl += pnl
        state.position_qty -= event.qty
        if state.reduced and state.position_qty > 0 and state.stop != state.entry:
            cmds.append(ReplaceStop(state.symbol, state.position_qty, state.entry))
        if state.position_qty <= 0:
            state.phase = "DONE"
            state.done = True
            cmds.append(Journal({
                "date": state.date, "symbol": state.symbol, "grade": state.grade,
                "shares": state.shares, "entry": state.entry, "exit": event.price,
                "pnl": round(state.realized_pnl, 2), "reason": "closed",
            }))
            cmds.append(LockDay("position closed"))
    cmds.append(Snapshot())


def handle(state: State, event) -> tuple[State, list]:
    cmds: list = []
    # Once locked, only market-state events and the fills of the exits we
    # ourselves emitted may pass; everything else (new scans, etc.) is Noop.
    if state.done and not isinstance(
        event, (Clock, Account, Bars, Ticks, Halted, Fill)
    ):
        return state, [Noop()]
    if isinstance(event, Clock):
        _on_clock(state, event, cmds)
    elif isinstance(event, Account):
        _on_account(state, event, cmds)
    elif isinstance(event, ScanResults):
        _on_scan(state, event, cmds)
    elif isinstance(event, Bars):
        _on_bars(state, event, cmds)
    elif isinstance(event, Ticks):
        _on_ticks(state, event, cmds)
    elif isinstance(event, Fill):
        _on_fill(state, event, cmds)
    return state, cmds
