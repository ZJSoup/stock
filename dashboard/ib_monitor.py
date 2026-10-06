"""Read-only Interactive Brokers monitor running on a background thread.

The thread owns the single ``ib_insync.IB`` instance and its asyncio event
loop; API handlers only ever read :meth:`IBMonitor.snapshot`. The monitor
connects with ``readonly=True`` and this module contains no order, trade,
cancel or flatten calls -- monitoring only.
"""

from __future__ import annotations

import asyncio
import copy
import threading
from datetime import datetime

from ib_insync import IB

POLL_INTERVAL = 5.0      # snapshot refresh while connected
RECONNECT_BACKOFF = 15.0  # wait after a failed connect / dropped connection
CONNECT_TIMEOUT = 5.0

_ACCOUNT_TAGS = ("NetLiquidation", "AvailableFunds", "UnrealizedPnL", "RealizedPnL")


def _now_iso() -> str | None:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _initial_snapshot() -> dict:
    return {
        "connected": False,
        "account": None,
        "net_liquidation": None,
        "available_funds": None,
        "unrealized_pnl": None,
        "realized_pnl": None,
        "positions": [],
        "updated_at": None,
        "error": None,
    }


class IBMonitor:
    def __init__(self, host: str = "127.0.0.1", port: int = 4002,
                 client_id: int = 50):
        self.host = host
        self.port = port
        self.client_id = client_id

        self._ib = IB()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._snapshot = _initial_snapshot()

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="ib-monitor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Signal the thread, disconnect, join (timeout 5s)."""
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=5)
            self._thread = None

    # ------------------------------------------------------------------
    # snapshot
    # ------------------------------------------------------------------
    def snapshot(self) -> dict:
        """Return a copy of the last good snapshot; never raises."""
        with self._lock:
            return copy.deepcopy(self._snapshot)

    def _store(self, snapshot: dict) -> None:
        with self._lock:
            self._snapshot = snapshot

    # ------------------------------------------------------------------
    # worker thread (owns the IB instance and its event loop)
    # ------------------------------------------------------------------
    def _run(self) -> None:
        # Python 3.13 no longer auto-creates a loop in non-main threads
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        while not self._stop.is_set():
            if not self._ib.isConnected():
                try:
                    self._ib.connect(
                        self.host, self.port,
                        clientId=self.client_id,
                        timeout=CONNECT_TIMEOUT,
                        readonly=True,
                    )
                    self._ib.reqMarketDataType(4)  # delayed-frozen
                except Exception as exc:  # noqa: BLE001 - report, keep retrying
                    self._mark_disconnected(exc)
                    if self._stop.wait(RECONNECT_BACKOFF):
                        break
                    continue

            try:
                self._refresh()
            except Exception as exc:  # noqa: BLE001 - drop into reconnect
                self._mark_disconnected(exc)
                if self._stop.wait(RECONNECT_BACKOFF):
                    break
                continue

            if self._stop.wait(POLL_INTERVAL):
                break

        # disconnect inside the thread that owns the event loop
        try:
            if self._ib.isConnected():
                self._ib.disconnect()
        except Exception:  # noqa: BLE001 - best effort on shutdown
            pass

    def _mark_disconnected(self, exc: Exception) -> None:
        snap = self.snapshot()
        snap["connected"] = False
        snap["error"] = f"{type(exc).__name__}: {exc}"
        self._store(snap)

    # ------------------------------------------------------------------
    # snapshot building (called only from the worker thread)
    # ------------------------------------------------------------------
    def _refresh(self) -> None:
        account_values = self._ib.accountSummary()
        tags: dict[str, float] = {}
        account: str | None = None
        for av in account_values:
            if account is None:
                account = av.account
            if av.tag in _ACCOUNT_TAGS and av.tag not in tags:
                try:
                    tags[av.tag] = float(av.value)
                except (TypeError, ValueError):
                    pass

        managed = self._ib.managedAccounts()
        if not account and managed:
            account = managed[0]

        positions = [self._position_item(item)
                     for item in self._ib.portfolioItems()]

        self._store({
            "connected": True,
            "account": account,
            "net_liquidation": tags.get("NetLiquidation"),
            "available_funds": tags.get("AvailableFunds"),
            "unrealized_pnl": tags.get("UnrealizedPnL"),
            "realized_pnl": tags.get("RealizedPnL"),
            "positions": positions,
            "updated_at": _now_iso(),
            "error": None,
        })

    @staticmethod
    def _position_item(item) -> dict:
        contract = item.contract

        def _num(value):
            try:
                return float(value)
            except (TypeError, ValueError):
                return None

        expiry = contract.lastTradeDateOrContractMonth or None
        strike = _num(contract.strike)
        right = contract.right or None
        return {
            "symbol": contract.symbol,
            "sec_type": contract.secType,
            "expiry": expiry,
            "strike": strike,
            "right": right,
            "position": float(item.position),
            "avg_cost": _num(item.averageCost) or 0.0,
            "market_value": _num(item.marketValue),
            "unrealized_pnl": _num(item.unrealizedPNL),
        }
