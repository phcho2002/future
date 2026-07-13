"""
analyze.py
==========
对单个品种做深度分析：
  1. 加载小时 K 线 → 计算砖块大小 → 生成 Renko
  2. 识别全部信号（密集区/趋势回调/RSI 风险）+ 密集成交区
  3. 输出统计 + 最近信号明细
  4. 画图：Renko 砖块 + 密集成交区带 + 信号标注 + Renko-RSI 子图
"""
from __future__ import annotations

import argparse
import os
from typing import Optional

import pandas as pd

from data_loader import load_config, load_klines
from renko import compute_brick_size, build_renko
from signals import find_all_signals
from run_scan import analyze_symbol, plot_renko


def analyze(symbol: str, cfg: Optional[dict] = None,
            lookback: int = 150, save_chart: bool = True) -> dict:
    cfg = cfg or load_config()
    print(f"[analyze] 加载 {symbol} ...")
    df = load_klines(symbol, cfg)
    print(f"[analyze] 小时K线: {len(df)} 根, "
          f"{df['datetime'].iloc[0]} ~ {df['datetime'].iloc[-1]}")

    a = analyze_symbol(df, cfg, symbol)
    rk = a["renko"]
    meta = a["meta"]
    res = a["sig"]

    print(f"\n=== {symbol} Renko 概览 ===")
    print(f"  砖块大小 bs = {a['brick_size']}  (ATR={meta['atr']:.4f})")
    print(f"  砖块数 = {meta['bricks']}  (涨:{meta['n_up']} 跌:{meta['n_down']})")
    print(f"  最近砖: dir={'UP' if meta['last_brick_dir']==1 else 'DN'} "
          f"close={meta['last_brick_close']} time={meta['last_brick_time']}")
    print(f"  Renko-RSI 末值 = {meta['last_rsi']:.1f}")

    print(f"\n密集成交区: {len(res['zones'])} 条")
    if len(res["zones"]):
        z = res["zones"].copy()
        z["mid"] = (z["price_lo"] + z["price_hi"]) / 2.0
        z["dist"] = (z["mid"] - meta["last_close"]).abs()
        z["side"] = z["mid"].apply(lambda m: "阻力" if m > meta["last_close"] else "支撑")
        print(z.sort_values("dist").head(5)[
            ["zone_id", "price_lo", "price_hi", "n_touch", "n_up", "n_down", "side"]
        ].to_string(index=False))

    print(f"\n趋势回调信号: {len(res['trend_sig'])} 条")
    if len(res["trend_sig"]):
        print(res["trend_sig"].tail(8)[[
            "type", "direction", "trend_bricks", "pullback_ratio",
            "entry_brick_idx", "entry_time", "entry_price"
        ]].to_string(index=False))

    print(f"\n密集区信号: {len(res['zone_sig'])} 条")
    if len(res["zone_sig"]):
        recent = res["zone_sig"].tail(8)
        print(recent[["type", "direction", "entry_brick_idx",
                      "entry_time", "entry_price", "zone_n_touch"]].to_string(index=False))

    print(f"\nRSI 超买超卖风险点: {len(res['rsi_risk'])} 条")
    if len(res["rsi_risk"]):
        print(res["rsi_risk"].tail(8)[[
            "type", "direction", "rsi", "entry_brick_idx", "entry_time", "entry_price"
        ]].to_string(index=False))

    if save_chart:
        out_dir = cfg["output"]["dir"]
        chart_dir = os.path.join(out_dir, cfg["output"]["charts_dir"])
        os.makedirs(chart_dir, exist_ok=True)
        # 品种名
        from data_loader import read_symbols
        syms = read_symbols(cfg)
        name_map = dict(zip(syms["symbol"], syms["name"]))
        name = name_map.get(symbol, "")
        chart_path = os.path.join(chart_dir, f"{symbol}_analysis.png")
        plot_renko(rk, res, meta, symbol, name, cfg, chart_path,
                   lookback_bricks=lookback)
        print(f"\n分析图表: {chart_path}")

    return {"symbol": symbol, "renko": rk, "signals": res, "meta": meta}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="AU0", help="品种代码")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--lookback", type=int, default=150, help="图表回看砖块数")
    args = ap.parse_args()
    analyze(args.symbol, load_config(args.config), lookback=args.lookback)


if __name__ == "__main__":
    main()
