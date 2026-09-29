#!/usr/bin/env python3
"""
Monte Carlo 模拟：评估不同期权策略参数组合下，小账户在 N 笔交易后的表现。

核心模型：
    每笔交易直接按"账户净值的百分比"计算盈亏，更贴近真实交易：
    - 赢：account += account × win_pct/100
    - 输：account -= account × loss_pct/100
    - 固定比例下注（账户涨→仓位自动变大；账户跌→自动缩小）

卖方策略的 win_pct/loss_pct 需要换算：
    代码里仓位按 max_risk = (spread_width - credit) 计算，
    但实际 TP/SL 是按 credit 的百分比触发。
    以 16Δ IC ($5宽, ~$1.00 credit) 为例：
    - 仓位 = account × 5% / $4.00 (max risk per share)
    - 40% TP 赢 = $0.40/share → 账户涨 0.40/4.00 × 5% = 0.50%
    - 120% SL 输 = $1.20/share → 账户跌 1.20/4.00 × 5% = 1.50%

用法：
    python3 monte_carlo_sim.py --compare              # 所有预设对比
    python3 monte_carlo_sim.py --sensitivity          # 胜率敏感性分析
    python3 monte_carlo_sim.py --account 2000 --trades 120 \
        --win-rate 0.75 --win-pct 0.5 --loss-pct 1.5  # 自定义
"""

import argparse
import numpy as np

# ---------------------------------------------------------------------------
# 策略预设（win_pct / loss_pct 是占"账户净值"的百分比，不是占权利金）
# ---------------------------------------------------------------------------
# 换算逻辑：
#   卖方 IC: $5宽, credit≈$1.00, max_risk≈$4.00
#     40% TP = $0.40 gain / $4.00 max_risk × risk_allocation
#     120% SL = $1.20 loss / $4.00 max_risk × risk_allocation
#   卖方 Spread: $3宽, credit≈$0.70, max_risk≈$2.30
#     40% TP = $0.28 / $2.30 × risk_allocation
#     120% SL = $0.84 / $2.30 × risk_allocation
#   买方: debit≈$3.00, 全亏=debit
#     50% TP = +50% × risk_allocation
#     50% SL = -50% × risk_allocation

PRESETS = {
    'old_conservative': {
        'desc': '旧参数: 2.5%风险, 50%TP/200%SL (负期望)',
        'win_rate': 0.75,
        'win_pct': 0.31,    # 0.50×$1/$4 × 2.5% = 0.31%
        'loss_pct': 1.25,   # 2.00×$1/$4 × 2.5% = 1.25%
    },
    'aggressive_seller': {
        'desc': '修正卖方 IC: 5%风险, 40%TP/120%SL',
        'win_rate': 0.72,
        'win_pct': 0.50,    # 0.40×$1/$4 × 5% = 0.50%
        'loss_pct': 1.50,   # 1.20×$1/$4 × 5% = 1.50%
    },
    'seller_25_80': {
        'desc': '薄利快跑卖方: 5%风险, 25%TP/80%SL',
        'win_rate': 0.82,   # 更窄TP → 更高胜率
        'win_pct': 0.31,    # 0.25×$1/$4 × 5%
        'loss_pct': 1.00,   # 0.80×$1/$4 × 5%
    },
    'directional_long': {
        'desc': '方向买方: 3%风险, 50%TP/50%SL (40%胜率)',
        'win_rate': 0.40,
        'win_pct': 1.50,    # 50% × 3%
        'loss_pct': 1.50,   # 50% × 3%
    },
    'long_50wr': {
        'desc': '方向买方(优化): 3%风险, 50%TP/50%SL (50%胜率)',
        'win_rate': 0.50,
        'win_pct': 1.50,
        'loss_pct': 1.50,
    },
    'long_trailing': {
        'desc': '买方+移动止损: 3%风险, 80%TP/40%SL (45%胜率)',
        'win_rate': 0.45,
        'win_pct': 2.40,    # 80% × 3%
        'loss_pct': 1.20,   # 40% × 3%
    },
    # --- Turbo 模式（周翻倍目标）---
    # 18Δ OTM 期权，每笔用 sleeve 的 10%
    # Win: +200% on premium × 10% = +20% sleeve
    # Loss: -65% on premium × 10% = -6.5% sleeve
    'turbo_25wr': {
        'desc': '⚡ TURBO 18Δ OTM: 10%风险, 200%TP/65%SL (25%胜率)',
        'win_rate': 0.25,
        'win_pct': 20.0,
        'loss_pct': 6.5,
    },
    'turbo_30wr': {
        'desc': '⚡ TURBO 18Δ OTM: 10%风险, 200%TP/65%SL (30%胜率)',
        'win_rate': 0.30,
        'win_pct': 20.0,
        'loss_pct': 6.5,
    },
    'turbo_35wr': {
        'desc': '⚡ TURBO 18Δ OTM: 10%风险, 200%TP/65%SL (35%胜率)',
        'win_rate': 0.35,
        'win_pct': 20.0,
        'loss_pct': 6.5,
    },
}

