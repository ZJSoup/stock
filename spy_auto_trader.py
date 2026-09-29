#!/usr/bin/env python3
"""
SPY 0DTE 期权自动化交易机器人
策略: Iron Condor (铁鹰策略)
"""

import asyncio
import logging
from datetime import datetime, time
import pytz
from ib_insync import *
import math

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/Users/zijunt/spy_trader.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# 交易配置
CONFIG = {
    'account': '',  # 留空自动获取
    'max_risk_per_trade_pct': 2.5,  # 单笔交易最大风险 %
    'daily_max_loss_pct': 5.0,      # 单日最大亏损 %
    'target_delta': 12,             # 卖出期权的Delta绝对值
    'spread_width': 2,              # 期权宽度（行权价间隔）
    'take_profit_pct': 60,          # 止盈百分比
    'stop_loss_pct': 50,            # 止损百分比
    'min_credit': 1.00,             # 最小权利金
    'max_credit': 2.00,             # 最大权利金
    'max_vix': 28,                  # 最大VIX阈值
    'close_before': time(14, 30),   # 14:30前必须平仓 (ET时间)
}

# 时区设置
NY_TZ = pytz.timezone('America/New_York')

def get_ny_time():
    """获取当前纽约时间"""
    return datetime.now(NY_TZ).time()

