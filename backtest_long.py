#!/usr/bin/env python3
"""
长期日线回测：用 3 年 SPY 日线 + VIX 近似 0DTE 策略表现。

局限（必须明白）：
- 日线没有分时路径，用 high/low 判断 TP/SL 是否触发
- 假设开盘后 30 分钟入场（用 Open 近似），收盘平仓
- IV 用当天 VIX 近似
- 这是一个粗略模型，不能代替分时回测，但能看 3 年不同市场环境

用法：
    python3 backtest_long.py                    # 3 年
    python3 backtest_long.py --years 1
    python3 backtest_long.py --capital 2000
    python3 backtest_long.py --no-fees
"""

import argparse
import math
from datetime import datetime, time
from collections import defaultdict

import numpy as np
import yfinance as yf
from scipy.stats import norm

from backtest import (
    bs_price, bs_delta, estimate_sigma, find_strike_by_delta,
    find_atm_strike, is_event_day, classify_regime,
)


def load_long_data(years):
    days = int(years * 365)
    print(f"\n📥 下载 SPY {years} 年日线...")
    spy = yf.Ticker('SPY').history(period=f'{days}d', interval='1d')
    print(f"   共 {len(spy)} 根日 K")
    print(f"📥 下载 ^VIX 同期...")
    vix = yf.Ticker('^VIX').history(period=f'{days}d', interval='1d')
    spy = spy.dropna().copy()
    vix = vix.dropna().copy()
    spy['ma20'] = spy['Close'].rolling(20).mean()
    spy['ma50'] = spy['Close'].rolling(50).mean()
    spy['ret'] = spy['Close'].pct_change()
    spy['date'] = spy.index.date
    vix['date'] = vix.index.date
    vix_map = dict(zip(vix['date'], vix['Close'].astype(float)))
    return spy, vix_map


def daily_sigma(vix, tod_frac=0.5):
    return estimate_sigma(vix, tod_frac)


