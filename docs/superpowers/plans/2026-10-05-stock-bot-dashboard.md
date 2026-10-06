# Stock Bot Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A local-only web dashboard (`dashboard/` in this repo) to manage the stock bots: start/stop bot processes, monitor IB account/positions in near-real-time, view bot logs, and edit momo bot config.

**Architecture:** FastAPI backend serves a single-file HTML/JS frontend (`dashboard/static/index.html`). Process management via `subprocess.Popen` + PID state JSON. IB monitoring runs in a dedicated background thread owning one `ib_insync.IB` connection (clientId=50, read-only — the dashboard never places orders); API handlers read a snapshot dict, never touch IB directly. Logs read by tailing known log files. Config editor reads/writes the momo TOML file with backup.

**Tech Stack:** Python 3.13, fastapi, uvicorn, ib_insync (all already installed on this machine), vanilla JS single-page frontend (no build step).

## Global Constraints

- **Read-only on IB: the dashboard MUST NOT place, modify, or cancel orders, and MUST NOT call `flatten`.** No UI affordance for order actions.
- Dashboard binds to **127.0.0.1 only**, default port **8765**.
- IB monitor connection: host `127.0.0.1`, port **4002** (paper gateway), **clientId 50** (bots use 1/2/3, momo uses 100). `reqMarketDataType(4)` (delayed-frozen) so no market-data subscription is needed.
- Paper-only: no UI path to live ports. Any port input field accepts only {4002, 7497}.
- Bot processes started by the dashboard are killed when the dashboard shuts down (atexit cleanup) and are marked `managed_by: dashboard` in state.
- No new third-party Python deps beyond fastapi/uvicorn/ib_insync (already installed). Frontend: no npm, no build step, no external CDN — everything inline in one HTML file.
- Run all commands from repo root `/Users/zijunt/Dev/Projects/stock`.
- Do NOT push; commit locally with message prefix `feat(dashboard):`. Do NOT modify `spy_multi_strategy.py`, `spy_auto_trader.py`, `trader.py`, or anything under `momo_bot/`.
- Python interpreter: `python3` (3.13, has fastapi/uvicorn/ib_insync). Tests: `python3 -m pytest tests/ -q` — the existing suite must stay green.

## File Structure

| File | Responsibility |
|---|---|
| `dashboard/__init__.py` | Empty package marker |
| `dashboard/process_manager.py` | Bot registry, start/stop/status, PID state JSON |
| `dashboard/ib_monitor.py` | Background thread + IB connection, snapshot dict |
| `dashboard/logs.py` | Tail known log files |
| `dashboard/config_editor.py` | Read/write momo TOML with backup |
| `dashboard/server.py` | FastAPI app wiring all endpoints + static file |
| `dashboard/static/index.html` | Single-file frontend |
| `tests/test_dashboard.py` | Unit tests (no IB, no real processes) |

---

### Task 1: Process Manager

**Files:**
- Create: `dashboard/__init__.py` (empty)
- Create: `dashboard/process_manager.py`
- Test: `tests/test_dashboard.py`

