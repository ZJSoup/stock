from __future__ import annotations

import asyncio

from ib_insync import ScannerSubscription, Stock

from .pillars import Candidate, grade_pillars, relative_volume


def build_subscription() -> ScannerSubscription:
    sub = ScannerSubscription(
        instrument="STK",
        locationCode="STK.US.MAJOR",
        scanCode="TOP_PERC_GAINERS",
    )
    sub.abovePrice = 2.0
    sub.belowPrice = 20.0
    return sub


async def run_scan(ib):
    """Ranked contracts from IB; market data for each is fetched separately
    because ScanDataData does not carry change% or volume."""
    # ib_insync 0.9.86 coroutine is reqScannerDataAsync(subscription,
    # scannerSubscriptionOptions, scannerSubscriptionFilterOptions).
    contracts = await ib.reqScannerDataAsync(build_subscription(), [], [])
    return [item.contract for item in contracts[:20]]


def to_candidate(symbol, con_id, last, prev_close, volume, avg_volume,
                 float_shares, has_news) -> Candidate | None:
    if prev_close <= 0:
        return None
    change = (last - prev_close) / prev_close
    rvol = relative_volume(volume, avg_volume)
    grade = grade_pillars(change, rvol, last, float_shares, has_news)
    if grade is None:
        return None
    return Candidate(
        symbol=symbol, con_id=con_id, last=last, prev_close=prev_close,
        change_pct=change, volume=volume, avg_volume=avg_volume, rvol=rvol,
        price=last, float_shares=float_shares, has_news=has_news, grade=grade,
    )


async def quote_candidate(ib, contract, enricher):
    """Snapshot one scanner hit: live quote + 50-day history + float/news.
    reqMktData is synchronous in ib_insync (returns the Ticker immediately);
    only asyncio.sleep and the *Async requests are awaited here."""
    ticker = ib.reqMktData(contract, "", False, False)
    await asyncio.sleep(2)
    # 90 calendar days covers >= 50 completed trading sessions.
    daily = await ib.reqHistoricalDataAsync(
        contract, "", "90 D", "1 day", "TRADES", True, formatDate=1
    )
    ib.cancelMktData(contract)
    if not daily:
        return None
    # Last bar is today's incomplete session; completed days precede it.
    completed = daily[:-1]
    window = completed[-50:]
    last = ticker.last or ticker.close
    # Without completed sessions there is no RVOL base and no prev close;
    # without a live last there is no change% to grade. Skip the hit.
    if not window or not last:
        return None
    prev_close = completed[-1].close
    avg_volume = sum(b.volume for b in window) / len(window)
    float_shares, has_news = await enricher(contract.symbol)
    return to_candidate(
        contract.symbol, contract.conId, last,
        prev_close, ticker.volume or 0, avg_volume, float_shares, has_news
    )