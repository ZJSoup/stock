# Multi-Strategy Shared Account Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let SPY STEADY, SPY TURBO, and MOMO run concurrently against one IB account without touching each other's positions, governed by a shared risk-budget protocol.

**Architecture:** Loose coupling — independent processes, fixed clientIds, tagged orders (`orderRef=tag:<id>`). New `common/` package provides ownership classification, startup reconcile, and a fail-closed risk-budget client. Dashboard aggregates holdings by tag into `/api/risk-budget` and holds a halt flag.

**Tech Stack:** Python 3.13, ib_insync 0.9.86, FastAPI, vanilla JS.

## Global Constraints

- Read-only guarantee for dashboard stays: dashboard never places/cancels orders; halt is only a flag.
- Fixed clientIds: spy-steady=2, spy-turbo=3, momo=100, dashboard=50.
- Every order (entries AND exits) MUST set `orderRef=f"tag:{strategy_id}"`.
- Position-closing logic may only close holdings classified as own. Unclaimed holdings → read-only monitor mode + warning, never auto-trade, never crash-exit.
- Risk client is fail-closed: if dashboard unreachable OR `halt=true` OR own budget exceeded → no new entry.
- Paper only throughout: port 4002, host 127.0.0.1.
- Run commands from repo root `/Users/zijunt/Dev/Projects/stock`; tests: `python3 -m pytest tests/ -q` must stay green.
- Commits prefix `feat(shared):` / `fix(shared):`; do NOT push; trailer `Co-Authored-By: Claude Code <noreply@anthropic.com>` on every commit.
- Do not change strategy signal logic — only ownership, order tagging, reconcile, and entry-gating.

## File Structure

| File | Responsibility |
|---|---|
| `common/__init__.py` | Package marker |
| `common/ownership.py` | Tag constants, clientId registry, classify_holdings() |
| `common/reconcile.py` | Reconcile at startup → (mine, others, unclaimed) |
| `common/risk_client.py` | RiskBudgetClient.can_open() / is_halted() |
| `dashboard/risk_budget.py` | Aggregate holdings + journals into budget snapshot; halt flag |
| `dashboard/config_editor.py` | Add risk_budget section read/write |
| `dashboard/server.py` | New /api/risk-budget, /api/halt endpoints |
| `dashboard/static/index.html` | Strategy column on positions, budget panel, Halt button |
| `spy_multi_strategy.py` | Tag orders, scope close logic, reconcile, entry gate, fixed clientIds |
| `momo_bot/engine.py` | restore_state signature change |
| `momo_bot/app.py` | Reconcile others/unclaimed, entry gate, read-only mode |
| `momo_bot/execution.py` | orderRef on every order |
| `run_daily.sh`, `launchd/*.plist`, `stop_daily.sh` | Fixed clientIds, consistent ids |
| `tests/test_ownership.py`, `tests/test_risk_client.py`, `tests/test_reconcile.py` | New tests |

---

### Task 1: Ownership Protocol (`common/ownership.py`)

**Files:**
- Create: `common/__init__.py`, `common/ownership.py`
- Test: `tests/test_ownership.py`

**Interfaces:**
- Produces:
  - `TAG_PREFIX = "tag:"`
  - `CLIENT_IDS: dict[str, int] = {"spy-steady": 2, "spy-turbo": 3, "momo": 100}`
  - `CLIENT_TO_STRATEGY: dict[int, str]` (inverse)
  - `make_order_ref(strategy_id: str) -> str` → `"tag:<id>"`
  - `parse_order_ref(ref: str|None) -> str|None` — returns strategy id or None
  - `classify_holdings(positions: list, executions: list|None = None) -> dict[str, list]` — returns dict keyed by strategy id plus keys `"others"` (unused; all registered ids present) and `"unclaimed"`. A "position" is any object exposing `.contract` and optionally a position-level orderRef (ib_insync PortfolioItem has no orderRef → use executions mapping by contract conId). Classification order: position orderRef tag → execution clientId by conId → unclaimed.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_ownership.py
from common.ownership import (make_order_ref, parse_order_ref, classify_holdings,
                              CLIENT_IDS)


class C:
    def __init__(self, conid=1): self.conId = conid

class Pos:
    def __init__(self, conid=1, ref=None):
        self.contract = C(conid); self.orderRef = ref
        self.position = 1; self.avgCost = 1.0

class Ex:
    def __init__(self, conid=1, client_id=2):
        self.contract = C(conid); self.clientId = client_id


def test_tag_roundtrip():
    assert make_order_ref("momo") == "tag:momo"
    assert parse_order_ref("tag:momo") == "momo"
    assert parse_order_ref(None) is None
    assert parse_order_ref("garbage") is None


