#!/usr/bin/env python3
"""
获取VIX和完整策略建议
"""
import sys
sys.path.insert(0, '.')
import yfinance as yf
from spy_multi_strategy import MarketAnalyzer, Strategy

# 获取VIX
vix_ticker = yf.Ticker("^VIX")
vix = vix_ticker.fast_info.get('last_price', 0)
print(f"VIX 当前值: {vix:.2f}")

# 市场分析
analyzer = MarketAnalyzer()
trend, confidence, details = analyzer.get_trend()

print(f"\nSPY 当前价格: ${details.get('current_price', 0):.2f}")
print(f"趋势: {trend.value} (信心: {confidence}%)")
print(f"RSI: {details.get('rsi', 0)}")

# 策略建议
print("\n" + "=" * 50)
print("🎯 今日操作建议")
print("=" * 50)

if vix > 30:
    print("⚠️ VIX过高(>30)，市场波动太大，建议观望不交易")
elif confidence >= 70:
    if trend.value == "看涨":
        print("✅ 趋势看涨，信心高，建议: Put Credit Spread (卖出看跌价差)")
        print("   - 选择虚值2-3个行权价的看跌期权卖出")
        print("   - 同时买入更低1-2个行权价的看跌期权做保护")
        print("   - 目标收益: 60-70%")
        print("   - 止损: 亏损50%时平仓")
    elif trend.value == "看跌":
        print("✅ 趋势看跌，信心高，建议: Call Credit Spread (卖出看涨价差)")
        print("   - 选择虚值2-3个行权价的看涨期权卖出")
        print("   - 同时买入更高1-2个行权价的看涨期权做保护")
        print("   - 目标收益: 60-70%")
        print("   - 止损: 亏损50%时平仓")
else:
    if vix < 18:
        print("📊 VIX较低，低波动市场，建议: Butterfly策略")
    elif 18 <= vix < 25:
        print("📊 VIX正常，建议: Iron Condor策略")
    else:
        print("📊 VIX较高，建议: Short Strangle策略")
    print(f"   - 趋势不明确(信心{confidence}%)，做区间策略")

print("\n⚠️ 风险提示:")
print("   - 0DTE期权波动大，严格止损")
print("   - 下午2:30前必须平仓，不留仓到到期")
print("   - 单只交易风险不超过账户2.5%")
