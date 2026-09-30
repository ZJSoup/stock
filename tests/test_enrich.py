import asyncio
from unittest.mock import patch
from momo_bot.enrich import parse_float, enrich

def test_parse_plain():
    assert parse_float(8_200_000) == 8_200_000

def test_parse_suffixes():
    assert parse_float("8.2M") == 8_200_000
    assert parse_float("1.5B") == 1_500_000_000
    assert parse_float("bad") is None

def test_enrich_degrades_on_error():
    with patch("momo_bot.enrich.yf.Ticker", side_effect=RuntimeError("net down")):
        result = asyncio.run(enrich("ZTG"))
    assert result == (None, False)

def test_enrich_success():
    class FakeInfo:
        def __init__(self): self._d = {"floatShares": 8_200_000}
        def get(self, k, d=None): return self._d.get(k, d)
    class FakeTicker:
        info = FakeInfo()
        news = [{"title": "contract win"}]
    with patch("momo_bot.enrich.yf.Ticker", return_value=FakeTicker()):
        assert asyncio.run(enrich("ZTG")) == (8_200_000, True)
