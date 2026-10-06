# Multi-Strategy Shared Account — Design Spec

**Date:** 2026-10-06
**Status:** Draft for review

## Goal

让不同策略（SPY STEADY、SPY TURBO、MOMO，以及未来新增策略）作为独立进程同时运行，共用同一个 IB 账户：各策略只管理自己的持仓，平仓不误伤他人；共享账户级风险预算，任何单一策略不能吃光整个账户的额度。

## Background — 今天暴露的三个冲突

1. `spy_multi_strategy.py:close_all_positions()` 遍历 `ib.positions()` 平掉账户内**全部**仓位。共账户时会误杀别的策略。
2. `momo_bot/engine.py:restore_state` 看到任何无本地快照的持仓就 raise 中止。它无法区分"自己的孤儿仓位"和"别的策略的正常仓位"。
3. 两边风控都以账户净值为口径（每笔 % / 日亏 %），但互不感知对方已占用的资金。STEADY 100% 资金 + MOMO 100% 资金会叠加超限。

## Approach — 松耦合（已确认）

各策略保持独立进程、独立 clientId，不做统一 runtime。改动是两层共享约定：

- **持仓归属协议**：每笔订单打标签，持仓按标签/clientId 归属，各策略只平自己的仓位。
- **风险预算接口**：dashboard 提供账户总配额与各策略占用的只读接口，bot 下单前自查；dashboard 的 halt 信号可让全部策略停止开新仓/各自平仓。

## Design

### 1. 持仓归属协议

**标签格式：** IB `Order.orderRef = "tag:<strategy-id>"`，strategy-id 与 dashboard 的 bot id 一致：
`spy-steady` / `spy-turbo` / `momo`。

**clientId 分配（固定，不再命令行随意指定）：**

| Strategy | clientId |
|---|---|
| spy-steady | 2 |
| spy-turbo | 3 |
| momo | 100 |
| dashboard monitor | 50 |
| 手动脚本/test | 70–79 |

**归属判定顺序（`common/ownership.py`）：**

1. 持仓/成交的 `orderRef` 以 `tag:` 开头 → 按标签归属。
2. 无标签 → 按产生该成交的 execution `clientId` 反查（Gateway 会回放当日 executions，带 clientId）。
3. 两者都无（历史遗留、IB 端手工开仓）→ 归属为 `unclaimed`。

**启动对账（reconcile）规则：**

- 属于自己的持仓：正常 restore（momo 现有 snapshot 逻辑照旧）。
- 属于其他已注册策略的持仓：**忽略**——不计入自己的状态，不报错，不平仓。
- `unclaimed` 持仓：
  - momo：保持保守行为，记录 journal 警告，**拒绝开新仓**但进程照常运行（监控模式），等人工处理；不再像今天一样直接崩溃退出。
  - spy 系：同样进入只读监控态并报警。
- 现存的 256 张 SPY 781C（今天 steady、clientId=2、无 orderRef）会通过规则 2 正确归给 spy-steady，无需人工干预。

**平仓范围：** `close_all_positions()` 及所有止损/止盈强平逻辑改为只处理归属自己的持仓；平仓订单也带同样的 orderRef（`tag:<id>`），方便事后审计。

**本地 journal 为准：** 标签是快速通道，策略自己的 journal/state 文件仍是持仓意图的权威来源；两者不一致时以保守方式处理（按 unclaimed 对待 + 告警）。

### 2. 共享风险预算

**配置（dashboard 配置面板新增，TOML 或页面可编辑）：**

```toml
[risk_budget]
# 占账户净值的比例
total_max = 0.60          # 全部策略合计最大名义敞口
account_daily_loss = 0.05 # 账户级当日最大亏损 → 触发全策略 halt

[[risk_budget.strategies]]
id = "spy-steady"
max_notional_pct = 0.30
daily_loss_pct = 0.02

[[risk_budget.strategies]]
id = "spy-turbo"
max_notional_pct = 0.10
daily_loss_pct = 0.03

[[risk_budget.strategies]]
id = "momo"
max_notional_pct = 0.20
daily_loss_pct = 0.02
```

**dashboard 聚合（每 5 秒，复用现有 IB monitor 线程）：**

