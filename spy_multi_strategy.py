#!/usr/bin/env python3
"""
SPY 0DTE 期权自动化交易机器人 - v2（paper trading 阶段）

v2 改动（基于 60 天 5 分钟回测 + 3 年日线回测）：
- STEADY 禁用 Iron Condor 和 Credit Spread（回测里都是拖累）
- STEADY 只做顺势 ATM 买方：TP80% / SL30%（SL 从 40 收紧到 30）
- TURBO TP 从 200 提到 250（让大趋势跑），SL65% 不变
- 两个模式都加 20MA 日线趋势过滤：只做顺势单
  * 日线 uptrend → 只做 call
  * 日线 downtrend → 只做 put
  * 日线 choppy → 双向都可以，但 steady 信心门槛提到 80

两种模式（--mode）：

STEADY（默认，3个月翻倍路径，v2 long-only）：
- VIX 12-30、日线顺势、信心 ≥ 75%（震荡 ≥ 80%） → 买 ATM Call/Put
- TP 80% / SL 30%、3% 账户/笔、日最多 2 笔、连亏 3 停、日亏 10% 停
- 回测：60 天 PF 1.01（+1.2%），3 年 PF 2.10（+3427%）

TURBO（--mode turbo，周翻倍目标，高风险）：
- VIX 10-35、日线顺势、信心 ≥ 55% → 买 18Δ OTM Call/Put
- TP 250% / SL 65%、10% sleeve/笔、日最多 5 笔、连亏 4 停
- 回测：60 天 PF 1.01（+15%），3 年 PF 2.28（+11131%，上限 20 张/笔）
- ⚠️ 35-50% 回撤是真实的，账户要扛得住

50/50 分仓同时跑两个模式：
  终端1: python3 spy_multi_strategy.py --mode steady --capital-pct 50 --client-id 2
  终端2: python3 spy_multi_strategy.py --mode turbo  --capital-pct 50 --client-id 3

通用过滤：
- VIX 期限结构 backwardation → 不交易
- FOMC/CPI/NFP 事件日 → 不交易
- 默认 paper (port 4002)，实盘需 --live + --port 4001
- 15:00/15:15 ET 前强平
"""

import argparse
import asyncio
import csv
import logging
import os
from datetime import datetime, time, date
from pathlib import Path
import pytz
from ib_insync import *
import math
from enum import Enum
import yfinance as yf
import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Macro event calendar
# ---------------------------------------------------------------------------
# 0DTE 短波动策略在 FOMC / CPI 当天风险陡增：新闻公布后 SPX 常常出现
# 3-5x 平日波动。回测和主流交易者的共识是：**这些日子不做**。
#
# 只维护 YYYY-MM-DD 列表；当天完全跳过（不管什么时段）。日期覆盖不全没关系，
# 缺失只会让机器人多做一些原本要过滤掉的日子——你观察 CSV 后可以往里加。
FOMC_DAYS_2026 = {
    '2026-01-28', '2026-01-29',
    '2026-03-17', '2026-03-18',
    '2026-04-28', '2026-04-29',
    '2026-06-16', '2026-06-17',
    '2026-07-28', '2026-07-29',
    '2026-09-15', '2026-09-16',
    '2026-10-27', '2026-10-28',
    '2026-12-15', '2026-12-16',
}
# CPI release: 通常每月第二或第三个 Tuesday 08:30 ET
CPI_DAYS_2026 = {
    '2026-01-13', '2026-02-11', '2026-03-11', '2026-04-14',
    '2026-05-13', '2026-06-11', '2026-07-15', '2026-08-12',
    '2026-09-11', '2026-10-15', '2026-11-13', '2026-12-10',
}
# NFP (Non-Farm Payrolls): 每月第一个 Friday 08:30 ET
NFP_DAYS_2026 = {
    '2026-01-02', '2026-02-06', '2026-03-06', '2026-04-03',
    '2026-05-01', '2026-06-05', '2026-07-03', '2026-08-07',
    '2026-09-04', '2026-10-02', '2026-11-06', '2026-12-04',
}
NO_TRADE_DAYS = FOMC_DAYS_2026 | CPI_DAYS_2026 | NFP_DAYS_2026

def is_no_trade_day(now_ny=None):
    """今天是不是 FOMC / CPI / NFP 之类的事件日"""
    if now_ny is None:
        now_ny = datetime.now(pytz.timezone('America/New_York'))
    return now_ny.strftime('%Y-%m-%d') in NO_TRADE_DAYS

# 趋势判断枚举
class Trend(Enum):
    BULLISH = "看涨"
    BEARISH = "看跌"
    SIDEWAYS = "震荡"

# 市场分析模块
class MarketAnalyzer:
    """SPY实时市场分析模块"""

    def __init__(self):
        self.ticker = yf.Ticker("SPY")

    def get_vix_term_structure(self):
        """返回 (vix, vix9d, contango)。
        contango=True 表示 vix9d < vix，市场平静；
        contango=False (backwardation) 表示短端 > 长端，市场紧张 —— 卖 gamma 危险。
        """
        try:
            vix = yf.Ticker('^VIX').fast_info['last_price']
            vix9d = yf.Ticker('^VIX9D').fast_info['last_price']
            if vix and vix9d and not math.isnan(vix) and not math.isnan(vix9d):
                # 允许 2% 的容差，避免误判
                contango = vix9d < vix * 1.02
                return float(vix), float(vix9d), contango
        except Exception as e:
            logging.warning(f"取 VIX 期限结构失败: {e}")
        return None, None, None

    def get_5min_data(self):
        """获取最近5分钟K线数据"""
        try:
            df = self.ticker.history(period='1d', interval='5m')
            if len(df) < 20:
                return None
            return df.tail(48)  # 最近4小时数据
        except Exception as e:
            logger.warning(f"获取5分钟数据失败: {e}")
            return None

    def get_daily_trend(self):
        """v2: 用【前一交易日】的收盘 vs 20MA 判断日线趋势。
        返回 'uptrend' / 'downtrend' / 'choppy' / 'unknown'。
        10:00 做决策时只能看到前一天的数据，避免 look-ahead bias。
        """
        try:
            df = self.ticker.history(period='60d', interval='1d')
            if len(df) < 21:
                return 'unknown'
            df['ma20'] = df['Close'].rolling(20).mean()
            prev = df.iloc[-2]  # 前一交易日
            if pd.isna(prev['ma20']):
                return 'unknown'
            if prev['Close'] > prev['ma20'] * 1.005:
                return 'uptrend'
            if prev['Close'] < prev['ma20'] * 0.995:
                return 'downtrend'
            return 'choppy'
        except Exception as e:
            logger.warning(f"获取日线趋势失败: {e}")
            return 'unknown'
    
    def calculate_rsi(self, prices, period=6):
        """计算RSI指标"""
        deltas = np.diff(prices)
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)
        
        avg_gain = np.mean(gains[-period:])
        avg_loss = np.mean(losses[-period:])
        
        if avg_loss == 0:
            return 100
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))
    
    def calculate_vwap(self, df):
        """计算VWAP (当日加权平均价)"""
        if len(df) < 2:
            return None
        typical_price = (df['High'] + df['Low'] + df['Close']) / 3
        vwap = (typical_price * df['Volume']).cumsum() / df['Volume'].cumsum()
        return vwap.iloc[-1]
    
    def get_gap(self):
        """计算开盘缺口"""
        try:
            today = self.ticker.history(period='2d')
            if len(today) < 2:
                return 0
            prev_close = today['Close'].iloc[-2]
            open_price = today['Open'].iloc[-1]
            gap_pct = (open_price - prev_close) / prev_close * 100
            return gap_pct
        except:
            return 0
    
    def get_trend(self):
        """综合判断当前趋势
        返回: (趋势, 信心指数%, 详细信息字典)
        """
        df = self.get_5min_data()
        if df is None or len(df) < 10:
            return Trend.SIDEWAYS, 50, {'error': '数据不足'}
        
        current_price = df['Close'].iloc[-1]
        
        # 1. RSI判断
        rsi = self.calculate_rsi(df['Close'].values, period=6)
        
        # 2. VWAP判断
        vwap = self.calculate_vwap(df)
        
        # 3. 缺口判断
        gap = self.get_gap()
        
        # 4. 短期均线 (5根K线 vs 10根K线)
        ma5 = df['Close'].rolling(5).mean().iloc[-1]
        ma10 = df['Close'].rolling(10).mean().iloc[-1]
        
        # 5. 最近5根K线涨跌
        last5_change = (df['Close'].iloc[-1] - df['Close'].iloc[-6]) / df['Close'].iloc[-6] * 100
        
        # 6. 成交量趋势
        vol_ma5 = df['Volume'].rolling(5).mean().iloc[-1]
        vol_ma10 = df['Volume'].rolling(10).mean().iloc[-1]
        vol_increasing = vol_ma5 > vol_ma10 * 1.2
        
        # 计分系统
        bullish_score = 0
        bearish_score = 0
        
        # RSI打分
        if rsi < 30:
            bullish_score += 2  # 超卖看涨
        elif rsi > 70:
            bearish_score += 2  # 超买看跌
        
        # VWAP打分
        if vwap and current_price > vwap * 1.001:
            bullish_score += 2
        elif vwap and current_price < vwap * 0.999:
            bearish_score += 2
        
        # 均线多头排列
        if ma5 > ma10 * 1.001:
            bullish_score += 2
        elif ma5 < ma10 * 0.999:
            bearish_score += 2
        
        # 最近5根K线
        if last5_change > 0.3:
            bullish_score += 1
        elif last5_change < -0.3:
            bearish_score += 1
        
        # 缺口回补判断
        if abs(gap) > 0.5:
            if gap > 0:
                bearish_score += 1  # 高开大概率回补
                logger.info(f"⚠ 高开缺口 {gap:.2f}%，注意回补风险")
            else:
                bullish_score += 1  # 低开大概率回补
                logger.info(f"⚠ 低开缺口 {gap:.2f}%，注意回补机会")
        
        # 成交量确认
        if vol_increasing:
            if bullish_score > bearish_score:
                bullish_score += 1
            elif bearish_score > bullish_score:
                bearish_score += 1
        
        # 计算总分和信心
        total = bullish_score + bearish_score
        if total == 0:
            confidence = 50
        else:
            confidence = int(50 + 30 * abs(bullish_score - bearish_score) / max(total, 1))
        
        # 趋势判断
        if bullish_score - bearish_score >= 2:
            trend = Trend.BULLISH
        elif bearish_score - bullish_score >= 2:
            trend = Trend.BEARISH
        else:
            trend = Trend.SIDEWAYS
        
        details = {
            'rsi': round(rsi, 1),
            'vwap': round(vwap, 2) if vwap else None,
            'current_price': round(current_price, 2),
            'gap_pct': round(gap, 2),
            'ma5': round(ma5, 2),
            'ma10': round(ma10, 2),
            'last5_change_pct': round(last5_change, 2),
            'bullish_score': bullish_score,
            'bearish_score': bearish_score,
            'vol_increasing': vol_increasing,
        }
        
        return trend, confidence, details

