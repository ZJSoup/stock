#!/usr/bin/env python3
"""
历史回测：用 yfinance 的 SPY 5分钟K线 + VIX 日数据，
通过 Black-Scholes 模拟期权 PnL，回测 steady 和 turbo 策略。

用法：
    python3 backtest.py                      # steady + turbo，$2000 起始
    python3 backtest.py --days 60
    python3 backtest.py --mode turbo
    python3 backtest.py --capital 2000
    python3 backtest.py --scan steady        # 扫描 steady 参数组合
    python3 backtest.py --no-fees            # 关闭手续费/滑点
    python3 backtest.py --regime             # 按市场环境分段统计

手续费模型（IBKR 标准散户）：
    - 期权佣金 $0.65 / 张（单边）
    - 滑点：买在 ask、卖在 bid，用 mid ± slippage_ticks 近似
    - 0DTE SPY 期权买卖价差约 1-3 cents，默认 1 cent
"""

import argparse
import math
from datetime import datetime, time
from collections import defaultdict

import numpy as np
import pandas as pd
import yfinance as yf
from scipy.stats import norm


# ---------------------------------------------------------------------------
# Black-Scholes
# ---------------------------------------------------------------------------

def bs_price(S, K, T, r, sigma, is_call=True):
    if T <= 0 or sigma <= 0:
        return max(0, S - K) if is_call else max(0, K - S)
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    if is_call:
        return S * norm.cdf(d1) - K * math.exp(-r * T) * norm.cdf(d2)
    return K * math.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)


def bs_delta(S, K, T, r, sigma, is_call=True):
    if T <= 0 or sigma <= 0:
        return 1.0 if (is_call and S > K) or (not is_call and S < K) else 0.0
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    return float(norm.cdf(d1)) if is_call else float(norm.cdf(d1) - 1)


def estimate_sigma(vix_close, time_of_day_frac=0.5):
    """VIX → 当期年化 IV，日内 U 型调整。"""
    base_iv = vix_close / 100.0
    if time_of_day_frac < 0.15:
        return base_iv * 1.2
    elif time_of_day_frac > 0.8:
        return base_iv * 1.15
    elif 0.3 < time_of_day_frac < 0.7:
        return base_iv * 0.9
    return base_iv


# ---------------------------------------------------------------------------
# 市场分析（复刻 MarketAnalyzer.get_trend 评分逻辑）
# ---------------------------------------------------------------------------

def calc_rsi(prices, period=6):
    deltas = np.diff(prices)
    gains = np.where(deltas > 0, deltas, 0)
    losses = np.where(deltas < 0, -deltas, 0)
    avg_gain = np.mean(gains[-period:])
    avg_loss = np.mean(losses[-period:])
    if avg_loss == 0:
        return 100.0
    return 100.0 - 100.0 / (1 + avg_gain / avg_loss)


def analyze_trend(bars, spy_price, vix):
    if len(bars) < 12:
        return 'sideways', 50, {}

    closes = bars['Close'].values.astype(float)
    rsi = calc_rsi(closes, 6)

    typical = (bars['High'].astype(float) + bars['Low'].astype(float)
               + bars['Close'].astype(float)) / 3
    vwap = (typical * bars['Volume'].astype(float)).cumsum() \
        / bars['Volume'].astype(float).cumsum()
    vwap_val = float(vwap.iloc[-1])

    ma5 = closes[-5:].mean()
    ma10 = closes[-10:].mean()
    last5_pct = (closes[-1] - closes[-6]) / closes[-6] * 100

    today_open = float(bars['Open'].iloc[0])
    prev_close = bars.attrs.get('prev_close', today_open)
    gap = (today_open - prev_close) / prev_close * 100

    vol_ma5 = bars['Volume'].iloc[-5:].mean()
    vol_ma10 = bars['Volume'].iloc[-10:].mean()
    vol_inc = vol_ma5 > vol_ma10 * 1.2

    bull = bear = 0
    if rsi < 30: bull += 2
    elif rsi > 70: bear += 2
    if vwap_val and spy_price > vwap_val * 1.001: bull += 2
    elif vwap_val and spy_price < vwap_val * 0.999: bear += 2
    if ma5 > ma10 * 1.001: bull += 2
    elif ma5 < ma10 * 0.999: bear += 2
    if last5_pct > 0.3: bull += 1
    elif last5_pct < -0.3: bear += 1
    if abs(gap) > 0.5:
        if gap > 0: bear += 1
        else: bull += 1
    if vol_inc:
        if bull > bear: bull += 1
        elif bear > bull: bear += 1

    total = bull + bear
    conf = int(50 + 30 * abs(bull - bear) / max(total, 1))
    if bull - bear >= 2:
        return 'bullish', conf, {'rsi': rsi, 'gap': gap}
    if bear - bull >= 2:
        return 'bearish', conf, {'rsi': rsi, 'gap': gap}
    return 'sideways', conf, {'rsi': rsi, 'gap': gap}


# ---------------------------------------------------------------------------
# 事件日（FOMC / CPI / NFP 简化）
# ---------------------------------------------------------------------------

