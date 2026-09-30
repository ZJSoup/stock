from __future__ import annotations

import dataclasses
import datetime as dt

from ib_insync import LimitOrder, StopOrder


@dataclasses.dataclass(frozen=True)
class Fill:
    qty: int
    price: float
    side: str


class Executor:
    """Thin order adapter. Marketable limits only; raw market orders banned."""

    def __init__(self, ib):
        self.ib = ib

    async def _await_fill(self, trade, timeout=10) -> Fill:
        deadline = dt.datetime.now() + dt.timedelta(seconds=timeout)
        while dt.datetime.now() < deadline:
            if trade.isDone():
                status = trade.orderStatus
                return Fill(int(trade.filled), float(status.avgTotalPrice or 0),
                            trade.order.action)
            await self.ib.waitOnUpdate()
        self.ib.cancelOrder(trade.order)
        raise TimeoutError("order did not fill in time")

    async def enter(self, contract, qty, ref_price) -> Fill:
        order = LimitOrder("BUY", qty, round(ref_price * 1.002, 4), tif="DAY")
        trade = self.ib.placeOrder(contract, order)
        return await self._await_fill(trade)

    async def stop(self, contract, qty, stop_price):
        order = StopOrder("SELL", qty, round(stop_price, 4), tif="DAY")
        return self.ib.placeOrder(contract, order)

    async def replace_stop(self, contract, qty, stop_price):
        # Cancel only THIS symbol's working stop — never reqGlobalCancel,
        # which would also kill orders on other instruments.
        for trade in await self.ib.reqAllOpenOrdersAsync():
            order = trade.order
            if (order.action == "SELL" and order.orderType == "STP"
                    and trade.contract.symbol == contract.symbol):
                self.ib.cancelOrder(order)
        order = StopOrder("SELL", qty, round(stop_price, 4), tif="DAY")
        return self.ib.placeOrder(contract, order)

    async def exit_position(self, contract, qty, ref_price) -> Fill:
        order = LimitOrder("SELL", qty, round(ref_price * 0.998, 4), tif="DAY")
        trade = self.ib.placeOrder(contract, order)
        return await self._await_fill(trade)

    async def cancel_all(self, contract) -> None:
        self.ib.reqGlobalCancel()

    async def execute(self, contract, command):
        from .engine import (
            AttachStop, CancelAll as CancelAllCmd, Enter as EnterCmd,
            Exit as ExitCmd, ReplaceStop,
        )
        if isinstance(command, EnterCmd):
            fill = await self.enter(contract, command.qty, command.ref_price)
            return fill
        if isinstance(command, AttachStop):
            return await self.stop(contract, command.qty, command.stop_price)
        if isinstance(command, ReplaceStop):
            return await self.replace_stop(contract, command.qty, command.stop_price)
        if isinstance(command, ExitCmd):
            fill = await self.exit_position(contract, command.qty, command.ref_price)
            return fill
        if isinstance(command, CancelAllCmd):
            return await self.cancel_all(contract)
        return None
