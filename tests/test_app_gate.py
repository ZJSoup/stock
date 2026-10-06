import unittest.mock as mock

import pytest

from momo_bot.app import ensure_live_allowed


def _settings(mode):
    s = mock.MagicMock()
    s.mode = mode
    return s


def test_paper_mode_never_gated():
    with mock.patch("momo_bot.app.summarize") as summarize:
        ensure_live_allowed(_settings("paper"), data_dir=mock.MagicMock())
    summarize.assert_not_called()


def test_live_mode_raises_when_ineligible():
    stats = {
        "eligible": False, "trading_days": 3, "setups": 2, "net": -10.0,
        "win_rate": 0.0, "max_drawdown": 0.5, "violations": 1,
    }
    with mock.patch("momo_bot.app.summarize", return_value=stats):
        with pytest.raises(RuntimeError):
            ensure_live_allowed(_settings("live"), data_dir=mock.MagicMock())


def test_live_mode_passes_when_eligible():
    stats = {
        "eligible": True, "trading_days": 20, "setups": 10, "net": 100.0,
        "win_rate": 0.7, "max_drawdown": 0.1, "violations": 0,
    }
    with mock.patch("momo_bot.app.summarize", return_value=stats):
        ensure_live_allowed(_settings("live"), data_dir=mock.MagicMock())
