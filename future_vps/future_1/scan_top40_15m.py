"""
TOP40 期货 15分钟 K线 三推衰竭信号扫描
注入模式：先 inject_many 单连接批量注入缓存（滚动 300 根），再逐个 analyze_df，
输出做多做空前3名（含信号邻近度评分）。
"""
import sys, time
from pathlib import Path

# 跨平台：从脚本位置向上推导到 future_vps 根目录（future_data 所在位置）
_SCRIPT_DIR = Path(__file__).resolve().parent
_VPS_ROOT = _SCRIPT_DIR.parent  # .../future_vps/future_1 -> .../future_vps
sys.path.insert(0, str(_SCRIPT_DIR))
sys.path.insert(0, str(_VPS_ROOT))

import pandas as pd
import numpy as np
from dataclasses import asdict

from future_data import inject_many
from future_quant.engine import QuantEngine
from future_quant.config import QuantConfig
from future_quant.core.types import SignalSide, ChannelType
from future_quant.data.universe import load_top40

# ─── 信号邻近度评分（参考 china-futures-data skill） ────
def signal_proximity_score(result) -> float:
    """
    连续评分 0-100：信号「多接近有效」
    即使 is_valid=False，评分越高说明结构越接近突破点。
    """
    pushes = len(result.push_set.pushes)
    ex_score = result.push_set.exhaustion_score or 0.0
    ch_type = result.channel.channel_type
    
    push_score = min(pushes * 10, 30)                     # 0-30: 推数越多越接近
    ex_score_pts = ex_score * 30                          # 0-30: 衰竭越高越接近
    
    ch_pts = {
        ChannelType.CONVERGING_WEDGE: 25,
        ChannelType.EXPANDING_TRIANGLE: 22,
        ChannelType.PARABOLIC_WEDGE: 25,
        ChannelType.THREE_PUSH_NON_WEDGE: 18,
        ChannelType.PARALLEL: 8,
    }.get(ch_type, 3)                                      # 0-25: 楔形=高分
    
    state_pts = 15 if result.market_state.allow_wedge_reversal else 5  # 0-15
    
    return push_score + ex_score_pts + ch_pts + state_pts  # max 100


def infer_direction(result) -> str:
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
            return "做空(提示)"  # 第三推向上 → 潜在看跌反转
        elif last_dir.value == "bear":
            return "做多(提示)"  # 第三推向下 → 潜在看涨反转
    return "待定"


