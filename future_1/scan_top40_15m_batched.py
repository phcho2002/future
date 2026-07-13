"""
TOP40 期货 15分钟 K线 三推衰竭信号扫描（分批 fetch_many 版）
每批 5 个品种单连接拉取，避免 40 品种一次性注入卡住。
"""
import sys, time
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_WORK_AI = _SCRIPT_DIR.parent
sys.path.insert(0, str(_SCRIPT_DIR))
sys.path.insert(0, str(_WORK_AI))

import pandas as pd
import numpy as np
from dataclasses import asdict

from future_data import fetch_many
from future_quant.engine import QuantEngine
from future_quant.config import QuantConfig
from future_quant.core.types import SignalSide, ChannelType
from future_quant.data.universe import load_top40


def signal_proximity_score(result) -> float:
    """连续评分 0-100：信号越接近有效分数越高。"""
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


def main():
    print(f"{'='*75}")
    print(f"  TOP40 期货 15分钟 K线 三推衰竭信号扫描（分批版）")
    print(f"  {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*75}")

    symbols = load_top40()
    print(f"  加载 {len(symbols)} 个品种")

    engine = QuantEngine()
    config = QuantConfig()

    all_signals = []
    errors = []
    batch_size = 5

    # 分批拉取，每批 5 个品种
    for batch_start in range(0, len(symbols), batch_size):
        batch = symbols[batch_start:batch_start + batch_size]
        symbol_tuples = [(s['symbol'], s['name'], s['exchange']) for s in batch]
        print(f"\n  批次 {batch_start//batch_size + 1}/{(len(symbols)-1)//batch_size + 1}: "
              f"{', '.join(t[0] for t in symbol_tuples)} 拉取 15m 100根...")
        try:
            klines = fetch_many(symbol_tuples, period="15", length=100)
            print(f"  成功 {len(klines)}/{len(batch)} 个品种")
        except Exception as e:
            print(f"  批次拉取失败: {e}")
            errors.append((symbol_tuples, str(e)))
            continue

        for s in batch:
            code = s['symbol']
            name = s['name']
            exchange = s['exchange']
            print(f"  [{code:6s}] {name:12s} ...", end=" ", flush=True)
            try:
                df = klines.get(code)
                if df is None or df.empty or len(df) < 10:
                    print(f"无数据({len(df) if df is not None else 0})")
                    continue

                result = engine.analyze_df(df)
                sig = result.signal
                score = signal_proximity_score(result)
                direction = infer_direction(result)

                all_signals.append({
                    "symbol": code,
                    "name": name,
                    "exchange": exchange,
                    "direction": direction,
                    "is_valid": sig.is_valid,
                    "side": sig.side.value if sig.side else "none",
                    "entry_reason": sig.entry_reason,
                    "push_count": len(result.push_set.pushes),
                    "exhaustion_score": round(result.push_set.exhaustion_score or 0, 3),
                    "channel_type": result.channel.channel_type.value if result.channel.channel_type else "unknown",
                    "proximity_score": round(score, 1),
                    "levels": asdict(sig.levels) if sig.levels else {},
                    "market_regime": result.market_state.regime.value if result.market_state.regime else "unknown",
                })

                if sig.is_valid and sig.side in (SignalSide.LONG, SignalSide.SHORT):
                    side_str = "⬆️做多" if sig.side == SignalSide.LONG else "⬇️做空"
                    entry = sig.levels.entry if sig.levels and sig.levels.entry else None
                    print(f"✅ {side_str} 衰竭={result.push_set.exhaustion_score or 0:.2f} 入场={entry}")
                else:
                    push_count = len(result.push_set.pushes)
                    ex_score = result.push_set.exhaustion_score or 0
                    ch_type = result.channel.channel_type.value if result.channel.channel_type else "?"
                    print(f"推={push_count} 衰竭={ex_score:.2f} 通道={ch_type} 邻近度={score:.0f}")
            except Exception as e:
                err = str(e).replace('\n', ' ')[:100]
                print(f"❌ {err}")
                errors.append((code, err))

    # 输出汇总
    print(f"\n{'='*75}")
    print(f"  扫描完成 | 总样本: {len(all_signals)} | 错误: {len(errors)}")
    print(f"{'='*75}")

    valid_signals = [s for s in all_signals if s['is_valid']]
    if valid_signals:
        df_valid = pd.DataFrame(valid_signals)
        df_valid = df_valid.sort_values('exhaustion_score', ascending=False).reset_index(drop=True)
        print("\n  ✅ 有效信号（按衰竭分排序）：")
        for _, r in df_valid.iterrows():
            entry = r['levels'].get('entry')
            stop = r['levels'].get('stop')
            target = r['levels'].get('target_1')
            rr = r['levels'].get('reward_risk')
            print(f"    {r['symbol']:6s} {r['name']:12s} {r['direction']:4s} 衰竭={r['exhaustion_score']:.2f} "
                  f"入场={entry} 止损={stop} 目标={target} R/R={rr}")
    else:
        print("\n  ⚠️ 无有效三推衰竭信号")

    # 邻近度前3
    near_signals = [s for s in all_signals if not s['is_valid']]
    if near_signals:
        near_df = pd.DataFrame(near_signals).sort_values('proximity_score', ascending=False).head(3)
        print("\n  📌 邻近有效前3（结构接近突破）：")
        for _, r in near_df.iterrows():
            print(f"    {r['symbol']:6s} {r['name']:12s} {r['direction']:4s} 邻近度={r['proximity_score']:.0f} "
                  f"推={r['push_count']} 衰竭={r['exhaustion_score']:.2f} 通道={r['channel_type']}")

    # 写入 CSV
    out_path = _SCRIPT_DIR / "scan_top40_15m_results.csv"
    df_all = pd.DataFrame(all_signals)
    if not df_all.empty:
        # 展开 levels 字段
        levels_df = pd.json_normalize(df_all['levels'])
        levels_df.columns = [f'level_{c}' for c in levels_df.columns]
        df_all = pd.concat([df_all.drop(columns=['levels']), levels_df], axis=1)
        df_all = df_all.sort_values(['is_valid', 'exhaustion_score', 'proximity_score'], ascending=[False, False, False])
    df_all.to_csv(out_path, index=False, encoding='utf-8-sig')
    print(f"\n  CSV 已输出: {out_path}")

    if errors:
        print(f"\n  错误明细 ({len(errors)} 条):")
        for e in errors[:10]:
            print(f"    {e}")


if __name__ == "__main__":
    main()