# 混合策略
MIXED_WEIGHTS = {
    'seller_25_80': 0.50,     # 薄利卖方为主
    'long_trailing': 0.30,    # 高信心时买方
    'aggressive_seller': 0.20,  # 正常 IC
}

RUIN_THRESHOLD = 0.25  # 账户跌到起始 25% 算爆仓


def simulate_one(win_rate, win_pct, loss_pct, n_trades, rng, account):
    """跑一次模拟。每次交易按账户固定比例盈亏。"""
    equity = account
    peak = account
    max_dd = 0.0

    for _ in range(n_trades):
        if rng.random() < win_rate:
            equity *= (1 + win_pct / 100)
        else:
            equity *= (1 - loss_pct / 100)

        if equity <= 0:
            return 0.0, 1.0, True

        peak = max(peak, equity)
        dd = (peak - equity) / peak
        max_dd = max(max_dd, dd)

        if equity < account * RUIN_THRESHOLD:
            return equity, max_dd, True

    return equity, max_dd, False


def run_preset(name, params, account=2000, n_trades=120, n_sims=10000, seed=42):
    """跑单一参数预设。"""
    rng = np.random.default_rng(seed)
    fv = np.empty(n_sims)
    mdd = np.empty(n_sims)
    ruined = 0

    wr = params['win_rate']
    wp = params['win_pct']
    lp = params['loss_pct']
    ev = wr * wp - (1 - wr) * lp

    for s in range(n_sims):
        v, d, r = simulate_one(wr, wp, lp, n_trades, rng, account)
        fv[s] = v
        mdd[s] = d
        if r:
            ruined += 1

    return {
        'name': name,
        'desc': params['desc'],
        'fv': fv,
        'mdd': mdd,
        'ruin_rate': ruined / n_sims,
        'ev': ev,
        'win_rate': wr,
        'win_pct': wp,
        'loss_pct': lp,
    }


def run_mixed(account=2000, n_trades=120, n_sims=10000, seed=42):
    """混合策略：每笔按权重随机选预设。"""
    rng = np.random.default_rng(seed)
    fv = np.empty(n_sims)
    mdd = np.empty(n_sims)
    ruined = 0

    names = list(MIXED_WEIGHTS.keys())
    w = np.array([MIXED_WEIGHTS[n] for n in names])
    probs = w / w.sum()

    ev = 0.0
    for n, p in zip(names, probs):
        prm = PRESETS[n]
        ev += p * (prm['win_rate'] * prm['win_pct']
                   - (1 - prm['win_rate']) * prm['loss_pct'])

    for s in range(n_sims):
        equity = account
        peak = account
        max_dd = 0.0
        is_ruin = False

        for _ in range(n_trades):
            idx = rng.choice(len(names), p=probs)
            prm = PRESETS[names[idx]]
            if rng.random() < prm['win_rate']:
                equity *= (1 + prm['win_pct'] / 100)
            else:
                equity *= (1 - prm['loss_pct'] / 100)

            if equity <= 0:
                equity = 0
                is_ruin = True
                break
            peak = max(peak, equity)
            max_dd = max(max_dd, (peak - equity) / peak)
            if equity < account * RUIN_THRESHOLD:
                is_ruin = True
                break

        fv[s] = equity
        mdd[s] = max_dd
        if is_ruin:
            ruined += 1

    mix_desc = ' + '.join(f"{int(p*100)}% {n}" for n, p in zip(names, probs))
    return {
        'name': 'mixed',
        'desc': f'混合: {mix_desc}',
        'fv': fv, 'mdd': mdd,
        'ruin_rate': ruined / n_sims,
        'ev': ev,
        'win_rate': -1, 'win_pct': -1, 'loss_pct': -1,
    }


def ascii_hist(data, bins=20, width=40, prefix="    "):
    counts, edges = np.histogram(data, bins=bins)
    mx = counts.max()
    for i, c in enumerate(counts):
        bar = "█" * int(c / mx * width) if mx > 0 else ""
        print(f"{prefix}${edges[i]:>8,.0f}~${edges[i+1]:<8,.0f} │{bar} {c}")


