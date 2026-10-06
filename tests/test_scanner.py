import asyncio
import unittest.mock as mock

from momo_bot.scanner import quote_candidate, run_scan, to_candidate


def test_run_scan_uses_reqScannerDataAsync():
    # ib_insync 0.9.86 coroutine is reqScannerDataAsync — reqScannerAsync
    # never existed (would raise AttributeError and kill every scan).
    ib = mock.MagicMock()
    item = mock.MagicMock()
    item.contract = "C1"
    ib.reqScannerDataAsync = mock.AsyncMock(return_value=[item])
    contracts = asyncio.run(run_scan(ib))
    ib.reqScannerDataAsync.assert_awaited_once()
    assert contracts == ["C1"]


def _dbar(o, h, l, c, v=1000):
    return mock.MagicMock(open=o, high=h, low=l, close=c, volume=v)


def test_quote_candidate_rejects_dirty_daily():
    # completed sessions contain a $2 -> $60 -> $5 pump-and-dump; even with a
    # live last the hit must be vetoed and never reach the enricher.
    completed = [
        _dbar(2, 2.1, 1.95, 2.0),
        _dbar(2, 10, 2, 9.0, 2000),
        _dbar(9, 30, 8.5, 28, 3000),
        _dbar(28, 60, 27, 55, 4000),
        _dbar(55, 55, 30, 32, 5000),
        _dbar(32, 33, 12, 14, 4000),
        _dbar(14, 15, 4.5, 5.0, 3000),
    ]
    daily = completed + [_dbar(5, 5.2, 4.9, 5.0)]  # last = today's incomplete bar

    ib = mock.MagicMock()
    ib.reqMktData.return_value = mock.MagicMock(last=5.0, volume=3000)
    ib.reqHistoricalDataAsync = mock.AsyncMock(return_value=daily)
    enricher = mock.AsyncMock(return_value=(8_000_000, False))

    result = asyncio.run(quote_candidate(ib, mock.MagicMock(), enricher))
    assert result is None
    enricher.assert_not_awaited()


def test_to_candidate_grades():
    c = to_candidate("ZTG", 123, last=5.78, prev_close=4.0, volume=5_000_000,
                     avg_volume=100_000, float_shares=8_000_000, has_news=False)
    assert c is not None and c.grade == "B"
    assert round(c.rvol, 1) == 50.0

def test_to_candidate_rejects():
    assert to_candidate("X", 1, 1.0, 1.0, 0, 1, 5_000_000, True) is None