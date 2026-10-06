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