FOMC = {
    # 2023
    '2023-02-01', '2023-03-22', '2023-05-03', '2023-06-14',
    '2023-07-26', '2023-09-20', '2023-11-01', '2023-12-13',
    # 2024
    '2024-01-31', '2024-03-20', '2024-05-01', '2024-06-12',
    '2024-07-31', '2024-09-18', '2024-11-07', '2024-12-18',
    # 2025
    '2025-01-29', '2025-03-19', '2025-05-07', '2025-06-18',
    '2025-07-30', '2025-09-17', '2025-10-29', '2025-12-10',
    # 2026
    '2026-01-28', '2026-01-29', '2026-03-17', '2026-03-18',
    '2026-04-28', '2026-04-29', '2026-06-16', '2026-06-17',
    '2026-07-28', '2026-07-29', '2026-09-16', '2026-09-17',
}
CPI = {
    # 2023
    '2023-02-14', '2023-03-14', '2023-04-12', '2023-05-10',
    '2023-06-13', '2023-07-12', '2023-08-10', '2023-09-13',
    '2023-10-12', '2023-11-14', '2023-12-12',
    # 2024
    '2024-01-11', '2024-02-13', '2024-03-12', '2024-04-10',
    '2024-05-15', '2024-06-12', '2024-07-11', '2024-08-14',
    '2024-09-11', '2024-10-10', '2024-11-13', '2024-12-11',
    # 2025
    '2025-01-15', '2025-02-12', '2025-03-13', '2025-04-10',
    '2025-05-14', '2025-06-11', '2025-07-15', '2025-08-13',
    '2025-09-11', '2025-10-15', '2025-11-13', '2025-12-11',
    # 2026
    '2026-01-14', '2026-02-12', '2026-03-13', '2026-04-10',
    '2026-05-14', '2026-06-11', '2026-07-15', '2026-08-12',
}
NO_TRADE = FOMC | CPI


def is_event_day(dt):
    return dt.strftime('%Y-%m-%d') in NO_TRADE


# ---------------------------------------------------------------------------
# 行权价搜索
# ---------------------------------------------------------------------------

def find_strike_by_delta(strikes, S, target_delta, T, r, sigma, is_call):
    best_k, best_d = None, 999.0
    for K in strikes:
        d = abs(abs(bs_delta(S, K, T, r, sigma, is_call)) - target_delta)
        if d < best_d:
            best_d, best_k = d, K
    return best_k


def find_atm_strike(strikes, S):
    return min(strikes, key=lambda k: abs(k - S))


# ---------------------------------------------------------------------------
# 单日回测
# ---------------------------------------------------------------------------

DEFAULT_PARAMS = {
    'steady': {
        'long_tp_pct': 80, 'long_sl_pct': 40,
        'long_risk_pct': 3.0, 'long_min_price': 0.30, 'long_max_price': 5.00,
        'ic_tp_pct': 40, 'ic_sl_pct': 120, 'ic_wing': 5.0,
        'ic_risk_pct': 8.0, 'ic_min_credit': 0.40, 'ic_short_delta': 0.16,
        'spread_tp_pct': 50, 'spread_sl_pct': 100, 'spread_width': 3.0,
        'spread_risk_pct': 5.0, 'spread_short_delta': 0.30,
        'conf_long': 75, 'conf_spread': 65,
    },
    'turbo': {
        'long_tp_pct': 200, 'long_sl_pct': 65,
        'long_risk_pct': 10.0, 'long_min_price': 0.15, 'long_max_price': 2.50,
        'long_delta': 0.18, 'conf_min': 55,
    },
}


