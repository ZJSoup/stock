import asyncio
import unittest.mock as mock

from momo_bot.scanner import run_scan, to_candidate


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


def test_to_candidate_grades():
    c = to_candidate("ZTG", 123, last=5.78, prev_close=4.0, volume=5_000_000,
                     avg_volume=100_000, float_shares=8_000_000, has_news=False)
    assert c is not None and c.grade == "B"
    assert round(c.rvol, 1) == 50.0

def test_to_candidate_rejects():
    assert to_candidate("X", 1, 1.0, 1.0, 0, 1, 5_000_000, True) is None