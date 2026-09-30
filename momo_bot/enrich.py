from __future__ import annotations

import asyncio

import yfinance as yf

SUFFIX = {"K": 1e3, "M": 1e6, "B": 1e9}


def parse_float(value) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    text = value.strip().replace(",", "")
    if not text:
        return None
    tail = text[-1].upper()
    try:
        if tail in SUFFIX:
            result = float(text[:-1]) * SUFFIX[tail]
            # Round to nearest integer to handle floating point precision
            return round(result)
        result = float(text)
        return result
    except ValueError:
        return None


async def enrich(symbol: str) -> tuple[float | None, bool]:
    """Float and news hint. Must never raise — callers rely on degradation."""
    def _work():
        t = yf.Ticker(symbol)
        shares = parse_float(t.info.get("floatShares"))
        has_news = bool(getattr(t, "news", []))
        return shares, has_news
    try:
        return await asyncio.to_thread(_work)
    except Exception:
        return None, False