def simulate_long_day(row, vix, prev_row, mode, capital, params,
                      commission=0.65, slippage=0.01, daily_filter=True,
                      max_contracts=20):
    """模拟一天 0DTE 交易（用日线 OHLC 近似）。
    返回 (trades, end_capital)。
    """
    S_open = float(row['Open'])
    S_close = float(row['Close'])
    S_high = float(row['High'])
    S_low = float(row['Low'])
    d = row['date']

    # 用前一天判断趋势（避免 look-ahead）
    if daily_filter and prev_row is not None and not np.isnan(prev_row['ma20']):
        if prev_row['Close'] > prev_row['ma20'] * 1.005:
            dtrend = 'uptrend'
        elif prev_row['Close'] < prev_row['ma20'] * 0.995:
            dtrend = 'downtrend'
        else:
            dtrend = 'choppy'
    else:
        dtrend = None

    trades = []

    # 跳过事件日
    if is_event_day(datetime.combine(d, time())):
        return trades, capital

    # VIX 过滤
    if mode == 'steady':
        if vix < 12 or vix > 30:
            return trades, capital
        conf_long, conf_spread = params.get('conf_long', 75), params.get('conf_spread', 65)
        long_tp, long_sl = params.get('long_tp_pct', 80), params.get('long_sl_pct', 40)
        long_risk = params.get('long_risk_pct', 3.0) / 100
        ic_short_delta = params.get('ic_short_delta', 0.16)
        ic_min_credit = params.get('ic_min_credit', 0.40)
        ic_tp, ic_sl = params.get('ic_tp_pct', 40), params.get('ic_sl_pct', 120)
        ic_wing = params.get('ic_wing', 5.0)
        ic_risk = params.get('ic_risk_pct', 8.0) / 100
    else:
        if vix < 10 or vix > 35:
            return trades, capital
        long_delta = params.get('long_delta', 0.18)
        long_tp, long_sl = params.get('long_tp_pct', 200), params.get('long_sl_pct', 65)
        long_risk = params.get('long_risk_pct', 10.0) / 100
        conf_min = params.get('conf_min', 55)

    # 用前一天数据算简单的趋势信号（RSI + MA 交叉）
    if prev_row is None or np.isnan(prev_row['ma20']):
        return trades, capital
    spy_ref = S_open
    lo = math.floor(spy_ref / 5) * 5 - 25
    hi = lo + 70
    strikes = np.arange(lo, hi, 1.0)

    r = 0.045
    # 入场时间约 10:00，剩余约 6 小时 = 0.25 天
    T_entry = 6 / (24 * 365)
    # 收盘剩余 ~0
    T_close = 1 / (24 * 365 * 60)

    # 信号：前一天收盘相对 MA20 + 前一天的涨跌
    prev_close = float(prev_row['Close'])
    prev_ma20 = float(prev_row['ma20'])
    prev_ret = float(prev_row['ret']) if not np.isnan(prev_row['ret']) else 0

    # 简化信心评分：和 MarketAnalyzer 对齐
    bull = bear = 0
    if prev_close > prev_ma20 * 1.002: bull += 2
    elif prev_close < prev_ma20 * 0.998: bear += 2
    if prev_ret > 0.005: bull += 1
    elif prev_ret < -0.005: bear += 1
    if not np.isnan(prev_row['ma50']):
        if prev_close > float(prev_row['ma50']): bull += 1
        else: bear += 1

    conf = int(50 + 30 * abs(bull - bear) / max(bull + bear, 1))
    if bull - bear >= 2:
        trend = 'bullish'
    elif bear - bull >= 2:
        trend = 'bearish'
    else:
        trend = 'sideways'

    # 日线过滤
    allow_call = dtrend != 'downtrend'
    allow_put = dtrend != 'uptrend'

    sigma = daily_sigma(vix, 0.3)

    if mode == 'steady':
        strategy_type = None
        is_call = False
        if conf >= conf_long and trend in ('bullish', 'bearish'):
            if (trend == 'bullish' and allow_call) or (trend == 'bearish' and allow_put):
                strategy_type = 'long'
                is_call = trend == 'bullish'
        if (strategy_type is None and conf < conf_spread) or trend == 'sideways':
            if dtrend in (None, 'choppy'):
                strategy_type = 'iron_condor'

        if strategy_type is None:
            return trades, capital

        if strategy_type == 'long':
            K = find_atm_strike(strikes, S_open)
            opt_mid = bs_price(S_open, K, T_entry, r, sigma, is_call)
            opt_fill = opt_mid + slippage
            if not (0.30 <= opt_fill <= 5.00):
                return trades, capital
            budget = capital * long_risk
            contracts = max(1, int(budget / (opt_fill * 100)))
            contracts = min(contracts, max_contracts)

            # 用 high/low 测试 TP/SL
            # 看涨：TP 在 high 时可能触发；SL 在 low 时可能触发
            # 假设两者都触及，保守取 SL（因为通常开盘波动先打止损）
            # 更合理：如果收盘价方向明确，用收盘价；否则保守假设 SL
            if is_call:
                opt_high = bs_price(S_high, K, T_entry * 0.7, r, sigma * 1.05, True)
                opt_low = bs_price(S_low, K, T_entry * 0.7, r, sigma * 0.95, True)
            else:
                opt_high = bs_price(S_high, K, T_entry * 0.7, r, sigma * 0.95, False)
                opt_low = bs_price(S_low, K, T_entry * 0.7, r, sigma * 1.05, False)

            tp_price = opt_fill * (1 + long_tp / 100)
            sl_price = opt_fill * max(0.01, 1 - long_sl / 100)
            hit_tp = False
            hit_sl = False
            if is_call:
                hit_tp = opt_high >= tp_price
                hit_sl = opt_low <= sl_price
            else:
                hit_tp = opt_low >= tp_price  # put: low 时 put 涨
                hit_sl = opt_high <= sl_price

            # 收盘价做决定性判断：
            # - 如果 close 明显在我们方向，假设 TP 先触发
            # - 如果 close 明显反向，假设 SL 先触发
            # - 都触发且收盘不明：50/50（用 idx 决定）
            close_opt = bs_price(S_close, K, T_close, r, sigma, is_call)
            close_in_favor = (is_call and S_close > S_open * 1.001) or \
                             ((not is_call) and S_close < S_open * 0.999)
            close_against = (is_call and S_close < S_open * 0.999) or \
                            ((not is_call) and S_close > S_open * 1.001)

            if hit_tp and hit_sl:
                if close_in_favor:
                    exit_price, reason = tp_price, 'tp'
                elif close_against:
                    exit_price, reason = sl_price, 'sl'
                else:
                    # 50/50，但用日期 hash 保持确定性
                    if hash(d.toordinal()) % 2 == 0:
                        exit_price, reason = tp_price, 'tp'
                    else:
                        exit_price, reason = sl_price, 'sl'
            elif hit_tp:
                exit_price, reason = tp_price, 'tp'
            elif hit_sl:
                exit_price, reason = sl_price, 'sl'
            else:
                exit_price, reason = close_opt, 'eod'
            exit_price = max(0.01, exit_price - slippage)
            pnl = (exit_price - opt_fill) * 100 * contracts - commission * contracts * 2
            trades.append({
                'date': d, 'strategy': f'long_{"call" if is_call else "put"}',
                'pnl': pnl, 'exit_reason': reason,
                'trend': trend, 'dtrend': dtrend,
            })
            return trades, capital + pnl

        elif strategy_type == 'iron_condor':
            put_K = find_strike_by_delta(strikes, S_open, ic_short_delta, T_entry, r, sigma, False)
            call_K = find_strike_by_delta(strikes, S_open, ic_short_delta, T_entry, r, sigma, True)
            if put_K is None or call_K is None or call_K - put_K < 5:
                return trades, capital
            put_mid = bs_price(S_open, put_K, T_entry, r, sigma, False)
            call_mid = bs_price(S_open, call_K, T_entry, r, sigma, True)
            cr_mid = put_mid + call_mid
            cr_fill = max(0.05, cr_mid - 2 * slippage)
            max_risk = (ic_wing - cr_fill) * 100
            if cr_fill < ic_min_credit or max_risk <= 0:
                return trades, capital
            budget = capital * ic_risk
            contracts = max(1, int(budget / max_risk))

            # 收盘时 IC 的价值
            put_val = bs_price(S_close, put_K, T_close, r, sigma, False)
            call_val = bs_price(S_close, call_K, T_close, r, sigma, True)
            cr_close = put_val + call_val + 2 * slippage
            # high/low 期间最差值
            worst_put = bs_price(S_low, put_K, T_entry * 0.7, r, sigma * 1.1, False)
            worst_call = bs_price(S_high, call_K, T_entry * 0.7, r, sigma * 1.1, True)
            worst_cr = worst_put + worst_call + 2 * slippage
            best_cr = 0.01

            worst_pnl_pct = (cr_fill - worst_cr) / cr_fill * 100
            best_pnl_pct = (cr_fill - best_cr) / cr_fill * 100
            end_pnl_pct = (cr_fill - cr_close) / cr_fill * 100

            if best_pnl_pct >= ic_tp:
                exit_cr = cr_fill * (1 - ic_tp / 100)
                reason = 'tp'
            elif worst_pnl_pct <= -ic_sl:
                exit_cr = cr_fill * (1 + ic_sl / 100)
                reason = 'sl'
            else:
                exit_cr = cr_close
                reason = 'eod'
            pnl = (cr_fill - exit_cr) * 100 * contracts - commission * contracts * 4
            trades.append({
                'date': d, 'strategy': 'iron_condor',
                'pnl': pnl, 'exit_reason': reason,
                'trend': trend, 'dtrend': dtrend,
            })
            return trades, capital + pnl

    else:  # turbo
        if conf < conf_min or trend not in ('bullish', 'bearish'):
            return trades, capital
        is_call = trend == 'bullish'
        if (is_call and not allow_call) or ((not is_call) and not allow_put):
            return trades, capital
        K = find_strike_by_delta(strikes, S_open, long_delta, T_entry, r, sigma, is_call)
        if K is None:
            return trades, capital
        opt_mid = bs_price(S_open, K, T_entry, r, sigma, is_call)
        opt_fill = opt_mid + slippage
        if not (0.15 <= opt_fill <= 2.50):
            return trades, capital
        budget = capital * long_risk
        contracts = max(1, int(budget / (opt_fill * 100)))
        contracts = min(contracts, max_contracts)

        if is_call:
            opt_high = bs_price(S_high, K, T_entry * 0.7, r, sigma * 1.05, True)
            opt_low = bs_price(S_low, K, T_entry * 0.7, r, sigma * 0.95, True)
        else:
            opt_high = bs_price(S_high, K, T_entry * 0.7, r, sigma * 0.95, False)
            opt_low = bs_price(S_low, K, T_entry * 0.7, r, sigma * 1.05, False)

        tp_price = opt_fill * (1 + long_tp / 100)
        sl_price = opt_fill * max(0.01, 1 - long_sl / 100)

        if is_call:
            hit_tp = opt_high >= tp_price
            hit_sl = opt_low <= sl_price
        else:
            hit_tp = opt_low >= tp_price
            hit_sl = opt_high <= sl_price

        close_in_favor = (is_call and S_close > S_open * 1.001) or \
                         ((not is_call) and S_close < S_open * 0.999)
        close_against = (is_call and S_close < S_open * 0.999) or \
                        ((not is_call) and S_close > S_open * 1.001)

        if hit_tp and hit_sl:
            if close_in_favor:
                exit_price, reason = tp_price, 'tp'
            elif close_against:
                exit_price, reason = sl_price, 'sl'
            else:
                if hash(d.toordinal()) % 2 == 0:
                    exit_price, reason = tp_price, 'tp'
                else:
                    exit_price, reason = sl_price, 'sl'
        elif hit_tp:
            exit_price, reason = tp_price, 'tp'
        elif hit_sl:
            exit_price, reason = sl_price, 'sl'
        else:
            exit_price, reason = (
                bs_price(S_close, K, T_close, r, sigma, is_call), 'eod')
        exit_price = max(0.01, exit_price - slippage)
        pnl = (exit_price - opt_fill) * 100 * contracts - commission * contracts * 2
        trades.append({
            'date': d, 'strategy': f'turbo_long_{"call" if is_call else "put"}',
            'pnl': pnl, 'exit_reason': reason,
            'trend': trend, 'dtrend': dtrend,
        })
        return trades, capital + pnl

    return trades, capital