def backtest_day(day_bars, vix_close, prev_close, mode, capital, params=None,
                 r=0.045, commission_per_contract=0.65, slippage=0.01,
                 daily_trend=None):
    """params 覆盖默认参数；commission/slippage 为单边成本。
    daily_trend: 'uptrend' / 'downtrend' / 'choppy' / None —— 若提供则
    做方向过滤：上涨市只开 call，下跌市只开 put，震荡市不做方向单。
    """
    trades = []
    p = dict(DEFAULT_PARAMS[mode])
    if params:
        p.update(params)

    spy_ref = float(day_bars['Close'].iloc[0])
    lo = math.floor(spy_ref / 5) * 5 - 25
    hi = lo + 70
    strikes = np.arange(lo, hi, 1.0)

    day_bars = day_bars.copy()
    day_bars.attrs['prev_close'] = prev_close

    if mode == 'steady':
        entry_start = time(10, 0)
        entry_end = time(14, 0)
        long_start = time(10, 30)
        long_end = time(13, 30)
        close_time = time(15, 0)
        max_trades = 2
        cons_loss_limit = 3
        vix_lo, vix_hi = 12.0, 30.0
    else:
        entry_start = time(10, 0)
        entry_end = time(14, 30)
        long_start = time(10, 0)
        long_end = time(14, 30)
        close_time = time(15, 15)
        max_trades = 5
        cons_loss_limit = 4
        vix_lo, vix_hi = 10.0, 35.0

    def fee(n_contracts, legs=1):
        """Round-turn cost for n contracts (entry+exit)."""
        return commission_per_contract * n_contracts * legs * 2

    position = None
    trades_today = 0
    consecutive_losses = 0

    for i in range(len(day_bars)):
        bar = day_bars.iloc[i]
        bar_time = day_bars.index[i].time()
        S = float(bar['Close'])

        minutes_to_close = 16 * 60 - (bar_time.hour * 60 + bar_time.minute)
        T = max(minutes_to_close / (365 * 24 * 60), 1 / (365 * 24 * 60))
        tod_frac = (bar_time.hour * 60 + bar_time.minute - 570) / 390.0
        sigma = estimate_sigma(vix_close, tod_frac)

        # ---------- 管理持仓 ----------
        if position is not None:
            if position['type'] == 'long':
                cur_mid = bs_price(S, position['strike'], T, r, sigma,
                                   position['is_call'])
                # 卖出时滑点：在 mid 下方成交
                cur_exit = max(0.01, cur_mid - slippage)
                pnl_total = ((cur_exit - position['entry_fill'])
                             * 100 * position['contracts'])
                pnl_total -= fee(position['contracts'])
                pnl_pct = ((cur_exit - position['entry_fill'])
                           / position['entry_fill'] * 100)

            elif position['type'] == 'iron_condor':
                put_mid = bs_price(S, position['put_K'], T, r, sigma, False)
                call_mid = bs_price(S, position['call_K'], T, r, sigma, True)
                # 平仓时买回两条腿，价格更差
                cur_exit = put_mid + call_mid + 2 * slippage
                pnl_total = ((position['entry_fill'] - cur_exit)
                             * 100 * position['contracts'])
                pnl_total -= fee(position['contracts'], legs=2)
                pnl_pct = ((position['entry_fill'] - cur_exit)
                           / position['entry_fill'] * 100)

            else:  # credit_spread
                if position['is_call_spread']:
                    sell_mid = bs_price(S, position['sell_K'], T, r, sigma, True)
                    buy_mid = bs_price(S, position['buy_K'], T, r, sigma, True)
                else:
                    sell_mid = bs_price(S, position['sell_K'], T, r, sigma, False)
                    buy_mid = bs_price(S, position['buy_K'], T, r, sigma, False)
                cur_exit = (sell_mid - buy_mid) + 2 * slippage
                pnl_total = ((position['entry_fill'] - cur_exit)
                             * 100 * position['contracts'])
                pnl_total -= fee(position['contracts'], legs=2)
                denom = position['entry_fill']
                pnl_pct = ((position['entry_fill'] - cur_exit) / denom * 100
                           if denom > 0 else 0)

            exit_reason = None
            if pnl_pct >= position['tp_pct']:
                exit_reason = 'tp'
            elif pnl_pct <= -position['sl_pct']:
                exit_reason = 'sl'
            elif bar_time >= close_time:
                exit_reason = 'time'

            if exit_reason is not None:
                trades.append({
                    'entry_time': position['entry_time'],
                    'exit_time': day_bars.index[i],
                    'strategy': position['strategy'],
                    'pnl': round(pnl_total, 2),
                    'pnl_pct': round(pnl_pct, 1),
                    'exit_reason': exit_reason,
                    'contracts': position['contracts'],
                })
                capital += pnl_total
                consecutive_losses = (consecutive_losses + 1
                                      if pnl_total < 0 else 0)
                position = None
                continue

        # ---------- 开新仓 ----------
        if position is not None:
            continue
        if trades_today >= max_trades:
            continue
        if consecutive_losses >= cons_loss_limit:
            continue
        if not (entry_start <= bar_time <= entry_end):
            continue
        if vix_close > vix_hi or vix_close < vix_lo:
            continue
        if i < 12:
            continue

        trend, conf, _ = analyze_trend(day_bars.iloc[:i + 1], S, vix_close)
        new_pos = None

        # 日线趋势过滤：和日线方向相反的单子不做
        if daily_trend == 'uptrend':
            allow_call, allow_put = True, False
        elif daily_trend == 'downtrend':
            allow_call, allow_put = False, True
        else:  # choppy / unknown —— 允许双向但不做趋势单
            allow_call = allow_put = True

        if mode == 'steady':
            in_long_window = long_start <= bar_time <= long_end

            # 1) 高信心 → ATM 买方（只在日线方向一致时做）
            long_ok = (conf >= p['conf_long']
                       and trend in ('bullish', 'bearish')
                       and in_long_window
                       and ((trend == 'bullish' and allow_call)
                            or (trend == 'bearish' and allow_put)))
            if long_ok:
                is_call = trend == 'bullish'
                K = find_atm_strike(strikes, S)
                opt_mid = bs_price(S, K, T, r, sigma, is_call)
                opt_fill = opt_mid + slippage
                if p['long_min_price'] <= opt_fill <= p['long_max_price']:
                    budget = capital * (p['long_risk_pct'] / 100)
                    contracts = max(1, int(budget / (opt_fill * 100)))
                    new_pos = dict(
                        type='long',
                        strategy=f'long_{"call" if is_call else "put"}',
                        strike=K, is_call=is_call,
                        entry_fill=opt_fill, contracts=contracts,
                        tp_pct=p['long_tp_pct'], sl_pct=p['long_sl_pct'],
                        entry_time=day_bars.index[i],
                    )

            # 2) 中信心 → 30Δ 信用价差（同样受日线方向约束）
            spread_ok = (new_pos is None
                         and conf >= p['conf_spread']
                         and trend in ('bullish', 'bearish')
                         and ((trend == 'bearish' and allow_put is False)
                              # 下跌市卖 call spread（用信用 call 反弹）
                              or (trend == 'bullish' and allow_call is False)
                              # 上涨市卖 put spread
                              or (trend == 'bullish' and allow_call)
                              or (trend == 'bearish' and allow_put is False and False)))
            # 简化：信用价差也做方向一致性：上涨市卖 put、下跌市卖 call
            spread_ok = (new_pos is None
                         and conf >= p['conf_spread']
                         and ((trend == 'bullish' and allow_call)
                              or (trend == 'bearish' and not allow_call)))
            if spread_ok:
                # bullish → 卖 put spread；bearish → 卖 call spread
                is_call_sell = (trend == 'bearish')
                sell_K = find_strike_by_delta(
                    strikes, S, p['spread_short_delta'],
                    T, r, sigma, is_call_sell)
                if sell_K is not None:
                    width = p['spread_width']
                    if is_call_sell:
                        buy_K = sell_K + width
                        cr_mid = (bs_price(S, sell_K, T, r, sigma, True)
                                  - bs_price(S, buy_K, T, r, sigma, True))
                    else:
                        buy_K = sell_K - width
                        cr_mid = (bs_price(S, sell_K, T, r, sigma, False)
                                  - bs_price(S, buy_K, T, r, sigma, False))
                    cr_fill = max(0.05, cr_mid - 2 * slippage)
                    max_risk_per = (width - cr_fill) * 100
                    if cr_fill >= 0.20 and max_risk_per > 0:
                        budget = capital * (p['spread_risk_pct'] / 100)
                        contracts = max(1, int(budget / max_risk_per))
                        new_pos = dict(
                            type='credit_spread',
                            strategy=(
                                f'credit_{"call" if is_call_sell else "put"}'),
                            sell_K=sell_K, buy_K=buy_K,
                            is_call_spread=is_call_sell,
                            entry_fill=cr_fill, width=width,
                            contracts=contracts,
                            tp_pct=p['spread_tp_pct'],
                            sl_pct=p['spread_sl_pct'],
                            entry_time=day_bars.index[i],
                        )

            # 3) 低信心 / 震荡 → 铁鹰
            #    只在日线震荡或无明确趋势时做；明确趋势日不做卖方
            ic_ok = (new_pos is None
                     and (conf < p['conf_spread'] or trend == 'sideways')
                     and daily_trend in (None, 'choppy', 'unknown'))
            if ic_ok:
                put_K = find_strike_by_delta(
                    strikes, S, p['ic_short_delta'], T, r, sigma, False)
                call_K = find_strike_by_delta(
                    strikes, S, p['ic_short_delta'], T, r, sigma, True)
                if put_K and call_K and call_K - put_K > 5:
                    put_mid = bs_price(S, put_K, T, r, sigma, False)
                    call_mid = bs_price(S, call_K, T, r, sigma, True)
                    cr_mid = put_mid + call_mid
                    cr_fill = max(0.05, cr_mid - 2 * slippage)
                    wing = p['ic_wing']
                    max_risk_per = (wing - cr_fill) * 100
                    if cr_fill >= p['ic_min_credit'] and max_risk_per > 0:
                        budget = capital * (p['ic_risk_pct'] / 100)
                        contracts = max(1, int(budget / max_risk_per))
                        new_pos = dict(
                            type='iron_condor',
                            strategy='iron_condor',
                            put_K=put_K, call_K=call_K,
                            entry_fill=cr_fill,
                            wing_width=wing,
                            contracts=contracts,
                            tp_pct=p['ic_tp_pct'], sl_pct=p['ic_sl_pct'],
                            entry_time=day_bars.index[i],
                        )

        else:  # turbo
            in_long_window = long_start <= bar_time <= long_end
            turbo_ok = (conf >= p['conf_min']
                        and trend in ('bullish', 'bearish')
                        and in_long_window
                        and ((trend == 'bullish' and allow_call)
                             or (trend == 'bearish' and not allow_call)))
            if turbo_ok:
                is_call = trend == 'bullish'
                K = find_strike_by_delta(
                    strikes, S, p['long_delta'], T, r, sigma, is_call)
                if K is not None:
                    opt_mid = bs_price(S, K, T, r, sigma, is_call)
                    opt_fill = opt_mid + slippage
                    if p['long_min_price'] <= opt_fill <= p['long_max_price']:
                        budget = capital * (p['long_risk_pct'] / 100)
                        contracts = max(1, int(budget / (opt_fill * 100)))
                        new_pos = dict(
                            type='long',
                            strategy=f'turbo_long_{"call" if is_call else "put"}',
                            strike=K, is_call=is_call,
                            entry_fill=opt_fill,
                            contracts=contracts,
                            tp_pct=p['long_tp_pct'], sl_pct=p['long_sl_pct'],
                            entry_time=day_bars.index[i],
                        )

        if new_pos is not None:
            position = new_pos
            trades_today += 1

    # ---------- 收盘强平 ----------
    if position is not None:
        last_S = float(day_bars['Close'].iloc[-1])
        T = 1.0 / (365 * 24 * 60)
        sigma = estimate_sigma(vix_close, 0.95)

        if position['type'] == 'long':
            cur_mid = bs_price(last_S, position['strike'], T, r, sigma,
                               position['is_call'])
            cur_exit = max(0.01, cur_mid - slippage)
            pnl_total = ((cur_exit - position['entry_fill'])
                         * 100 * position['contracts'])
            pnl_total -= fee(position['contracts'])
            pnl_pct = ((cur_exit - position['entry_fill'])
                       / position['entry_fill'] * 100)
        elif position['type'] == 'iron_condor':
            put_mid = bs_price(last_S, position['put_K'], T, r, sigma, False)
            call_mid = bs_price(last_S, position['call_K'], T, r, sigma, True)
            cur_exit = put_mid + call_mid + 2 * slippage
            pnl_total = ((position['entry_fill'] - cur_exit)
                         * 100 * position['contracts'])
            pnl_total -= fee(position['contracts'], legs=2)
            pnl_pct = ((position['entry_fill'] - cur_exit)
                       / position['entry_fill'] * 100)
        else:  # credit_spread
            if position['is_call_spread']:
                sell_mid = bs_price(last_S, position['sell_K'], T, r, sigma, True)
                buy_mid = bs_price(last_S, position['buy_K'], T, r, sigma, True)
            else:
                sell_mid = bs_price(last_S, position['sell_K'], T, r, sigma, False)
                buy_mid = bs_price(last_S, position['buy_K'], T, r, sigma, False)
            cur_exit = (sell_mid - buy_mid) + 2 * slippage
            pnl_total = ((position['entry_fill'] - cur_exit)
                         * 100 * position['contracts'])
            pnl_total -= fee(position['contracts'], legs=2)
            pnl_pct = ((position['entry_fill'] - cur_exit)
                       / position['entry_fill'] * 100) if position['entry_fill'] > 0 else 0

        trades.append({
            'entry_time': position['entry_time'],
            'exit_time': day_bars.index[-1],
            'strategy': position['strategy'],
            'pnl': round(pnl_total, 2),
            'pnl_pct': round(pnl_pct, 1),
            'exit_reason': 'eod',
            'contracts': position['contracts'],
        })
        capital += pnl_total

    return trades, capital


