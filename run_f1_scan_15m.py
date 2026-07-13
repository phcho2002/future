"""
future_1 三推衰竭策略 60分钟扫描 — akshare数据源
直接调用 QuantEngine.analyze_df，绕过 TqSdk 连接问题（已切换至 xtquant）
"""
import sys, time, json, warnings
import pandas as pd
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

warnings.filterwarnings('ignore')

sys.path.insert(0, r"D:\work_ai")
sys.path.insert(0, r"D:\work_ai\future_1")

from future_quant.engine import QuantEngine
from future_quant.config import QuantConfig
from future_quant.core.types import SignalSide, ChannelType

# ── 信号邻近度评分 ──
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

def infer_direction(result) -> str:
    side = result.signal.side
    if side == SignalSide.LONG:
        return "做多"
    if side == SignalSide.SHORT:
        return "做空"
    if result.push_set.pushes:
        last_dir = result.push_set.pushes[-1].direction
        if last_dir.value == "bull":
            return "做空(提示)"
        elif last_dir.value == "bear":
            return "做多(提示)"
    return "待定"

# ── akshare 数据获取 ──
def fetch_15min(symbol):
    """获取15分钟K线"""
    try:
        import akshare as ak
        df = ak.futures_zh_minute_sina(symbol=symbol, period="15")
        if df is None or df.empty:
            return None
        # 清洗
        rename = {c: str(c).strip().lower() for c in df.columns}
        df = df.rename(columns=rename)
        cn_map = {'日期': 'date', '开盘': 'open', '最高': 'high', '最低': 'low',
                  '收盘': 'close', '成交量': 'volume', '持仓量': 'hold'}
        df = df.rename(columns=cn_map)
        if 'datetime' not in df.columns and 'date' in df.columns:
            df['datetime'] = pd.to_datetime(df['date'])
        for col in ['open', 'high', 'low', 'close', 'volume']:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')
        df = df.dropna(subset=['open', 'high', 'low', 'close', 'volume'])
        df['datetime'] = pd.to_datetime(df['datetime'])
        df = df.sort_values('datetime').reset_index(drop=True)
        return df
    except Exception as e:
        return None

# ── 主流程 ──
print("=" * 75)
print("  TOP40 期货 15分钟 K线 三推衰竭信号扫描 (akshare)")
print("  " + pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"))
print("=" * 75)

# 读取品种列表
json_path = r"D:\work_ai\futures_top40.json"
with open(json_path, 'r', encoding='utf-8') as f:
    symbols = json.load(f)['symbols']
print(f"  加载 {len(symbols)} 个品种\n")

engine = QuantEngine()
results_long = []
results_short = []
errors = []

start = time.time()
success = 0

for i, sym in enumerate(symbols):
    code = sym[0]
    name = sym[1]
    
    print(f"  [{i+1}/{len(symbols)}] {code} {name} ...", end=" ", flush=True)
    
    df = fetch_15min(code)
    if df is None or len(df) < 20:
        print("数据不足")
        errors.append((code, name, "数据不足"))
        continue
    
    try:
        result = engine.analyze_df(df)
        score = signal_proximity_score(result)
        direction = infer_direction(result)
        signal = result.signal
        
        entry = signal.levels.entry if signal.levels else 0
        stop = signal.levels.stop if signal.levels else 0
        target_1 = signal.levels.target_1 if signal.levels else 0
        target_2 = signal.levels.target_2 if signal.levels else 0
        pushes = len(result.push_set.pushes)
        wedge = result.channel.channel_type.name if result.channel else "N/A"
        
        row = {
            'symbol': code,
            'name': name,
            'direction': direction,
            'score': score,
            'entry': entry,
            'stop': stop,
            'target_1': target_1,
            'target_2': target_2,
            'pushes': pushes,
            'wedge': wedge,
        }
        
        if "做多" in direction:
            results_long.append(row)
        elif "做空" in direction:
            results_short.append(row)
        
        print(f"信号={pushes}推 方向={direction} 评分={score:.0f} 类型={wedge}")
        success += 1
        
    except Exception as e:
        print(f"错误: {str(e)[:50]}")
        errors.append((code, name, str(e)))

elapsed = time.time() - start
print(f"\n  扫描完成: {elapsed:.0f}秒, 成功 {success}/{len(symbols)}, 失败 {len(errors)}")

# ── 输出 ──
results_long.sort(key=lambda x: x['score'], reverse=True)
results_short.sort(key=lambda x: x['score'], reverse=True)

print("\n" + "=" * 75)
print("  🟢 做多信号 TOP3 (邻近度评分)")
print("=" * 75)
for i, r in enumerate(results_long[:3], 1):
    print(f"  [{i}] {r['symbol']} {r['name']}")
    print(f"      方向: {r['direction']}  评分: {r['score']:.0f}  推数: {r['pushes']}  类型: {r['wedge']}")
    e = r['entry'] if r['entry'] else 0
    s = r['stop'] if r['stop'] else 0
    t1 = r['target_1'] if r['target_1'] else 0
    t2 = r['target_2'] if r['target_2'] else 0
    print(f"      入场: {e:.2f}  止损: {s:.2f}  目标1: {t1:.2f}  目标2: {t2:.2f}")
    print()

print("\n" + "=" * 75)
print("  🔴 做空信号 TOP3 (邻近度评分)")
print("=" * 75)
for i, r in enumerate(results_short[:3], 1):
    print(f"  [{i}] {r['symbol']} {r['name']}")
    print(f"      方向: {r['direction']}  评分: {r['score']:.0f}  推数: {r['pushes']}  类型: {r['wedge']}")
    e = r['entry'] if r['entry'] else 0
    s = r['stop'] if r['stop'] else 0
    t1 = r['target_1'] if r['target_1'] else 0
    t2 = r['target_2'] if r['target_2'] else 0
    print(f"      入场: {e:.2f}  止损: {s:.2f}  目标1: {t1:.2f}  目标2: {t2:.2f}")
    print()

if errors:
    print(f"\n失败品种 ({len(errors)}个):")
    for sym, name, err in errors[:10]:
        print(f"  {sym} {name}: {err[:50]}")

print("\n" + "=" * 75)
