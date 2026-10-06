"""Risk-budget aggregation and halt flag.

Builds the /api/risk-budget snapshot from the read-only IB monitor and an
injected risk-budget config. The halt flag is the only piece of mutable
state; this module never places or cancels orders.

Defaults (used when no risk_budget config exists):

  total max exposure 60% of net liquidation; account daily loss limit 5%;
  per-strategy max notional / daily loss limit:
    spy-steady 30% / 2%, spy-turbo 10% / 3%, momo 20% / 2%.

Realized per-strategy daily PnL is not yet parsed from journals; strategy
daily_pnl currently reflects unrealized PnL only.
"""

from __future__ import annotations

from common.ownership import CLIENT_IDS, classify_holdings

DEFAULT_TOTAL_MAX = 0.60
DEFAULT_ACCOUNT_DAILY_LOSS = 0.05
# strategy id -> (max_notional_pct, daily_loss_limit_pct)
DEFAULT_STRATEGIES: dict[str, tuple[float, float]] = {
    "spy-steady": (0.30, 0.02),
    "spy-turbo": (0.10, 0.03),
    "momo": (0.20, 0.02),
}


class _Contract:
    def __init__(self, con_id):
        self.conId = con_id


class _Position:
    """Adapt a monitor position dict to classify_holdings' object protocol."""

    def __init__(self, raw: dict):
        self._raw = raw
        self.contract = _Contract(raw.get("con_id"))
        self.orderRef = None


class _Execution:
    def __init__(self, raw: dict):
        self.contract = _Contract(raw.get("con_id"))
        self.clientId = raw.get("client_id")


def _fnum(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


class RiskBudgetService:
    def __init__(self, ib_monitor, get_config, set_config):
        self._monitor = ib_monitor
        self._get_config = get_config
        self._set_config = set_config
        self._halt = False

    # -- halt ------------------------------------------------------------
    def set_halt(self, halted: bool) -> None:
        self._halt = bool(halted)

    def is_halted(self) -> bool:
        return self._halt

    # -- config ----------------------------------------------------------
    def _limits(self) -> tuple[float, float, dict[str, tuple[float, float]]]:
        try:
            raw = self._get_config() or {}
        except Exception:  # noqa: BLE001 - bad config never crashes the service
            raw = {}
        section = raw.get("risk_budget", raw) if isinstance(raw, dict) else {}
        if not isinstance(section, dict):
            section = {}

        total_max = _fnum(section.get("total_max")) or DEFAULT_TOTAL_MAX
        account_daily_loss = (
            _fnum(section.get("account_daily_loss")) or DEFAULT_ACCOUNT_DAILY_LOSS)

        strategies = {sid: defaults for sid, defaults in DEFAULT_STRATEGIES.items()}
        for entry in section.get("strategies", []) or []:
            sid = entry.get("id")
            if sid not in CLIENT_IDS:
                continue
            max_pct = _fnum(entry.get("max_notional_pct"))
            loss_pct = _fnum(entry.get("daily_loss_pct"))
            old_max, old_loss = strategies[sid]
            strategies[sid] = (max_pct or old_max, loss_pct or old_loss)
        return total_max, account_daily_loss, strategies

    # -- snapshot --------------------------------------------------------
    def snapshot(self) -> dict:
        mon = self._monitor.snapshot()
        netliq = _fnum(mon.get("net_liquidation"))

        positions = [_Position(p) for p in mon.get("positions", [])]
        executions = [_Execution(e) for e in mon.get("executions", [])]
        buckets = classify_holdings(positions, executions)

        total_max, account_daily_loss, limits = self._limits()

        raw_by_id = {p._raw.get("con_id"): p._raw for p in positions}
        notionals: dict[str, float] = {}
        pnl_money: dict[str, float] = {}
        for sid in CLIENT_IDS:
            notional = 0.0
            pnl = 0.0
            for pos in buckets[sid]:
                raw = raw_by_id.get(pos.contract.conId, {})
                qty = abs(_fnum(raw.get("position")))
                multiplier = int(_fnum(raw.get("multiplier")) or 1)
                price = raw.get("market_price")
                if price is None:
                    # Fallback per plan: avgCost * position count.
                    notional += qty * _fnum(raw.get("avg_cost"))
                else:
                    notional += qty * _fnum(price) * multiplier
                pnl += _fnum(raw.get("unrealized_pnl"))
            notionals[sid] = notional
            pnl_money[sid] = pnl

        total_notional = sum(notionals.values())
        total_used_pct = total_notional / netliq if netliq > 0 else (1.0 if total_notional else 0.0)

        account_daily_pnl = 0.0
        if netliq > 0:
            account_daily_pnl = (
                _fnum(mon.get("unrealized_pnl")) + _fnum(mon.get("realized_pnl"))
            ) / netliq
        if account_daily_pnl <= -account_daily_loss:
            # account-level daily loss breach auto-latches halt
            self._halt = True

        strategies_out: dict[str, dict] = {}
        for sid in CLIENT_IDS:
            max_pct, loss_limit = limits[sid]
            used_pct = notionals[sid] / netliq if netliq > 0 else (1.0 if notionals[sid] else 0.0)
            daily_pnl = pnl_money[sid] / netliq if netliq > 0 else 0.0
            can_open = (
                not self._halt
                and total_used_pct < total_max
                and used_pct < max_pct
                and daily_pnl > -loss_limit
            )
            strategies_out[sid] = {
                "used_pct": used_pct,
                "max_pct": max_pct,
                "daily_pnl": daily_pnl,
                "daily_loss_limit_pct": loss_limit,
                "can_open": can_open,
            }

        return {
            "account": {
                "net_liquidation": netliq,
                "daily_pnl": account_daily_pnl,
                "total_used_pct": total_used_pct,
                "total_max_pct": total_max,
                "halt": self._halt,
            },
            "strategies": strategies_out,
        }
