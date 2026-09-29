#!/usr/bin/env python3
"""获取更接近的0DTE期权报价"""
import yfinance as yf
from datetime import datetime

spy = yf.Ticker("SPY")
opt = spy.option_chain()
puts = opt.puts

current_price = spy.fast_info['last_price']

print("=" * 60)
print("📊 SPY 0DTE Paper Trading - 寻找更好的行权价")
print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M')} NY Time")
print(f"SPY当前价格: ${current_price:.2f}")
print("=" * 60)

# 找出虚值1-3元的Put
target_strikes = []
for strike in sorted(puts['strike'].unique()):
    if strike < current_price:  # 虚值Put
        distance = current_price - strike
        if 0.5 <= distance <= 4.0:
            target_strikes.append(strike)

print("\n--- 可用的虚值Put报价 ---")
for strike in target_strikes:
    put_data = puts[puts['strike'] == strike]
    if not put_data.empty:
        bid = put_data.iloc[0]['bid']
        ask = put_data.iloc[0]['ask']
        distance = current_price - strike
        print(f"Put {strike:6.0f}: bid={bid:5.2f}, ask={ask:5.2f}, 距现价: {distance:.1f}元")

# 建议组合: 卖出738 Put, 买入736 Put
print("\n--- 建议组合: 738/736 Put Credit Spread ---")
put_738 = puts[puts['strike'] == 738]
put_736 = puts[puts['strike'] == 736]

if not put_738.empty and not put_736.empty:
    bid_738 = put_738.iloc[0]['bid']
    ask_736 = put_736.iloc[0]['ask']
    net_credit = bid_738 - ask_736
    max_risk = 2.0 - net_credit
    print(f"卖出 738 Put: ${bid_738:.2f} (bid)")
    print(f"买入 736 Put: ${ask_736:.2f} (ask)")
    print(f"净权利金: ${net_credit:.2f} / 合约")
    print(f"最大风险: ${max_risk:.2f} / 合约")
    print(f"风险收益比: 1 : {net_credit/max_risk:.2f}")
    print(f"60%止盈目标: ${net_credit * 0.6:.2f}")
    print(f"50%止损点: 亏损 ${max_risk * 0.5:.2f}")
