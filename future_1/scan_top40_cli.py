"""
TOP40 期货 15分钟 K线 三推衰竭信号扫描
注入模式：先 inject_many 单连接批量注入缓存（滚动 300 根），再逐个 analyze_df。
仅输出有效信号（is_valid=True）的结果到飞书。
"""
import sys, time, json
from pathlib import Path
from dataclasses import asdict
# 兼容 Linux/Windows：从脚本位置向上推导到 work_ai 根目录
_SCRIPT_DIR = Path(__file__).resolve().parent
_WORK_AI = _SCRIPT_DIR.parent  # .../work_ai/future_1 -> .../work_ai
sys.path.insert(0, str(_SCRIPT_DIR))
sys.path.insert(0, str(_WORK_AI))

import pandas as pd
import numpy as np

from future_data import inject_many
from future_quant.engine import QuantEngine
from future_quant.core.types import SignalSide
from future_quant.data.universe import load_top40


def signal_to_dict(symbol, name, exchange, result, elapsed_s):
    """将 AnalysisResult 转为可序列化的信号字典"""
    signal = result.signal
    if not signal.is_valid:
        return None

    push_set = result.push_set
    channel = result.channel
    ms = result.market_state
    levels = signal.levels

    return {
        "symbol": symbol,
        "name": name,
        "exchange": exchange,
        "direction": signal.side.value if signal.side else "none",
        "entry_reason": signal.entry_reason,
        "push_count": len(push_set.pushes),
        "exhaustion_score": round(push_set.exhaustion_score or 0, 3),
        "channel_type": channel.channel_type.value if channel.channel_type else "unknown",
        "market_regime": ms.regime.value if ms.regime else "unknown",
        "entry_price": round(levels.entry, 2) if levels and levels.entry and not np.isnan(levels.entry) else None,
        "stop_price": round(levels.stop, 2) if levels and levels.stop and not np.isnan(levels.stop) else None,
        "target_1": round(levels.target_1, 2) if levels and levels.target_1 and not np.isnan(levels.target_1) else None,
        "target_2": round(levels.target_2, 2) if levels and levels.target_2 and not np.isnan(levels.target_2) else None,
        "reward_risk": round(levels.reward_risk, 2) if levels and levels.reward_risk and not np.isnan(levels.reward_risk) else None,
        "elapsed": round(elapsed_s, 1),
    }


def main():
    print(f"TOP40 15分钟三推衰竭信号扫描", flush=True)
    print(f"启动时间: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S')}", flush=True)

    # 读取 TOP40
    symbols = load_top40()
    print(f"加载 {len(symbols)} 个品种", flush=True)

    # 注入模式：单连接批量注入 15m 缓存（滚动窗口 300 根），盘中可反复跑
    symbol_tuples = [(s['symbol'], s['name'], s['exchange']) for s in symbols]
    print(f"注入15分钟K线数据 (滚动窗口300根)...", flush=True)
    klines = inject_many(symbol_tuples, period="15", length=300)
    print(f"成功注入 {len(klines)}/{len(symbols)} 个品种", flush=True)

    engine = QuantEngine()
    signals = []
    errors = []
    total_start = time.time()

    for i, sym in enumerate(symbols):
        code = sym['symbol']
        name = sym['name']
        exchange = sym['exchange']
        sym_start = time.time()

        if code not in klines:
            print(f"  [{i+1}/{len(symbols)}] {code} {name} -> 无数据", flush=True)
            continue

        df = klines[code]
        if df.empty or len(df) < 10:
            print(f"  [{i+1}/{len(symbols)}] {code} {name} -> 数据不足({len(df)})", flush=True)
            continue

        print(f"  [{i+1}/{len(symbols)}] {code} {name} ({exchange}) ...", end=" ", flush=True)

        try:
            result = engine.analyze_df(df)

            signal_data = signal_to_dict(code, name, exchange, result, time.time() - sym_start)

            if signal_data:
                signals.append(signal_data)
                direction_emoji = "⬆️做多" if signal_data['direction'] == 'long' else "⬇️做空"
                print(f"✅ {direction_emoji} 评分={signal_data.get('exhaustion_score',0):.2f} 入场={signal_data['entry_price']}", flush=True)
            else:
                push_count = len(result.push_set.pushes)
                ex_score = result.push_set.exhaustion_score or 0
                channel_type = result.channel.channel_type.value if result.channel.channel_type else "?"
                print(f"推={push_count} 衰竭={ex_score:.2f} 通道={channel_type}", flush=True)

        except Exception as e:
            err = str(e).replace('\n', ' ')[:100]
            print(f"❌ {err}", flush=True)
            errors.append(f"{code}({name}): {err}")

    elapsed = time.time() - total_start
    print(f"\n扫描完成: {elapsed:.0f}s", flush=True)
    print(f"有效信号: {len(signals)} / {len(symbols)}", flush=True)

    # 输出信号
    if signals:
        df = pd.DataFrame(signals)
        df = df.sort_values('exhaustion_score', ascending=False).reset_index(drop=True)

        # 拆分多空
        longs = df[df['direction'] == 'long']
        shorts = df[df['direction'] == 'short']

        # 构建飞书消息
        msg_parts = []
        msg_parts.append(f"📊 TOP40 15分钟 三推衰竭信号")
        msg_parts.append(f"⏱ {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')} | 扫描{len(symbols)}个品种 | 耗时{elapsed:.0f}s")
        msg_parts.append(f"")

        if len(longs) > 0:
            msg_parts.append(f"⬆️ 做多信号 ({len(longs)}个)")
            for _, r in longs.iterrows():
                rr = f" R/R={r['reward_risk']}" if r.get('reward_risk') else ""
                msg_parts.append(f"  {r['symbol']} {r['name']} 入场{r['entry_price']} 止损{r['stop_price']} 目标{r.get('target_1','?')}{rr}")
            msg_parts.append(f"")

        if len(shorts) > 0:
            msg_parts.append(f"⬇️ 做空信号 ({len(shorts)}个)")
            for _, r in shorts.iterrows():
                rr = f" R/R={r['reward_risk']}" if r.get('reward_risk') else ""
                msg_parts.append(f"  {r['symbol']} {r['name']} 入场{r['entry_price']} 止损{r['stop_price']} 目标{r.get('target_1','?')}{rr}")
            msg_parts.append(f"")

        # 完整列表
        if len(signals) > 0:
            msg_parts.append(f"完整信号列表:")
            for _, r in df.iterrows():
                dir_emoji = "⬆️" if r['direction'] == 'long' else "⬇️"
                msg_parts.append(f"  {dir_emoji} {r['symbol']}({r['name']}) 衰竭{r['exhaustion_score']:.2f} 通道{r['channel_type']}")

        if errors:
            msg_parts.append(f"\n错误 {len(errors)} 个:")
            for e in errors[:5]:
                msg_parts.append(f"  {e}")

        # 打印消息（cron捕获stdout发飞书）
        print("\n" + "=" * 60)
        print("FEISHU_MESSAGE_BELOW")
        print("=" * 60)
        print("\n".join(msg_parts))
        print("=" * 60)
    else:
        print(f"\n无有效信号，不发送飞书")
        # 即使无信号也打印一个简单摘要
        print(f"\n{'='*50}")
        print(f"TOP40 15分钟扫描完成 — 无有效信号")
        print(f"{pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')} | {len(symbols)}品种 | {elapsed:.0f}s")
        if errors:
            print(f"错误: {len(errors)}个")
        print(f"{'='*50}")

    print(f"\n结束: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S')}", flush=True)


if __name__ == '__main__':
    main()
