"""
综合量化分析 — TOP40 期货多系统共振
整合: 小时线强弱 + future_6 Renko + future_7 EMA缠绕
输出精简报告到 stdout，供 cron 捕获发飞书
"""
import sys, warnings, os
warnings.filterwarnings('ignore')
import pandas as pd
import sqlite3
import numpy as np

sys.path.insert(0, r'D:\work_ai')
sys.path.insert(0, r'D:\work_ai\future_1')
sys.path.insert(0, r'D:\work_ai\future_7')

from future_data import fetch_many
from future_quant.engine import QuantEngine as F1Engine
from quant_system.strategy import StrategyConfig as F7Config, generate_signals as f7_generate

DB = r'D:\work_ai\futures_data.db'
conn = sqlite3.connect(DB)
symbols = pd.read_sql("SELECT 排名, symbol, name, exchange FROM futures_top40 ORDER BY 排名", conn).to_dict('records')
conn.close()

# ── 批量拉取数据 ──
symbol_tuples = [(s['symbol'], s['name'], s['exchange']) for s in symbols]
klines_60 = fetch_many(symbol_tuples, period="60", length=300, wait_timeout=45.0)
klines_30 = fetch_many(symbol_tuples, period="30", length=500, wait_timeout=45.0)

# ── 小时线强弱评分 ──
strength_map = {}
for sym in symbols:
    code = sym['symbol']
    if code not in klines_60:
        continue
    df = klines_60[code].copy()
    df['datetime'] = pd.to_datetime(df['datetime'])
    df = df.sort_values('datetime').reset_index(drop=True)
    if len(df) < 30:
        continue
    recent = df.tail(30)
    ma10 = recent['close'].rolling(10).mean()
    trend_score = 15
    if len(ma10) > 0 and not pd.isna(ma10.iloc[-1]):
        if recent['close'].iloc[-1] > ma10.iloc[-1]:
            trend_score = min(20, 15 + (recent['close'].iloc[-1] / ma10.iloc[-1] - 1) * 100)
        else:
            trend_score = max(0, 15 - (ma10.iloc[-1] / recent['close'].iloc[-1] - 1) * 100)
    high_20 = df.tail(120)['high'].max()
    low_20 = df.tail(120)['low'].min()
    range_20 = high_20 - low_20
    pos_score = 10 + ((recent['close'].iloc[-1] - low_20) / range_20 * 20) if range_20 > 0 else 10
    pos_score = max(0, min(20, pos_score))
    vol_trend = 10
    if len(df) >= 10:
        vol_recent = df.tail(5)['volume'].mean()
        vol_prev = df.iloc[-10:-5]['volume'].mean()
        if vol_prev > 0:
            vol_trend = min(20, max(0, 10 + (vol_recent / vol_prev - 1) * 10))
    total = min(100, max(0, trend_score + pos_score + vol_trend + 20 + 15))
    strength_map[code] = total

# ── future_6 Renko 信号 ──
f6_map = {}
try:
    import glob
    f6_files = glob.glob(r'D:\work_ai\future_6\output\renko_signals_*.csv')
    if f6_files:
        f6_latest = max(f6_files)
        f6_df = pd.read_csv(f6_latest)
        f6_df = f6_df[f6_df['direction'] != 0]
        for _, r in f6_df.iterrows():
            sym = r['symbol']
            if sym not in f6_map or r['strength_score'] > f6_map[sym]['strength']:
                f6_map[sym] = {'dir': r['direction'], 'type': r['signal_type']}
except Exception:
    pass

# ── future_7 EMA缠绕 ──
f7_cfg = F7Config(short_ema=8, long_ema=21, vol_factor=1.5, prev_vol_factor=1.8, body_pct=0.5)
f7_map = {}
for sym in symbols:
    code = sym['symbol']
    if code not in klines_30:
        continue
    df = klines_30[code].copy()
    if df.empty or len(df) < 30:
        continue
    try:
        df = df[['datetime', 'open', 'high', 'low', 'close', 'volume']].copy()
        result = f7_generate(df, f7_cfg)
        sig_rows = result[result['signal'] != '']
        if len(sig_rows) > 0:
            latest = sig_rows.iloc[-1]
            f7_map[code] = {
                'dir': 1 if latest['signal'] == 'long' else -1,
                'stars': latest['stars'],
                'entry': latest['entry_price']
            }
    except Exception:
        pass