def print_result(r, account, n_trades):
    fv = r['fv']
    mdd = r['mdd']
    ret = ((fv - account) / account) * 100

    print("=" * 70)
    print(f"📊 {r['name']}")
    print(f"   {r['desc']}")
    print("-" * 70)
    if r['win_rate'] >= 0:
        print(f"   胜率: {r['win_rate']*100:.0f}% | "
              f"赢: +{r['win_pct']:.2f}%/笔 | 输: -{r['loss_pct']:.2f}%/笔")
    print(f"   期望/笔 (账户%): {r['ev']:+.3f}%  {'✅ 正期望' if r['ev'] > 0 else '❌ 负期望'}")
    print(f"   理论 120 笔后:   {(1 + r['ev']/100)**n_trades:.2f}x "
          f"({((1 + r['ev']/100)**n_trades - 1)*100:+.0f}%)")
    print()
    print(f"   终值中位数:  ${np.median(fv):>10,.0f}  ({np.median(ret):+.1f}%)")
    print(f"   终值均值:    ${fv.mean():>10,.0f}  ({ret.mean():+.1f}%)")
    print(f"   P10 (倒霉):  ${np.percentile(fv, 10):>10,.0f}")
    print(f"   P90 (幸运):  ${np.percentile(fv, 90):>10,.0f}")
    print()
    print(f"   🎯 翻倍概率:        {np.mean(fv >= account*2)*100:5.1f}%")
    print(f"   🚀 三倍概率:        {np.mean(fv >= account*3)*100:5.1f}%")
    print(f"   💀 腰斩概率:        {np.mean(fv < account*0.5)*100:5.1f}%")
    print(f"   💀 爆仓概率 (<25%): {r['ruin_rate']*100:5.1f}%")
    print()
    print(f"   最大回撤 中位数:   {np.median(mdd)*100:5.1f}%")
    print(f"   最大回撤 P90:      {np.percentile(mdd, 90)*100:5.1f}%")
    print()
    print("   终值分布:")
    ascii_hist(fv)
    print()


def run_sensitivity(account, n_trades, n_sims, seed):
    """胜率敏感性分析：固定盈亏比，扫胜率看翻倍/爆仓概率。"""
    print()
    print("=" * 70)
    print("🔬 敏感性分析：固定盈亏比，不同胜率下的结果")
    print("=" * 70)

    scenarios = [
        # (label, win_pct, loss_pct, win_rates)
        ("卖方 40%TP/120%SL (赢0.50%/输1.50%)", 0.50, 1.50,
         [0.70, 0.72, 0.75, 0.78, 0.80, 0.85]),
        ("卖方 25%TP/80%SL (赢0.31%/输1.00%)", 0.31, 1.00,
         [0.75, 0.78, 0.80, 0.82, 0.85, 0.88]),
        ("买方 50%TP/50%SL (赢1.50%/输1.50%)", 1.50, 1.50,
         [0.40, 0.45, 0.50, 0.55, 0.60]),
        ("买方 80%TP/40%SL (赢2.40%/输1.20%)", 2.40, 1.20,
         [0.35, 0.40, 0.45, 0.50, 0.55]),
    ]

    for label, wp, lp, win_rates in scenarios:
        print()
        print(f"📌 {label}")
        print(f"   理论保本胜率: {lp/(wp+lp)*100:.1f}%")
        print("-" * 70)
        print(f"   {'胜率':>6}  {'期望/笔':>8}  {'翻倍率':>7}  {'腰斩率':>7}  "
              f"{'爆仓率':>7}  {'中位终值':>10}  {'中位回撤':>8}")
        for wr in win_rates:
            r = run_preset(f'sens_{wr}', {
                'desc': '', 'win_rate': wr,
                'win_pct': wp, 'loss_pct': lp,
            }, account, n_trades, n_sims, seed)
            fv = r['fv']
            print(f"   {wr*100:>5.0f}%  {r['ev']:>+7.3f}%  "
                  f"{np.mean(fv >= account*2)*100:>6.1f}%  "
                  f"{np.mean(fv < account*0.5)*100:>6.1f}%  "
                  f"{r['ruin_rate']*100:>6.1f}%  "
                  f"${np.median(fv):>9,.0f}  "
                  f"{np.median(r['mdd'])*100:>7.1f}%")
    print()