**Interfaces:**
- Produces:
  - `BOTS: dict[str, dict]` — registry, e.g. `BOTS["spy-steady"] = {"label": "SPY STEADY", "cmd": [sys.executable, "spy_multi_strategy.py", "--mode", "steady", "--capital-pct", "100", "--client-id", "2"], "cwd": REPO_ROOT}`
  - `BotManager()` with methods:
    - `status() -> dict[str, dict]` — per bot: `{"running": bool, "pid": int|None, "managed_by": "dashboard"|"external"|None, "cmd": str, "label": str}`
    - `start(bot_id: str) -> dict` — raises `ValueError` on unknown id or already running
    - `stop(bot_id: str) -> dict` — SIGTERM; only allowed if `managed_by == "dashboard"`, else raises `ValueError("not managed by dashboard")`
    - `shutdown()` — terminate all dashboard-managed processes (for atexit)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_dashboard.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_dashboard.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'dashboard'`

- [ ] **Step 3: Implement `dashboard/process_manager.py`**

Requirements (the implementer writes the code; these are the contract):
- Module-level `BOTS` registry:
  - `spy-steady`: cmd = `[sys.executable, "spy_multi_strategy.py", "--mode", "steady", "--capital-pct", "100", "--client-id", "2"]`, cwd = repo root (parent of `dashboard/`)
  - `spy-turbo`: same but `--mode turbo --client-id 3`
  - `momo`: cmd = `[sys.executable, "-m", "momo_bot"]`, cwd = repo root
- `BotManager(state_file=None)`: default state file `dashboard/.pids.json`. Tracks `self._procs: dict[str, subprocess.Popen]` for dashboard-managed bots.
- `register_test_bot(bot_id, cmd, cwd)` — adds an entry (test support).
- `mark_external(bot_id, pid)` — record an externally-started PID in state (test support + future use).
- `start(bot_id)`: check registry; if already running (managed proc alive, or `pgrep -f <pattern>` finds an external match) raise ValueError; else `Popen(cmd, cwd=cwd, stdout=open(logfile,'ab'), stderr=STDOUT, start_new_session=True)` where logfile is `/tmp/dashboard_<bot_id>.log`; persist state; return `status()[bot_id]`.
- `stop(bot_id)`: only dashboard-managed; `proc.terminate()`, wait up to 10s then `kill()`; update state.
- `status()`: for each registry bot: if managed proc exists use `proc.poll() is None`; else fall back to pgrep on the script name (e.g. `spy_multi_strategy.py` / `momo_bot`) to detect external instances, marked `managed_by: "external"`.
- `shutdown()`: terminate all managed procs; tolerate already-dead.
- State JSON: `{bot_id: {"pid": int, "managed_by": str, "started_at": iso8601}}`; tolerate missing/corrupt file.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_dashboard.py -q`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add dashboard/__init__.py dashboard/process_manager.py tests/test_dashboard.py
git commit -m "feat(dashboard): bot process manager with start/stop/status"
```

---

### Task 2: IB Monitor (read-only snapshot thread)

**Files:**
- Create: `dashboard/ib_monitor.py`
- Test: `tests/test_dashboard.py` (append)

**Interfaces:**
- Produces:
  - `IBMonitor(host="127.0.0.1", port=4002, client_id=50)` with:
    - `start()` / `stop()` — lifecycle of the background thread
    - `snapshot() -> dict` — `{"connected": bool, "account": str|None, "net_liquidation": float|None, "available_funds": float|None, "unrealized_pnl": float|None, "realized_pnl": float|None, "positions": [{"symbol": str, "sec_type": str, "expiry": str|None, "strike": float|None, "right": str|None, "position": float, "avg_cost": float, "market_value": float|None, "unrealized_pnl": float|None}], "updated_at": iso8601|None, "error": str|None}` — always returns the last good snapshot; never raises.

- [ ] **Step 1: Write the failing test**

```python
def test_ib_snapshot_shape_when_disconnected():
    from dashboard.ib_monitor import IBMonitor
    mon = IBMonitor(port=49999)  # nothing listening
    snap = mon.snapshot()
    assert snap["connected"] is False
    assert snap["positions"] == []
    assert "error" in snap
    # must never raise even with no IB running
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest tests/test_dashboard.py::test_ib_snapshot_shape_when_disconnected -q`
Expected: FAIL — module missing

- [ ] **Step 3: Implement `dashboard/ib_monitor.py`**

Contract:
- A daemon thread loop: if not connected, try `ib.connect(host, port, clientId=client_id, timeout=5, readonly=True)`; on success `ib.reqMarketDataType(4)`.
- Every 5s when connected: rebuild snapshot from `ib.accountSummary()` (filter tags `NetLiquidation`, `AvailableFunds`, `UnrealizedPnL`, `RealizedPnL`), `ib.portfolioItems()` for positions (map `contract.secType`, `lastTradeDateOrContractMonth`, `strike`, `right`, `position`, `averageCost`, `marketValue`, `unrealizedPNL`; secType OPT vs STK).
- All IB calls wrapped in try/except — on `ConnectionError`/timeout set `connected=False`, keep last snapshot, retry connect with 15s backoff.
- `snapshot()` returns a deep-ish copy (dict + list copy); thread-safe via a `threading.Lock`.
- `stop()` sets an event, calls `ib.disconnect()` if connected, joins thread (timeout 5).
- **No order/trade/flatten calls anywhere in this module.**

- [ ] **Step 4: Run test; verify pass**

Run: `python3 -m pytest tests/test_dashboard.py -q`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add dashboard/ib_monitor.py tests/test_dashboard.py
git commit -m "feat(dashboard): read-only IB monitor thread"
```

