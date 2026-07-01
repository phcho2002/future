"""
scanner.py
==========
期货 Top40 的 Renko 量化扫描主入口。

用法：
    python scanner.py                  # 全量扫描 Top40
    python scanner.py --limit 5        # 只扫描前 5 个品种（测试用）
    python scanner.py --force          # 强制刷新缓存
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

# 确保父目录在路径中，以便 import data_loader / indicators / renko / signals
_PROJECT_ROOT = Path(__file__).resolve().parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from data_loader import load_all_klines, load_config, read_symbols
from indicators import atr, round_brick_size
from renko import build_renko
from signals import generate_signals, Signal


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Renko 量化扫描系统 (future_6)")
    p.add_argument("--limit", type=int, default=None, help="仅扫描前 N 个品种")
    p.add_argument("--force", action="store_true", help="强制刷新缓存")
    p.add_argument("--no-plot", action="store_true", help="不生成图表")
    p.add_argument("--config", default="config.yaml", help="配置文件路径")
    return p.parse_args()


def _brick_size_for_symbol(
    symbol: str,
    df: pd.DataFrame,
    renko_cfg: dict,
) -> float:
    """基于小时线 ATR 计算并圆整砖块大小。"""
    atr_period = renko_cfg["atr_period"]
    mult = renko_cfg["brick_atr_mult"]
    min_size = renko_cfg["min_brick_size"]

    atr_series = atr(df, period=atr_period)
    last_atr = float(atr_series.iloc[-1])
    last_price = float(df["close"].iloc[-1])

    raw_size = max(last_atr * mult, min_size, last_price * renko_cfg.get("min_brick_ratio", 0.0))
    return round_brick_size(raw_size, last_price, min_size=min_size)


def _signal_to_row(s: Signal) -> dict:
    return {
        "symbol": s.symbol,
        "name": s.name,
        "datetime": s.datetime,
        "close": round(s.close, 4),
        "brick_size": round(s.brick_size, 6),
        "signal_type": s.signal_type,
        "direction": s.direction,
        "strength_score": s.strength_score,
        "rsi_brick": round(s.rsi_brick, 2) if s.rsi_brick is not None else None,
        "nearest_support": round(s.nearest_support, 4) if s.nearest_support is not None else None,
        "nearest_resistance": round(s.nearest_resistance, 4) if s.nearest_resistance is not None else None,
        "risk_note": s.risk_note,
        "detail": s.detail,
    }


def main() -> int:
    args = parse_args()
    cfg = load_config(args.config)

    out_dir = Path(cfg["output"]["dir"])
    charts_dir = Path(cfg["output"]["charts_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    charts_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    signals_path = out_dir / cfg["output"]["signals_csv"].format(timestamp=timestamp)
    summary_path = out_dir / cfg["output"]["summary_csv"].format(timestamp=timestamp)

    print("=" * 80)
    print(f"Renko 量化扫描启动 | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"输出目录: {out_dir.resolve()}")
    print("=" * 80)

    # 加载 K 线
    klines = load_all_klines(cfg, limit=args.limit, progress=True)
    print()

    all_signals: list[Signal] = []
    summary_rows = []
    plot_tasks = []

    for symbol, df in klines.items():
        try:
            # 查找品种名
            syms_df = read_symbols(cfg)
            name_row = syms_df[syms_df["symbol"] == symbol]
            name = str(name_row["name"].iloc[0]) if not name_row.empty else symbol

            brick_size = _brick_size_for_symbol(symbol, df, cfg["strategy"]["renko"])
            renko = build_renko(
                df,
                brick_size=brick_size,
                reversal_mult=cfg["strategy"]["renko"]["reversal_mult"],
                max_bricks_per_bar=cfg["strategy"]["renko"]["max_bricks_per_bar"],
            )

            summary_rows.append({
                "symbol": symbol,
                "name": name,
                "klines": len(df),
                "bricks": len(renko),
                "brick_size": brick_size,
                "last_close": round(float(df["close"].iloc[-1]), 4),
                "last_datetime": df["datetime"].iloc[-1],
            })

            if renko.empty:
                print(f"[{symbol}] Renko 为空，跳过")
                continue

            sigs = generate_signals(symbol, name, df, renko, brick_size, cfg)
            all_signals.extend(sigs)

            if sigs:
                print(f"[{symbol}] {name}: 砖块={len(renko)}, 信号={len(sigs)}")
                if not args.no_plot:
                    plot_tasks.append((symbol, name, df, renko, brick_size, sigs))

        except Exception as e:
            print(f"[{symbol}] 处理失败: {e}")
            continue

    # 保存汇总
    summary_df = pd.DataFrame(summary_rows)
    if not summary_df.empty:
        summary_df.to_csv(summary_path, index=False, encoding="utf-8-sig")
        print(f"\n汇总表已保存: {summary_path}")

    # 保存信号
    if all_signals:
        sig_df = pd.DataFrame([_signal_to_row(s) for s in all_signals])
        # 排序：有方向的信号在前，强度降序
        sig_df["_has_dir"] = sig_df["direction"].abs()
        sig_df = sig_df.sort_values(["_has_dir", "strength_score"], ascending=[False, False]).drop(columns=["_has_dir"])
        sig_df.to_csv(signals_path, index=False, encoding="utf-8-sig")
        print(f"信号表已保存: {signals_path}（共 {len(sig_df)} 条）")

        # 生成图表
        if not args.no_plot:
            try:
                from plot import plot_renko_for_symbol
                top_n = cfg["output"].get("plot_top_n", 10)
                plotted = 0
                for symbol, name, df, renko, brick_size, sigs in plot_tasks[:top_n]:
                    try:
                        chart_path = charts_dir / f"{symbol}_{timestamp}.png"
                        plot_renko_for_symbol(symbol, name, renko, brick_size, sigs, str(chart_path))
                        plotted += 1
                    except Exception as e:
                        print(f"  绘图失败 {symbol}: {e}")
                print(f"图表已保存: {charts_dir}（{plotted} 张）")
            except Exception as e:
                print(f"绘图模块加载失败: {e}")
    else:
        print("\n本次扫描未产生信号。")

    print("=" * 80)
    print("扫描完成")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
