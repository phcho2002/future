"""
TOP40 5分钟 三推衰竭信号扫描 (注入模式)
使用 future_data.inject_many 单连接批量注入缓存（滚动 300 根），再逐个分析。
注入模式：增量拉新数据注入缓存、删最老的，窗口恒定，盘中可反复跑。
"""
import sys, time, warnings
warnings.filterwarnings('ignore')
import pandas as pd
import numpy as np
from pathlib import Path

# 兼容 Linux/Windows：从脚本位置向上推导到 work_ai 根目录
_SCRIPT_DIR = Path(__file__).resolve().parent
_WORK_AI = _SCRIPT_DIR.parent  # .../work_ai/future_1 -> .../work_ai
sys.path.insert(0, str(_WORK_AI))
sys.path.insert(0, str(_SCRIPT_DIR))

from future_data import inject_many
from future_quant.engine import QuantEngine
from future_quant.core.types import SignalSide
from future_quant.data.universe import load_top40

symbols = load_top40()

print('=' * 80)
print('future_1 三推衰竭系统 — TOP40 5分钟K线扫描 (注入模式)')
print('=' * 80)
print(f'加载 {len(symbols)} 个品种')
print()

# 批量注入缓存 (单连接，滚动窗口 300 根)
print('[1/3] 注入5分钟K线数据 (滚动窗口300根)...')
symbol_tuples = [(s['symbol'], s['name'], s['exchange']) for s in symbols]
klines = inject_many(symbol_tuples, period="5", length=300)
print(f'  成功注入 {len(klines)}/{len(symbols)} 个品种')
print()

# 逐个分析（直接复用已注入的 df，不再二次联网）
print('[2/3] 执行三推衰竭分析...')
engine = QuantEngine()
signals = []
errors = []

for i, sym in enumerate(symbols):
    code = sym['symbol']
    name = sym['name']
    exchange = sym['exchange']

    if code not in klines:
        print(f'[{i+1}/{len(symbols)}] {code} {name} -> 无数据')
        continue

    df = klines[code]
    if df.empty or len(df) < 10:
        print(f'[{i+1}/{len(symbols)}] {code} {name} -> 数据不足({len(df)})')
        continue

    try:
        result = engine.analyze_df(df)

        sig = result.signal
        if sig.is_valid:
            levels = sig.levels
            signals.append({
                'symbol': code,
                'name': name,
                'exchange': exchange,
                'direction': sig.side.value if sig.side else 'none',
                'entry_reason': sig.entry_reason,
                'push_count': len(result.push_set.pushes),
                'exhaustion_score': round(result.push_set.exhaustion_score or 0, 3),
                'channel_type': result.channel.channel_type.value if result.channel.channel_type else 'unknown',
                'entry_price': round(levels.entry, 2) if levels and levels.entry else None,
                'stop_price': round(levels.stop, 2) if levels and levels.stop else None,
                'target_1': round(levels.target_1, 2) if levels and levels.target_1 else None,
                'target_2': round(levels.target_2, 2) if levels and levels.target_2 else None,
                'reward_risk': round(levels.reward_risk, 2) if levels and levels.reward_risk else None,
            })
            print(f'[{i+1}/{len(symbols)}] {code} {name} -> ✅ 信号: {sig.side.value} 入场={levels.entry}')
        else:
            print(f'[{i+1}/{len(symbols)}] {code} {name} -> 无信号: {sig.entry_reason}')

    except Exception as e:
        print(f'[{i+1}/{len(symbols)}] {code} {name} -> 错误: {str(e)[:60]}')
        errors.append(f'{code}: {str(e)[:60]}')

print()
print('=' * 80)
print(f'[3/3] 扫描完成: 有效信号 {len(signals)} / {len(symbols)}')
print('=' * 80)

if signals:
    df = pd.DataFrame(signals)
    longs = df[df['direction'] == 'long']
    shorts = df[df['direction'] == 'short']
    
    print()
    print('⬆️ 做多信号:')
    print('-' * 80)
    for _, r in longs.iterrows():
        print(f'  {r["symbol"]:6s} {r["name"]:8s} 入场={r["entry_price"]:>10} 止损={r["stop_price"]:>10} 目标={r["target_1"]:>10} R/R={r["reward_risk"]}')
    
    print()
    print('⬇️ 做空信号:')
    print('-' * 80)
    for _, r in shorts.iterrows():
        print(f'  {r["symbol"]:6s} {r["name"]:8s} 入场={r["entry_price"]:>10} 止损={r["stop_price"]:>10} 目标={r["target_1"]:>10} R/R={r["reward_risk"]}')
    
    print()
    print('完整信号列表:')
    print('-' * 80)
    for _, r in df.iterrows():
        dir_emoji = '⬆️' if r['direction'] == 'long' else '⬇️'
        print(f'  {dir_emoji} {r["symbol"]:6s} {r["name"]:8s} 方向={r["direction"]:6s} 评分={r["exhaustion_score"]:>6.2f} 通道={r["channel_type"]:20s} 入场={r["entry_price"]}')
else:
    print()
    print('无有效信号')

if errors:
    print()
    print(f'错误: {len(errors)}个')
    for e in errors[:5]:
        print(f'  {e}')
