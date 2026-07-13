"""
future_1 三推衰竭策略 60分钟扫描 — xtquant数据源
直接调用 QuantEngine.analyze_df
"""
import sys, time, json, warnings
import pandas as pd
from pathlib import Path

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

# ── 数据获取（同 futures_strength_analysis.py，优先future_data.get_klines xtquant缓存）──
# future_data.get_klines 内部使用 xtquant + TTL parquet 缓存，避免反复联网
try:
    from future_data import get_klines as _tq_get_klines
    _HAS_TQ = True
except ImportError:
    _HAS_TQ = False

def fetch_60min_tqsdk_cached(symbol, exchange):
    """获取60分钟K线（优先xtquant缓存，失败返回None）"""
    if not _HAS_TQ:
        return None
    try:
        df = _tq_get_klines(symbol=symbol, exchange=exchange, period="60", length=200, ttl_hours=2)
        return df if df is not None and len(df) > 0 else None
    except Exception:
        return None

# ── 主流程 ──
print("=" * 75)
print("  TOP40 期货 60分钟 K线 三推衰竭信号扫描 (xtquant)")
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
    exchange = sym[2]
    
    print(f"  [{i+1}/{len(symbols)}] {code} {name} ...", end=" ", flush=True)
    
    df = fetch_60min_tqsdk_cached(code, exchange)
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

print("\n" + "=" * 80)
print("  🟢 做多信号 TOP5 (邻近度评分)")
print("=" * 80)
for i, r in enumerate(results_long[:5], 1):
    print(f"  [{i}] {r['symbol']} {r['name']}")
    print(f"      方向: {r['direction']}  评分: {r['score']:.0f}  推数: {r['pushes']}  类型: {r['wedge']}")
    e = r['entry'] if r['entry'] else 0
    s = r['stop'] if r['stop'] else 0
    t1 = r['target_1'] if r['target_1'] else 0
    t2 = r['target_2'] if r['target_2'] else 0
    print(f"      入场: {e:.2f}  止损: {s:.2f}  目标1: {t1:.2f}  目标2: {t2:.2f}")
    print()

print("\n" + "=" * 80)
print("  🔴 做空信号 TOP5 (邻近度评分)")
print("=" * 80)
for i, r in enumerate(results_short[:5], 1):
    print(f"  [{i}] {r['symbol']} {r['name']}")
    print(f"      方向: {r['direction']}  评分: {r['score']:.0f}  推数: {r['pushes']}  类型: {r['wedge']}")
    e = r['entry'] if r['entry'] else 0
    s = r['stop'] if r['stop'] else 0
    t1 = r['target_1'] if r['target_1'] else 0
    t2 = r['target_2'] if r['target_2'] else 0
    print(f"      入场: {e:.2f}  止损: {s:.2f}  目标1: {t1:.2f}  目标2: {t2:.2f}")
    print()

# 3推品种明细
three_push = [r for r in results_long + results_short if r['pushes'] >= 3]
if three_push:
    print("\n" + "=" * 80)
    print("  ★ 3推+ 品种明细 (高优先级)")
    print("=" * 80)
    for r in three_push:
        print(f"  {r['symbol']} {r['name']} — {r['direction']} 评分={r['score']:.0f} 类型={r['wedge']} 入场={r['entry']}")

if errors:
    print(f"\n失败品种 ({len(errors)}个):")
    for sym, name, err in errors[:10]:
        print(f"  {sym} {name}: {err[:50]}")

print("\n" + "=" * 75)