# ─── 主流程 ─────────────────────────────────────────────
def main():
    print(f"{'='*75}")
    print(f"  TOP40 期货 15分钟 K线 三推衰竭信号扫描")
    print(f"  {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*75}")
    
    # 1. 从 futures_top40.json 读取 TOP40
    symbols = load_top40()
    print(f"  加载 {len(symbols)} 个品种")

    # 注入模式：单连接批量注入 15m 缓存（滚动窗口 300 根）
    symbol_tuples = [(s['symbol'], s['name'], s['exchange']) for s in symbols]
    print(f"  注入15分钟K线数据 (滚动窗口300根)...")
    klines = inject_many(symbol_tuples, period="15", length=300)
    print(f"  成功注入 {len(klines)}/{len(symbols)} 个品种，开始扫描...\n")

    engine = QuantEngine()
    config = QuantConfig()

    results_long = []
    results_short = []
    errors = []

    start = time.time()
    for i, sym in enumerate(symbols):
        code = sym['symbol']
        name = sym['name']
        exchange = sym['exchange']

        if code not in klines:
            print(f"  [{i+1}/{len(symbols)}] {code} {name} -> 无数据")
            continue
        df = klines[code]
        if df.empty or len(df) < 10:
            print(f"  [{i+1}/{len(symbols)}] {code} {name} -> 数据不足({len(df)})")
            continue

        print(f"  [{i+1}/{len(symbols)}] {code} {name} ({exchange}) ...", end=" ", flush=True)

        try:
            result = engine.analyze_df(df)
            
            signal = result.signal
            score = signal_proximity_score(result)
            direction = infer_direction(result)
            
            entry = None
            stop = None
            target = None
            rr = None
            if signal.levels:
                entry = signal.levels.entry
                stop = signal.levels.stop
                target = signal.levels.target_1
                rr = signal.levels.reward_risk
            
            # 安全处理 None 值
            push_count = len(result.push_set.pushes)
            ex_score = result.push_set.exhaustion_score or 0
            ch_type = result.channel.channel_type.value if result.channel.channel_type else "unknown"
            market_regime = result.market_state.regime.value if result.market_state.regime else "unknown"
            
            valid_mark = "✅有效" if signal.is_valid else ""
            
            print(f"推={push_count} 衰竭={ex_score:.2f} 通道={ch_type} "
                  f"方向={direction} 评分={score:.0f} {valid_mark}")
            
            row = {
                '排名': sym['排名'],
                '代码': code,
                '名称': name,
                '方向': direction,
                '评分': round(score, 1),
                '有效信号': signal.is_valid,
                '推数': push_count,
                '衰竭分': round(ex_score, 2),
                '通道类型': ch_type,
                '市场状态': market_regime,
                '入场价': round(entry, 2) if entry and not (isinstance(entry, float) and np.isnan(entry)) else '',
                '止损价': round(stop, 2) if stop and not (isinstance(stop, float) and np.isnan(stop)) else '',
                '目标价': round(target, 2) if target and not (isinstance(target, float) and np.isnan(target)) else '',
                '盈亏比': round(rr, 2) if rr and not (isinstance(rr, float) and np.isnan(rr)) else '',
                '信号原因': signal.entry_reason[:60] if signal.entry_reason else '',
            }
            
            if direction == "做多":
                results_long.append(row)
            elif direction == "做空":
                results_short.append(row)
            else:
                pass
        
        except Exception as e:
            err_msg = str(e).replace('\n', ' ')[:80]
            print(f"❌ {err_msg}")
            errors.append({'代码': code, '名称': name, '错误': err_msg})

    elapsed = time.time() - start
    print(f"\n  扫描完成: 耗时 {elapsed:.0f}s")
    print(f"  做多信号: {len(results_long)}  做空信号: {len(results_short)}  错误: {len(errors)}")
    
    # 排序输出
    df_long = pd.DataFrame(results_long).sort_values('评分', ascending=False).reset_index(drop=True)
    df_short = pd.DataFrame(results_short).sort_values('评分', ascending=False).reset_index(drop=True)
    
    # 打印 TOP3 做多
    print(f"\n{'='*75}")
    print(f"  【做多 TOP 3】⬆️")
    print(f"{'='*75}")
    if len(df_long) > 0:
        print(f"  {'排名':>3} {'代码':>6} {'名称':<8} {'评分':>5} {'推数':>3} {'衰竭':>5} {'通道':>18} {'入场':>8} {'止损':>8} {'目标':>8}")
        print(f"  {'-'*75}")
        for _, r in df_long.head(3).iterrows():
            valid_tag = "✅" if r['有效信号'] else ""
            print(f"  {r['排名']:>3} {r['代码']:>6} {r['名称']:<8} {r['评分']:>5} {r['推数']:>3} "
                  f"{r['衰竭分']:>5.2f} {r['通道类型']:>18} {r['入场价']:>8} {r['止损价']:>8} {r['目标价']:>8} {valid_tag}")
    else:
        print("  (无做多信号)")
    
    # 打印 TOP3 做空
    print(f"\n{'='*75}")
    print(f"  【做空 TOP 3】⬇️")
    print(f"{'='*75}")
    if len(df_short) > 0:
        print(f"  {'排名':>3} {'代码':>6} {'名称':<8} {'评分':>5} {'推数':>3} {'衰竭':>5} {'通道':>18} {'入场':>8} {'止损':>8} {'目标':>8}")
        print(f"  {'-'*75}")
        for _, r in df_short.head(3).iterrows():
            valid_tag = "✅" if r['有效信号'] else ""
            print(f"  {r['排名']:>3} {r['代码']:>6} {r['名称']:<8} {r['评分']:>5} {r['推数']:>3} "
                  f"{r['衰竭分']:>5.2f} {r['通道类型']:>18} {r['入场价']:>8} {r['止损价']:>8} {r['目标价']:>8} {valid_tag}")
    else:
        print("  (无做空信号)")
    
    # 总体统计
    print(f"\n{'='*75}")
    print(f"  整体统计")
    print(f"{'='*75}")
    all_results = results_long + results_short
    df_all = pd.DataFrame(all_results)
    if not df_all.empty:
        valid_count = df_all['有效信号'].sum()
        top_long = df_long.head(3)['代码'].tolist() if len(df_long) > 0 else []
        top_short = df_short.head(3)['代码'].tolist() if len(df_short) > 0 else []
        print(f"  有效信号数: {valid_count}")
        print(f"  做多TOP3: {', '.join(top_long)}")
        print(f"  做空TOP3: {', '.join(top_short)}")
        print(f"  平均评分(做多): {df_long['评分'].mean():.1f}" if len(df_long) > 0 else "")
        print(f"  平均评分(做空): {df_short['评分'].mean():.1f}" if len(df_short) > 0 else "")
    
    if errors:
        print(f"\n  错误 {len(errors)} 个:")
        for e in errors:
            print(f"    {e['代码']} {e['名称']}: {e['错误']}")
    
    print(f"\n{'='*75}")

if __name__ == '__main__':
    main()
