
import os, sys, json, time, warnings
warnings.filterwarnings('ignore')

VPS_ROOT = r"D:\work_ai\future_vps"
F1_DIR = os.path.join(VPS_ROOT, "future_1")

sys.path.insert(0, F1_DIR)
sys.path.insert(0, VPS_ROOT)

import pandas as pd
import numpy as np
import akshare as ak
from datetime import datetime

from future_quant.engine import QuantEngine
from future_quant.config import QuantConfig
from future_quant.core.types import ChannelType, SignalSide
from future_quant.data.universe import load_top40

def load_top40_json(path):
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    return data

TOP40_PATH = r"D:\work_ai\future\futures_top40.json"
if not os.path.exists(TOP40_PATH):
    TOP40_PATH = os.path.join(VPS_ROOT, "futures_top40.json")

symbols = load_top40_json(TOP40_PATH)
print(f"加载 {len(symbols)} 个品种")

def fetch_akshare_15m(symbol):
    try:
        df = ak.futures_zh_minute_sina(symbol=symbol, period="15")
        if df is None or df.empty:
            return None
        df.columns = [str(c).strip().lower().replace(' ', '_') for c in df.columns]
        cn_map = {'日期': 'date', '开盘': 'open', '最高': 'high', '最低': 'low', '收盘': 'close', '成交量': 'volume', '持仓量': 'hold'}
        df = df.rename(columns=cn_map)
        if 'datetime' not in df.columns and 'date' in df.columns:
            df['datetime'] = pd.to_datetime(df['date'])
        elif 'datetime' not in df.columns:
            df['datetime'] = pd.to_datetime(df.iloc[:, 0])
        for col in ['open', 'high', 'low', 'close', 'volume']:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')
        df = df.dropna(subset=['open', 'high', 'low', 'close']).sort_values('datetime').reset_index(drop=True)
        return df[['datetime', 'open', 'high', 'low', 'close', 'volume']]
    except Exception as e:
        return None

def signal_proximity_score(result) -> float:
    pushes = len(result.push_set.pushes)
    ex_score = result.push_set.exhaustion_score or 0.0
    ch_type = result.channel.channel_type
    push_score = min(pushes * 10, 30)
    ex_score_pts = ex_score * 30
    ch_pts = {
        ChannelType.CONVERGING_WEDGE: 25,
        ChannelType.EXPANDING_TRIANGLE: 22,
        ChannelType.PARABOLIC_WEDGE: 25,
        ChannelType.THREE_PUSH_NON_WEDGE: 18,
        ChannelType.PARALLEL: 8,
    }.get(ch_type, 3)
    state_pts = 15 if result.market_state.allow_wedge_reversal else 5
    return push_score + ex_score_pts + ch_pts + state_pts

def infer_direction(result):
    """确定交易方向，必须与 signal.levels（入场/止损/目标）一致。

    signal.side 是引擎按突破/反转模型算出的真实方向，且 levels（entry/stop/
    target）就是按这个方向构造的——它是唯一权威。三推反转的"最后一推方向"
    只是当引擎尚未给出方向（side==NONE，例如未突破）时的结构提示，不能覆盖
    signal.side，否则会出现"做多但止损在上方"的矛盾行。
    """
    side = result.signal.side
    if side == SignalSide.LONG:
        return "做多"
    if side == SignalSide.SHORT:
        return "做空"
    # 引擎无明确方向时，才用最后一推方向作结构提示（仅供邻近度参考）
    if result.push_set.pushes:
        last_dir = result.push_set.pushes[-1].direction
        if last_dir.value == "bull":
            return "做空(提示)"
        elif last_dir.value == "bear":
            return "做多(提示)"
    return "待定"

engine = QuantEngine()
config = QuantConfig()

results = []
errors = []

print(f"{'='*75}")
print(f"  TOP40 期货 15分钟 K线 三推衰竭信号扫描 (akshare 数据源)")
print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print(f"{'='*75}")

t0 = time.time()
for i, sym in enumerate(symbols):
    code = sym['symbol']
    name = sym['name']
    exchange = sym.get('exchange', '')
    print(f"  [{i+1}/{len(symbols)}] {code} {name} ...", end=" ", flush=True)
    df = fetch_akshare_15m(code)
    if df is None or df.empty or len(df) < 10:
        print(f"无数据/数据不足({len(df) if df is not None else 0})")
        continue
    try:
        result = engine.analyze_df(df)
        signal = result.signal
        score = signal_proximity_score(result)
        direction = infer_direction(result)
        entry = stop = target = rr = None
        if signal.levels:
            entry = signal.levels.entry
            stop = signal.levels.stop
            target = signal.levels.target_1
            rr = signal.levels.reward_risk
        push_count = len(result.push_set.pushes)
        ex_score = result.push_set.exhaustion_score or 0
        ch_type = result.channel.channel_type.value if result.channel.channel_type else "unknown"
        valid_mark = "✅有效" if signal.is_valid else ""
        print(f"推={push_count} 衰竭={ex_score:.2f} 通道={ch_type} 方向={direction} 评分={score:.0f} {valid_mark}")
        results.append({
            'symbol': code, 'name': name, 'exchange': exchange,
            'direction': direction, 'score': score, 'valid': signal.is_valid,
            'push_count': push_count, 'exhaustion': round(ex_score, 2),
            'channel_type': ch_type,
            'entry': round(entry, 2) if entry is not None else '',
            'stop': round(stop, 2) if stop is not None else '',
            'target': round(target, 2) if target is not None else '',
            'rr': round(rr, 2) if rr is not None else '',
            'reason': (signal.entry_reason or '')[:50],
        })
    except Exception as e:
        err = str(e).replace('\n', ' ')[:80]
        print(f"❌ {err}")
        errors.append({'symbol': code, 'error': err})

print(f"\n  扫描耗时: {time.time()-t0:.0f}s, 成功: {len(results)}, 错误: {len(errors)}")

if not results:
    print("  无结果")
    sys.exit(0)

df_all = pd.DataFrame(results).sort_values('score', ascending=False).reset_index(drop=True)

print(f"\n{'='*75}")
print(f"  最好的 5 个信号（按评分排序）")
print(f"{'='*75}")
print(df_all.head(5).to_string(index=False))

out_path = r"D:\work_ai\future_vps\future_1\akshare_15m_top5.json"
df_all.head(5).to_json(out_path, orient='records', force_ascii=False, indent=2)
print(f"\n已保存: {out_path}")

if errors:
    print(f"\n错误 {len(errors)} 个")
    for e in errors[:5]:
        print(f"    {e['symbol']}: {e['error']}")
