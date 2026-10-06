# Stock Bot Dashboard

本地 Web 仪表盘：启停 bot 进程、监控 IB 账户与持仓、查看日志、编辑 momo 配置。

## 运行

```bash
# 在仓库根目录
python3 -m dashboard.server
# 打开 http://127.0.0.1:8765
```

需要 IB Gateway / TWS 运行并开启 API（paper gateway 端口 4002）。Gateway 未运行时 Dashboard 正常启动，IB 面板显示"未连接"，后台每 15 秒自动重连。

依赖：`fastapi`、`uvicorn`、`ib_insync`（已安装；Python 3.13）。

## 面板

| 面板 | 功能 |
|---|---|
| **Bots** | 显示 spy-steady / spy-turbo / momo 的运行状态、PID、管理者；Start / Stop。Dashboard 启动的进程在 Dashboard 退出时自动终止；外部（launchd / 手动）启动的进程只能查看，Stop 被禁用。 |
| **风险预算** | 账户总敞口条（已用/上限）、净值、当日盈亏、各策略 used/max 条与"可开仓"标记；Halt / Resume 按钮（带确认）。Halt 只翻转本地标志位，从不触碰订单。账户日亏触限（默认 5%）自动 latch halt。 |
| **IB 账户** | 连接状态、账号、NetLiq、Available Funds、浮动/已实现盈亏、持仓明细表（含**归属**列：按 executions 的 clientId 判定策略，无法归属显示"未认领"）。使用 delayed-frozen 行情（`reqMarketDataType(4)`），无需行情订阅。 |
| **日志** | 选择 spy-steady / spy-turbo / momo / spy-trader 日志源，100/200/500 行，每 5 秒自动刷新。 |
| **Momo 配置** | 编辑 `~/.config/stock-momo/config.toml`，保存前校验，覆盖前自动备份为 `.bak`。端口只允许 4002 / 7497。 |

## 风险预算协议

- `GET /api/risk-budget`：`account`（净值、当日盈亏、总敞口 used/max、halt）+ `strategies`（每策略 used_pct/max_pct/daily_pnl/日亏限/can_open）。
- `POST /api/halt`，body `{"halt": true|false}`：手动 halt / resume，返回最新快照。
- 预算配置（可选，缺省走内置默认值：总敞口 60%、账户日亏 5%；steady 30%/2%、turbo 10%/3%、momo 20%/2%）：`~/.config/stock-dashboard/risk_budget.toml`。
- Bot 侧（`common/risk_client.py`）开仓前轮询本接口；**dashboard 不可达 / halt / 自身超限 → fail-closed 不开新仓**。
- 名义口径：`|持仓| × 市价 × multiplier`（OPT 100）；市价缺失时回退 avgCost。
- 当前策略 daily_pnl 只计浮盈（journal 已实现盈亏解析尚未接入）。

## 安全保证

- **只读 IB**：Dashboard 以 `readonly=True` 连接，代码中没有任何下单、改单、撤单或 `flatten` 调用，也没有对应 UI；Halt 只写本地标志位。
- **仅本地监听**：固定绑定 `127.0.0.1:8765`。
- **Paper 端口**：Dashboard 只连 4002；配置编辑器只接受 paper 端口。

## 端口 / clientId 分配

| 客户端 | clientId | 端口 |
|---|---|---|
| spy-steady | 2 | 4002 |
| spy-turbo | 3 | 4002 |
| momo bot | 100 | 4002 |
| **dashboard 监控** | **50** | 4002 |

## 文件结构

```
dashboard/
  process_manager.py   # bot 注册表 + Popen 启停 + PID 状态 JSON
  ib_monitor.py        # 后台线程持有唯一只读 IB 连接，API 只读快照
  risk_budget.py       # 持仓归属聚合 + 预算快照 + halt 标志
  logs.py              # 固定白名单日志 tail
  config_editor.py     # momo TOML 与 risk_budget TOML 读写 + 校验 + 备份
  server.py            # FastAPI 入口
  static/index.html    # 单文件前端（内联 CSS/JS，无 CDN）
```

## 测试

```bash
python3 -m pytest tests/ -q
```
