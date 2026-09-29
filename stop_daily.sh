#!/bin/bash
# SPY 0DTE bot —— 收盘后停止（由 launchd 在 13:15 PT / 16:15 ET 调用）
# bot 自身会在 15:00/15:15 ET 强平持仓；这里收盘后再收掉进程，避免过夜实例与次日 clientId 冲突。
LOG=/tmp/spy_trader_stop.log

if pkill -f "spy_multi_strategy.py"; then
  echo "$(date '+%F %T') 已停止 bot" >> "$LOG"
else
  echo "$(date '+%F %T') 无运行中的 bot 实例" >> "$LOG"
fi
