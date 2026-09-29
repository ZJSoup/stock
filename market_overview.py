#!/usr/bin/env python3
"""
获取VIX和其他主要股票表现
"""
import yfinance as yf
from datetime import datetime

print("=" * 50)
print("📊 今日市场概览 -", datetime.now().strftime("%Y-%m-%d"))
print("=" * 50)

# 获取VIX - 使用另一种方法
try:
    vix = yf.Ticker("^VIX")
    hist = vix.history(period='1d')
    if len(hist) > 0:
        vix_current = hist['Close'].iloc[-1]
        print(f"\nVIX: {vix_current:.2f}")
        if vix_current < 18:
            print("  低波动环境 - 适合Butterfly/Iron Condor")
        elif vix_current < 25:
            print("  正常波动 - 适合Iron Condor")
        elif vix_current < 30:
            print("  较高波动 - 适合Strangle/观望")
        else:
            print("  高波动 - 建议观望")
except Exception as e:
    print(f"VIX获取: {e}")

# 主要股票
print("\n💼 主要股票表现:")
tickers = ['SPY', 'QQQ', 'AAPL', 'MSFT', 'NVDA', 'GOOGL', 'AMZN', 'TSLA', 'META']
for ticker in tickers:
    try:
        t = yf.Ticker(ticker)
        hist = t.history(period='2d')
        if len(hist) >= 2:
            current = hist['Close'].iloc[-1]
            prev = hist['Close'].iloc[-2]
            change = ((current - prev) / prev) * 100
            emoji = "📈" if change > 0.2 else "📉" if change < -0.2 else "➡️"
            print(f"  {emoji} {ticker:5s}: ${current:8.2f} ({change:+6.2f}%)")
    except:
        pass

print("\n🎯 SPY 0DTE操作建议:")
print("  趋势看涨，信心80% → Put Credit Spread策略")
print("  - 卖出: SPY 737或736 Put (虚值3-4元)")
print("  - 买入: SPY 735 Put 做保护")
print("  - 目标: 权利金的60%止盈")
print("  - 止损: 亏损50%时平仓")
print("  - 关键: 下午2:30前必须平仓！")