def run_long_backtest(years=3, capital_start=2000, commission=0.65,
                      slippage=0.01, max_contracts=20):
    spy, vix_map = load_long_data(years)

    # 参数：steady 用 v2 long-only 最优 + 少量 IC
    steady_params = {
        'conf_long': 70, 'conf_spread': 101,
        'long_tp_pct': 80, 'long_sl_pct': 30,
        'long_risk_pct': 3.0,
        'ic_short_delta': 0.05, 'ic_min_credit': 99,  # 禁用 IC
        'ic_tp_pct': 40, 'ic_sl_pct': 80, 'ic_wing': 5.0,
        'ic_risk_pct': 8.0,
    }
    turbo_params = {
        'long_delta': 0.18,
        'long_tp_pct': 250, 'long_sl_pct': 65,
        'long_risk_pct': 10.0,
        'conf_min': 55,
    }

    results = {}
    for mode, params in (('steady', steady_params), ('turbo', turbo_params)):
        print(f"\n{'=' * 65}")
        print(f"🔄 长期回测 {mode.upper()} | ${capital_start:,.0f} | {years} 年")
        print(f"{'=' * 65}")

        capital = float(capital_start)
        equity = [capital]
        all_trades = []
        by_year = defaultdict(list)
        by_dtrend = defaultdict(list)
        prev_row = None
        peak = capital
        max_dd = 0

        for _, row in spy.iterrows():
            d = row['date']
            vix = float(vix_map.get(d, 16))
            day_trades, capital = simulate_long_day(
                row, vix, prev_row, mode, capital, params,
                commission=commission, slippage=slippage,
                max_contracts=max_contracts,
            )
            all_trades.extend(day_trades)
            equity.append(capital)
            peak = max(peak, capital)
            if peak > 0:
                max_dd = max(max_dd, (peak - capital) / peak * 100)
            for t in day_trades:
                by_year[d.year].append(t)
                by_dtrend[t.get('dtrend', 'unknown')].append(t)
            prev_row = row

        _print_long_report(mode, capital_start, capital, equity,
                           all_trades, by_year, by_dtrend, max_dd)
        results[mode] = {'capital': capital, 'trades': all_trades,
                         'equity': equity, 'max_dd': max_dd}

    print(f"\n{'=' * 65}")
    print(f"⚖️  3 年对比")
    print(f"{'=' * 65}")
    for m in ('steady', 'turbo'):
        r = results[m]
        ret = (r['capital'] - capital_start) / capital_start * 100
        wr = len([t for t in r['trades'] if t['pnl'] > 0]) / max(1, len(r['trades'])) * 100
        print(f"   {m.upper():<8} 最终 ${r['capital']:>10,.0f}  "
              f"收益 {ret:>+8.1f}%  胜率 {wr:>5.1f}%  "
              f"回撤 {r['max_dd']:>5.1f}%  笔数 {len(r['trades'])}")
    print()
    return results