# ---------------------------------------------------------------------------
# 主回测
# ---------------------------------------------------------------------------

def _load_data(days):
    print(f"\n📥 下载 SPY {days}天 5分钟数据...")
    df = yf.Ticker('SPY').history(period=f'{days}d', interval='5m')
    print(f"   共 {len(df)} 根 K 线")

    print(f"📥 下载 ^VIX 日线...")
    vix_df = yf.Ticker('^VIX').history(period=f'{days+10}d', interval='1d')

    print(f"📥 下载 SPY 日线（用于分段）...")
    spy_daily = yf.Ticker('SPY').history(period=f'{days+30}d', interval='1d')

    df = df.copy()
    df['date'] = df.index.date
    vix_df = vix_df.copy()
    vix_df['date'] = vix_df.index.date
    vix_map = dict(zip(vix_df['date'], vix_df['Close'].astype(float)))

    spy_daily = spy_daily.copy()
    spy_daily['date'] = spy_daily.index.date
    spy_daily['ret'] = spy_daily['Close'].pct_change()
    spy_daily['ma20'] = spy_daily['Close'].rolling(20).mean()

    dates = sorted(df['date'].unique())
    print(f"✅ {len(dates)} 个交易日: {dates[0]} ~ {dates[-1]}\n")
    return df, vix_map, spy_daily, dates