def run_turbo_weekly(account, n_trades, n_sims, seed):
    """Turbo 周翻倍分析：20 笔/周（5天×4笔），扫描胜率。"""
    print()
    print("=" * 70)
    print("⚡ TURBO 周翻倍分析 (20笔/周, 10% sleeve/笔, 200%TP/65%SL)")
    print("=" * 70)
    wp, lp = 20.0, 6.5  # 赢+20% sleeve，输-6.5% sleeve
    breakeven = lp / (wp + lp)
    print(f"   保本胜率: {breakeven*100:.1f}% | "
          f"赢+{wp}% sleeve | 输-{lp}% sleeve")
    print("-" * 70)
    print(f"   {'胜率':>6}  {'期望/笔':>8}  {'周翻倍率':>8}  {'腰斩率':>7}  "
          f"{'爆仓率':>7}  {'中位终值':>10}  {'中位回撤':>8}")

    for wr in [0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.45]:
        r = run_preset(f'turbo_{wr}', {
            'desc': '', 'win_rate': wr,
            'win_pct': wp, 'loss_pct': lp,
        }, account, n_trades, n_sims, seed)
        fv = r['fv']
        doubled = np.mean(fv >= account * 2) * 100
        halved = np.mean(fv < account * 0.5) * 100
        print(f"   {wr*100:>5.0f}%  {r['ev']:>+7.2f}%  "
              f"{doubled:>7.1f}%  {halved:>6.1f}%  "
              f"{r['ruin_rate']*100:>6.1f}%  "
              f"${np.median(fv):>9,.0f}  "
              f"{np.median(r['mdd'])*100:>7.1f}%")
    print()
    print("   注: 20笔 ≈ 1周 (5天×4笔/天). 爆仓=<25%起始, 腰斩=<50%.")
    print()


def main():
    p = argparse.ArgumentParser(description='期权策略 Monte Carlo 模拟')
    p.add_argument('--account', type=float, default=2000)
    p.add_argument('--trades', type=int, default=120, help='交易笔数 (默认120≈3个月)')
    p.add_argument('--sims', type=int, default=10000)
    p.add_argument('--seed', type=int, default=42)
    p.add_argument('--compare', action='store_true', help='跑所有预设对比')
    p.add_argument('--mixed', action='store_true', help='跑混合策略')
    p.add_argument('--sensitivity', action='store_true', help='胜率敏感性分析')
    p.add_argument('--turbo', action='store_true', help='Turbo 周翻倍分析(20笔/周)')
    p.add_argument('--win-rate', type=float, default=None)
    p.add_argument('--win-pct', type=float, default=None, help='赢时账户涨幅%%')
    p.add_argument('--loss-pct', type=float, default=None, help='输时账户跌幅%%')
    args = p.parse_args()

    print()
    print(f"💰 起始 ${args.account:,.0f} | {args.trades} 笔 | {args.sims:,} 次模拟")

    if args.sensitivity:
        run_sensitivity(args.account, args.trades, args.sims, args.seed)
        return

    if args.turbo:
        # Turbo 周翻倍：默认 20 笔（一周），可以用 --trades 覆盖
        n = args.trades if args.trades != 120 else 20
        run_turbo_weekly(args.account, n, args.sims, args.seed)
        return

    results = []

    if args.compare or args.mixed:
        if args.compare:
            for name, params in PRESETS.items():
                results.append(run_preset(name, params, args.account,
                                          args.trades, args.sims, args.seed))
        results.append(run_mixed(args.account, args.trades, args.sims, args.seed))
    elif args.win_rate is not None:
        custom = {
            'desc': f'自定义: wr={args.win_rate}, win={args.win_pct}%, loss={args.loss_pct}%',
            'win_rate': args.win_rate,
            'win_pct': args.win_pct or 0.5,
            'loss_pct': args.loss_pct or 1.5,
        }
        results.append(run_preset('custom', custom, args.account,
                                  args.trades, args.sims, args.seed))
    else:
        # 默认：跑最有希望的几个
        for name in ['seller_25_80', 'long_trailing', 'directional_long']:
            results.append(run_preset(name, PRESETS[name], args.account,
                                      args.trades, args.sims, args.seed))
        results.append(run_mixed(args.account, args.trades, args.sims, args.seed))

    for r in results:
        print_result(r, args.account, args.trades)

    # 汇总表
    if len(results) > 1:
        print("=" * 70)
        print("📋 对比汇总")
        print("-" * 70)
        print(f"{'策略':<22} {'胜率':>5} {'期望/笔':>8} {'翻倍率':>7} "
              f"{'腰斩率':>7} {'爆仓率':>7} {'中位回撤':>8}")
        print("-" * 70)
        for r in results:
            wr_s = f"{r['win_rate']*100:.0f}%" if r['win_rate'] >= 0 else "mix"
            fv = r['fv']
            print(f"{r['name']:<22} {wr_s:>5} {r['ev']:>+7.3f}% "
                  f"{np.mean(fv >= args.account*2)*100:>6.1f}% "
                  f"{np.mean(fv < args.account*0.5)*100:>6.1f}% "
                  f"{r['ruin_rate']*100:>6.1f}% "
                  f"{np.median(r['mdd'])*100:>7.1f}%")
        print("=" * 70)


if __name__ == '__main__':
    main()
