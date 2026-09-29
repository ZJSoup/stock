#!/usr/bin/env python3
"""
测试IB Gateway连接和基本功能
"""

from ib_insync import *
import logging

logging.basicConfig(level=logging.INFO)

def test_connection():
    print("=" * 60)
    print("测试 IB Gateway 连接...")
    print("=" * 60)
    
    try:
        ib = IB()
        # 尝试连接 TWS (paper trading通常是7497, live是7496)
        # IB Gateway paper通常是4002, live是4001
        ports_to_try = [7497, 7496, 4002, 4001]
        
        for port in ports_to_try:
            try:
                print(f"尝试连接端口 {port}...")
                ib.connect('127.0.0.1', port, clientId=999, timeout=5)
                print(f"✓ 成功连接端口 {port}!")
                break
            except Exception as e:
                print(f"  端口 {port} 连接失败: {e}")
                continue
        else:
            print("✗ 所有端口都无法连接")
            print("\n请确保:")
            print("1. IB Gateway 或 TWS 正在运行")
            print("2. 已在设置中启用API (Settings -> API -> Enable Active X and Socket Clients)")
            print("3. 端口号正确 (TWS paper=7497, live=7496; IB Gateway paper=4002, live=4001)")
            return False
        
        # 获取账户信息
        print("\n" + "=" * 60)
        print("账户信息")
        print("=" * 60)
        
        account = ib.managedAccounts()[0]
        print(f"账户号: {account}")
        
        ib.accountSummary()
        for item in ib.accountSummary(account):
            if item.tag in ['NetLiquidation', 'AvailableFunds', 'DailyPnL', 'BuyingPower']:
                print(f"{item.tag}: ${float(item.value):,.2f}")
        
        # 获取SPY价格
        print("\n" + "=" * 60)
        print("SPY 数据测试")
        print("=" * 60)
        
        spy = Stock('SPY', 'SMART', 'USD')
        ib.qualifyContracts(spy)
        ticker = ib.reqTickers(spy)[0]
        print(f"SPY 当前价格: ${ticker.marketPrice():.2f}")
        print(f"Bid: ${ticker.bid:.2f}  Ask: ${ticker.ask:.2f}")
        
        # 获取期权链
        print("\n" + "=" * 60)
        print("期权链测试")
        print("=" * 60)
        
        chains = ib.reqSecDefOptParams(spy.symbol, '', spy.secType, spy.conId)
        chain = next(c for c in chains if c.exchange == 'SMART')
        
        print(f"到期日数量: {len(chain.expirations)}")
        print(f"行权价数量: {len(chain.strikes)}")
        print(f"最近5个到期日: {sorted(chain.expirations)[:5]}")
        
        # 找到今日到期的期权
        from datetime import datetime
        today = datetime.now().strftime('%Y%m%d')
        print(f"\n今日日期: {today}")
        
        if today in chain.expirations:
            print("✓ 找到今日到期的0DTE期权!")
            
            # 获取一个ATM期权
            atm_strike = round(ticker.marketPrice() * 2) / 2
            print(f"ATM 行权价: ${atm_strike}")
            
            # 获取Call价格
            call_opt = Option('SPY', today, atm_strike, 'C', 'SMART')
            ib.qualifyContracts(call_opt)
            call_ticker = ib.reqTickers(call_opt)[0]
            print(f"Call ${atm_strike}: Bid ${call_ticker.bid:.2f} / Ask ${call_ticker.ask:.2f}")
            
            # 获取Put价格
            put_opt = Option('SPY', today, atm_strike, 'P', 'SMART')
            ib.qualifyContracts(put_opt)
            put_ticker = ib.reqTickers(put_opt)[0]
            print(f"Put ${atm_strike}:  Bid ${put_ticker.bid:.2f} / Ask ${put_ticker.ask:.2f}")
        else:
            print("⚠ 今日没有到期的期权 (可能是周末或节假日)")
        
        # 检查当前持仓
        print("\n" + "=" * 60)
        print("当前持仓")
        print("=" * 60)
        
        positions = ib.positions()
        if positions:
            for pos in positions:
                print(f"{pos.contract.symbol} {pos.contract.right} {pos.contract.strike}: {pos.position} 股")
        else:
            print("(无持仓)")
        
        ib.disconnect()
        print("\n" + "=" * 60)
        print("✓ 所有测试完成!")
        print("=" * 60)
        return True
        
    except Exception as e:
        print(f"\n✗ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == '__main__':
    test_connection()