def classify_regime(d, spy_daily):
    """根据【前一交易日】的收盘 vs 20MA 分类（避免 look-ahead bias）。
    10:00 做决策时能知道的只有前一天的收盘。"""
    dates = list(spy_daily['date'])
    if d not in dates:
        return 'unknown'
    idx = dates.index(d)
    if idx == 0:
        return 'unknown'
    prev = spy_daily.iloc[idx - 1]
    if pd.isna(prev['ma20']):
        return 'unknown'
    if prev['Close'] > prev['ma20'] * 1.005:
        return 'uptrend'
    if prev['Close'] < prev['ma20'] * 0.995:
        return 'downtrend'
    return 'choppy'


def run_backtest(days=60, mode='all', capital_start=2000,
                 params=None, commission=0.65, slippage=0.01,
                 regime=False, quiet=False, daily_filter=False):
    df, vix_map, spy_daily, dates = _load_data(days)
    fee_label = (f"佣金 ${commission}/张 + 滑点 ${slippage:.2f}"
                 if commission > 0 or slippage > 0 else "无手续费/滑点")
    if daily_filter:
        fee_label += " | 20MA 趋势过滤"

    results = {}
    modes = ['steady', 'turbo'] if mode == 'all' else [mode]

    for m in modes:
        if not quiet:
            print(f"{'=' * 65}")
            print(f"🔄 回测 {m.upper():<8} | 起始 ${capital_start:,.0f} | {fee_label}")
            print(f"{'=' * 65}")

        capital = float(capital_start)
        equity_curve = [capital]
        all_trades = []
        regime_trades = defaultdict(list)
        prev_close = None
        skipped = 0

        for d in dates:
            day_df = df[df['date'] == d].copy()
            if len(day_df) < 20:
                skipped += 1
                continue
            if is_event_day(datetime.combine(d, time())):
                prev_close = float(day_df['Close'].iloc[-1])
                skipped += 1
                continue

            vix = float(vix_map.get(d, 16.0))
            if prev_close is None:
                prev_close = float(day_df['Open'].iloc[0])

            dtrend = classify_regime(d, spy_daily) if daily_filter else None
            day_trades, capital = backtest_day(
                day_df, vix, prev_close, m, capital,
                params=params,
                commission_per_contract=commission,
                slippage=slippage,
                daily_trend=dtrend,
            )
            all_trades.extend(day_trades)
            equity_curve.append(capital)
            prev_close = float(day_df['Close'].iloc[-1])

            if regime:
                r = classify_regime(d, spy_daily)
                regime_trades[r].extend(day_trades)

        if not quiet:
            _print_report(m, capital_start, capital, equity_curve,
                          all_trades, len(dates), skipped)
            if regime:
                _print_regime(regime_trades, capital_start)

        results[m] = _build_result_dict(
            all_trades, capital, capital_start, equity_curve)

    if not quiet and len(results) == 2:
        _print_comparison(results)

    if not quiet:
        print()
    return results


