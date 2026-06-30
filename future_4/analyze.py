"""
analyze.py
==========
对指定品种做深度分析：
  1. 加载 K 线并计算指标
  2. 识别历史全部信号
  3. 回测得到每笔交易明细和绩效
  4. 输出信号统计 + 绩效指标
  5. 画图：K线 + EMA + 缠绕带 + 所有信号标注
"""
from __future__ import annotations

import argparse
import os
from typing import Optional

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

from data_loader import load_config, load_klines
from indicators import attach_indicators
from signals import find_signals
from backtest import backtest, performance


def analyze(symbol: str, cfg: Optional[dict] = None, lookback: int = 200, save_chart: bool = True) -> dict:
    cfg = cfg or load_config()
    p = cfg["strategy"]
    bt_p = cfg["backtest"]

    print(f"[analyze] 加载 {symbol} ...")
    df = load_klines(symbol, cfg)
    print(f"[analyze] K线: {len(df)} 根, 时间范围 {df['datetime'].iloc[0]} ~ {df['datetime'].iloc[-1]}")

    trades, d = backtest(df, p, bt_p)
    sigs = find_signals(df, p)
    perf = performance(trades)

    print(f"\n=== {symbol} 绩效 ===")
    for k, v in perf.items():
        print(f"  {k}: {v:.3f}" if isinstance(v, float) else f"  {k}: {v}")

    print(f"\n历史信号总数: {len(sigs)}")
    if len(sigs):
        print("\n最近 10 个信号:")
        print(sigs.tail(10)[["datetime", "direction", "bonus", "twist_cross", "entry_price"]].to_string(index=False))

    if len(trades):
        print(f"\n最近 10 笔交易:")
        print(trades.tail(10)[["entry_time", "exit_time", "direction", "entry_price",
                               "exit_price", "r_mult", "exit_reason", "bonus"]].to_string(index=False))

    if save_chart:
        out_dir = cfg["output"]["dir"]
        chart_dir = os.path.join(out_dir, cfg["output"]["charts_dir"])
        os.makedirs(chart_dir, exist_ok=True)
        chart_path = os.path.join(chart_dir, f"{symbol}_analysis.png")
        _plot(df, sigs, trades, p, symbol, chart_path, lookback=lookback)
        print(f"\n分析图表: {chart_path}")

    return {
        "symbol": symbol,
        "perf": perf,
        "signals": sigs,
        "trades": trades,
        "df_with_indicators": d,
    }


def _plot(df: pd.DataFrame, sigs: pd.DataFrame, trades: pd.DataFrame, p: dict,
          symbol: str, out_path: str, lookback: int = 200) -> None:
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "SimSun", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False

    d = attach_indicators(df, p).tail(lookback).reset_index(drop=True)
    cutoff = len(df) - lookback

    sigs = sigs[sigs["cross_idx"] >= cutoff].copy()
    for c in ["cross_idx", "confirm_idx", "entry_idx"]:
        if len(sigs):
            sigs[c] = sigs[c].astype(int) - cutoff

    trades = trades[trades["entry_time"] >= d["datetime"].iloc[0]].copy() if len(trades) else trades

    fig, (ax, axv) = plt.subplots(2, 1, figsize=(15, 8), gridspec_kw={"height_ratios": [3, 1]}, sharex=True)
    x = np.arange(len(d))
    up = d["close"] >= d["open"]
    ax.vlines(x[up], d["low"][up], d["high"][up], color="#d62728", lw=0.8)
    ax.vlines(x[~up], d["low"][~up], d["high"][~up], color="#2ca02c", lw=0.8)
    for i in x:
        o, c = d["open"].iloc[i], d["close"].iloc[i]
        col = "#d62728" if c >= o else "#2ca02c"
        ax.add_patch(Rectangle((i - 0.3, min(o, c)), 0.6, abs(c - o) or 0.01, color=col, lw=0))

    ax.plot(x, d["ema_fast"], label=f"EMA{p['ema_fast']}", color="#ff7f0e", lw=1.2)
    ax.plot(x, d["ema_slow"], label=f"EMA{p['ema_slow']}", color="#1f77b4", lw=1.2)

    # 缠绕带高亮
    twist = d["twist_cross"] >= p["min_crossings"]
    for i in x:
        if twist.iloc[i]:
            ax.axvspan(i - 0.5, i + 0.5, color="#ffdd44", alpha=0.12, lw=0)

    # 信号标注
    for _, s in sigs.iterrows():
        kfi = int(s["confirm_idx"]) if pd.notna(s["confirm_idx"]) else int(s["cross_idx"])
        if 0 <= kfi < len(d):
            mk = "^" if s["direction"] == 1 else "v"
            col = "#d62728" if s["direction"] == 1 else "#2ca02c"
            ax.scatter(kfi, d["low"].iloc[kfi] * 0.995, marker=mk, color=col, s=90, zorder=5,
                       edgecolors="black", linewidths=0.5)

    # 交易出入场连线
    for _, t in trades.iterrows():
        ei = int(t["entry_idx"])
        xi = int(t["exit_idx"])
        if cutoff <= ei < len(df) and cutoff <= xi < len(df):
            ei -= cutoff
            xi -= cutoff
            col = "#d62728" if t["direction"] == 1 else "#2ca02c"
            ax.plot([ei, xi], [t["entry_price"], t["exit_price"]], color=col, lw=1.2, alpha=0.7)

    ax.set_title(f"{symbol} 深度分析 — 双均线缠绕放量突破 ({p['ema_fast']}/{p['ema_slow']})")
    ax.legend(loc="best", fontsize=8)
    ax.grid(alpha=0.25)

    # 成交量
    vol = d["volume"]
    vma = d["vol_ma"]
    axv.bar(x, vol, color=np.where(up, "#d62728", "#2ca02c"), width=0.7, alpha=0.7)
    axv.plot(x, vma, color="gray", lw=1, label=f"vol_ma{p['vol_ma']}")
    volbar = (vol > p["vol_factor"] * vma) & (d["body_ratio"] >= p["body_pct"])
    axv.bar(x[volbar], vol[volbar], color="#ffdd44", width=0.7, alpha=0.9)
    axv.legend(loc="best", fontsize=8)
    axv.grid(alpha=0.25)

    plt.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="AU0", help="分析品种代码")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--lookback", type=int, default=200, help="图表回看 K 线数")
    args = ap.parse_args()
    analyze(args.symbol, load_config(args.config), lookback=args.lookback)


if __name__ == "__main__":
    main()
