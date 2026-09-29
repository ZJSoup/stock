import datetime as dt
import os
import pathlib
from momo_bot.config import load_config

TOML = """
host = "127.0.0.1"
port = 4002
client_id = 100
mode = "paper"

[risk]
risk_pct = 0.10
daily_loss_pct = 0.15
giveback_pct = 0.50

[window]
start = "07:00"
end = "10:00"

[signals]
impulse_lookback = 5
impulse_min_pct = 0.15
retrace_min = 0.25
retrace_max = 0.70
basing_low_tol = 0.003
sell_pressure_pct = 0.65
divergence_bars = 3
divergence_decline = 0.30
entry_rr = 1.0
"""

def test_load_config(tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(TOML)
    monkeypatch.delenv("MOMO_PORT", raising=False)
    s = load_config(cfg_path)
    assert s.port == 4002
    assert s.mode == "paper"
    assert s.risk_pct == 0.10
    assert s.window_start == dt.time(7, 0)
    assert s.window_end == dt.time(10, 0)
    assert s.impulse_min_pct == 0.15
    assert s.entry_rr == 1.0

def test_env_overrides_port(tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(TOML)
    monkeypatch.setenv("MOMO_PORT", "7497")
    assert load_config(cfg_path).port == 7497

def test_default_path_outside_repo(monkeypatch):
    monkeypatch.setenv("MOMO_CONFIG", "/tmp/whatever.toml")
    # default resolution honors MOMO_CONFIG and never points inside the repo
    from momo_bot import config
    assert config.default_config_path() == pathlib.Path("/tmp/whatever.toml")
