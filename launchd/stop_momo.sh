#!/bin/zsh
# launchd/stop_momo.sh — kill a running momo bot
pkill -f "python3 -m momo_bot" || true