# 策略枚举
class Strategy(Enum):
    IRON_CONDOR = "iron_condor"
    BUTTERFLY = "butterfly"
    STRANGLE = "strangle"
    DIRECTIONAL_PUT_SPREAD = "directional_put_spread"
    DIRECTIONAL_CALL_SPREAD = "directional_call_spread"
    DIRECTIONAL_LONG_CALL = "directional_long_call"
    DIRECTIONAL_LONG_PUT = "directional_long_put"
    TURBO_LONG_CALL = "turbo_long_call"
    TURBO_LONG_PUT = "turbo_long_put"
    NO_TRADE = "no_trade"

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

# ---------------------------------------------------------------------------
# 两套配置：steady（3个月翻倍）和 turbo（周翻倍，高风险）
# ---------------------------------------------------------------------------

CONFIG_STEADY = {
    'account': '',
    'max_risk_per_trade_pct': 5.0,
    'daily_max_loss_pct': 10.0,
    'entry_window_start': time(10, 0),
    'entry_window_end': time(14, 0),
    'close_before': time(15, 0),
    'long_entry_window_start': time(10, 30),
    'long_entry_window_end': time(13, 30),
    'max_daily_trades': 2,
    'consecutive_loss_limit': 3,
    'stop_flag_path': str(Path.home() / '.spy_bot_STOP'),
    'trade_log_csv': str(Path.home() / 'spy_trades.csv'),
    'mode_name': 'steady',
}

CONFIG_TURBO = {
    'account': '',
    'max_risk_per_trade_pct': 10.0,      # 每笔风险 sleeve 的 10%
    'daily_max_loss_pct': 25.0,           # 日亏 25% 停
    'entry_window_start': time(10, 0),
    'entry_window_end': time(14, 30),     # 更晚入场截止（多做几笔）
    'close_before': time(15, 15),         # 尾盘前 45 分钟平
    'long_entry_window_start': time(10, 0),
    'long_entry_window_end': time(14, 30),
    'max_daily_trades': 5,                # 每天最多 5 笔
    'consecutive_loss_limit': 4,          # 连亏 4 笔停
    'stop_flag_path': str(Path.home() / '.spy_bot_STOP'),
    'trade_log_csv': str(Path.home() / 'spy_trades_turbo.csv'),
    'mode_name': 'turbo',
}

# 向后兼容：默认 steady
CONFIG = CONFIG_STEADY

# 时区设置
NY_TZ = pytz.timezone('America/New_York')

def get_ny_time():
    """获取当前纽约时间"""
    return datetime.now(NY_TZ).time()

# 各策略参数
# Paper 阶段只启用 Iron Condor（震荡）和方向性 Credit Spread（趋势明确）。
# Butterfly 和裸 Strangle 风险收益结构不好，暂不启用；等 paper 数据足够再放开。
STRATEGY_PARAMS = {
    Strategy.IRON_CONDOR: {
        # 主流 16Δ 短腿 / $5 spread width（更宽的翼更能抗极端波动）
        'target_delta': 16,
        'spread_width': 5,
        'take_profit_pct': 40,       # 40% 锁利（数学期望更优）
        'stop_loss_pct': 120,        # 1.2x credit 止损（不等大亏才走）
        'min_credit': 0.40,
        'max_credit': 3.00,
        'min_vix': 12,
        'max_vix': 30,
    },
    # Butterfly 暂停：debit spread，需要精准回归，paper 阶段不玩
    # Strategy.BUTTERFLY: { ... },
    # Strangle 暂停：裸卖无保护翼，不符合 defined-risk 原则
    # Strategy.STRANGLE: { ... },
    Strategy.DIRECTIONAL_PUT_SPREAD: {
        # 看涨时卖 30Δ OTM Put、买更 OTM Put（$3 翼）
        'target_delta': 30,
        'spread_width': 3,
        'take_profit_pct': 40,
        'stop_loss_pct': 120,
        'min_credit': 0.30,
        'max_credit': 2.50,
    },
    Strategy.DIRECTIONAL_CALL_SPREAD: {
        'target_delta': 30,
        'spread_width': 3,
        'take_profit_pct': 40,
        'stop_loss_pct': 120,
        'min_credit': 0.30,
        'max_credit': 2.50,
    },
    # v2: 方向买方，TP80/SL30（回测显示 SL30 比 SL40 更好，让利润跑+快砍亏）
    # 60 天 5 分钟回测 PF 1.01（唯一正 EV 组合），3 年日线 PF 2.10
    Strategy.DIRECTIONAL_LONG_CALL: {
        'target_delta': 50,          # ATM
        'take_profit_pct': 80,       # +80% 走人
        'stop_loss_pct': 30,         # v2: -30% 砍（比 -40% 更紧）
        'min_debit': 0.30,
        'max_debit': 5.00,
        'risk_pct_of_account': 3.0,
    },
    Strategy.DIRECTIONAL_LONG_PUT: {
        'target_delta': 50,
        'take_profit_pct': 80,
        'stop_loss_pct': 30,
        'min_debit': 0.30,
        'max_debit': 5.00,
        'risk_pct_of_account': 3.0,
    },
}

# Turbo 模式：只做方向买方，18Δ OTM，高赔率
STRATEGY_PARAMS_TURBO = {
    Strategy.TURBO_LONG_CALL: {
        'target_delta': 18,          # 18Δ OTM
        'take_profit_pct': 250,      # v2: +250% 让利润跑（回测最优）
        'stop_loss_pct': 65,         # -65% 快砍
        'min_debit': 0.15,
        'max_debit': 2.50,
        'risk_pct_of_account': 10.0,
    },
    Strategy.TURBO_LONG_PUT: {
        'target_delta': 18,
        'take_profit_pct': 250,
        'stop_loss_pct': 65,
        'min_debit': 0.15,
        'max_debit': 2.50,
        'risk_pct_of_account': 10.0,
    },
}

