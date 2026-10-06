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
| **IB 账户** | 连接状态、账号、NetLiq、Available Funds、浮动/已实现盈亏、持仓明细表。使用 delayed-frozen 行情（`reqMarketDataType(4)`），无需行情订阅。 |
| **日志** | 选择 spy-steady / spy-turbo / momo / spy-trader 日志源，100/200/500 行，每 5 秒自动刷新。 |
| **Momo 配置** | 编辑 `~/.config/stock-momo/config.toml`，保存前校验，覆盖前自动备份为 `.bak`。端口只允许 4002 / 7497。 |

## 安全保证

- **只读 IB**：Dashboard 以 `readonly=True` 连接，代码中没有任何下单、改单、撤单或 `flatten` 调用，也没有对应 UI。
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
  logs.py              # 固定白名单日志 tail
  config_editor.py     # momo TOML 读写 + 校验 + 备份
  server.py            # FastAPI 入口
  static/index.html    # 单文件前端（内联 CSS/JS，无 CDN）
```

## 测试

```bash
python3 -m pytest tests/ -q
```
