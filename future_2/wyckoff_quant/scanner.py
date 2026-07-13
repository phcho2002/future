"""Scanner — batch scan futures symbols for Wyckoff signals using xtquant."""

import sqlite3
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

from wyckoff_quant.config import WyckoffConfig
from wyckoff_quant.data.tqsdk_provider import build_tq_symbol, fetch_kline, fetch_many
from wyckoff_quant.engine import WyckoffEngine
from wyckoff_quant.core.types import WyckoffSignal

DB_PATH = Path("D:/work_ai/futures_data.db")


def get_top40() -> list[tuple[str, str, str]]:
    """Fetch top 40 futures symbols from database."""
    conn = sqlite3.connect(str(DB_PATH))
    try:
        cur = conn.cursor()
        cur.execute("SELECT symbol, name, exchange FROM futures_top40 ORDER BY 排名")
        return [(r[0], r[1], r[2]) for r in cur.fetchall()]
    finally:
        conn.close()


def scan_all(
    period: str = "15",
    config: WyckoffConfig | None = None,
    data_length: int = 200,
) -> list[dict]:
    """Scan all futures symbols for Wyckoff signals.

    Parameters
    ----------
    period : str
        K-line period in minutes, e.g. "15", "30", "60", "240".
    config : WyckoffConfig | None
        Analysis configuration. Uses default if None.
    data_length : int
        Number of K-line bars to fetch per symbol.

    Returns
    -------
    list[dict]
        List of signal dictionaries for symbols with valid signals.
    """
    engine = WyckoffEngine(config=config)
    symbols = get_top40()

    print(f"[{datetime.now():%H:%M:%S}] 威科夫扫描 — {len(symbols)} 品种, {period}分钟K线")
    print(f"  (单连接批量拉取 {len(symbols)} 品种 ...)", flush=True)

    signals_found = []
    errors = []

    # 关键优化：原代码每个品种重连。
    # 改为一次 fetch_many 单连接拿全量，再逐个分析。
    t_fetch_start = time.time()
    data = fetch_many(symbols, period=period, length=data_length)
    print(f"  数据拉取完成: {len(data)}/{len(symbols)} 成功, {time.time()-t_fetch_start:.1f}s")
    print()

    for idx, (sym, name, ex) in enumerate(symbols, 1):
        prefix = f"[{idx:02d}/{len(symbols)}] {sym:6s} {name:12s} ({ex})"
        print(f"{prefix}  ... ", end="", flush=True)

        try:
            df = data.get(sym)
            if df is None or df.empty:
                raise ValueError(f"无法获取数据: {build_tq_symbol(sym, ex)}")

            result = engine.analyze_df(df)
            sig = result.signal

            if sig.is_valid and sig.side in ("long", "short"):
                side_str = "📈 LONG" if sig.side == "long" else "📉 SHORT"
                lv = sig.levels
                signals_found.append({
                    "symbol": sym,
                    "name": name,
                    "exchange": ex,
                    "side": sig.side,
                    "entry": lv.entry,
                    "stop": lv.stop,
                    "target": lv.target,
                    "rsi": sig.rsi_value,
                    "phase": sig.phase.phase,
                    "stage": sig.phase.stage,
                    "phase_desc": sig.phase.description,
                    "reason": sig.entry_reason,
                    "metadata": sig.metadata,
                })
                print(f"⚠️  {side_str} 入场={lv.entry:.2f} 止损={lv.stop:.2f}")
            else:
                ra = result.range_analysis
                va = result.volume_analysis
                sb = result.stop_behavior
                ph = result.phase

                range_str = f"支撑={ra.support_level:.0f} 阻力={ra.resistance_level:.0f} 测试={ra.test_count}次"
                vol_str = f"量趋势={va.volume_trend} 背离={va.effort_result_divergence}"
                stop_str = f"停止={sb.has_stop}"
                phase_str = f"阶段={ph.phase}"
                print(f"无信号 {phase_str} | {range_str} | {vol_str} | {stop_str} | ({sig.entry_reason})")
        except Exception as e:
            errors.append((sym, name, str(e)))
            print(f"❌ {e}")

    # --- report ---
    print()
    print("=" * 78)
    print(f"  扫描完成  |  信号: {len(signals_found)}  |  错误: {len(errors)}")
    print("=" * 78)

    if signals_found:
        print()
        print("╔══════════════════════════════════════════════════════════════════════════╗")
        print("║                    威科夫量价分析 — 交易信号列表                         ║")
        print("╚══════════════════════════════════════════════════════════════════════════╝")
        for i, s in enumerate(signals_found, 1):
            side_cn = "📈 做多" if s["side"] == "long" else "📉 做空"
            pattern = s["metadata"].get("pattern", "")
            print()
            print(f"  ┌── 信号 #{i} ──────────────────────────────────────────────────")
            print(f"  │ {s['symbol']:6s} {s['name']:10s} ({s['exchange']})")
            print(f"  │ 方向: {side_cn} ({pattern})")
            print(f"  │ 阶段: {s['phase_desc']}")
            print(f"  │ 入场: {s['entry']:>10.2f}  止损: {s['stop']:>10.2f}")
            print(f"  │ 目标: {s['target']:>10.2f}  RSI: {s['rsi']:>8.1f}")
            print(f"  │ 逻辑: {s['reason']}")
            print(f"  └────────────────────────────────────────────────────────────────")

    if errors:
        print("\n错误明细:")
        for sym, name, err in errors:
            print(f"  {sym:6s} {name}: {err}")

    print(f"\n[{datetime.now():%H:%M:%S}] 结束")

    return signals_found