class SPY0DTETrader:
    def __init__(self, host='127.0.0.1', port=7497, client_id=1):
        self.ib = IB()
        self.host = host
        self.port = port
        self.client_id = client_id
        self.account_value = 0
        self.daily_pnl = 0
        self.positions = {}
        
    def connect(self):
        """连接IB Gateway"""
        try:
            self.ib.connect(self.host, self.port, clientId=self.client_id)
            logger.info("成功连接IB Gateway")
            
            # 获取账户信息
            account = self.ib.managedAccounts()[0]
            self.ib.accountSummary()
            for item in self.ib.accountSummary(account):
                if item.tag == 'NetLiquidation':
                    self.account_value = float(item.value)
                elif item.tag == 'DailyPnL':
                    self.daily_pnl = float(item.value)
            
            logger.info(f"账户净值: ${self.account_value:,.2f}")
            logger.info(f"当日盈亏: ${self.daily_pnl:,.2f}")
            return True
        except Exception as e:
            logger.error(f"连接失败: {e}")
            return False
    
    def get_spy_price(self):
        """获取SPY当前价格"""
        spy = Stock('SPY', 'SMART', 'USD')
        self.ib.qualifyContracts(spy)
        ticker = self.ib.reqTickers(spy)[0]
        price = ticker.marketPrice()
        logger.info(f"SPY 当前价格: ${price:.2f}")
        return price
    
    def get_vix_price(self):
        """获取VIX价格"""
        vix = Index('VIX', 'CBOE')
        self.ib.qualifyContracts(vix)
        ticker = self.ib.reqTickers(vix)[0]
        vix_price = ticker.marketPrice()
        logger.info(f"VIX 当前价格: {vix_price:.2f}")
        return vix_price
    
    def get_0dte_expiry(self):
        """获取今日到期的期权日期"""
        # 先用STK获取SPY的conId
        spy_stk = Stock('SPY', 'SMART', 'USD')
        self.ib.qualifyContracts(spy_stk)
        chains = self.ib.reqSecDefOptParams('SPY', '', 'STK', spy_stk.conId)
        chain = next(c for c in chains if c.exchange == 'SMART')
        
        # 找到最近的到期日 (今天或明天)
        today = datetime.now().date()
        for exp in sorted(chain.expirations):
            exp_date = datetime.strptime(exp, '%Y%m%d').date()
            if exp_date >= today:
                logger.info(f"0DTE 到期日: {exp}")
                return exp
        return None
    
    def get_option_chain(self, expiry):
        """获取期权链数据"""
        spy_stk = Stock('SPY', 'SMART', 'USD')
        self.ib.qualifyContracts(spy_stk)
        chains = self.ib.reqSecDefOptParams('SPY', '', 'STK', spy_stk.conId)
        chain = next(c for c in chains if c.exchange == 'SMART')
        return sorted(chain.strikes)
    
    def calculate_strikes(self, spy_price, strikes):
        """计算Iron Condor的行权价"""
        # 找到最接近SPY价格的行权价
        atm_idx = min(range(len(strikes)), key=lambda i: abs(strikes[i] - spy_price))
        atm_strike = strikes[atm_idx]
        logger.info(f"ATM 行权价: ${atm_strike}")
        
        # 计算Call侧: 找到target_delta的Call
        call_strike_sell = None
        put_strike_sell = None
        
        # 简化版: 基于SPY价格位置选择行权价
        # 实际应该用真实Delta，这里用价格距离估算
        est_price_per_delta = spy_price * 0.003  # 估算每个Delta约0.3%价格
        
        # Call侧: 高于当前价格
        call_offset = int(CONFIG['target_delta'] * est_price_per_delta)
        call_strike_sell = atm_strike + (math.ceil(call_offset / 0.5) * 0.5)
        call_strike_buy = call_strike_sell + CONFIG['spread_width']
        
        # Put侧: 低于当前价格
        put_strike_sell = atm_strike - (math.ceil(call_offset / 0.5) * 0.5)
        put_strike_buy = put_strike_sell - CONFIG['spread_width']
        
        # 确保行权价在期权链中
        call_strike_sell = min(strikes, key=lambda x: abs(x - call_strike_sell))
        call_strike_buy = min(strikes, key=lambda x: abs(x - call_strike_buy))
        put_strike_sell = min(strikes, key=lambda x: abs(x - put_strike_sell))
        put_strike_buy = min(strikes, key=lambda x: abs(x - put_strike_buy))
        
        logger.info(f"Call Spread: 卖出 ${call_strike_sell} / 买入 ${call_strike_buy}")
        logger.info(f"Put Spread:  卖出 ${put_strike_sell} / 买入 ${put_strike_buy}")
        
        return {
            'call_sell': call_strike_sell,
            'call_buy': call_strike_buy,
            'put_sell': put_strike_sell,
            'put_buy': put_strike_buy
        }
    
    def get_option_price(self, expiry, strike, right):
        """获取期权价格"""
        opt = Option('SPY', expiry, strike, right, 'SMART')
        self.ib.qualifyContracts(opt)
        ticker = self.ib.reqTickers(opt)[0]
        bid = ticker.bid
        ask = ticker.ask
        mid = (bid + ask) / 2 if bid and ask else ticker.marketPrice()
        return {'bid': bid, 'ask': ask, 'mid': mid, 'contract': opt}
    
    def calculate_position_size(self, max_risk):
        """计算仓位大小"""
        # Iron Condor的最大风险 = 宽度 - 权利金收入
        # 简化估算: 每个合约最大风险约 $100
        risk_per_contract = 100.0
        contracts = int(max_risk / risk_per_contract)
        return max(1, contracts)
    
    def place_iron_condor(self, expiry, strikes):
        """下单Iron Condor"""
        # 获取各期权价格
        call_sell = self.get_option_price(expiry, strikes['call_sell'], 'C')
        call_buy = self.get_option_price(expiry, strikes['call_buy'], 'C')
        put_sell = self.get_option_price(expiry, strikes['put_sell'], 'P')
        put_buy = self.get_option_price(expiry, strikes['put_buy'], 'P')
        
        # 计算净权利金
        credit_received = (call_sell['bid'] + put_sell['bid']) - (call_buy['ask'] + put_buy['ask'])
        max_risk = (CONFIG['spread_width'] * 2) - credit_received
        
        logger.info(f"预期净权利金: ${credit_received:.2f}")
        logger.info(f"每股最大风险: ${max_risk:.2f}")
        
        # 检查权利金范围
        if credit_received < CONFIG['min_credit'] or credit_received > CONFIG['max_credit']:
            logger.warning(f"权利金 ${credit_received:.2f} 不在目标范围 ${CONFIG['min_credit']}-${CONFIG['max_credit']}")
            return None
        
        # 计算仓位大小
        max_risk_amount = self.account_value * (CONFIG['max_risk_per_trade_pct'] / 100)
        contracts = self.calculate_position_size(max_risk_amount)
        
        logger.info(f"仓位大小: {contracts} 个合约")
        logger.info(f"总最大风险: ${contracts * max_risk * 100:.2f}")
        
        # 检查当日风险
        if contracts * max_risk * 100 + abs(self.daily_pnl) > self.account_value * (CONFIG['daily_max_loss_pct'] / 100):
            logger.warning("当日风险超过限制，取消交易")
            return None
        
        # 下单 (四腿定单)
        orders = []
        
        # 卖出Call Credit Spread
        call_spread_order = MarketOrder('SELL', contracts)
        call_spread_order.transmit = False
        call_sell_trade = self.ib.placeOrder(call_sell['contract'], call_spread_order)
        
        buy_call_order = MarketOrder('BUY', contracts)
        buy_call_order.transmit = False
        call_buy_trade = self.ib.placeOrder(call_buy['contract'], buy_call_order)
        
        # 卖出Put Credit Spread
        put_spread_order = MarketOrder('SELL', contracts)
        put_spread_order.transmit = False
        put_sell_trade = self.ib.placeOrder(put_sell['contract'], put_spread_order)
        
        buy_put_order = MarketOrder('BUY', contracts)
        buy_put_order.transmit = True
        put_buy_trade = self.ib.placeOrder(put_buy['contract'], buy_put_order)
        
        logger.info("Iron Condor订单已提交!")
        
        # 记录持仓
        self.positions['iron_condor'] = {
            'contracts': contracts,
            'strikes': strikes,
            'credit': credit_received,
            'entry_time': datetime.now(),
            'call_sell': call_sell,
            'call_buy': call_buy,
            'put_sell': put_sell,
            'put_buy': put_buy
        }
        
        return self.positions['iron_condor']
    
    def check_and_close_positions(self):
        """检查并平仓"""
        if 'iron_condor' not in self.positions:
            return
        
        pos = self.positions['iron_condor']
        
        # 获取当前价格
        call_sell = self.get_option_price(pos['call_sell']['contract'].lastTradeDateOrContractMonth,
                                         pos['call_sell']['contract'].strike, 'C')
        call_buy = self.get_option_price(pos['call_buy']['contract'].lastTradeDateOrContractMonth,
                                        pos['call_buy']['contract'].strike, 'C')
        put_sell = self.get_option_price(pos['put_sell']['contract'].lastTradeDateOrContractMonth,
                                        pos['put_sell']['contract'].strike, 'P')
        put_buy = self.get_option_price(pos['put_buy']['contract'].lastTradeDateOrContractMonth,
                                       pos['put_buy']['contract'].strike, 'P')
        
        # 计算当前盈亏
        current_debit = (call_sell['ask'] + put_sell['ask']) - (call_buy['bid'] + put_buy['bid'])
        pnl = (pos['credit'] - current_debit) * pos['contracts'] * 100
        pnl_pct = (pnl / (pos['credit'] * 100 * pos['contracts'])) * 100
        
        logger.info(f"当前盈亏: ${pnl:.2f} ({pnl_pct:.1f}%)")
        
        # 止盈检查
        if pnl_pct >= CONFIG['take_profit_pct']:
            logger.info(f"达到止盈目标 {CONFIG['take_profit_pct']}%，平仓!")
            self.close_all_positions()
            return True
        
        # 止损检查
        if pnl_pct <= -CONFIG['stop_loss_pct']:
            logger.warning(f"达到止损 {CONFIG['stop_loss_pct']}%，平仓!")
            self.close_all_positions()
            return True
        
        # 时间检查 (使用纽约时间)
        now = get_ny_time()
        if now >= CONFIG['close_before']:
            logger.info(f"达到平仓时间 {CONFIG['close_before'].strftime('%H:%M')} (NY)，强制平仓!")
            self.close_all_positions()
            return True
        
        return False
    
    def close_all_positions(self):
        """平仓所有持仓"""
        for pos in self.ib.positions():
            if 'SPY' in pos.contract.symbol:
                action = 'BUY' if pos.position < 0 else 'SELL'
                qty = abs(pos.position)
                order = MarketOrder(action, qty)
                self.ib.placeOrder(pos.contract, order)
                logger.info(f"平仓 {pos.contract.symbol} {pos.contract.right} {pos.contract.strike}")
        
        if 'iron_condor' in self.positions:
            del self.positions['iron_condor']
    
    def run_trading_cycle(self):
        """执行交易循环"""
        logger.info("=" * 50)
        logger.info("开始交易循环")
        
        # 1. 市场检查
        vix = self.get_vix_price()
        if vix > CONFIG['max_vix']:
            logger.warning(f"VIX ({vix}) 超过阈值 {CONFIG['max_vix']}，不交易")
            return
        
        # 2. 检查是否已有持仓
        if 'iron_condor' in self.positions:
            self.check_and_close_positions()
            return
        
        # 3. 检查交易时间 (只在9:30-13:00 ET开仓, 使用纽约时间)
        now = get_ny_time()
        if not (time(9, 30) <= now <= time(13, 0)):
            logger.info(f"不在开仓时间窗口. 当前纽约时间: {now.strftime('%H:%M:%S')}")
            return
        
        # 4. 获取数据
        spy_price = self.get_spy_price()
        expiry = self.get_0dte_expiry()
        if not expiry:
            logger.error("找不到0DTE到期日")
            return
        
        strikes = self.get_option_chain(expiry)
        
        # 5. 计算行权价
        ic_strikes = self.calculate_strikes(spy_price, strikes)
        
        # 6. 下单
        self.place_iron_condor(expiry, ic_strikes)
        
        logger.info("交易循环完成")
        logger.info("=" * 50)
    
    def disconnect(self):
        """断开连接"""
        self.ib.disconnect()
        logger.info("已断开IB连接")

async def main():
    trader = SPY0DTETrader()
    
    if not trader.connect():
        return
    
    try:
        while True:
            trader.run_trading_cycle()
            await asyncio.sleep(60)  # 每分钟检查一次
    except KeyboardInterrupt:
        logger.info("用户中断")
    finally:
        trader.disconnect()

if __name__ == '__main__':
    asyncio.run(main())