def _print_report(m, capital_start, capital, equity_curve,
                  all_trades, total_dates, skipped):
    if not all_trades:
        print("⚠️ 没有产生任何交易\n")
        return

    pnls = [t['pnl'] for t in all_trades]
    wins = [t for t in all_trades if t['pnl'] > 0]
    losses = [t for t in all_trades if t['pnl'] <= 0]

    equity = np.array(equity_curve, dtype=float)
    peak = np.maximum.accumulate(equity)
    dd = (peak - equity) / np.where(peak == 0, 1, peak) * 100
    total_ret = (capital - capital_start) / capital_start * 100

    by_strat = defaultdict(list)
    for t in all_trades:
        by_strat[t['strategy']].append(t)

    by_exit = defaultdict(int)
    for t in all_trades:
        by_exit[t['exit_reason']] += 1

    print(f"\n📊 总体结果:")
    print(f"   交易日:       {total_dates - skipped} (跳过 {skipped})")
    print(f"   总交易:       {len(all_trades)}")
    print(f"   盈利:         {len(wins)} ({len(wins)/len(all_trades)*100:.1f}%)")
    print(f"   亏损:         {len(losses)} ({len(losses)/len(all_trades)*100:.1f}%)")
    print()
    print(f"   起始资金:     ${capital_start:,.2f}")
    print(f"   最终资金:     ${capital:,.2f}")
    print(f"   总收益:       {total_ret:+.1f}%")
    print(f"   最大回撤:     {dd.max():.1f}%")
    print()
    if wins:
        print(f"   平均盈利:     ${np.mean([t['pnl'] for t in wins]):+.2f}")
    if losses:
        print(f"   平均亏损:     ${np.mean([t['pnl'] for t in losses]):+.2f}")
    if wins and losses:
        tw = sum(t['pnl'] for t in wins)
        tl = abs(sum(t['pnl'] for t in losses))
        pf = tw / tl if tl > 0 else float('inf')
        print(f"   盈亏比(PF):   {pf:.2f}")
    print(f"   平均/笔:      ${np.mean(pnls):+.2f}")
    print()
    print(f"   退出:  ✅TP={by_exit['tp']}  ❌SL={by_exit['sl']}  "
          f"⏰Time={by_exit['time']}  🔚EOD={by_exit['eod']}")

    print(f"\n📋 按策略:")
    print(f"   {'策略':<22} {'笔数':>4}  {'胜率':>5}  {'平均PnL':>10}  {'总PnL':>10}")
    print("   " + "-" * 58)
    for strat, ts in sorted(by_strat.items()):
        w = len([t for t in ts if t['pnl'] > 0])
        avg_p = np.mean([t['pnl'] for t in ts])
        sum_p = sum(t['pnl'] for t in ts)
        print(f"   {strat:<22} {len(ts):>4}  {w/len(ts)*100:>4.0f}%  "
              f"${avg_p:>+9.2f}  ${sum_p:>+9.2f}")

    print(f"\n📅 周度净值:")
    week_returns = []
    wk = 0
    for i in range(0, len(equity_curve) - 1, 5):
        wk += 1
        s = equity_curve[i]
        e_idx = min(i + 5, len(equity_curve) - 1)
        e = equity_curve[e_idx]
        ret = (e - s) / s * 100 if s != 0 else 0
        week_returns.append(ret)
        bar_len = max(0, int(abs(ret) / 5))
        bar = "█" * min(bar_len, 20)
        sign = "🟢" if ret >= 0 else "🔴"
        print(f"   W{wk:>2}: ${s:>10,.0f} → ${e:>10,.0f}  "
              f"{sign} {ret:>+7.1f}% {bar}")

    if week_returns:
        dbl = sum(1 for r in week_returns if r >= 100)
        print(f"\n   周翻倍: {dbl}/{len(week_returns)} "
              f"({dbl / len(week_returns) * 100:.0f}%)")
        print(f"   周均:   {np.mean(week_returns):+.1f}%")
        print(f"   最佳周: {max(week_returns):+.1f}%")
        print(f"   最差周: {min(week_returns):+.1f}%")
    print()


def _build_result_dict(all_trades, capital, capital_start, equity_curve):
    if not all_trades:
        return {'trades': [], 'capital': capital, 'total_ret': 0,
                'win_rate': 0, 'max_dd': 0, 'pf': None}
    wins = [t for t in all_trades if t['pnl'] > 0]
    losses = [t for t in all_trades if t['pnl'] <= 0]
    equity = np.array(equity_curve, dtype=float)
    peak = np.maximum.accumulate(equity)
    dd = (peak - equity) / np.where(peak == 0, 1, peak) * 100
    pf = None
    if wins and losses:
        tw = sum(t['pnl'] for t in wins)
        tl = abs(sum(t['pnl'] for t in losses))
        pf = tw / tl if tl > 0 else None
    return {
        'trades': all_trades,
        'capital': capital,
        'total_ret': (capital - capital_start) / capital_start * 100,
        'win_rate': len(wins) / len(all_trades) * 100,
        'max_dd': float(dd.max()),
        'pf': pf,
    }


def _print_comparison(results):
    s = results.get('steady', {})
    t = results.get('turbo', {})
    if not s or not t:
        return
    print(f"{'=' * 65}")
    print(f"⚖️  STEADY vs TURBO")
    print(f"{'=' * 65}")
    print(f"   {'指标':<14} {'STEADY':>14} {'TURBO':>14}")
    print("   " + "-" * 44)
    print(f"   {'总收益':<14} {s['total_ret']:>+13.1f}% {t['total_ret']:>+13.1f}%")
    print(f"   {'胜率':<14} {s['win_rate']:>13.1f}% {t['win_rate']:>13.1f}%")
    print(f"   {'最大回撤':<14} {s['max_dd']:>13.1f}% {t['max_dd']:>13.1f}%")
    print(f"   {'交易笔数':<14} {len(s['trades']):>14} {len(t['trades']):>14}")
    if s['pf'] is not None and t['pf'] is not None:
        print(f"   {'盈亏比':<14} {s['pf']:>14.2f} {t['pf']:>14.2f}")
    print(f"   {'最终资金':<14} ${s['capital']:>12,.0f} ${t['capital']:>12,.0f}")


