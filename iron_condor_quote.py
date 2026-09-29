#!/usr/bin/env python3
"""Iron Condor报价计算"""
import yfinance as yf
from datetime import datetime

spy = yf.Ticker("SPY")
opt = spy.option_chain()
calls = opt.calls
puts = opt.puts

current_price = spy.fast_info['last_price']

print("=" * 60)
print("📊 Iron Condor 报价分析")
print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M')} NY Time")
print(f"SPY当前价格: ${current_price:.2f}")
print(f"VIX: 17.93 (低波动，适合Iron Condor)")
print("=" * 60)

# 寻找合适的行权价
# Put侧: 虚值2-3元 (卖出)
# Call侧: 虚值2-3元 (卖出)

print("\n--- Put侧 (下方保护) ---")
put_sell_strike = 738  # 虚值约1.1元
put_buy_strike = 736   # 再低2元
put_sell = puts[puts['strike'] == put_sell_strike]
put_buy = puts[puts['strike'] == put_buy_strike]

if not put_sell.empty and not put_buy.empty:
    put_sell_bid = put_sell.iloc[0]['bid']
    put_buy_ask = put_buy.iloc[0]['ask']
    put_credit = put_sell_bid - put_buy_ask
    print(f"卖出 Put {put_sell_strike}: bid=${put_sell_bid:.2f}")
    print(f"买入 Put {put_buy_strike}: ask=${put_buy_ask:.2f}")
    print(f"Put侧净权利金: ${put_credit:.2f}")

print("\n--- Call侧 (上方保护) ---")
call_sell_strike = 742  # 虚值约2.9元
call_buy_strike = 744   # 再高2元
call_sell = calls[calls['strike'] == call_sell_strike]
call_buy = calls[calls['strike'] == call_buy_strike]

if not call_sell.empty and not call_buy.empty:
    call_sell_bid = call_sell.iloc[0]['bid']
    call_buy_ask = call_buy.iloc[0]['ask']
    call_credit = call_sell_bid - call_buy_ask
    print(f"卖出 Call {call_sell_strike}: bid=${call_sell_bid:.2f}")
    print(f"买入 Call {call_buy_strike}: ask=${call_buy_ask:.2f}")
    print(f"Call侧净权利金: ${call_credit:.2f}")

if not put_sell.empty and not put_buy.empty and not call_sell.empty and not call_buy.empty:
    total_credit = put_credit + call_credit
    max_risk = 2.0 - total_credit  # 每个spread宽度2元
    print("\n" + "=" * 60)
    print("📈 Iron Condor (736/738 - 742/744)")
    print("=" * 60)
    print(f"总净权利金: ${total_credit:.2f} / 合约")
    print(f"最大风险: ${max_risk:.2f} / 合约")
    print(f"风险收益比: 1 : {total_credit/max_risk:.2f}")
    print(f"60%止盈目标: ${total_credit * 0.6:.2f}")
    print(f"50%止损点: 亏损 ${max_risk * 0.5:.2f}")
    print(f"\n盈亏区间: ${put_sell_strike - total_credit:.2f} 到 ${call_sell_strike + total_credit:.2f}")
