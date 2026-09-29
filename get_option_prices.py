#!/usr/bin/env python3
"""获取0DTE期权报价"""
import yfinance as yf
from datetime import datetime, date
import pandas as pd

# 获取SPY期权链
spy = yf.Ticker("SPY")
opt = spy.option_chain()

# 提取今天的期权链（第一个到期日就是0DTE）
calls = opt.calls
puts = opt.puts

# 获取736 Put和735 Put的报价
put_736 = puts[puts['strike'] == 736]
put_735 = puts[puts['strike'] == 735]

print("=" * 60)
print("📊 SPY 0DTE Paper Trading - 入场报价")
print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M')} NY Time")
print("=" * 60)

print(f"\nSPY当前价格: ${spy.fast_info['last_price']:.2f}")

print("\n--- Put Credit Spread (卖出736, 买入735) ---")
if not put_736.empty:
    bid_736 = put_736.iloc[0]['bid']
    ask_736 = put_736.iloc[0]['ask']
    print(f"SPY 736 Put: 卖出价 {bid_736:.2f} (bid)")
else:
    print("SPY 736 Put: 没有报价")

if not put_735.empty:
    bid_735 = put_735.iloc[0]['bid']
    ask_735 = put_735.iloc[0]['ask']
    print(f"SPY 735 Put: 买入价 {ask_735:.2f} (ask)")
else:
    print("SPY 735 Put: 没有报价")

if not put_736.empty and not put_735.empty:
    net_credit = bid_736 - ask_735
    max_risk = 1.0 - net_credit
    print(f"\n净权利金: ${net_credit:.2f} / 合约")
    print(f"最大风险: ${max_risk:.2f} / 合约")
    print(f"风险收益比: 1 : {net_credit/max_risk:.2f}")