- 从持仓按标签分组：每策略当前名义敞口、浮盈。
- 从各策略 journal（spy 的 trade CSV/log、momo 的 data_dir）读当日已实现 PnL。
- 产出：`/api/risk-budget`
  ```json
  {
    "account": {"net_liquidation": 905258.6, "daily_pnl": 0.0,
                "total_used_pct": 0.028, "total_max_pct": 0.60, "halt": false},
    "strategies": {
      "spy-steady": {"used_pct": 0.028, "max_pct": 0.30, "daily_pnl": 0.0,
                     "daily_loss_limit_pct": 0.02, "can_open": true}
    }
  }
  ```

**bot 侧（`common/risk_client.py`）：**

- 每次准备开新仓前调用 `localhost:8765/api/risk-budget`，检查 `strategies.<self>.can_open` 与 `account.halt`；不满足则跳过并记录原因。
- dashboard 没运行时：fail-closed（不开新仓），保守且可预测。
- 轮询模型，不做分布式锁——期权流动性场景下偶尔超额的代价远小于引入强一致的复杂度；总敞口上限（0.60）留出了缓冲。

**Halt 信号：**

- 账户日亏触及 `account_daily_loss` → dashboard 自动置 `halt=true`。
- 页面提供手动 Halt / Resume 按钮（这是 dashboard 首个"写"操作，但只写本地标志位，不碰 IB 订单）。
- bot 看到 `halt=true`：停止开新仓；是否立即平仓由各策略自己的配置决定（默认：spy 系按各自 exit 规则处理、momo 按风险规则退出），dashboard **不代下单**。

### 3. 代码结构

新增共享包（repo 根，两个 bot 都能 import）：

| File | Responsibility |
|---|---|
| `common/__init__.py` | 包标记 |
| `common/ownership.py` | orderRef 协议常量、归属判定函数 `classify_holdings(positions, executions, registry) -> dict[str,list]`（含 `unclaimed` 桶） |
| `common/risk_client.py` | `RiskBudgetClient(base_url)`：`can_open(strategy_id) -> tuple[bool, str]`、`is_halted() -> bool`，dashboard 不可达时 fail-closed |
| `common/reconcile.py` | 启动对账：返回 `(mine, others, unclaimed)` |

修改点（只动必要部分，不重写策略逻辑）：

- `spy_multi_strategy.py`：下单设 orderRef；`close_all_positions` 过滤归属；启动时用 common.reconcile 识别存量；开仓前查 budget；`--client-id` 与策略绑定（不再自由传）。
- `momo_bot/app.py`：reconcile 改为忽略他人仓位、unclaimed 进入只读态（不再 raise 退出）；`execution.py` 订单设 orderRef；开仓前查 budget。
- `momo_bot/engine.py:restore_state`：签名扩展，接收已分类的持仓（`mine`/`others`/`unclaimed`）而非单个 bool。
- `dashboard/`：新增 `/api/risk-budget`、halt 状态与按钮、风险预算配置；持仓表增加 strategy 列。

### 4. Testing

- `tests/test_ownership.py`：标签/ clientId / unclaimed 三种归属；256 张现存仓位的回归用例（clientId=2 无标签 → spy-steady）。
- `tests/test_risk_client.py`：dashboard 不可达 fail-closed；halt 时 can_open=False；超额时 False。用 FastAPI TestClient 起假接口。
- `tests/test_reconcile.py`：别人仓位被忽略；unclaimed 触发只读态。
- spy/momo 现有测试保持绿；`restore_state` 新签名的旧调用点全部更新。
- 真实验收：dashboard + steady + momo 三进程同跑，确认 momo 不再崩溃、互不平仓，预算接口数据正确。

## Non-Goals

- 不做统一 runtime / 单连接复用 / 强一致锁。
- dashboard 不代下单、不平仓（halt 只发信号）。
- 不支持 IB 实盘端口切换的自动化（仍维持手动、paper gate 机制）。
- 不做策略间盈亏分成核算。

## Migration Notes

- 现存无标签仓位靠 clientId 归属，平滑过渡；新增仓位立即带标签。
- clientId 从"命令行参数"改为"策略固定值"，需同步更新 `run_daily.sh`、launchd plist 与 dashboard 的 BOTS 注册。
- 风险预算默认值偏保守（总敞口 60%），上线前由老大确认具体数字。