def test_classify_by_order_ref():
    out = classify_holdings([Pos(ref="tag:momo")])
    assert len(out["momo"]) == 1
    assert out["unclaimed"] == []


def test_classify_by_client_id_regression_for_existing_256_spy_calls():
    # Today's real holding: 256 SPY 781C, no orderRef, executed by clientId 2
    p = Pos(conid=927852425, ref=None)
    out = classify_holdings([p], executions=[Ex(conid=927852425, client_id=2)])
    assert len(out["spy-steady"]) == 1
    assert out["unclaimed"] == []


def test_classify_unclaimed():
    out = classify_holdings([Pos(conid=5, ref=None)], executions=[])
    assert len(out["unclaimed"]) == 1


def test_unknown_client_id_is_unclaimed():
    out = classify_holdings([Pos(conid=7, ref=None)],
                            executions=[Ex(conid=7, client_id=999)])
    assert len(out["unclaimed"]) == 1
```

- [ ] **Step 2: Run, verify fail** — `python3 -m pytest tests/test_ownership.py -q` → ModuleNotFoundError

- [ ] **Step 3: Implement** `common/ownership.py` per contract. Build `conId → clientId` map from executions (execution objects expose `.contract.conId` and `.clientId`). Ensure output dict always contains every key in CLIENT_IDS plus `"unclaimed"`, initialized to `[]`.

- [ ] **Step 4: Run, verify 5 passed**

- [ ] **Step 5: Commit** `feat(shared): ownership protocol with orderRef and clientId classification`

---

### Task 2: Startup Reconcile (`common/reconcile.py`)

**Files:**
- Create: `common/reconcile.py`
- Test: `tests/test_reconcile.py`

**Interfaces:**
- Produces:
  - `reconcile(strategy_id: str, positions: list, executions: list|None = None) -> tuple[list, list, list]` — returns `(mine, others, unclaimed)` using `classify_holdings`; `mine` = bucket for own id; `others` = concatenation of all other registered strategy buckets; `unclaimed` passed through.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_reconcile.py
from common.reconcile import reconcile
from tests.test_ownership import Pos, Ex


def test_mine_others_unclaimed_split():
    positions = [
        Pos(conid=1, ref="tag:momo"),
        Pos(conid=2, ref="tag:spy-steady"),
        Pos(conid=3, ref=None),
    ]
    mine, others, unclaimed = reconcile(
        "momo", positions, executions=[Ex(3, 999)])
    assert len(mine) == 1
    assert len(others) == 1
    assert len(unclaimed) == 1


def test_other_strategy_positions_ignored_not_error():
    mine, others, unclaimed = reconcile(
        "momo", [Pos(conid=2, ref="tag:spy-steady")], executions=[])
    assert mine == [] and len(others) == 1 and unclaimed == []
```

- [ ] **Step 2: Run, verify fail**
- [ ] **Step 3: Implement** per contract.
- [ ] **Step 4: Run, verify pass**
- [ ] **Step 5: Commit** `feat(shared): startup reconcile splitting mine/others/unclaimed`

---

### Task 3: Fail-Closed Risk Client (`common/risk_client.py`)

**Files:**
- Create: `common/risk_client.py`
- Test: `tests/test_risk_client.py`

**Interfaces:**
- Produces:
  - `RiskBudgetClient(base_url="http://127.0.0.1:8765", timeout=3.0)`
    - `can_open(strategy_id: str) -> tuple[bool, str]` — `(False, reason)` when: request fails (any exception), `account.halt` true, `strategies[id].can_open` false; else `(True, "")`.
    - `is_halted() -> bool` — true on halt OR unreachable (fail-closed).
    - Uses stdlib `urllib.request` (no new deps).

- [ ] **Step 1: Write failing tests**

```python
# tests/test_risk_client.py
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from common.risk_client import RiskBudgetClient


def serve(payload, port):
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers(); self.wfile.write(body)
        def log_message(self, *a): pass
    srv = HTTPServer(("127.0.0.1", port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_can_open_true():
    srv = serve({"account": {"halt": False},
                 "strategies": {"momo": {"can_open": True}}}, 8911)
    c = RiskBudgetClient("http://127.0.0.1:8911")
    ok, reason = c.can_open("momo")
    assert ok, reason
    srv.shutdown()


def test_halt_blocks():
    srv = serve({"account": {"halt": True},
                 "strategies": {"momo": {"can_open": True}}}, 8912)
    c = RiskBudgetClient("http://127.0.0.1:8912")
    ok, _ = c.can_open("momo")
    assert ok is False
    assert c.is_halted() is True
    srv.shutdown()


def test_strategy_budget_blocks():
    srv = serve({"account": {"halt": False},
                 "strategies": {"momo": {"can_open": False}}}, 8913)
    ok, reason = RiskBudgetClient("http://127.0.0.1:8913").can_open("momo")
    assert ok is False and reason
    srv.shutdown()


def test_unreachable_fail_closed():
    c = RiskBudgetClient("http://127.0.0.1:8999", timeout=1.0)
    ok, reason = c.can_open("momo")
    assert ok is False and reason
    assert c.is_halted() is True
```

