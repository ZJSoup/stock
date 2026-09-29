#!/usr/bin/env python3
"""检查Paper Trading盈亏"""
import yfinance as yf
from datetime import datetime

spy = yf.Ticker("SPY")
opt = spy.option_chain()
calls = opt.calls
puts = opt.puts

current_price = spy.fast_info['last_price']

print("=" * 60)
print("📊 Paper Trading 盈亏检查 - Iron Condor (736/738 - 742/744)")
print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M')} NY Time")
print("=" * 60)
print(f"\nSPY当前价格: ${current_price:.2f}")
print(f"入场价格: $739.10")
print(f"盈亏区间: $737.83 - $742.17")

# 检查当前盈亏
# 入场: 卖出738 Put@0.19, 买入736 Put@0.12
#       卖出742 Call@0.15, 买入744 Call@0.05
# 净权利金: $0.17

print("\n--- 当前期权报价 ---")

# Put侧
put_738 = puts[puts['strike'] == 738]
put_736 = puts[puts['strike'] == 736]
if not put_738.empty:
    print(f"Put 738 (卖出): 现在bid={put_738.iloc[0]['bid']:.2f}, ask={put_738.iloc[0]['ask']:.2f}")
if not put_736.empty:
    print(f"Put 736 (买入): 现在bid={put_736.iloc[0]['bid']:.2f}, ask={put_736.iloc[0]['ask']:.2f}")

# Call侧
call_742 = calls[calls['strike'] == 742]
call_744 = calls[calls['strike'] == 744]
if not call_742.empty:
    print(f"Call 742 (卖出): 现在bid={call_742.iloc[0]['bid']:.2f}, ask={call_742.iloc[0]['ask']:.2f}")
if not call_744.empty:
    print(f"Call 744 (买入): 现在bid={call_744.iloc[0]['bid']:.2f}, ask={call_744.iloc[0]['ask']:.2f}")

# 估算当前平仓成本
if not put_738.empty and not put_736.empty and not call_742.empty and not call_744.empty:
    close_cost = (put_738.iloc[0]['ask'] - put_736.iloc[0]['bid'] + 
                  call_742.iloc[0]['ask'] - call_744.iloc[0]['bid'])
    current_pnl = 0.17 - close_cost
    pnl_pct = current_pnl / 0.17 * 100
    
    print(f"\n--- 盈亏估算 ---")
    print(f"当前平仓成本: ${close_cost:.2f}")
    print(f"当前盈亏: ${current_pnl:.2f} / 合约 ({pnl_pct:+.1f}%)")
    print(f"止盈目标: $0.10 (60%)")
    print(f"止损线: -$0.92 (50%亏损)")
    
    if current_pnl >= 0.10:
        print("\n✅ 已达到止盈目标！建议平仓")
    elif current_pnl <= -0.92:
        print("\n❌ 已触发止损！立即平仓")
    else:
        print("\n⏳ 继续持有...")
