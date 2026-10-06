import unittest.mock as mock

import pytest

from spy_multi_strategy import (
    SPYMultiStrategyTrader, build_parser,
)


def _trader(mode='steady'):
    return SPYMultiStrategyTrader(mode=mode)


# -- fixed clientIds derived from mode -------------------------------------

def test_mode_selects_fixed_client_id():
    assert _trader('steady').client_id == 2
    assert _trader('turbo').client_id == 3
    assert _trader('steady').strategy_id == 'spy-steady'
    assert _trader('turbo').strategy_id == 'spy-turbo'


def test_client_id_argument_removed():
    with pytest.raises(SystemExit):
        build_parser().parse_args(['--client-id', '5'])


def test_explicit_mismatched_client_id_refused():
    with pytest.raises(ValueError):
        SPYMultiStrategyTrader(mode='steady', client_id=5)


# -- close scoping ----------------------------------------------------------

class _Contract:
    def __init__(self, conid, right='C'):
        self.conId = conid
        self.symbol = 'SPY'
        self.secType = 'OPT'
        self.lastTradeDateOrContractMonth = '20261006'
        self.strike = 781.0
        self.right = right
        self.multiplier = '100'
        self.currency = 'USD'
        self.localSymbol = f'SPY {conid}'
        self.tradingClass = 'SPY'


class _Pos:
    def __init__(self, conid, ref, qty=1):
        self.contract = _Contract(conid)
        self.orderRef = ref
        self.position = qty
        self.avgCost = 1.0


def test_close_only_own_classified_positions():
    trader = _trader('steady')
    own = _Pos(111, 'tag:spy-steady')
    momos = _Pos(222, 'tag:momo')
    trader.ib = mock.MagicMock()
    trader.ib.positions.return_value = [own, momos]
    trader.ib.fills.return_value = []
    trader.ib.qualifyContracts.return_value = [mock.MagicMock()]

    trader.close_all_positions()

    assert trader.ib.placeOrder.call_count == 1
    order = trader.ib.placeOrder.call_args.args[1]
    assert order.orderRef == 'tag:spy-steady'


def test_close_uses_clientid_fallback_for_untagged_own():
    trader = _trader('steady')
    legacy = _Pos(927852425, None, qty=256)
    trader.ib = mock.MagicMock()
    trader.ib.positions.return_value = [legacy]

    class _Fill:
        def __init__(self):
            self.contract = _Contract(927852425)
            self.execution = mock.MagicMock(clientId=2)

    trader.ib.fills.return_value = [_Fill()]
    trader.ib.qualifyContracts.return_value = [mock.MagicMock()]

    trader.close_all_positions()

    assert trader.ib.placeOrder.call_count == 1
    assert trader.ib.placeOrder.call_args.args[1].orderRef == 'tag:spy-steady'


# -- entry gate -------------------------------------------------------------

def test_entry_blocked_when_risk_client_denies():
    trader = _trader('steady')
    trader.ib = mock.MagicMock()
    trader.risk = mock.MagicMock()
    trader.risk.can_open.return_value = (False, 'account halt is active')

    result = trader.place_long_option(
        '20261006', 780.0, {'target_delta': 50})

    assert result is None
    trader.ib.placeOrder.assert_not_called()


def test_readonly_monitor_blocks_entry():
    trader = _trader('steady')
    trader.readonly_monitor = True
    trader.ib = mock.MagicMock()
    trader.risk = mock.MagicMock()
    trader.risk.can_open.return_value = (True, '')

    assert trader.place_long_option('20261006', 780.0,
                                    {'target_delta': 50}) is None
    trader.ib.placeOrder.assert_not_called()
