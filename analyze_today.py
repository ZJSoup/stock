#!/usr/bin/env python3
"""
SPY 今日市场快速分析
"""
import sys
sys.path.insert(0, '.')
from spy_multi_strategy import MarketAnalyzer

analyzer = MarketAnalyzer()
trend, confidence, details = analyzer.get_trend()

print('=' * 50)
print('📊 SPY 市场分析报告')
print('=' * 50)
print(f'\n趋势判断: {trend.value}')
print(f'信心指数: {confidence}%')
print(f'\n详细指标:')
for k, v in details.items():
    print(f'  {k}: {v}')
