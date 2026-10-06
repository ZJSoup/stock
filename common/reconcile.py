"""Startup reconciliation: split account holdings into mine/others/unclaimed."""

from .ownership import CLIENT_IDS, classify_holdings


def reconcile(strategy_id: str, positions: list,
              executions: list | None = None) -> tuple[list, list, list]:
    buckets = classify_holdings(positions, executions)
    mine = buckets[strategy_id]
    others: list = []
    for sid, items in buckets.items():
        if sid != strategy_id and sid in CLIENT_IDS:
            others.extend(items)
    return mine, others, buckets["unclaimed"]