class SPYMultiStrategyTrader:
    def __init__(self, host='127.0.0.1', port=4002, client_id=2, live=False,
                 mode='steady', capital_pct=100):
        """
        host/port: IB 连接目标。默认 4002 = IB Gateway paper。
        live=True 会切到 4001 (Gateway 实盘) 并要求端口显式传入才敢下单。
        mode: 'steady'（3个月翻倍路径）或 'turbo'（周翻倍，高风险）。
        capital_pct: 使用账户资金的百分比（50/50 分仓时各传 50）。
        """
        self.ib = IB()
        self.host = host
        self.port = port
        self.client_id = client_id
        self.live = live
        self.mode = mode
        self.capital_pct = capital_pct
        # paper 端口白名单，任何不在里面的组合都视作实盘
        self.is_paper = (port in (4002, 7497)) and not live

        # 根据模式选择配置和策略参数
        if mode == 'turbo':
            self.config = CONFIG_TURBO
            self.strategy_params = STRATEGY_PARAMS_TURBO
        else:
            self.config = CONFIG_STEADY
            self.strategy_params = STRATEGY_PARAMS

        self.account_value = 0
        self.working_capital = 0   # account_value × capital_pct%
        self.daily_pnl = 0
        self.current_position = None
        self.current_strategy = None
        self.analyzer = MarketAnalyzer()

        # 会话内 kill-switch 状态
        self.trades_today = 0
        self.consecutive_losses = 0
        self.session_date = None   # date 对象；跨日重置
        self.halted_reason = None  # 停机原因

        # yfinance 期权链缓存（按到期日），paper 账户拿不到 IB 期权报价时的 fallback
        self._yf_chain_cache: dict = {}   # expiry_str -> {'calls': df, 'puts': df}
        self._yf_chain_ttl: dict = {}     # expiry_str -> fetch_time
        self._yf_ticker = yf.Ticker('SPY')

    def connect(self):
        """连接IB Gateway"""
        try:
            self.ib.connect(self.host, self.port, clientId=self.client_id, timeout=None)
            # 使用延迟数据 (3 = delayed, 1 = live)
            self.ib.reqMarketDataType(3)

            # 明显 banner 提示 paper vs 实盘 —— 保证你启动时看得见
            if self.is_paper:
                logger.info("💤 PAPER TRADING (port=%d) —— 不动真钱", self.port)
            else:
                logger.warning("🔴 LIVE TRADING (port=%d) —— 会下真单!!!", self.port)

            account = self.ib.managedAccounts()[0]
            self.ib.accountSummary()
            for item in self.ib.accountSummary(account):
                if item.tag == 'NetLiquidation':
                    self.account_value = float(item.value)
                elif item.tag == 'DailyPnL':
                    self.daily_pnl = float(item.value)

            self.working_capital = self.account_value * (self.capital_pct / 100)
            logger.info(f"账户: {account}, 净值: ${self.account_value:,.2f}")
            logger.info(f"当日盈亏: ${self.daily_pnl:,.2f}")
            logger.info(
                f"🎮 模式: {self.mode.upper()} | "
                f"资金: {self.capital_pct:.0f}% (${self.working_capital:,.2f} "
                f"of ${self.account_value:,.2f})"
            )
            return True
        except Exception as e:
            logger.error(f"连接失败: {e}")
            return False

    # ------------------------------------------------------------------
    # Kill switch / 会话状态
    # ------------------------------------------------------------------

    def _reset_daily_state_if_new_day(self):
        """跨日重置计数器"""
        today = datetime.now(NY_TZ).date()
        if self.session_date != today:
            if self.session_date is not None:
                logger.info(f"新交易日 {today}，重置计数器")
            self.session_date = today
            self.trades_today = 0
            self.consecutive_losses = 0
            self.halted_reason = None

    def _check_kill_switches(self):
        """所有 kill switch 集中检查。返回 (可否交易, 原因)。
        触发过一次的 halted_reason 会一直保留，本交易日不会再交易。
        """
        self._reset_daily_state_if_new_day()

        if self.halted_reason:
            return False, self.halted_reason

        # STOP.flag 文件：你在 terminal 敲 `touch ~/.spy_bot_STOP` 就立刻停
        if os.path.exists(self.config['stop_flag_path']):
            self.halted_reason = f"外部 STOP flag 存在 ({self.config['stop_flag_path']})"
            return False, self.halted_reason

        # 事件日：FOMC/CPI/NFP
        if is_no_trade_day():
            self.halted_reason = "今天是 FOMC/CPI/NFP 事件日，跳过"
            return False, self.halted_reason

        # 日内交易次数上限
        if self.trades_today >= self.config['max_daily_trades']:
            self.halted_reason = f"日内交易已达 {self.config['max_daily_trades']} 笔上限"
            return False, self.halted_reason

        # 连亏上限
        if self.consecutive_losses >= self.config['consecutive_loss_limit']:
            self.halted_reason = f"连亏 {self.consecutive_losses} 次，今日停"
            return False, self.halted_reason

        # 日亏损上限（用 IB DailyPnL）
        max_daily_loss = self.working_capital * (self.config['daily_max_loss_pct'] / 100)
        if self.daily_pnl < -max_daily_loss:
            self.halted_reason = f"日亏损 ${self.daily_pnl:.0f} 超过 {self.config['daily_max_loss_pct']}% 上限"
            return False, self.halted_reason

        return True, None

    # ------------------------------------------------------------------
    # Trade log CSV
    # ------------------------------------------------------------------

    def _log_trade(self, event, position, pnl=None, pnl_pct=None, extra=None):
        """把每笔关键事件写进 CSV，便于赛后统计胜率/盈亏比。
        event: 'open' | 'close_tp' | 'close_sl' | 'close_time' | 'skip'
        """
        csv_path = self.config['trade_log_csv']
        header = ['timestamp_ny', 'event', 'strategy', 'contracts',
                  'credit_debit', 'is_credit', 'strikes',
                  'pnl_dollar', 'pnl_pct', 'extra']
        write_header = not os.path.exists(csv_path)
        row = {
            'timestamp_ny': datetime.now(NY_TZ).isoformat(timespec='seconds'),
            'event': event,
            'strategy': position['strategy'].value if position else '',
            'contracts': position['contracts'] if position else 0,
            'credit_debit': position['credit_debit'] if position else 0,
            'is_credit': position['is_credit'] if position else '',
            'strikes': str(position['strikes']) if position else '',
            'pnl_dollar': f"{pnl:.2f}" if pnl is not None else '',
            'pnl_pct': f"{pnl_pct:.1f}" if pnl_pct is not None else '',
            'extra': extra or '',
        }
        try:
            with open(csv_path, 'a', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=header)
                if write_header:
                    writer.writeheader()
                writer.writerow(row)
        except Exception as e:
            logger.warning(f"写 trade log 失败: {e}")
    
    def get_spy_price(self):
        """获取SPY当前价格 (优先用Yahoo Finance实时数据，避免IB延迟)"""
        try:
            # 先用Yahoo Finance获取实时价格
            spy = yf.Ticker("SPY")
            price = spy.fast_info['last_price']
            if price and not math.isnan(price):
                logger.info(f"SPY 当前价格 (Yahoo): ${price:.2f}")
                return price
        except Exception as e:
            logger.warning(f"Yahoo Finance获取失败: {e}")
        
        # Fallback到IB
        spy = Stock('SPY', 'SMART', 'USD')
        self.ib.qualifyContracts(spy)
        ticker = self.ib.reqTickers(spy)[0]
        price = ticker.marketPrice()
        logger.info(f"SPY 当前价格 (IB): ${price:.2f}")
        return price
    
    def get_vix_price(self):
        """获取VIX价格 (优先用Yahoo Finance)"""
        try:
            vix = yf.Ticker("^VIX")
            price = vix.fast_info['last_price']
            if price and not math.isnan(price):
                logger.info(f"VIX 当前价格 (Yahoo): {price:.2f}")
                return price
        except Exception as e:
            logger.warning(f"Yahoo Finance获取VIX失败: {e}")
        
        # Fallback到IB
        vix = Index('VIX', 'CBOE')
        self.ib.qualifyContracts(vix)
        ticker = self.ib.reqTickers(vix)[0]
        price = ticker.marketPrice()
        logger.info(f"VIX 当前价格 (IB): {price:.2f}")
        return price
    
    def select_strategy(self, vix):
        """v2: 只做顺势 ATM 买方，禁用 IC 和 credit spread。
        日线趋势过滤：只在日内方向与 20MA 趋势一致时做单。
        返回: (策略, 趋势, 信心指数, 详情)
        """
        trend, confidence, details = self.analyzer.get_trend()

        vix_full, vix9d, contango = self.analyzer.get_vix_term_structure()
        if contango is False:
            logger.warning(
                f"VIX backwardation (VIX9D={vix9d:.2f} > VIX={vix_full:.2f})，"
                "市场紧张，不做"
            )
            details['term_structure'] = 'backwardation'
            return Strategy.NO_TRADE, trend, confidence, details
        elif contango is True:
            details['term_structure'] = 'contango'
            details['vix9d'] = vix9d

        # v2: 日线趋势过滤（前一日收盘 vs 20MA）
        daily_trend = self.analyzer.get_daily_trend()
        details['daily_trend'] = daily_trend

        logger.info("=" * 40)
        logger.info(f"📊 v2 市场分析:")
        logger.info(f"   日内趋势: {trend.value}  信心: {confidence}%")
        logger.info(f"   日线趋势: {daily_trend} (20MA)")
        if 'rsi' in details:
            logger.info(f"   RSI(6): {details['rsi']}")
        if 'gap_pct' in details:
            logger.info(f"   开盘缺口: {details['gap_pct']}%")
        if 'vix9d' in details:
            logger.info(f"   VIX/VIX9D: {vix_full:.2f} / {details['vix9d']:.2f} (contango)")
        logger.info("=" * 40)

        if vix > 30:
            logger.info(f"VIX={vix:.2f} > 30，不交易")
            return Strategy.NO_TRADE, trend, confidence, details
        if vix < 12:
            logger.info(f"VIX={vix:.2f} < 12，不交易")
            return Strategy.NO_TRADE, trend, confidence, details

        # v2: 顺势 ATM 买方。日线上涨只做 call，日线下跌只做 put，
        # 震荡日允许两个方向（但要求更高信心）
        need_conf = 75 if daily_trend in ('uptrend', 'downtrend') else 80

        if confidence >= need_conf:
            if trend == Trend.BULLISH and daily_trend != 'downtrend':
                logger.info(f"✅ 顺势看涨 (信心{confidence}%, 日线{daily_trend}) → Long ATM Call")
                return Strategy.DIRECTIONAL_LONG_CALL, trend, confidence, details
            if trend == Trend.BEARISH and daily_trend != 'uptrend':
                logger.info(f"✅ 顺势看跌 (信心{confidence}%, 日线{daily_trend}) → Long ATM Put")
                return Strategy.DIRECTIONAL_LONG_PUT, trend, confidence, details
            logger.info(f"日内 {trend.value} 与日线 {daily_trend} 相反，不做")
            return Strategy.NO_TRADE, trend, confidence, details

        # v2: 中低信心 → 不做（禁用 IC 和 credit spread）
        logger.info(f"信心 {confidence}% < {need_conf}%，v2 不做卖方/震荡单")
        return Strategy.NO_TRADE, trend, confidence, details

    def select_strategy_turbo(self, vix):
        """Turbo v2: 顺势 OTM 买方，TP250/SL65。
        日线上涨只做 call，日线下跌只做 put，震荡日双向都可以。
        """
        trend, confidence, details = self.analyzer.get_trend()

        vix_full, vix9d, contango = self.analyzer.get_vix_term_structure()
        if contango is False:
            logger.warning(
                f"VIX backwardation (VIX9D={vix9d:.2f} > VIX={vix_full:.2f})，不做"
            )
            details['term_structure'] = 'backwardation'
            return Strategy.NO_TRADE, trend, confidence, details

        daily_trend = self.analyzer.get_daily_trend()
        details['daily_trend'] = daily_trend

        logger.info("=" * 40)
        logger.info(f"⚡ TURBO v2 市场分析:")
        logger.info(f"   日内: {trend.value} {confidence}% | 日线: {daily_trend} | VIX: {vix:.2f}")
        if 'rsi' in details:
            logger.info(f"   RSI(6): {details['rsi']}")
        logger.info("=" * 40)

        if vix > 35 or vix < 10:
            logger.info(f"VIX={vix:.2f} 超出 turbo 窗口 (10-35)，不交易")
            return Strategy.NO_TRADE, trend, confidence, details

        if confidence < 55:
            logger.info(f"信心 {confidence}% < 55%，不做")
            return Strategy.NO_TRADE, trend, confidence, details

        # v2: 日线方向过滤
        if trend == Trend.BULLISH and daily_trend != 'downtrend':
            logger.info(f"⚡ TURBO 顺势看涨 ({confidence}%, 日线{daily_trend}) → 18Δ Call (TP250/SL65)")
            return Strategy.TURBO_LONG_CALL, trend, confidence, details
        if trend == Trend.BEARISH and daily_trend != 'uptrend':
            logger.info(f"⚡ TURBO 顺势看跌 ({confidence}%, 日线{daily_trend}) → 18Δ Put (TP250/SL65)")
            return Strategy.TURBO_LONG_PUT, trend, confidence, details

        logger.info(f"日内 {trend.value} 与日线 {daily_trend} 相反，不做")
        return Strategy.NO_TRADE, trend, confidence, details

    def _get_spy_chain(self):
        """找到 SMART 交易所、tradingClass=SPY 的那条链。
        IB 对 SPY 返回两条 chain：
          - 'SPY'（标准 100 股乘数期权，每个交易日有 0DTE，我们要的）
          - '2SPY'（mini 期权，乘数 50，只有远月周二到期，不是我们要的）
        不能用 next(c for c in chains if c.exchange == 'SMART')，因为迭代顺序不稳，
        可能拿到 2SPY 链（只返回 3 个远月到期日）。
        """
        spy_stk = Stock('SPY', 'SMART', 'USD')
        self.ib.qualifyContracts(spy_stk)
        chains = self.ib.reqSecDefOptParams('SPY', '', 'STK', spy_stk.conId)
        for c in chains:
            if c.exchange == 'SMART' and c.tradingClass == 'SPY':
                return c
        raise RuntimeError("找不到 SMART/SPY 期权链")

    def get_0dte_expiry(self):
        """获取今日到期的期权日期（必须是当日，否则 0DTE 没有意义）"""
        chain = self._get_spy_chain()
        today_str = datetime.now(NY_TZ).strftime('%Y%m%d')
        if today_str in chain.expirations:
            logger.info(f"0DTE 到期日: {today_str}")
            return today_str
        # 美东当天可能是周末/节假日
        logger.warning(f"今日 {today_str} 不在 SPY 到期日列表里（节假日/周末？）")
        return None

    def get_option_chain(self, expiry):
        """获取期权链行权价列表"""
        chain = self._get_spy_chain()
        return sorted(chain.strikes)
    
    # ------------------------------------------------------------------
    # yfinance 期权链（paper 账户拿不到 IB 期权报价时的 fallback）
    # ------------------------------------------------------------------

    def _refresh_yf_chain(self, expiry_yymmdd: str):
        """从 yfinance 拉一次完整期权链，缓存 5 分钟。
        expiry_yymmdd 是 IB 用的 'YYYYMMDD' 格式，yfinance 要 'YYYY-MM-DD'。
        """
        import time as _time
        now = _time.time()
        if expiry_yymmdd in self._yf_chain_cache and now - self._yf_chain_ttl[expiry_yymmdd] < 300:
            return self._yf_chain_cache[expiry_yymmdd]
        try:
            yf_date = f"{expiry_yymmdd[:4]}-{expiry_yymmdd[4:6]}-{expiry_yymmdd[6:8]}"
            chain = self._yf_ticker.option_chain(yf_date)
            self._yf_chain_cache[expiry_yymmdd] = {
                'calls': chain.calls,
                'puts': chain.puts,
            }
            self._yf_chain_ttl[expiry_yymmdd] = now
            logger.debug(f"yfinance 期权链已刷新 (expiry={yf_date})")
            return self._yf_chain_cache[expiry_yymmdd]
        except Exception as e:
            logger.warning(f"yfinance 期权链拉取失败 ({expiry_yymmdd}): {e}")
            return None

    def _yf_lookup(self, expiry, strike, right):
        """在 yfinance 链里找某个 (strike, right) 的行；返回 (bid, ask, last) 或 (None, None, None)"""
        chain = self._refresh_yf_chain(expiry)
        if chain is None:
            return None, None, None
        df = chain['calls'] if right == 'C' else chain['puts']
        row = df[df['strike'] == float(strike)]
        if row.empty:
            # 找最接近的
            row = df.iloc[(df['strike'] - float(strike)).abs().argsort()[:1]]
            if row.empty:
                return None, None, None
        r = row.iloc[0]

        def _f(col):
            v = r.get(col)
            try:
                if v is None or (isinstance(v, float) and math.isnan(v)) or v <= 0:
                    return None
                return float(v)
            except (TypeError, ValueError):
                return None
        return _f('bid'), _f('ask'), _f('lastPrice')

    @staticmethod
    def _bs_delta(S, K, T, r, sigma, is_call=True):
        """Black-Scholes delta。T 用年分数；r 利率（近似 0.05）。
        yfinance 给的 IV 是小数（0.07 = 7%）。
        """
        import math as _m
        from scipy.stats import norm
        if sigma <= 0 or T <= 0 or K <= 0:
            return 0.0 if is_call else -0.0
        d1 = (_m.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * _m.sqrt(T))
        return float(norm.cdf(d1)) if is_call else float(norm.cdf(d1) - 1)

    def find_strike_by_delta(self, expiry, spy_price, target_delta, side='call'):
        """从 yfinance 期权链里找 |delta| 最接近 target_delta（0~1）的行权价。
        用 Black-Scholes 从 yfinance 的 impliedVolatility 算 delta（yfinance 本身没有 delta 列）。
        0DTE T 用 (到期剩余分钟数/全年分钟数)，近似。
        """
        chain = self._refresh_yf_chain(expiry)
        if chain is None:
            return None
        df = chain['calls'] if side == 'call' else chain['puts']

        # 计算 T：距离 16:00 ET 收盘还有多少年
        now_ny = datetime.now(NY_TZ)
        expiry_dt = NY_TZ.localize(datetime.strptime(expiry, '%Y%m%d').replace(hour=16, minute=0))
        minutes_left = max((expiry_dt - now_ny).total_seconds() / 60.0, 5.0)
        T = minutes_left / (365 * 24 * 60)
        r = 0.045  # 近似无风险利率

        # 只看 OTM
        otm = df[df['strike'] > spy_price].copy() if side == 'call' else df[df['strike'] < spy_price].copy()
        if otm.empty:
            otm = df.copy()
        # 过滤掉 IV <=0 的行
        otm = otm[otm['impliedVolatility'] > 0.01].copy()
        if otm.empty:
            return None

        is_call = (side == 'call')
        deltas = []
        for _, row in otm.iterrows():
            d = self._bs_delta(spy_price, row['strike'], T, r, row['impliedVolatility'], is_call)
            deltas.append(abs(d))
        otm['abs_delta'] = deltas
        best = otm.iloc[(otm['abs_delta'] - target_delta).abs().argsort()[:1]]
        if best.empty:
            return None
        chosen = float(best.iloc[0]['strike'])
        logger.info(f"   {side} {int(target_delta*100)}Δ → strike ${chosen} (IV={best.iloc[0]['impliedVolatility']:.2%}, T={T:.5f}y)")
        return chosen

    def find_atm_strike(self, expiry, spy_price):
        """找到最接近当前价的行权价（ATM）。"""
        strikes = self.get_option_chain(expiry)
        atm = min(strikes, key=lambda x: abs(x - spy_price))
        logger.info(f"   ATM strike: ${atm} (SPY ${spy_price:.2f})")
        return atm

    # ------------------------------------------------------------------
    # 取一条腿的报价（IB 优先，yfinance fallback）
    # ------------------------------------------------------------------

    def get_option_price(self, expiry, strike, right):
        """获取期权价格。paper account 下 IB 期权 bid/ask 经常是 NaN，
        我们先试 IB；不行再用 yfinance 期权链（15 分钟延迟快照）。
        """
        opt = Option('SPY', expiry, strike, right, 'SMART')
        self.ib.qualifyContracts(opt)
        ib_ticker = None
        try:
            ib_ticker = self.ib.reqTickers(opt)[0]
        except Exception:
            pass

        def _clean(v):
            if v is None:
                return None
            try:
                if math.isnan(v) or v <= 0:
                    return None
            except TypeError:
                return None
            return float(v)

        bid = ask = last = market = None
        if ib_ticker:
            bid = _clean(ib_ticker.bid)
            ask = _clean(ib_ticker.ask)
            last = _clean(ib_ticker.last)
            market = _clean(ib_ticker.marketPrice())

        source = 'IB'
        # IB 没有就 fallback yfinance
        if (bid is None or ask is None):
            yf_bid, yf_ask, yf_last = self._yf_lookup(expiry, strike, right)
            if yf_bid is not None:
                bid = bid or yf_bid
                ask = ask or yf_ask
                last = last or yf_last
                source = 'yfinance'

        if bid and ask:
            mid = (bid + ask) / 2
        elif last:
            mid = last
        elif market:
            mid = market
        else:
            logger.warning(f"SPY {right} {strike} {expiry}: IB+yfinance 都拿不到报价")
            mid = None

        return {'bid': bid, 'ask': ask, 'mid': mid, 'last': last, 'source': source, 'contract': opt}

    
    def calculate_iron_condor_strikes(self, spy_price, strikes, params, expiry=None):
        """计算 Iron Condor 行权价：
        - 短腿 (sell put / sell call) 用 |delta| ≈ target_delta（主流做法 16Δ）
        - 长腿 (buy put / buy call) 再往外 spread_width 美元
        target_delta 在 STRATEGY_PARAMS 里是"百分比"数字（12 就是 12Δ），这里先除 100。
        """
        target_delta = params['target_delta'] / 100.0

        # 优先用 yfinance 的 delta 列找短腿（更准确）；
        # 没 expiry 或 yfinance 没数据时退到原来的近似算法
        call_strike_sell = put_strike_sell = None
        if expiry:
            call_strike_sell = self.find_strike_by_delta(expiry, spy_price, target_delta, 'call')
            put_strike_sell = self.find_strike_by_delta(expiry, spy_price, target_delta, 'put')

        if call_strike_sell is None or put_strike_sell is None:
            # Fallback: 按 delta * spy_price * 经验倍率估算偏移
            offset = max(params['spread_width'] * 2,
                         int(target_delta * spy_price * 1.5 / 0.5) * 0.5)
            atm = min(strikes, key=lambda x: abs(x - spy_price))
            call_strike_sell = call_strike_sell or min(strikes, key=lambda x: abs(x - (atm + offset)))
            put_strike_sell = put_strike_sell or min(strikes, key=lambda x: abs(x - (atm - offset)))

        # 按 spread_width 加保护翼，snap 到最近的合法行权价
        call_strike_buy = min(strikes, key=lambda x: abs(x - (call_strike_sell + params['spread_width'])))
        put_strike_buy = min(strikes, key=lambda x: abs(x - (put_strike_sell - params['spread_width'])))

        logger.info(
            f"Iron Condor: Call ${call_strike_sell}/${call_strike_buy}, "
            f"Put ${put_strike_sell}/${put_strike_buy} (target {params['target_delta']}Δ)"
        )
        return {
            'call_sell': call_strike_sell,
            'call_buy': call_strike_buy,
            'put_sell': put_strike_sell,
            'put_buy': put_strike_buy,
        }
    
    def calculate_butterfly_strikes(self, spy_price, strikes, params, expiry=None):
        """计算Butterfly行权价（Call Butterfly）。
        把蝶心放在离 ATM 最近的行权价，蝶翼按 spread_width 展开。
        """
        middle_strike = min(strikes, key=lambda x: abs(x - spy_price))
        width = params['spread_width']
        lower_strike = min(strikes, key=lambda x: abs(x - (middle_strike - width)))
        upper_strike = min(strikes, key=lambda x: abs(x - (middle_strike + width)))

        # 确保三条行权价都不同且顺序对
        if not (lower_strike < middle_strike < upper_strike):
            logger.warning("Butterfly 行权价计算异常，跳过")
            return None

        logger.info(f"Butterfly: Buy ${lower_strike}, Sell 2x ${middle_strike}, Buy ${upper_strike}")
        return {'lower': lower_strike, 'middle': middle_strike, 'upper': upper_strike}
    
    def calculate_strangle_strikes(self, spy_price, strikes, params, expiry=None):
        """计算 Strangle（裸卖）行权价 —— 两个短腿都按 target_delta 找"""
        target_delta = params['target_delta'] / 100.0

        call_strike = put_strike = None
        if expiry:
            call_strike = self.find_strike_by_delta(expiry, spy_price, target_delta, 'call')
            put_strike = self.find_strike_by_delta(expiry, spy_price, target_delta, 'put')

        if call_strike is None or put_strike is None:
            offset = int(target_delta * spy_price * 1.5 / 0.5) * 0.5
            atm = min(strikes, key=lambda x: abs(x - spy_price))
            call_strike = call_strike or min(strikes, key=lambda x: abs(x - (atm + offset)))
            put_strike = put_strike or min(strikes, key=lambda x: abs(x - (atm - offset)))

        logger.info(
            f"Strangle: Sell Call ${call_strike}, Sell Put ${put_strike} "
            f"(target {params['target_delta']}Δ)"
        )
        return {'call_sell': call_strike, 'put_sell': put_strike}
    
    def calculate_position_size(self, risk_per_contract):
        """计算仓位大小"""
        max_risk_amount = self.working_capital * (self.config['max_risk_per_trade_pct'] / 100)
        contracts = int(max_risk_amount / risk_per_contract)
        return max(1, contracts)
    
    def place_iron_condor(self, expiry, strikes, params):
        """下单Iron Condor"""
        call_sell = self.get_option_price(expiry, strikes['call_sell'], 'C')
        call_buy = self.get_option_price(expiry, strikes['call_buy'], 'C')
        put_sell = self.get_option_price(expiry, strikes['put_sell'], 'P')
        put_buy = self.get_option_price(expiry, strikes['put_buy'], 'P')

        # 报价健全性检查：4 条腿都要有有效 bid/ask，否则 credit 算不准
        legs_prices = {'call_sell': call_sell, 'call_buy': call_buy,
                       'put_sell': put_sell, 'put_buy': put_buy}
        missing = [name for name, p in legs_prices.items()
                   if p['bid'] is None or p['ask'] is None]
        if missing:
            logger.warning(
                f"⛔ Iron Condor 报价不全 (缺 {missing})，跳过。"
                "延迟数据在盘前/盘后经常这样，等 10:00 ET 后重试。"
            )
            return None

        credit_received = (call_sell['bid'] + put_sell['bid']) - (call_buy['ask'] + put_buy['ask'])
        max_risk = (params['spread_width'] * 2) - credit_received

        logger.info(f"预期净权利金: ${credit_received:.2f}, 每股最大风险: ${max_risk:.2f}")

        if credit_received < params['min_credit'] or credit_received > params['max_credit']:
            logger.warning(f"权利金不在目标范围 {params['min_credit']}-{params['max_credit']}")
            return None
        
        risk_per_contract = max_risk * 100
        contracts = self.calculate_position_size(risk_per_contract)
        logger.info(f"仓位: {contracts} 合约, 总最大风险: ${contracts * risk_per_contract:.2f}")
        
        if contracts * risk_per_contract + abs(self.daily_pnl) > self.working_capital * (self.config['daily_max_loss_pct'] / 100):
            logger.warning("当日风险超过限制")
            return None
        
        # 下单
        order_list = []
        legs = [
            (call_sell['contract'], 'SELL'),
            (call_buy['contract'], 'BUY'),
            (put_sell['contract'], 'SELL'),
            (put_buy['contract'], 'BUY'),
        ]
        
        for i, (contract, action) in enumerate(legs):
            order = MarketOrder(action, contracts)
            order.transmit = (i == len(legs) - 1)
            trade = self.ib.placeOrder(contract, order)
            order_list.append(trade)
        
        logger.info("Iron Condor订单已提交!")
        
        return {
            'strategy': Strategy.IRON_CONDOR,
            'contracts': contracts,
            'strikes': strikes,
            'credit_debit': credit_received,
            'is_credit': True,
            'legs': {'call_sell': call_sell, 'call_buy': call_buy, 'put_sell': put_sell, 'put_buy': put_buy},
            'params': params,
        }
    
    def place_butterfly(self, expiry, strikes, params):
        """下单Call Butterfly"""
        lower = self.get_option_price(expiry, strikes['lower'], 'C')
        middle = self.get_option_price(expiry, strikes['middle'], 'C')
        upper = self.get_option_price(expiry, strikes['upper'], 'C')

        missing = [n for n, p in dict(lower=lower, middle=middle, upper=upper).items()
                   if p['bid'] is None or p['ask'] is None]
        if missing:
            logger.warning(f"⛔ Butterfly 报价不全 (缺 {missing})，跳过")
            return None

        debit_paid = lower['ask'] + upper['ask'] - 2 * middle['bid']
        max_profit = params['spread_width'] - debit_paid

        logger.info(f"成本: ${debit_paid:.2f}, 最大盈利: ${max_profit:.2f}")
        
        if debit_paid < params['min_debit'] or debit_paid > params['max_debit']:
            logger.warning(f"成本不在目标范围 {params['min_debit']}-{params['max_debit']}")
            return None
        
        risk_per_contract = debit_paid * 100
        contracts = self.calculate_position_size(risk_per_contract)
        logger.info(f"仓位: {contracts} 合约, 总最大风险: ${contracts * risk_per_contract:.2f}")
        
        if contracts * risk_per_contract + abs(self.daily_pnl) > self.working_capital * (self.config['daily_max_loss_pct'] / 100):
            logger.warning("当日风险超过限制")
            return None
        
        legs = [
            (lower['contract'], 'BUY'),
            (middle['contract'], 'SELL', 2),
            (upper['contract'], 'BUY'),
        ]
        
        for i, item in enumerate(legs):
            if len(item) == 2:
                contract, action = item
                qty = contracts
            else:
                contract, action, qty_mult = item
                qty = contracts * qty_mult
            
            order = MarketOrder(action, qty)
            order.transmit = (i == len(legs) - 1)
            self.ib.placeOrder(contract, order)
        
        logger.info("Butterfly订单已提交!")
        
        return {
            'strategy': Strategy.BUTTERFLY,
            'contracts': contracts,
            'strikes': strikes,
            'credit_debit': debit_paid,
            'is_credit': False,
            'legs': {'lower': lower, 'middle': middle, 'upper': upper},
            'params': params,
        }
    
    def place_strangle(self, expiry, strikes, params):
        """下单Short Strangle"""
        call_sell = self.get_option_price(expiry, strikes['call_sell'], 'C')
        put_sell = self.get_option_price(expiry, strikes['put_sell'], 'P')

        missing = [n for n, p in dict(call_sell=call_sell, put_sell=put_sell).items()
                   if p['bid'] is None]
        if missing:
            logger.warning(f"⛔ Strangle 报价不全 (缺 {missing})，跳过")
            return None

        credit_received = call_sell['bid'] + put_sell['bid']
        logger.info(f"预期净权利金: ${credit_received:.2f}")

        if credit_received < params['min_credit'] or credit_received > params['max_credit']:
            logger.warning(f"权利金不在目标范围 {params['min_credit']}-{params['max_credit']}")
            return None
        
        # Strangle风险估算: 假设移动3个行权价止损
        estimated_risk_per_contract = 300  # 估算每个合约最大风险约$300
        contracts = self.calculate_position_size(estimated_risk_per_contract)
        logger.info(f"仓位: {contracts} 合约")
        
        if contracts * estimated_risk_per_contract + abs(self.daily_pnl) > self.working_capital * (self.config['daily_max_loss_pct'] / 100):
            logger.warning("当日风险超过限制")
            return None
        
        # 卖出Call
        call_order = MarketOrder('SELL', contracts)
        call_order.transmit = False
        self.ib.placeOrder(call_sell['contract'], call_order)
        
        # 卖出Put
        put_order = MarketOrder('SELL', contracts)
        put_order.transmit = True
        self.ib.placeOrder(put_sell['contract'], put_order)
        
        logger.info("Strangle订单已提交!")
        
        return {
            'strategy': Strategy.STRANGLE,
            'contracts': contracts,
            'strikes': strikes,
            'credit_debit': credit_received,
            'is_credit': True,
            'legs': {'call_sell': call_sell, 'put_sell': put_sell},
            'params': params,
        }
    
    def calculate_directional_spread_strikes(self, spy_price, strikes, params, is_put_spread, expiry=None):
        """计算方向性 Credit Spread 的行权价
        is_put_spread=True:  看涨时用，卖出虚值 Put，买入更虚值 Put
        is_put_spread=False: 看跌时用，卖出虚值 Call，买入更虚值 Call
        """
        target_delta = params.get('target_delta', 30) / 100.0  # 30 → 0.30

        if expiry:
            side = 'put' if is_put_spread else 'call'
            sell_strike = self.find_strike_by_delta(expiry, spy_price, target_delta, side)
        else:
            sell_strike = None

        if sell_strike is None:
            # Fallback 估算
            offset = max(params['spread_width'] * 2,
                         int(target_delta * spy_price * 1.0 / 0.5) * 0.5)
            atm = min(strikes, key=lambda x: abs(x - spy_price))
            sell_strike = atm - offset if is_put_spread else atm + offset
            sell_strike = min(strikes, key=lambda x: abs(x - sell_strike))

        if is_put_spread:
            # Put Credit Spread: 卖出接近ATM的Put，买入更虚值的Put
            buy_strike = sell_strike - params['spread_width']
            buy_strike = min(strikes, key=lambda x: abs(x - buy_strike))
            logger.info(f"Put Credit Spread: 卖出 ${sell_strike} / 买入 ${buy_strike} (target {int(target_delta*100)}Δ)")
            return {'sell': sell_strike, 'buy': buy_strike, 'right': 'P'}
        else:
            # Call Credit Spread: 卖出接近ATM的Call，买入更虚值的Call
            buy_strike = sell_strike + params['spread_width']
            buy_strike = min(strikes, key=lambda x: abs(x - buy_strike))
            logger.info(f"Call Credit Spread: 卖出 ${sell_strike} / 买入 ${buy_strike} (target {int(target_delta*100)}Δ)")
            return {'sell': sell_strike, 'buy': buy_strike, 'right': 'C'}

    def place_directional_spread(self, expiry, strikes, params, is_put_spread):
        """下单方向性Credit Spread"""
        spread_strikes = self.calculate_directional_spread_strikes(
            self.get_spy_price(), strikes, params, is_put_spread, expiry=expiry
        )
        
        right = spread_strikes['right']
        sell_strike = spread_strikes['sell']
        buy_strike = spread_strikes['buy']
        
        sell_opt = self.get_option_price(expiry, sell_strike, right)
        buy_opt = self.get_option_price(expiry, buy_strike, right)

        missing = [n for n, p in dict(sell=sell_opt, buy=buy_opt).items()
                   if p['bid'] is None or p['ask'] is None]
        if missing:
            logger.warning(f"⛔ 方向性 spread 报价不全 (缺 {missing})，跳过")
            return None

        credit_received = sell_opt['bid'] - buy_opt['ask']
        max_risk = (params['spread_width'] - credit_received) * 100
        
        logger.info(f"预期净权利金: ${credit_received:.2f}, 每股最大风险: ${max_risk:.2f}")
        
        if credit_received < params['min_credit'] or credit_received > params['max_credit']:
            logger.warning(f"权利金不在目标范围 {params['min_credit']}-{params['max_credit']}")
            return None
        
        risk_per_contract = max_risk
        contracts = self.calculate_position_size(risk_per_contract)
        logger.info(f"仓位: {contracts} 合约, 总最大风险: ${contracts * risk_per_contract:.2f}")
        
        if contracts * risk_per_contract + abs(self.daily_pnl) > self.working_capital * (self.config['daily_max_loss_pct'] / 100):
            logger.warning("当日风险超过限制")
            return None
        
        # 卖出期权
        sell_order = MarketOrder('SELL', contracts)
        sell_order.transmit = False
        self.ib.placeOrder(sell_opt['contract'], sell_order)
        
        # 买入保护期权
        buy_order = MarketOrder('BUY', contracts)
        buy_order.transmit = True
        self.ib.placeOrder(buy_opt['contract'], buy_order)
        
        strategy_name = "Put Credit Spread" if is_put_spread else "Call Credit Spread"
        logger.info(f"{strategy_name} 订单已提交!")
        
        if is_put_spread:
            strategy = Strategy.DIRECTIONAL_PUT_SPREAD
        else:
            strategy = Strategy.DIRECTIONAL_CALL_SPREAD
        
        return {
            'strategy': strategy,
            'contracts': contracts,
            'strikes': spread_strikes,
            'credit_debit': credit_received,
            'is_credit': True,
            'legs': {'sell': sell_opt, 'buy': buy_opt},
            'params': params,
        }

    def place_long_option(self, expiry, spy_price, params, is_call=True,
                          strategy_enum=None):
        """买 Call/Put（方向性买方），支持 ATM 和 OTM。
        - params['target_delta'] == 50 → ATM（steady 模式）
        - params['target_delta'] < 50  → OTM（turbo 模式，用 delta 找行权价）
        - strategy_enum: 显式指定 Strategy 枚举（steady vs turbo）
        """
        right = 'C' if is_call else 'P'
        target_delta = params.get('target_delta', 50)

        if target_delta >= 50:
            strike = self.find_atm_strike(expiry, spy_price)
        else:
            side = 'call' if is_call else 'put'
            strike = self.find_strike_by_delta(expiry, spy_price,
                                               target_delta / 100.0, side)
            if strike is None:
                logger.warning(f"⛔ 找不到 {target_delta}Δ {side} 行权价，回退 ATM")
                strike = self.find_atm_strike(expiry, spy_price)

        opt = self.get_option_price(expiry, strike, right)

        if opt['ask'] is None:
            logger.warning(f"⛔ Long {right} 报价不全，跳过")
            return None

        debit = opt['ask']  # 用 ask 买入
        if debit < params['min_debit'] or debit > params['max_debit']:
            logger.warning(f"权利金 ${debit:.2f} 不在范围 {params['min_debit']}-{params['max_debit']}")
            return None

        # 仓位：用 working_capital 的 N% 买，亏完最多亏这么多
        budget = self.working_capital * (params['risk_pct_of_account'] / 100)
        contracts = int(budget / (debit * 100))
        contracts = max(1, contracts)

        total_cost = contracts * debit * 100
        logger.info(f"Long {right} ${strike} ({target_delta}Δ) @ ${debit:.2f}, "
                    f"{contracts} 合约, 总成本 ${total_cost:.2f}")

        # 当日亏损上限检查（长仓最大亏损 = 付出的全部权利金）
        if total_cost + abs(self.daily_pnl) > self.working_capital * (self.config['daily_max_loss_pct'] / 100):
            logger.warning("当日风险超过限制")
            return None

        order = MarketOrder('BUY', contracts)
        self.ib.placeOrder(opt['contract'], order)
        if strategy_enum is None:
            strategy_enum = (Strategy.DIRECTIONAL_LONG_CALL if is_call
                             else Strategy.DIRECTIONAL_LONG_PUT)
        logger.info(f"{strategy_enum.value} 订单已提交!")

        return {
            'strategy': strategy_enum,
            'contracts': contracts,
            'strikes': {'strike': strike, 'right': right},
            'credit_debit': debit,
            'is_credit': False,
            'legs': {'long': opt},
            'params': params,
        }
    
    def calculate_pnl(self):
        """计算当前持仓盈亏"""
        if not self.current_position:
            return 0, 0
        
        pos = self.current_position
        strategy = pos['strategy']
        expiry = list(pos['legs'].values())[0]['contract'].lastTradeDateOrContractMonth
        
        if strategy == Strategy.IRON_CONDOR:
            call_sell = self.get_option_price(expiry, pos['strikes']['call_sell'], 'C')
            call_buy = self.get_option_price(expiry, pos['strikes']['call_buy'], 'C')
            put_sell = self.get_option_price(expiry, pos['strikes']['put_sell'], 'P')
            put_buy = self.get_option_price(expiry, pos['strikes']['put_buy'], 'P')
            
            current_debit = (call_sell['ask'] + put_sell['ask']) - (call_buy['bid'] + put_buy['bid'])
            pnl = (pos['credit_debit'] - current_debit) * pos['contracts'] * 100
            pnl_pct = (pnl / (pos['credit_debit'] * 100 * pos['contracts'])) * 100
            
            return pnl, pnl_pct
        
        elif strategy == Strategy.BUTTERFLY:
            lower = self.get_option_price(expiry, pos['strikes']['lower'], 'C')
            middle = self.get_option_price(expiry, pos['strikes']['middle'], 'C')
            upper = self.get_option_price(expiry, pos['strikes']['upper'], 'C')
            
            current_credit = (2 * middle['bid']) - (lower['ask'] + upper['ask'])
            pnl = (-pos['credit_debit'] + current_credit) * pos['contracts'] * 100
            max_profit = (pos['params']['spread_width'] - pos['credit_debit']) * 100 * pos['contracts']
            pnl_pct = (pnl / max_profit) * 100 if max_profit > 0 else 0
            
            return pnl, pnl_pct
        
        elif strategy == Strategy.STRANGLE:
            call_sell = self.get_option_price(expiry, pos['strikes']['call_sell'], 'C')
            put_sell = self.get_option_price(expiry, pos['strikes']['put_sell'], 'P')
            
            current_debit = call_sell['ask'] + put_sell['ask']
            pnl = (pos['credit_debit'] - current_debit) * pos['contracts'] * 100
            pnl_pct = (pnl / (pos['credit_debit'] * 100 * pos['contracts'])) * 100
            
            return pnl, pnl_pct
        
        elif strategy in [Strategy.DIRECTIONAL_PUT_SPREAD, Strategy.DIRECTIONAL_CALL_SPREAD]:
            right = pos['strikes']['right']
            sell_opt = self.get_option_price(expiry, pos['strikes']['sell'], right)
            buy_opt = self.get_option_price(expiry, pos['strikes']['buy'], right)

            current_debit = sell_opt['ask'] - buy_opt['bid']
            pnl = (pos['credit_debit'] - current_debit) * pos['contracts'] * 100
            pnl_pct = (pnl / (pos['credit_debit'] * 100 * pos['contracts'])) * 100

            return pnl, pnl_pct

        elif strategy in (Strategy.DIRECTIONAL_LONG_CALL, Strategy.DIRECTIONAL_LONG_PUT,
                          Strategy.TURBO_LONG_CALL, Strategy.TURBO_LONG_PUT):
            right = pos['strikes']['right']
            opt = self.get_option_price(expiry, pos['strikes']['strike'], right)
            if opt['mid'] is None:
                return 0, 0
            entry_cost = pos['credit_debit'] * pos['contracts'] * 100
            pnl = (opt['mid'] - pos['credit_debit']) * pos['contracts'] * 100
            pnl_pct = (pnl / entry_cost) * 100 if entry_cost > 0 else 0
            return pnl, pnl_pct

        return 0, 0
    
    def close_all_positions(self):
        """平仓所有持仓。

        IB positions() 返回的 contract 通常没有 exchange 字段，直接下单会
        Error 321（缺失委托单交易所）。这里基于 localSymbol/conId 重新构造
        一个 exchange='SMART' 的完整 Option/Stock contract 再下单。
        """
        from ib_insync import Option, Stock
        for pos in self.ib.positions():
            c = pos.contract
            if 'SPY' not in c.symbol:
                continue
            if pos.position == 0:
                continue

            action = 'BUY' if pos.position < 0 else 'SELL'
            qty = abs(pos.position)

            if c.secType == 'OPT':
                close_contract = Option(
                    symbol=c.symbol,
                    lastTradeDateOrContractMonth=c.lastTradeDateOrContractMonth,
                    strike=c.strike,
                    right=c.right,
                    multiplier=c.multiplier or '100',
                    currency=c.currency or 'USD',
                    exchange='SMART',
                    localSymbol=c.localSymbol,
                    tradingClass=c.tradingClass or 'SPY',
                )
            else:
                close_contract = Stock(
                    symbol=c.symbol,
                    exchange='SMART',
                    currency=c.currency or 'USD',
                )
            close_contract.includeExpired = True
            q = self.ib.qualifyContracts(close_contract)
            if not q:
                logger.error(f"无法 qualify 平仓合约: {c.localSymbol}，跳过")
                continue

            order = MarketOrder(action, qty, tif='DAY')
            self.ib.placeOrder(close_contract, order)
            logger.info(f"平仓 {action} {qty}x {c.localSymbol} via SMART")

        self.current_position = None
        self.current_strategy = None
    
    def check_and_close_positions(self):
        """检查并平仓"""
        if not self.current_position:
            return False

        pnl, pnl_pct = self.calculate_pnl()
        params = self.current_position['params']
        position_snapshot = self.current_position  # close_all_positions 会清空

        logger.info(f"当前盈亏: ${pnl:.2f} ({pnl_pct:.1f}%)")

        # 止盈
        if pnl_pct >= params['take_profit_pct']:
            logger.info(f"达到止盈目标 {params['take_profit_pct']}%，平仓!")
            self.close_all_positions()
            self.consecutive_losses = 0
            self._log_trade('close_tp', position_snapshot, pnl, pnl_pct)
            return True

        # 止损
        if pnl_pct <= -params['stop_loss_pct']:
            logger.warning(f"达到止损 {params['stop_loss_pct']}%，平仓!")
            self.close_all_positions()
            self.consecutive_losses += 1
            self._log_trade('close_sl', position_snapshot, pnl, pnl_pct,
                            extra=f"consecutive_losses={self.consecutive_losses}")
            return True

        # 时间强平
        now = get_ny_time()
        if now >= self.config['close_before']:
            logger.info(f"达到平仓时间 {self.config['close_before'].strftime('%H:%M')} (NY)，强制平仓!")
            self.close_all_positions()
            if pnl < 0:
                self.consecutive_losses += 1
            else:
                self.consecutive_losses = 0
            self._log_trade('close_time', position_snapshot, pnl, pnl_pct)
            return True

        return False
    
    def execute_strategy(self, strategy, spy_price, expiry, strikes):
        """执行选定策略"""
        params = self.strategy_params[strategy]

        if strategy == Strategy.IRON_CONDOR:
            ic_strikes = self.calculate_iron_condor_strikes(spy_price, strikes, params, expiry=expiry)
            return self.place_iron_condor(expiry, ic_strikes, params)

        elif strategy == Strategy.BUTTERFLY:
            bf_strikes = self.calculate_butterfly_strikes(spy_price, strikes, params, expiry=expiry)
            if not bf_strikes:
                return None
            return self.place_butterfly(expiry, bf_strikes, params)

        elif strategy == Strategy.STRANGLE:
            st_strikes = self.calculate_strangle_strikes(spy_price, strikes, params, expiry=expiry)
            return self.place_strangle(expiry, st_strikes, params)
        
        elif strategy == Strategy.DIRECTIONAL_PUT_SPREAD:
            return self.place_directional_spread(expiry, strikes, params, is_put_spread=True)
        
        elif strategy == Strategy.DIRECTIONAL_CALL_SPREAD:
            return self.place_directional_spread(expiry, strikes, params, is_put_spread=False)

        elif strategy == Strategy.DIRECTIONAL_LONG_CALL:
            return self.place_long_option(expiry, spy_price, params, is_call=True)

        elif strategy == Strategy.DIRECTIONAL_LONG_PUT:
            return self.place_long_option(expiry, spy_price, params, is_call=False)

        elif strategy == Strategy.TURBO_LONG_CALL:
            return self.place_long_option(expiry, spy_price, params, is_call=True,
                                          strategy_enum=Strategy.TURBO_LONG_CALL)

        elif strategy == Strategy.TURBO_LONG_PUT:
            return self.place_long_option(expiry, spy_price, params, is_call=False,
                                          strategy_enum=Strategy.TURBO_LONG_PUT)

        return None
    
    def run_trading_cycle(self):
        """执行交易循环"""
        logger.info("=" * 60)
        logger.info("开始交易循环")

        # 0. 先看 kill switch —— 只影响新开仓，已开仓的持仓还是要管
        can_trade, halt_reason = self._check_kill_switches()

        # 1. 市场检查和策略选择
        vix = self.get_vix_price()
        if self.mode == 'turbo':
            strategy_result = self.select_strategy_turbo(vix)
        else:
            strategy_result = self.select_strategy(vix)

        if isinstance(strategy_result, tuple) and len(strategy_result) >= 1:
            strategy = strategy_result[0]
        else:
            strategy = strategy_result

        # 2. 检查是否已有持仓 —— 有持仓就先管理
        if self.current_position:
            self.check_and_close_positions()
            logger.info("=" * 60)
            return

        # 3. kill switch 挡住就不开新仓
        if not can_trade:
            logger.info(f"⛔ 不开新仓: {halt_reason}")
            logger.info("=" * 60)
            return

        # 4. 策略层不做
        if strategy == Strategy.NO_TRADE:
            logger.info("今天不交易")
            logger.info("=" * 60)
            return

        # 5. 检查交易时间 (使用纽约时间)
        #    买方策略使用 long_entry 窗口（steady 更窄，turbo 更宽）
        now = get_ny_time()
        is_long = strategy in (
            Strategy.DIRECTIONAL_LONG_CALL, Strategy.DIRECTIONAL_LONG_PUT,
            Strategy.TURBO_LONG_CALL, Strategy.TURBO_LONG_PUT,
        )
        if is_long:
            win_start = self.config['long_entry_window_start']
            win_end = self.config['long_entry_window_end']
        else:
            win_start = self.config['entry_window_start']
            win_end = self.config['entry_window_end']
        if not (win_start <= now <= win_end):
            logger.info(
                f"不在开仓窗口 {win_start}~{win_end} ET. "
                f"当前: {now.strftime('%H:%M:%S')}"
            )
            logger.info("=" * 60)
            return

        # 6. 获取数据
        spy_price = self.get_spy_price()
        expiry = self.get_0dte_expiry()
        if not expiry:
            logger.error("找不到0DTE到期日")
            logger.info("=" * 60)
            return

        all_strikes = self.get_option_chain(expiry)

        # 7. 执行策略
        position = self.execute_strategy(strategy, spy_price, expiry, all_strikes)
        if position:
            self.current_position = position
            self.current_strategy = strategy
            self.trades_today += 1
            self._log_trade('open', position)
            logger.info(
                f"成功开仓: {strategy.value} "
                f"(今日第 {self.trades_today}/{self.config['max_daily_trades']} 笔)"
            )

        logger.info("交易循环完成")
        logger.info("=" * 60)
    
    def disconnect(self):
        """断开连接"""
        self.ib.disconnect()
        logger.info("已断开IB连接")