def _print_long_report(mode, c0, c1, equity, trades, by_year, by_dtrend, max_dd):
    if not trades:
        print("⚠️ 没有交易\n")
        return
    wins = [t for t in trades if t['pnl'] > 0]
    losses = [t for t in trades if t['pnl'] <= 0]
    total_ret = (c1 - c0) / c0 * 100
    wr = len(wins) / len(trades) * 100
    tw = sum(t['pnl'] for t in wins)
    tl = abs(sum(t['pnl'] for t in losses))
    pf = tw / tl if tl > 0 else float('inf')
    print(f"\n📊 {len(trades)} 笔 | 胜率 {wr:.1f}% | PF {pf:.2f}")
    print(f"   ${c0:,.0f} → ${c1:,.0f} ({total_ret:+.1f}%)")
    print(f"   最大回撤 {max_dd:.1f}%")

    print(f"\n📅 按年:")
    print(f"   {'年':<6} {'笔数':>5}  {'胜率':>5}  {'总PnL':>10}  {'平均/笔':>10}")
    print("   " + "-" * 48)
    for year in sorted(by_year.keys()):
        ts = by_year[year]
        w = len([t for t in ts if t['pnl'] > 0])
        total = sum(t['pnl'] for t in ts)
        avg = total / len(ts)
        print(f"   {year:<6} {len(ts):>5}  {w/len(ts)*100:>4.0f}%  "
              f"${total:>+9.0f}  ${avg:>+9.2f}")

    print(f"\n🌦️  按日线趋势:")
    print(f"   {'趋势':<10} {'笔数':>5}  {'胜率':>5}  {'总PnL':>10}  {'平均/笔':>10}")
    print("   " + "-" * 48)
    for label, key in (('📈 上涨', 'uptrend'), ('〰️ 震荡', 'choppy'),
                        ('📉 下跌', 'downtrend')):
        ts = by_dtrend.get(key, [])
        if not ts: continue
        w = len([t for t in ts if t['pnl'] > 0])
        total = sum(t['pnl'] for t in ts)
        avg = total / len(ts)
        print(f"   {label:<10} {len(ts):>5}  {w/len(ts)*100:>4.0f}%  "
              f"${total:>+9.0f}  ${avg:>+9.2f}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--years', type=float, default=3)
    p.add_argument('--capital', type=float, default=2000)
    p.add_argument('--no-fees', action='store_true')
    args = p.parse_args()
    c = 0 if args.no_fees else 0.65
    s = 0 if args.no_fees else 0.01
    run_long_backtest(args.years, args.capital, c, s)


if __name__ == '__main__':
    main()