- [ ] **Step 2: Run, verify fail**
- [ ] **Step 3: Implement** per contract. GET `/api/risk-budget`; 200 + parse JSON; any non-200/missing keys treated as failure.
- [ ] **Step 4: Run, verify pass**
- [ ] **Step 5: Commit** `feat(shared): fail-closed risk budget client`

---

### Task 4: Dashboard Risk Budget + Halt

**Files:**
- Create: `dashboard/risk_budget.py`
- Modify: `dashboard/server.py`, `dashboard/config_editor.py`
- Test: `tests/test_dashboard.py` (append)

**Interfaces:**
- Consumes: IB snapshot positions (from ib_monitor, include conId), `classify_holdings`, executions list (ib_monitor must capture `ib.executions()` — add that field), risk budget config.
- Produces:
  - `RiskBudgetService(ib_monitor, get_config, set_config)`:
    - `snapshot() -> dict` — shape used by Task 3 tests:
      ```json
      {"account": {"net_liquidation": float, "daily_pnl": float,
                   "total_used_pct": float, "total_max_pct": float, "halt": bool},
       "strategies": {id: {"used_pct", "max_pct", "daily_pnl",
                           "daily_loss_limit_pct", "can_open"}}}
      ```
    - `set_halt(bool)`, `is_halted()`
  - Notional per position: `|position| * marketPrice * multiplier` (OPT multiplier 100; STK multiplier 1). If marketPrice missing, use avgCost*position count fallback; document it.
  - Defaults from design spec: total_max=0.60, account_daily_loss=0.05, per-strategy spy-steady 0.30/0.02, spy-turbo 0.10/0.03, momo 0.20/0.02. Used when config has no `risk_budget` section.
  - `can_open` rule: not halt, total_used_pct < total_max_pct, own used_pct < max_pct, own daily_pnl > -daily_loss_limit_pct.
- Endpoints: `GET /api/risk-budget`, `POST /api/halt` body `{"halt": true|false}`.
- ib_monitor change: snapshot gains `"executions": [{"con_id": int, "client_id": int}]` from `ib.executions()`; keep backward compatible.

- [ ] **Step 1: Write failing tests** (TestClient, fake ib_monitor object with snapshot() returning holdings incl. the 256-call shape; fake get_config returning defaults)

Tests to include:
```python
def test_risk_budget_spy_steady_holding_counted():
    # fake monitor: netliq 905258.6, one pos conId 927852425 qty 256 mktpx 0.99 OPT,
    # execution conId→clientId 2; config defaults
    # expect strategies["spy-steady"]["used_pct"] ≈ 0.028, can_open True,
    # strategies["momo"]["used_pct"] == 0.0

def test_risk_budget_halt_blocks_all():
    # set_halt(True) → every can_open False, account.halt True

def test_risk_budget_unreachable_config_uses_defaults():
    # get_config returns {} → no KeyError, total_max_pct 0.60
```

- [ ] **Step 2: Run, verify fail**
- [ ] **Step 3: Implement** per contract; config_editor gains tolerant read of optional `risk_budget` TOML and write passthrough (validate numeric ranges 0<x<=1).
- [ ] **Step 4: Run, verify pass**
- [ ] **Step 5: Commit** `feat(shared): dashboard risk budget aggregation and halt`

---

### Task 5: MOMO Adoption

**Files:**
- Modify: `momo_bot/engine.py`, `momo_bot/app.py`, `momo_bot/execution.py`
- Test: update `tests/test_engine_exit.py` for new signature; add cases

**Interfaces:**
- `restore_state` new signature:
  `restore_state(date, settings, snapshot, mine_qty, mine_last_price, has_unclaimed) -> tuple[state, mode]`
  where mode ∈ `"normal"`, `"readonly"` (unclaimed present → monitor only; state tracked, no entries).
- Existing bool `has_broker_position` semantics replaced by explicit `mine_qty` (own classified position count). Others' positions are simply not passed in.
- app.py run(): after connect, call `reconcile("momo", ib.positions(), ib.executions())`; use `mine` for restore (qty + avgCost), ignore `others`; `has_unclaimed=bool(unclaimed)`.
- app.py: hold a `RiskBudgetClient`; before any Enter command is emitted, gate in the command-translation loop (skip + journal reason when blocked); on halt, don't open; engine risk exits remain owned by strategy.
- execution.py: every order gets `orderRef="tag:momo"` — add kwarg/helper so all 5 order constructions (enter, stop, replace_stop, exit, cancel-then-replace) carry it.