def build_parser():
    p = argparse.ArgumentParser(
        description='SPY 0DTE 多策略自动交易机器人（默认 paper trading）'
    )
    p.add_argument('--host', default='127.0.0.1',
                   help='IB Gateway/TWS host (default: 127.0.0.1)')
    p.add_argument('--port', type=int, default=4002,
                   help='端口。4002=Gateway paper (默认), 7497=TWS paper, '
                        '4001=Gateway 实盘, 7496=TWS 实盘')
    p.add_argument('--client-id', type=int, default=2,
                   help='IB client ID。两个模式同时跑时用不同 ID（如 2 和 3）')
    p.add_argument('--live', action='store_true',
                   help='显式开启实盘。必须同时把 --port 改为 4001/7496 才生效。')
    p.add_argument('--mode', choices=['steady', 'turbo'], default='steady',
                   help='steady=v2 顺势 ATM 买方(TP80/SL30), '
                        'turbo=v2 顺势 18Δ OTM (TP250/SL65, 高风险)')
    p.add_argument('--capital-pct', type=float, default=100,
                   help='使用账户资金的百分比(默认100)。'
                        '50/50 分仓时，两个实例各传 --capital-pct 50')
    return p


def main():
    args = build_parser().parse_args()

    # 双重保险：--live 但端口是 paper 端口 → 抛错
    live_ports = {4001, 7496}
    paper_ports = {4002, 7497}
    if args.live and args.port in paper_ports:
        raise SystemExit(
            f"❌ --live 与 paper 端口 {args.port} 冲突。要实盘请传 --port 4001 或 7496。"
        )
    if not args.live and args.port in live_ports:
        raise SystemExit(
            f"❌ 端口 {args.port} 是实盘端口，但没有加 --live。"
            "要实盘必须显式 --live；要 paper 请传 --port 4002。"
        )

    if args.mode == 'turbo':
        logger.warning("⚡⚡⚡ TURBO 模式 —— 高风险，每周翻倍目标，可能爆仓 ⚡⚡⚡")

    trader = SPYMultiStrategyTrader(
        host=args.host,
        port=args.port,
        client_id=args.client_id,
        live=args.live,
        mode=args.mode,
        capital_pct=args.capital_pct,
    )

    if not trader.connect():
        return

    try:
        while True:
            trader.run_trading_cycle()
            trader.ib.sleep(60)
    except KeyboardInterrupt:
        logger.info("用户中断")
    finally:
        trader.disconnect()


if __name__ == '__main__':
    main()
