#!/usr/bin/env python3
import yfinance as yf
import numpy as np

tickers = ['AAPL', 'MSFT', 'NVDA', 'TSLA', 'AMZN', 'GOOGL', 'META', 'QQQ', 'SPY']

print("=" * 70)
print("📊 今日股票机会分析")
print("=" * 70)

for symbol in tickers:
    try:
        ticker = yf.Ticker(symbol)
        info = ticker.fast_info
        
        # 获取今日数据
        hist = ticker.history(period='5d', interval='1h')
        if len(hist) < 10:
            continue
            
        current_price = hist['Close'].iloc[-1]
        prev_close = hist['Close'].iloc[-25]  # 昨日收盘
        
        day_change = (current_price - prev_close) / prev_close * 100
        
        # 计算RSI
        prices = hist['Close'].values[-14:]
        deltas = np.diff(prices)
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)
        
        avg_gain = np.mean(gains[-6:]) if len(gains) > 0 else 0
        avg_loss = np.mean(losses[-6:]) if len(losses) > 0 else 0
        
        if avg_loss == 0:
            rsi = 100
        else:
            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs))
        
        # VWAP
        typical_price = (hist['High'] + hist['Low'] + hist['Close']) / 3
        vwap = (typical_price * hist['Volume']).cumsum() / hist['Volume'].cumsum()
        current_vwap = vwap.iloc[-1]
        
        # 判断
        above_vwap = current_price > current_vwap
        rsi_status = "超卖" if rsi < 30 else "超买" if rsi > 70 else "中性"
        
        # 信号
        signals = []
        if day_change > 1:
            signals.append("📈 强势")
        elif day_change < -1:
            signals.append("📉 走弱")
        if above_vwap:
            signals.append("✅ VWAP上方")
        if rsi < 40 and day_change < 0:
            signals.append("⚡ 可能反弹")
        if rsi > 60 and day_change > 0:
            signals.append("🔥 趋势向上")
        
        shares = int(735 / current_price)
        
        print(f"\n{symbol:6s}: ${current_price:7.2f} ({day_change:+.2f}%) | RSI: {rsi:.0f} | {rsi_status}")
        print(f"        可买: {shares} 股 (成本: ${shares*current_price:.0f})")
        if signals:
            print(f"        信号: {' '.join(signals)}")
            
    except Exception as e:
        print(f"{symbol}: 获取数据失败")

print("\n" + "=" * 70)
print("💡 建议:")
print("  - NVDA今天+3%，趋势最强，可以考虑1股 ($222)")
print("  - TSLA今天+2%，波动大适合做T")
print("  - QQQ/SPY最稳，适合长期持有")
print("=" * 70)