def _print_regime(regime_trades, capital_start):
    print(f"\n🌦️  按市场环境分段（SPY 相对 20MA）:")
    print(f"   {'环境':<12} {'笔数':>5}  {'胜率':>5}  {'总PnL':>10}  {'平均/笔':>10}  {'PF':>6}")
    print("   " + "-" * 58)
    for r in ('uptrend', 'choppy', 'downtrend', 'unknown'):
        ts = regime_trades.get(r, [])
        if not ts:
            continue
        wins = [t for t in ts if t['pnl'] > 0]
        losses = [t for t in ts if t['pnl'] <= 0]
        total = sum(t['pnl'] for t in ts)
        avg = total / len(ts)
        tw = sum(t['pnl'] for t in wins) if wins else 0
        tl = abs(sum(t['pnl'] for t in losses)) if losses else 0
        pf = f"{tw/tl:.2f}" if tl > 0 else "∞"
        wr = len(wins) / len(ts) * 100
        label = {'uptrend': '📈 上涨', 'downtrend': '📉 下跌',
                 'choppy': '〰️ 震荡', 'unknown': '❓ 未知'}.get(r, r)
        print(f"   {label:<12} {len(ts):>5}  {wr:>4.0f}%  "
              f"${total:>+9.0f}  ${avg:>+9.2f}  {pf:>6}")


# ---------------------------------------------------------------------------
# 参数扫描
# ---------------------------------------------------------------------------

STEADY_GRID = {
    # (label, overrides)
    'baseline 80/40 IC40/120': {},
    'tight IC SL 80': {'ic_sl_pct': 80, 'ic_tp_pct': 40},
    'tight IC SL 80 + TP30': {'ic_sl_pct': 80, 'ic_tp_pct': 30},
    'IC 12Δ + SL80': {'ic_short_delta': 0.12, 'ic_sl_pct': 80},
    'long TP50 SL40': {'long_tp_pct': 50, 'long_sl_pct': 40},
    'long TP60 SL30': {'long_tp_pct': 60, 'long_sl_pct': 30},
    'long TP40 SL30': {'long_tp_pct': 40, 'long_sl_pct': 30},
    'long TP40 SL25': {'long_tp_pct': 40, 'long_sl_pct': 25},
    'long risk2% TP50': {'long_risk_pct': 2.0, 'long_tp_pct': 50,
                          'long_sl_pct': 40},
    'spread only (no long)': {
        'conf_long': 101,  # 禁用 long
        'conf_spread': 60,
        'spread_tp_pct': 40, 'spread_sl_pct': 80,
    },
    'IC only (no long/spread)': {
        'conf_long': 101, 'conf_spread': 101,
        'ic_sl_pct': 80, 'ic_tp_pct': 35,
    },
    'mixed v2: TP50 + IC SL80': {
        'long_tp_pct': 50, 'long_sl_pct': 35,
        'ic_sl_pct': 80, 'ic_tp_pct': 35,
        'spread_tp_pct': 40, 'spread_sl_pct': 80,
    },
    'mixed v3: tighter all': {
        'long_tp_pct': 50, 'long_sl_pct': 30,
        'ic_sl_pct': 70, 'ic_tp_pct': 30,
        'spread_tp_pct': 40, 'spread_sl_pct': 70,
    },
    # ---- v2: 配合 20MA 过滤 ----
    'v2 long-only TP60 SL30': {
        'conf_long': 70, 'conf_spread': 101, 'ic_short_delta': 0.05,
        'ic_min_credit': 99,  # 禁用 IC
        'long_tp_pct': 60, 'long_sl_pct': 30,
    },
    'v2 long-only TP80 SL30': {
        'conf_long': 70, 'conf_spread': 101, 'ic_min_credit': 99,
        'long_tp_pct': 80, 'long_sl_pct': 30,
    },
    'v2 long-only TP100 SL35': {
        'conf_long': 70, 'conf_spread': 101, 'ic_min_credit': 99,
        'long_tp_pct': 100, 'long_sl_pct': 35,
    },
    'v2 long-only conf75 TP100': {
        'conf_long': 75, 'conf_spread': 101, 'ic_min_credit': 99,
        'long_tp_pct': 100, 'long_sl_pct': 35,
    },
    'v2 long+spread TP80 SL35': {
        'conf_long': 70, 'conf_spread': 65,
        'ic_min_credit': 99,
        'long_tp_pct': 80, 'long_sl_pct': 35,
        'spread_tp_pct': 50, 'spread_sl_pct': 70,
    },
    'v2 directional-only risk4%': {
        'conf_long': 70, 'conf_spread': 101, 'ic_min_credit': 99,
        'long_tp_pct': 80, 'long_sl_pct': 30,
        'long_risk_pct': 4.0,
    },
    'v2 conf80 TP80 SL30': {
        'conf_long': 80, 'conf_spread': 101, 'ic_min_credit': 99,
        'long_tp_pct': 80, 'long_sl_pct': 30,
    },
}

