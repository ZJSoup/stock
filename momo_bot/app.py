from __future__ import annotations

import asyncio
import dataclasses
import datetime as dt
import pathlib
from zoneinfo import ZoneInfo

from ib_insync import IB, Stock

from . import engine as eng
from .enrich import enrich
from .execution import Executor
from .scanner import quote_candidate, run_scan
from .signals import Bar, Tick
from .state import Snapshot, load_snapshot, save_snapshot
from .journal import record_trade, record_violation, summarize

ET = ZoneInfo("America/New_York")


def ensure_live_allowed(settings, data_dir) -> None:
    """Paper gate for live mode: abort before new scanning unless the paper
    journal clears the eligibility criteria. Paper mode is always allowed."""
    if settings.mode != "live":
        return
    stats = summarize(data_dir)
    if not stats["eligible"]:
        raise RuntimeError(
            "live mode blocked: paper-gate eligibility not met "
            f"(trading_days={stats['trading_days']}/20, "
            f"setups={stats['setups']}/10, net={stats['net']}, "
            f"win_rate={stats['win_rate']:.2f}/0.60, "
            f"max_drawdown={stats['max_drawdown']:.2f}/0.15, "
            f"violations={stats['violations']}/0)"
        )


def classify_tick_side(price: float, bid: float, ask: float) -> str:
    if ask and price >= ask:
        return "B"
    if bid and price <= bid:
        return "S"
    return "S"  # conservative default inside the spread


def to_bar(ib_bar) -> Bar:
    return Bar(ib_bar.date, ib_bar.open, ib_bar.high, ib_bar.low,
               ib_bar.close, ib_bar.volume)


def _snapshot_from_state(state: eng.State) -> Snapshot:
    return Snapshot(
        date=state.date, phase=state.phase, symbol=state.symbol, con_id=state.con_id,
        grade=state.grade,
        shares=state.shares, position_qty=state.position_qty, entry=state.entry,
        stop=state.stop, target=state.target, realized_pnl=state.realized_pnl,
        peak_pnl=state.peak_pnl, reduced=state.reduced, done=state.done,
        start_equity=state.start_equity,
    )


