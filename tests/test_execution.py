import asyncio
import unittest.mock as mock
from ib_insync import LimitOrder, StopOrder
from momo_bot.execution import Executor
from momo_bot.engine import Enter, AttachStop, ReplaceStop, Exit, CancelAll

def ib_mock():
    ib = mock.MagicMock()
    ib.placeOrder = mock.MagicMock()
    ib.whatIfOrder = mock.AsyncMock()
    ib.reqAllOpenOrders = mock.AsyncMock(return_value=[])
    ib.reqAllOpenOrdersAsync = mock.AsyncMock(return_value=[])
    return ib

contract = mock.MagicMock()


def run(coro):
    return asyncio.run(coro)

def test_enter_uses_marketable_limit():
    ib = ib_mock()
    ib.placeOrder.side_effect = lambda c, o: setattr(o, "permId", 1)
    # simulate immediate fill via the waiter ib.waitOnUpdate
    ib.waitOnUpdate = mock.AsyncMock()
    ex = Executor(ib)
    fill_obj = mock.MagicMock()
    fill_obj.qty = 100
    fill_obj.price = 6.01
    fill_obj.side = "BUY"
    with mock.patch.object(ex, "_await_fill", new=mock.AsyncMock(return_value=fill_obj)):
        f = run(ex.enter(contract, 100, 6.0))
    assert f.qty == 100
    order = ib.placeOrder.call_args.args[1]
    assert isinstance(order, LimitOrder)
    assert order.action == "BUY"
    assert order.lmtPrice == round(6.0 * 1.002, 4)

def test_stop_order_is_stop_not_market():
    ib = ib_mock()
    ex = Executor(ib)
    run(ex.stop(contract, 100, 5.9))
    order = ib.placeOrder.call_args.args[1]
    assert isinstance(order, StopOrder) and order.auxPrice == 5.9

def test_exit_sells_marketable_limit():
    ib = ib_mock()
    ex = Executor(ib)
    fill_obj = mock.MagicMock()
    fill_obj.qty = 100
    fill_obj.price = 5.98
    fill_obj.side = "SELL"
    with mock.patch.object(ex, "_await_fill", new=mock.AsyncMock(return_value=fill_obj)):
        f = run(ex.exit_position(contract, 100, 6.0))
    order = ib.placeOrder.call_args.args[1]
    assert order.action == "SELL" and order.lmtPrice == round(6.0 * 0.998, 4)
    assert f.price < 6.0

def test_cancel_all():
    ib = ib_mock()
    ib.reqGlobalCancel = mock.MagicMock()
    ex = Executor(ib)
    run(ex.cancel_all(contract))
    ib.reqGlobalCancel.assert_called_once()

def test_replace_stop_cancels_old_stop_only():
    ib = ib_mock()
    old = mock.MagicMock()
    old.contract.symbol = "ZTG"
    old.order.action, old.order.orderType = "SELL", "STP"
    other = mock.MagicMock()
    other.contract.symbol = "AAA"
    other.order.action, other.order.orderType = "SELL", "STP"
    ib.reqAllOpenOrdersAsync = mock.AsyncMock(return_value=[old, other])
    ib.cancelOrder = mock.MagicMock()
    ex = Executor(ib)
    ztg = mock.MagicMock(symbol="ZTG")
    run(ex.replace_stop(ztg, 86, 6.0))
    # only ZTG's old stop is cancelled, never the other symbol's order
    ib.cancelOrder.assert_called_once_with(old.order)
    order = ib.placeOrder.call_args.args[1]
    assert isinstance(order, StopOrder) and order.auxPrice == 6.0
