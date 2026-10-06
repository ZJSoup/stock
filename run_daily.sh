#!/bin/bash
# SPY 0DTE STEADY paper bot —— 每个交易日早上启动（由 launchd 调用）
# 前提：IB Gateway 已在 4002 端口登录 paper 账户，否则 bot connect 失败会自行退出。
set -uo pipefail

DIR=/Users/zijunt/Dev/Projects/stock
PY=/Library/Frameworks/Python.framework/Versions/13/bin/python3
LOG=/tmp/spy_steady_$(date +%Y%m%d).out

# 防重复：已有 steady 实例在跑就跳过（turbo 可并行，不互相阻塞；
# launchd 补跑/手动重复触发时安全）
if pgrep -f "spy_multi_strategy.py --mode steady" >/dev/null 2>&1; then
  echo "$(date '+%F %T') 已有 steady 实例在跑，跳过启动" >> "$LOG"
  exit 0
fi

cd "$DIR" || exit 1
# clientId 按 mode 固定（steady=2），不再从命令行传入。
nohup "$PY" spy_multi_strategy.py --mode steady --capital-pct 100 >> "$LOG" 2>&1 &
echo "$(date '+%F %T') 启动 STEADY bot, PID $!" >> "$LOG"
