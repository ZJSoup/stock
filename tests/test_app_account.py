import asyncio
import unittest.mock as mock

from momo_bot.app import App
from momo_bot import engine as eng


def _av(tag, value):
    return mock.MagicMock(tag=tag, value=value)


def _app():
    app = App(mock.MagicMock(), mock.MagicMock())
    app.ib = mock.MagicMock()
    app.ib.reqAccountSummaryAsync = mock.AsyncMock()
    return app


def test_non_numeric_tags_are_skipped():
    # Real gateways include string-valued tags such as accountType=INDIVIDUAL;
    # these must not crash the float conversion.
    app = _app()
    app.ib.accountSummary.return_value = [
        _av("accountType", "INDIVIDUAL"),
        _av("currency", "BASE"),
        _av("NetLiquidation", "2000"),
        _av("CashBalance", "2000"),
    ]
    with mock.patch.object(app, "_dispatch") as dispatch:
        asyncio.run(app._update_account())
    dispatch.assert_called_once()
    event = dispatch.call_args.args[0]
    assert isinstance(event, eng.Account)
    assert event.equity == 2000.0
    assert event.settled_cash == 2000.0


def test_execute_commands_schedules_without_ib_loop():
    # ib_insync 0.9.86 removed IB.loop; scheduling must use the running loop.
    app = _app()
    seen = {}

    async def fake_subscribe(symbol):
        seen["symbol"] = symbol

    app._subscribe = fake_subscribe

    async def drive():
        app._execute_commands([eng.Subscribe("ZTG")])
        await asyncio.sleep(0)

    asyncio.run(drive())
    assert seen.get("symbol") == "ZTG"