# ── 综合汇总 ──
results = []
for sym in symbols:
    code = sym['symbol']
    name = sym['name']
    strength = strength_map.get(code, 50)
    f6 = f6_map.get(code, None)
    f7 = f7_map.get(code, None)
    f6_dir = f6['dir'] if f6 else 0
    f7_dir = f7['dir'] if f7 else 0
    
    dirs = [d for d in [f6_dir, f7_dir] if d != 0]
    if dirs:
        avg_dir = np.mean(dirs)
        consensus = '做多' if avg_dir > 0.5 else '做空' if avg_dir < -0.5 else '分歧'
    else:
        consensus = '观望'
    
    resonance = 0
    if f6_dir != 0 and f7_dir != 0 and f6_dir == f7_dir:
        resonance = 3
    elif f6_dir != 0 or f7_dir != 0:
        resonance = 2
    if f7 and f7.get('stars', 0) >= 2:
        resonance += 1
    
    results.append({
        'symbol': code, 'name': name, 'strength': strength,
        'f6_dir': f6_dir, 'f7_dir': f7_dir,
        'consensus': consensus, 'resonance': resonance,
        'f7_stars': f7['stars'] if f7 else 0,
    })

results_df = pd.DataFrame(results)
results_df['sort_key'] = results_df['resonance'] * 100 + results_df['strength']
results_df = results_df.sort_values('sort_key', ascending=False).reset_index(drop=True)

# ── 生成飞书消息 ──
now = pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')
long_consensus = results_df[results_df['consensus'] == '做多']
short_consensus = results_df[results_df['consensus'] == '做空']
divergence = results_df[results_df['consensus'] == '分歧']
high_res = results_df[results_df['resonance'] >= 3]
short_high = high_res[high_res['consensus'] == '做空']
long_high = high_res[high_res['consensus'] == '做多']

msg_lines = []
msg_lines.append(f"📊 TOP40 综合量化共振分析 | {now}")
msg_lines.append(f"")
msg_lines.append(f"【核心结论】做空{len(short_consensus)}个 | 做多{len(long_consensus)}个 | 分歧{len(divergence)}个")
msg_lines.append(f"")

if len(short_high) > 0:
    msg_lines.append(f"⬇️ 高共振做空 (重点):")
    for _, r in short_high.head(10).iterrows():
        msg_lines.append(f"  {r['symbol']} {r['name']} 共振{r['resonance']} 强度{r['strength']:.0f}")
    msg_lines.append(f"")

if len(long_high) > 0:
    msg_lines.append(f"⬆️ 高共振做多 (重点):")
    for _, r in long_high.iterrows():
        msg_lines.append(f"  {r['symbol']} {r['name']} 共振{r['resonance']} 强度{r['strength']:.0f}")
    msg_lines.append(f"")

if len(divergence) > 0:
    msg_lines.append(f"⚖️ 分歧回避:")
    for _, r in divergence.iterrows():
        f6_str = '多' if r['f6_dir'] > 0 else '空' if r['f6_dir'] < 0 else '-'
        f7_str = '多' if r['f7_dir'] > 0 else '空' if r['f7_dir'] < 0 else '-'
        msg_lines.append(f"  {r['symbol']} {r['name']} f6={f6_str} f7={f7_str}")
    msg_lines.append(f"")

msg_lines.append(f"【策略】空为主，多仅限国债。单品种≤10%，总空头≤60%。")
msg_lines.append(f"TqSdk trial 剩余4天，请及时续费（数据源已切换至 xtquant）。")

print("\n".join(msg_lines))
