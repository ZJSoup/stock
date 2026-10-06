#!/bin/zsh
# launchd/run_momo.sh — start momo bot (paper by default)
set -euo pipefail
cd /Users/zijunt/Dev/Projects/stock
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
/usr/bin/env python3 -m momo_bot &
PID=$!
# Hard backstop: ET 10:10 = 07:10 America/Vancouver (PDT; 08:10 in PST).
killat=$(date -j -f "%H:%M" "07:10" +%s)
now=$(date +%s)
if (( killat > now )); then
  ( sleep $((killat - now)); kill "$PID" 2>/dev/null) &
fi
wait "$PID" || true
