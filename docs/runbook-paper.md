# Momo Bot — Paper Trading Runbook

## One-time setup
1. `mkdir -p ~/.config/stock-momo ~/.local/share/stock-momo ~/Library/Logs/stock-momo`
2. `cp config.example.toml ~/.config/stock-momo/config.toml` — verify `mode = "paper"`, port 4002/7497.
3. Start IB Gateway with API enabled (Edit → Settings → API → Enable ActiveX and Socket Clients; uncheck "Read-Only").
4. `python3 test_ib_connection.py` must pass before anything else.
5. Install the schedule: `cp launchd/com.stock-momo.bot.plist ~/Library/LaunchAgents/ && launchctl load ~/Library/LaunchAgents/com.stock-momo.bot.plist`

## Daily routine
- Bot starts 6:55 local-equivalent ET, stops itself after the day locks (hard kill 10:10).
- Check the gate anytime:
  ```python
  from momo_bot.journal import summarize
  from pathlib import Path
  print(summarize(Path.home()/".local/share/stock-momo"))
  ```

## Going live (manual, only when eligible is True)
1. Confirm ≥20 days WITH TRADES (no-trade days do not count), ≥10 setups, net positive, win rate ≥60%, max DD ≤15%, zero violations.
2. Deposit exactly $2,000. Edit `~/.config/stock-momo/config.toml`: `mode = "live"`, live port.
3. First live week: set `risk_pct = 0.05` (soft start), then restore to 0.10.
4. Never edit the code to bypass the gate. If the bot locks the day, leave it locked.
