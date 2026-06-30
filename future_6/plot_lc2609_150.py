"""
plot_lc2609_150.py
==================
利用现有模块为 LC2609 绘制最近 150 块 Renko 砖块图。
"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

# 确保项目根目录在路径中
_PROJECT_ROOT = Path(__file__).resolve().parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# 确保 work_ai 目录在路径中，以便 import future_data
_WORK_AI = Path(__file__).resolve().parents[1]
if str(_WORK_AI) not in sys.path:
    sys.path.insert(0, str(_WORK_AI))

from data_loader import load_config, load_klines
from indicators import atr, round_brick_size
from renko import build_renko
from plot import plot_renko_for_symbol


def main() -> int:
    symbol = "LC2609"
    exchange = "gfex"
    output_dir = Path("output/charts")
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = output_dir / f"{symbol}_renko_150bricks_{timestamp}.png"

    cfg = load_config("config.yaml")
    print(f"加载 {symbol} K线...")
    df = load_klines(symbol, cfg, exchange=exchange, force_refresh=False)
    print(f"K线数: {len(df)}")
    print(df.tail(3).to_string(index=False))

    # 基于 ATR 计算砖块大小
    renko_cfg = cfg["strategy"]["renko"]
    atr_series = atr(df, period=renko_cfg["atr_period"])
    last_atr = float(atr_series.iloc[-1])
    last_price = float(df["close"].iloc[-1])
    raw_size = max(
        last_atr * renko_cfg["brick_atr_mult"],
        renko_cfg["min_brick_size"],
        last_price * renko_cfg.get("min_brick_ratio", 0.0),
    )
    brick_size = round_brick_size(raw_size, last_price, min_size=renko_cfg["min_brick_size"])
    print(f"最新价: {last_price:.2f}, ATR: {last_atr:.2f}, 砖块大小: {brick_size:.4f}")

    # 生成 Renko 序列
    renko = build_renko(
        df,
        brick_size=brick_size,
        reversal_mult=renko_cfg["reversal_mult"],
        max_bricks_per_bar=renko_cfg["max_bricks_per_bar"],
    )
    print(f"Renko 砖块总数: {len(renko)}")

    if renko.empty:
        print("Renko 为空，无法绘图。")
        return 1

    print(f"最近 150 块砖起始时间: {renko['datetime'].iloc[-150] if len(renko) >= 150 else renko['datetime'].iloc[0]}")
    print(f"最近 150 块砖结束时间: {renko['datetime'].iloc[-1]}")

    # 绘图：最近 150 块砖
    plot_renko_for_symbol(
        symbol=symbol,
        name=f"碳酸锂2609 ({exchange.upper()})",
        renko=renko,
        brick_size=brick_size,
        signals=[],
        output_path=str(output_path),
        max_bars=150,
    )
    print(f"图表已保存: {output_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