---

### Task 3: Logs + Config Editor

**Files:**
- Create: `dashboard/logs.py`, `dashboard/config_editor.py`
- Test: `tests/test_dashboard.py` (append)

**Interfaces:**
- Produces:
  - `tail_log(name: str, lines: int = 200) -> {"name": str, "lines": list[str], "path": str|None}` — `name` is one of a fixed allowlist (below); unknown name raises `ValueError`. Missing file returns `{"lines": [], "path": None}`.
  - `read_momo_config() -> dict` — parsed TOML plus `{"path": str, "exists": bool}`.
  - `write_momo_config(data: dict) -> dict` — validates required keys/types, backs up existing file to `<path>.bak`, writes TOML, returns `read_momo_config()`.

Log allowlist (name → path):
- `spy-steady` → `/tmp/dashboard_spy-steady.log` AND today's `/tmp/spy_steady_YYYYMMDD.out` (concatenate; newest last)
- `spy-turbo` → `/tmp/dashboard_spy-turbo.log`
- `momo` → `/tmp/dashboard_momo.log`
- `spy-trader` → `/Users/zijunt/spy_trader.log`

- [ ] **Step 1: Write failing tests**

```python
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
```

(Use `config.example.toml` in repo root as the reference shape. Validation: `mode in {"paper","live"}`; `port in {4002,7497}` when mode=paper; required sections risk/window/signals present with the keys from the roundtrip test; numeric fields coercible to float/int.)

- [ ] **Step 2: Run to verify fail**

Run: `python3 -m pytest tests/test_dashboard.py -q -k "log or config"`
Expected: FAIL — modules missing

- [ ] **Step 3: Implement `dashboard/logs.py` and `dashboard/config_editor.py`**

- `logs.py`: `LOG_SOURCES` dict as specced above; `tail_log` reads each existing source file's last N lines (binary-safe read, `errors="replace"`), concatenates, returns last `lines` entries.
- `config_editor.py`: module-level `CONFIG_PATH = pathlib.Path.home() / ".config/stock-momo/config.toml"` (tests monkeypatch this name — do not bury it in a function). Writing: stdlib has no TOML writer — serialize manually with a small `_dump_toml(dict)` (sections as `[section]`, values: str quoted, int/float bare, keys in stable order matching `config.example.toml`). Backup existing file to `CONFIG_PATH.with_suffix(CONFIG_PATH.suffix + ".bak")` before overwrite. Validate per the tests.

- [ ] **Step 4: Run tests; verify pass**

