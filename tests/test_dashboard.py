import time
import sys
import pytest
from dashboard.process_manager import BotManager, BOTS


def make_manager(tmp_path):
    return BotManager(state_file=tmp_path / "pids.json")


def test_registry_has_expected_bots():
    assert "spy-steady" in BOTS
    assert "spy-turbo" in BOTS
    assert "momo" in BOTS
    # registry commands must reference real entry points
    assert "spy_multi_strategy.py" in " ".join(BOTS["spy-steady"]["cmd"])
    assert "momo_bot" in " ".join(BOTS["momo"]["cmd"])


def test_status_unknown_bot_not_running(tmp_path):
    m = make_manager(tmp_path)
    s = m.status()
    assert s["spy-steady"]["running"] is False
    assert s["spy-steady"]["pid"] is None


def test_start_stop_echo_bot(tmp_path):
    m = make_manager(tmp_path)
    m.register_test_bot("echo", [sys.executable, "-c", "import time; time.sleep(60)"], cwd=str(tmp_path))
    info = m.start("echo")
    assert info["running"] is True
    pid = info["pid"]
    assert pid and pid > 0
    # second start must refuse
    with pytest.raises(ValueError):
        m.start("echo")
    out = m.stop("echo")
    assert out["running"] is False
    time.sleep(0.2)
    assert m.status()["echo"]["running"] is False


def test_stop_external_process_refused(tmp_path):
    m = make_manager(tmp_path)
    m.register_test_bot("ext", [sys.executable, "-c", "import time; time.sleep(60)"], cwd=str(tmp_path))
    m.mark_external("ext", pid=999999)  # simulated external instance
    with pytest.raises(ValueError):
        m.stop("ext")


def test_start_unknown_bot_raises(tmp_path):
    m = make_manager(tmp_path)
    with pytest.raises(ValueError):
        m.start("nope")


def test_ib_snapshot_shape_when_disconnected():
    from dashboard.ib_monitor import IBMonitor
    mon = IBMonitor(port=49999)  # nothing listening
    snap = mon.snapshot()
    assert snap["connected"] is False
    assert snap["positions"] == []
    assert "error" in snap
    # must never raise even with no IB running


def test_tail_log_unknown_name():
    from dashboard.logs import tail_log
    import pytest
    with pytest.raises(ValueError):
        tail_log("bogus")


def test_tail_log_missing_file():
    from dashboard.logs import tail_log
    out = tail_log("spy-turbo", lines=10)
    assert out["lines"] == [] or isinstance(out["lines"], list)


def test_config_roundtrip(tmp_path, monkeypatch):
    from dashboard import config_editor
    monkeypatch.setattr(config_editor, "CONFIG_PATH", tmp_path / "config.toml")
    data = {
        "host": "127.0.0.1", "port": 4002, "client_id": 100, "mode": "paper",
        "risk": {"risk_pct": 0.10, "daily_loss_pct": 0.15, "giveback_pct": 0.50},
        "window": {"start": "07:00", "end": "10:00"},
        "signals": {"impulse_lookback": 5, "impulse_min_pct": 0.15, "retrace_min": 0.25,
                    "retrace_max": 0.70, "basing_low_tol": 0.003, "sell_pressure_pct": 0.65,
                    "divergence_bars": 3, "divergence_decline": 0.30, "entry_rr": 1.0},
    }
    written = config_editor.write_momo_config(data)
    assert written["exists"] is True
    back = config_editor.read_momo_config()
    assert back["port"] == 4002
    assert back["risk"]["risk_pct"] == pytest.approx(0.10)


def test_config_rejects_bad_mode(tmp_path, monkeypatch):
    from dashboard import config_editor
    monkeypatch.setattr(config_editor, "CONFIG_PATH", tmp_path / "config.toml")
    with pytest.raises(ValueError):
        config_editor.write_momo_config({"mode": "yolo"})


