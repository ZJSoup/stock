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