class App:
    SCAN_INTERVAL_S = 60

    def __init__(self, settings, data_dir: pathlib.Path):
        self.settings = settings
        self.data_dir = data_dir
        self.ib = IB()
        self.executor = Executor(self.ib)
        self.state = None
        self._contracts: dict[str, object] = {}
        self._bars: dict[str, list] = {}
        self._quotes: dict[str, object] = {}       # NBBO tickers (reqMktData)
        self._tick_window: dict[str, list] = {}    # 30 s rolling tape window
        self._last_halt: dict[str, int] = {}       # last halted flag per symbol
        self._fatal = False                        # unrecoverable adapter error

    async def run(self):
        await self.ib.connectAsync(self.settings.host, self.settings.port,
                                   clientId=self.settings.client_id)
        today = dt.datetime.now(ET).date().isoformat()
        snap = load_snapshot(self.data_dir, today)
        # Explicit request: the auto-started position download is often still
        # empty the instant connectAsync returns.
        positions = await self.ib.reqPositionsAsync()
        # Even with no snapshot we must NOT ignore real positions: restore
        # raises in that case and run() aborts before any trading.
        wanted = snap.symbol if snap else None
        broker_pos = next(
            (p for p in positions
             if wanted is None or p.contract.symbol == wanted),
            None,
        )
        self.state = eng.restore_state(
            today, self.settings, snap,
            has_broker_position=broker_pos is not None,
            broker_qty=int(abs(broker_pos.position)) if broker_pos else 0,
            last_price=broker_pos.avgCost if broker_pos else None,
        )
        # Establish real equity (and thus a valid daily-loss threshold)
        # BEFORE subscribing to market events, so a restored state can never
        # act on a start_equity of 0.
        await self._update_account()
        if self.state.phase == "IN_POSITION":
            # A restored live position must stay manageable even if the paper
            # gate would not pass: subscribe and re-attach its stop first.
            await self._subscribe(self.state.symbol)
            await self._ensure_stop()
        else:
            # No position to service — abort before any new scanning unless
            # the paper journal proves live eligibility.
            ensure_live_allowed(self.settings, self.data_dir)
        await self._loop()

    async def _loop(self):
        while not self.state.done and not self._fatal:
            now = dt.datetime.now(ET)
            self._dispatch(eng.Clock(now))
            await self._update_account()
            if self.state.phase == "SCANNING":
                await self._scan_once()
            await self.ib.sleep(1)
        # give stop/exits a moment, then stop; launchd hard-kills at 10:10
        await self.ib.sleep(5)

    def _dispatch(self, event):
        self.state, commands = eng.handle(self.state, event)
        self._execute_commands(commands)

    async def _update_account(self):
        # Explicit download: the auto-started accountSummary() cache is often
        # empty the first moments after connect (resolves to None/0 values).
        await self.ib.reqAccountSummaryAsync()
        summary = {i.tag: float(i.value) for i in self.ib.accountSummary()}
        if "NetLiquidation" not in summary:
            # No values yet; retry next tick. Never dispatch equity 0 — the
            # reducer treats that as "start_equity still unknown" anyway, but
            # skipping keeps the IDLE->SCANNING transition on real data.
            return
        # CashBalance is the settled-cash proxy: the strategy makes one
        # round-trip per day and only acts after overnight (T+1) settlement.
        # Whether the gateway actually exposes a SettledCash tag must be
        # verified against the real gateway during the paper run before
        # relying on it instead.
        self._dispatch(eng.Account(
            summary["NetLiquidation"],
            summary.get("CashBalance", summary["NetLiquidation"]),
        ))

    async def _scan_once(self):
        try:
            contracts = await run_scan(self.ib)
            candidates = []
            for contract in contracts[:8]:
                c = await quote_candidate(self.ib, contract, enrich)
                if c:
                    candidates.append(c)
            self._dispatch(eng.ScanResults(candidates))
        except Exception as exc:
            record_violation(self.data_dir, f"scan error: {exc}", self.state.date)

    async def _subscribe(self, symbol: str):
        contract = Stock(symbol, "SMART", "USD")
        await self.ib.qualifyContractsAsync(contract)
        self._contracts[symbol] = contract
        # Trade-price bars, matching the strategy (not MIDPOINT).
        self._bars[symbol] = [
            to_bar(b) for b in await self.ib.reqHistoricalDataAsync(
                contract, "", "1 D", "1 min", "TRADES", True)
        ]
        bars_ib = self.ib.reqRealTimeBars(contract, 60, "TRADES", True)
        bars_ib.updateEvent += self._on_bars
        # AllLast carries prints but no NBBO; keep a separate top-of-book
        # subscription so tick sides can be classified against real bid/ask.
        quote = self.ib.reqMktData(contract, "", False, False)
        self._quotes[symbol] = quote
        quote.updateEvent += self._on_quote
        self._tick_window[symbol] = []
        ticker = self.ib.reqTickByTickData(contract, "AllLast", numberOfTicks=0)
        ticker.updateEvent += self._on_tick

    def _on_quote(self, ticker):
        # IB halt status: 1 = halted. Dispatch only on change so a bar stream
        # of identical flags does not re-trigger cancels.
        symbol = ticker.contract.symbol
        flag = int(ticker.halted or 0)
        if self._last_halt.get(symbol) != flag:
            self._last_halt[symbol] = flag
            self._dispatch(eng.Halted(symbol, flag == 1))

    async def _ensure_stop(self):
        """After a restored position, attach a stop unless the broker already
        shows a working SELL stop for this symbol."""
        symbol = self.state.symbol
        orders = await self.ib.reqAllOpenOrdersAsync()
        has_stop = any(
            o.action == "SELL" and o.orderType == "STP"
            and t.contract.symbol == symbol
            for t in orders for o in [t.order]
        )
        if not has_stop and self.state.stop is not None:
            await self.executor.stop(
                self._contracts[symbol], self.state.position_qty, self.state.stop
            )

    def _on_bars(self, bars, has_new_bar):
        symbol = bars.contract.symbol
        bar = to_bar(bars[-1])
        series = self._bars[symbol]
        # RealTimeBars fires roughly every 5 s for the SAME forming bar;
        # replace it in place rather than appending duplicates (duplicates
        # would corrupt the pattern/divergence signal window). A new minute
        # starts a fresh bar.
        if series and series[-1].date == bar.date:
            series[-1] = bar
        else:
            series.append(bar)
        self._bars[symbol] = series[-400:]
        self._dispatch(eng.Bars(symbol, self._bars[symbol]))

    def _on_tick(self, ticker, event):
        # AllLast only delivers prints, so no tickType filter is needed
        # (comparing the tickType enum against the string "Last" would drop
        # every tick).
        symbol = ticker.contract.symbol
        quote = self._quotes.get(symbol)
        bid = quote.bid if quote is not None else 0.0
        ask = quote.ask if quote is not None else 0.0
        side = classify_tick_side(event.price, bid, ask)
        window = self._tick_window[symbol]
        window.append(Tick(event.time, event.price, event.size, side))
        cutoff = event.time - dt.timedelta(seconds=30)
        self._tick_window[symbol] = [t for t in window if t.time >= cutoff]
        # The reducer's sell-pressure ratio is computed over this whole list,
        # so it reflects a true rolling 30 s tape, not a single print.
        self._dispatch(eng.Ticks(symbol, self._tick_window[symbol]))

    async def _order_and_fill(self, cmd):
        contract = self._contracts.get(cmd.symbol)
        if contract is None:
            contract = Stock(cmd.symbol, "SMART", "USD")
            await self.ib.qualifyContractsAsync(contract)
            self._contracts[cmd.symbol] = contract
        if isinstance(cmd, eng.Enter):
            try:
                result = await self.executor.execute(contract, cmd)
            except Exception as exc:
                # The executor cancels an unfilled entry on timeout, so no
                # position exists. Rather than continue in a half-known state,
                # record a violation and stop for the day (runbook procedure).
                record_violation(
                    self.data_dir, f"entry failed: {exc}", self.state.date
                )
                self._fatal = True
                return
            if result is not None:
                self._dispatch(eng.Fill(cmd.symbol, result.qty, result.price, "B"))
        elif isinstance(cmd, eng.Exit):
            result = await self._exit_with_retries(contract, cmd)
            if result is not None:
                self._dispatch(eng.Fill(cmd.symbol, result.qty, result.price, "S"))
        else:
            # AttachStop / ReplaceStop / CancelAll return a Trade or None,
            # no reducer Fill follows.
            await self.executor.execute(contract, cmd)

    async def _exit_with_retries(self, contract, cmd):
        """Sell with up to three attempts, walking the limit 1% lower each
        time. On total failure the broker-side stop remains as the backstop."""
        ref = cmd.ref_price
        last: Exception | None = None
        for attempt in range(3):
            try:
                return await self.executor.exit_position(
                    contract, cmd.qty, ref * (1 - 0.01 * attempt)
                )
            except Exception as exc:
                last = exc
        record_violation(
            self.data_dir, f"exit failed: {last}", self.state.date
        )
        return None

    def _execute_commands(self, commands):
        # Event callbacks run inside the ib event loop's thread: schedule
        # follow-up coroutines on that loop instead of nesting ib.run().
        loop = self.ib.loop
        for cmd in commands:
            if isinstance(cmd, eng.Subscribe):
                asyncio.ensure_future(self._subscribe(cmd.symbol), loop=loop)
            elif isinstance(cmd, (eng.Enter, eng.AttachStop, eng.ReplaceStop,
                                 eng.Exit, eng.CancelAll)):
                asyncio.ensure_future(self._order_and_fill(cmd), loop=loop)
            elif isinstance(cmd, eng.Journal):
                record_trade(self.data_dir, cmd.record)
            elif isinstance(cmd, eng.Violation):
                record_violation(self.data_dir, cmd.reason, self.state.date)
            elif isinstance(cmd, eng.Snapshot):
                save_snapshot(self.data_dir, _snapshot_from_state(self.state))

    async def stop(self):
        if self.ib.isConnected():
            await self.ib.disconnectAsync()