def test_config_rejects_live_port(tmp_path, monkeypatch):
    from dashboard import config_editor
    monkeypatch.setattr(config_editor, "CONFIG_PATH", tmp_path / "config.toml")
    with pytest.raises(ValueError):
        config_editor.write_momo_config({"mode": "paper", "port": 4001})


class _FakeMonitor:
    def __init__(self, snap):
        self._snap = snap

    def snapshot(self):
        import copy
        return copy.deepcopy(self._snap)


def _steady_snapshot():
    return {
        "connected": True,
        "net_liquidation": 905258.6,
        "unrealized_pnl": 0.0,
        "realized_pnl": 0.0,
        "positions": [{
            "con_id": 927852425,
            "symbol": "SPY",
            "sec_type": "OPT",
            "position": 256.0,
            "market_price": 0.99,
            "avg_cost": 0.0,
            "multiplier": 100,
            "unrealized_pnl": 0.0,
        }],
        "executions": [{"con_id": 927852425, "client_id": 2}],
    }


def _make_service(snap, get_config=None):
    from dashboard.risk_budget import RiskBudgetService
    return RiskBudgetService(_FakeMonitor(snap),
                             get_config or (lambda: {}),
                             lambda data: data)


def test_risk_budget_spy_steady_holding_counted():
    s = _make_service(_steady_snapshot()).snapshot()
    assert s["strategies"]["spy-steady"]["used_pct"] == pytest.approx(0.028, abs=0.001)
    assert s["strategies"]["spy-steady"]["can_open"] is True
    assert s["strategies"]["momo"]["used_pct"] == 0.0
    assert s["account"]["total_max_pct"] == 0.60


def test_risk_budget_halt_blocks_all():
    svc = _make_service(_steady_snapshot())
    svc.set_halt(True)
    s = svc.snapshot()
    assert s["account"]["halt"] is True
    assert all(not v["can_open"] for v in s["strategies"].values())


def test_risk_budget_unreachable_config_uses_defaults():
    s = _make_service(_steady_snapshot(), get_config=lambda: {}).snapshot()
    assert s["account"]["total_max_pct"] == 0.60
    assert s["strategies"]["spy-steady"]["max_pct"] == 0.30
    assert s["strategies"]["momo"]["daily_loss_limit_pct"] == 0.02


def test_risk_budget_endpoints(tmp_path):
    from fastapi.testclient import TestClient
    from dashboard.server import create_app
    from dashboard.process_manager import BotManager
    svc = _make_service(_steady_snapshot())
    app = create_app(bot_manager=BotManager(state_file=tmp_path/"p.json"),
                     ib_monitor=_FakeMonitor(_steady_snapshot()),
                     risk_service=svc)
    c = TestClient(app)
    r = c.get("/api/risk-budget")
    assert r.status_code == 200
    assert r.json()["strategies"]["spy-steady"]["used_pct"] == pytest.approx(0.028, abs=0.001)
    r = c.post("/api/halt", json={"halt": True})
    assert r.status_code == 200 and r.json()["account"]["halt"] is True
    assert all(not v["can_open"] for v in r.json()["strategies"].values())
    r = c.post("/api/halt", json={"halt": False})
    assert r.json()["account"]["halt"] is False


def test_ib_snapshot_includes_executions_when_disconnected():
    from dashboard.ib_monitor import IBMonitor
    assert IBMonitor(port=49999).snapshot()["executions"] == []


def test_api_bots_and_ib(tmp_path):
    from fastapi.testclient import TestClient
    from dashboard.server import create_app
    from dashboard.process_manager import BotManager
    from dashboard.ib_monitor import IBMonitor
    app = create_app(bot_manager=BotManager(state_file=tmp_path/"p.json"),
                     ib_monitor=IBMonitor(port=49999))
    c = TestClient(app)
    r = c.get("/api/bots")
    assert r.status_code == 200 and "spy-steady" in r.json()
    r = c.get("/api/ib")
    assert r.status_code == 200 and r.json()["connected"] is False
    r = c.post("/api/bots/nope/start")
    assert r.status_code == 400
    r = c.get("/api/logs/spy-turbo")
    assert r.status_code == 200