TURBO_GRID = {
    'baseline 200/65 18Δ': {},
    'TP150 SL50': {'long_tp_pct': 150, 'long_sl_pct': 50},
    'TP120 SL40': {'long_tp_pct': 120, 'long_sl_pct': 40},
    'TP200 SL50': {'long_tp_pct': 200, 'long_sl_pct': 50},
    'TP250 SL65': {'long_tp_pct': 250, 'long_sl_pct': 65},
    '25Δ TP150 SL50': {'long_delta': 0.25, 'long_tp_pct': 150,
                       'long_sl_pct': 50},
    '15Δ TP200 SL60': {'long_delta': 0.15, 'long_tp_pct': 200,
                       'long_sl_pct': 60},
    '12Δ TP250 SL60': {'long_delta': 0.12, 'long_tp_pct': 250,
                       'long_sl_pct': 60},
    'risk 7% TP200 SL50': {'long_risk_pct': 7.0, 'long_tp_pct': 200,
                            'long_sl_pct': 50},
    'risk 5% TP150 SL50': {'long_risk_pct': 5.0, 'long_tp_pct': 150,
                            'long_sl_pct': 50},
    'conf65 TP200 SL50': {'conf_min': 65, 'long_tp_pct': 200,
                           'long_sl_pct': 50},
    'conf50 25Δ TP150 SL50': {'conf_min': 50, 'long_delta': 0.25,
                               'long_tp_pct': 150, 'long_sl_pct': 50},
}


def scan(days, capital_start, which, commission, slippage,
         daily_filter=False):
    """跑参数扫描表。"""
    print(f"\n🔬 参数扫描: {which.upper()} | "
          f"${commission}/张 + ${slippage:.2f} 滑点"
          f"{' | 20MA 过滤' if daily_filter else ''}")
    grid = STEADY_GRID if which == 'steady' else TURBO_GRID

    # 数据只下载一次
    df, vix_map, spy_daily, dates = _load_data(days)

    rows = []
    for label, overrides in grid.items():
        capital = float(capital_start)
        equity_curve = [capital]
        all_trades = []
        prev_close = None

        for d in dates:
            day_df = df[df['date'] == d].copy()
            if len(day_df) < 20:
                continue
            if is_event_day(datetime.combine(d, time())):
                prev_close = float(day_df['Close'].iloc[-1])
                continue
            vix = float(vix_map.get(d, 16.0))
            if prev_close is None:
                prev_close = float(day_df['Open'].iloc[0])

            dtrend = classify_regime(d, spy_daily) if daily_filter else None
            day_trades, capital = backtest_day(
                day_df, vix, prev_close, which, capital,
                params=overrides,
                commission_per_contract=commission,
                slippage=slippage,
                daily_trend=dtrend,
            )
            all_trades.extend(day_trades)
            equity_curve.append(capital)
            prev_close = float(day_df['Close'].iloc[-1])

        if not all_trades:
            rows.append((label, 0, 0, 0, 0, 0, capital_start, capital))
            continue

        wins = [t for t in all_trades if t['pnl'] > 0]
        losses = [t for t in all_trades if t['pnl'] <= 0]
        wr = len(wins) / len(all_trades) * 100
        total_ret = (capital - capital_start) / capital_start * 100
        equity = np.array(equity_curve, dtype=float)
        peak = np.maximum.accumulate(equity)
        dd = float(((peak - equity) / np.where(peak == 0, 1, peak)).max() * 100)
        tw = sum(t['pnl'] for t in wins)
        tl = abs(sum(t['pnl'] for t in losses))
        pf = tw / tl if tl > 0 else float('inf')
        rows.append((label, len(all_trades), wr, total_ret, dd, pf,
                     capital_start, capital))

    # 排序：按总收益降序
    rows.sort(key=lambda r: r[3], reverse=True)

    print(f"\n📊 {which.upper()} 参数扫描结果（按总收益排序）:")
    print(f"   {'参数组合':<32} {'笔数':>4}  {'胜率':>5}  "
          f"{'总收益':>9}  {'回撤':>7}  {'PF':>6}  最终$")
    print("   " + "-" * 88)
    for label, n, wr, ret, dd, pf, c0, c1 in rows:
        pf_s = f"{pf:.2f}" if pf != float('inf') else "  ∞"
        marker = " ✅" if ret > 0 else " 💀"
        print(f"   {label:<32} {n:>4}  {wr:>4.0f}%  "
              f"{ret:>+8.1f}%  {dd:>6.1f}%  {pf_s:>6}  "
              f"${c1:>8,.0f}{marker}")
    print()
    return rows


def main():
    p = argparse.ArgumentParser(description='SPY 0DTE 历史回测')
    p.add_argument('--days', type=int, default=60)
    p.add_argument('--mode', choices=['steady', 'turbo', 'all'], default='all')
    p.add_argument('--capital', type=float, default=2000)
    p.add_argument('--no-fees', action='store_true',
                   help='关闭佣金和滑点')
    p.add_argument('--commission', type=float, default=0.65,
                   help='每张合约单边佣金（默认 $0.65）')
    p.add_argument('--slippage', type=float, default=0.01,
                   help='单边滑点（美元，默认 0.01 = 1 cent）')
    p.add_argument('--regime', action='store_true',
                   help='按市场环境（上涨/震荡/下跌）分段统计')
    p.add_argument('--filter', dest='daily_filter', action='store_true',
                   help='启用 20MA 日线趋势过滤（只做顺势单）')
    p.add_argument('--scan', choices=['steady', 'turbo'],
                   help='跑参数扫描表（不跑普通回测）')
    args = p.parse_args()

    if args.no_fees:
        args.commission = 0
        args.slippage = 0

    if args.scan:
        scan(args.days, args.capital, args.scan,
             args.commission, args.slippage,
             daily_filter=args.daily_filter)
    else:
        run_backtest(args.days, args.mode, args.capital,
                     commission=args.commission, slippage=args.slippage,
                     regime=args.regime, daily_filter=args.daily_filter)


if __name__ == '__main__':
    main()