- [ ] **Step 1: Update/extend tests first**

Update the two existing `restore_state(...)` calls in tests/test_engine_exit.py:102,134 to new signature, and add:
```python
def test_unclaimed_returns_readonly_mode():
    s, mode = restore_state("2026-09-29", settings(), None, 0, None, True)
    assert mode == "readonly"

def test_others_positions_not_passed_normal_start():
    s, mode = restore_state("2026-09-29", settings(), None, 0, None, False)
    assert mode == "normal"
```
execution orderRef: assert in a lightweight test by monkeypatching Executor's ib and capturing placed order (`order.orderRef == "tag:momo"`) for enter and stop.

- [ ] **Step 2: Run, verify old calls fail**
- [ ] **Step 3: Implement** per contract.
- [ ] **Step 4: Run tests, verify pass**
- [ ] **Step 5: Commit** `feat(shared): momo reconcile, entry gate, tagged orders, readonly mode`

---

### Task 6: SPY Multi-Strategy Adoption

**Files:**
- Modify: `spy_multi_strategy.py`
- Test: add `tests/test_spy_shared.py`

**Interfaces (internal edits):**
- Remove free `--client-id`; map `--mode` → clientId from CLIENT_IDS (steady=2, turbo=3). Keep `--host/--port/--capital-pct/--live`; client-id mismatch is now impossible.
- On connect: `reconcile("spy-steady"/"spy-turbo", ib.positions(), ib.executions())`; restore `current_position` only from `mine` (existing dict shape, from contract/strike parsing); `others` ignored; unclaimed → set `self.readonly_monitor = True` and log warning.
- Every order construction (all MarketOrder sites listed in exploration: 1070, 1130, 1174, 1179, 1266, 1271, 1338, 1464) sets `orderRef=tag:<id>`.
- `close_all_positions()`: iterate only positions classified own; keep contract-qualification workaround at :1425.
- Entry paths: before placing buys, `RiskBudgetClient.can_open(self.strategy_id)`; blocked → log and skip.

- [ ] **Step 1: Write failing tests** — parse-args mode→clientId; close filter (fake positions with mixed tags + monkeypatched placeOrder, assert only own conId acted on); entry gate blocks when client returns False (monkeypatch can_open).
- [ ] **Step 2: Run, verify fail**
- [ ] **Step 3: Implement** per contract.
- [ ] **Step 4: Run, verify pass**
- [ ] **Step 5: Commit** `feat(shared): spy strategies tagged orders, scoped closes, entry gate`

---

### Task 7: Ops Files + Frontend + Live E2E

**Files:**
- Modify: `run_daily.sh`, `stop_daily.sh`, `launchd/*.plist`, `dashboard/static/index.html`, `dashboard/README.md`

- [ ] **Step 1: Ops files** — remove explicit client-id args (now derived), keep ids consistent; add momo launch if plist exists. Commit `chore(shared): fixed clientIds in launchd and daily scripts`.
- [ ] **Step 2: Frontend** — positions table gains "归属" column (classify via executions already in /api/ib; show strategy label or unclaimed badge); new 风险预算 panel (account bars + per-strategy used/max, can_open badges) and Halt/Resume button with confirm. Maintain ui-ux-pro-max visual quality, single file, no CDN. Commit `feat(shared): budget panel, ownership column, halt button`.
- [ ] **Step 3: Live E2E (lead verifies)**
  - `python3 -m pytest tests/ -q` all green
  - Start dashboard, start spy-steady AND momo simultaneously: momo no longer aborts; each sees only own holdings; budget endpoint shows steady used ~2.8%, momo 0.
  - Trigger halt via API → both skip new entries (check logs); resume.
  - No cross-position touches throughout.
  - Commit any fixes; update README; final report.

## Self-Review

- Spec coverage: ownership ✓ T1, reconcile ✓ T2, budget client ✓ T3, budget service/halt ✓ T4, momo ✓ T5, spy ✓ T6, ops/UI/e2e ✓ T7. Existing 256-holding migration ✓ (T1 regression + T4 test).
- Placeholder scan: T4/T6 give behavioral contracts with named shapes and exact rule values — no TBDs.
- Type consistency: `/api/risk-budget` JSON shape identical in T3 tests, T4 implementation, T7 panel. `restore_state` signature consistent across T5 spec and its call-site updates. orderRef string format identical everywhere.