Run: `python3 -m pytest tests/test_dashboard.py -q`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add dashboard/logs.py dashboard/config_editor.py tests/test_dashboard.py
git commit -m "feat(dashboard): log tailing and momo config editor"
```

---

### Task 4: FastAPI Server

**Files:**
- Create: `dashboard/server.py`
- Test: `tests/test_dashboard.py` (append, using fastapi.testclient)

**Interfaces:**
- Consumes: `BotManager`, `IBMonitor`, `tail_log`, `read_momo_config`, `write_momo_config` from earlier tasks.
- Produces: `create_app(bot_manager=None, ib_monitor=None) -> FastAPI` (injectable for tests). Endpoints:
  - `GET /` → static/index.html
  - `GET /api/bots` → `BotManager.status()`
  - `POST /api/bots/{bot_id}/start` / `POST /api/bots/{bot_id}/stop` → 200 with status dict; 400 with `{"detail": str(e)}` on ValueError
  - `GET /api/ib` → `IBMonitor.snapshot()`
  - `GET /api/logs/{name}?lines=200` → `tail_log`
  - `GET /api/config/momo` → `read_momo_config()`
  - `PUT /api/config/momo` (JSON body) → `write_momo_config`; 400 on ValueError
  - `python3 -m dashboard.server` runs uvicorn on 127.0.0.1:8765, starts IBMonitor, registers atexit `bot_manager.shutdown()` + `ib_monitor.stop()`.

- [ ] **Step 1: Write failing tests**

```python
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
```

- [ ] **Step 2: Run to verify fail** — module missing.

- [ ] **Step 3: Implement `dashboard/server.py`**

Per the interface contract. Mount static via `fastapi.responses.FileResponse` for `/` (path: `dashboard/static/index.html` — created in Task 5; server must still import cleanly without it, resolve path lazily at request time). ValueError from any handler → `HTTPException(400, str(e))`.

- [ ] **Step 4: Run tests; verify pass.** Also verify import side-effect free: `python3 -c "import dashboard.server"` exits 0 without IB running.

- [ ] **Step 5: Commit**

```bash
git add dashboard/server.py tests/test_dashboard.py
git commit -m "feat(dashboard): FastAPI server wiring"
```

---

### Task 5: Frontend (single-file HTML) + end-to-end verification

**Files:**
- Create: `dashboard/static/index.html`

**Interfaces:**
- Consumes: all `/api/*` endpoints from Task 4.

No unit tests for the HTML itself; acceptance is the manual smoke checklist below.

- [ ] **Step 1: Write `dashboard/static/index.html`**

Single file, inline CSS + JS (no CDN). Dark theme. Auto-refresh every 5s via `fetch`. Four panels:
1. **Bots** — one row per bot from `/api/bots`: label, running badge (绿/灰), pid, managed_by, Start/Stop buttons (Stop disabled unless `managed_by === "dashboard"`). Start/Stop POST then refresh.
2. **IB 账户** — from `/api/ib`: connected badge, account, NetLiq, AvailableFunds, unrealized/realized PnL; positions table (symbol, expiry, strike, right, qty, avgCost, mktValue, uPnL). Show `error` text when disconnected.
3. **日志** — dropdown of log names + lines selector (100/200/500), `<pre>` with tail output, auto-refresh.
4. **Momo 配置** — form generated from `/api/config/momo` (sections risk/window/signals + top-level host/port/client_id/mode), Save button PUTs JSON; show success/error banner. Port input restricted to {4002, 7497}.

- [ ] **Step 2: End-to-end smoke (manual, lead verifies)**

```bash
python3 -m pytest tests/ -q          # full suite green
python3 -m dashboard.server &        # start without IB Gateway
curl -s localhost:8765/api/bots | python3 -m json.tool
curl -s localhost:8765/api/ib | python3 -m json.tool   # connected:false, no crash
curl -s localhost:8765/ | head -5    # HTML served
kill %1
```

- [ ] **Step 3: Commit**

```bash
git add dashboard/static/index.html
git commit -m "feat(dashboard): single-file web UI"
```

---

### Task 6: Docs

**Files:**
- Modify: none of the trading code; optionally create `dashboard/README.md`

- [ ] **Step 1: Write `dashboard/README.md`** — how to run (`python3 -m dashboard.server`), what each panel does, the read-only guarantee, how ports/clientIds are allocated, note that dashboard-started bots die with the dashboard.
- [ ] **Step 2: Commit** `docs(dashboard): usage README`

## Self-Review

- Spec coverage: 启停 ✓ (T1/T4), 实时持仓盈亏 ✓ (T2), 日志 ✓ (T3), 参数配置 ✓ (T3), 只监控不落单 ✓ (global constraint + T2 contract), 本地 Web ✓.
- Placeholders: none — every code step has either the code or an exact behavioral contract; module code itself is written by the implementer per contract (standard for this plan format).
- Type consistency: `snapshot()` keys used identically in T2 tests, T4 tests, and T5 frontend spec. `BotManager.status()` shape consistent across T1 test, T4 endpoint, T5 frontend.
