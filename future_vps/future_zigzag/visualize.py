"""ZigZag 可视化 —— 纯 matplotlib（不依赖 mplfinance，降低风险）。

借鉴 stock/visualize.py:plot_simple 的"价格线 + 散点标注 + 成交量子图"结构，
但把价格线换成手绘 candlestick，并叠加 ZigZag 连线与拐点标记。

每品种出两张图：
    - 全景：近 3000 根 close 线 + ZigZag 连线 + 拐点（看整体贴合度）
    - 特写：近 400 根 candlestick + ZigZag（看局部锯齿/假突破处理）
"""
from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # 无头运行
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib import dates as mdates

from .zigzag import ZigZagResult

# CJK 字体：Windows 上 SimHei / Microsoft YaHei 通常可用
for _f in ("Microsoft YaHei", "SimHei", "Arial Unicode MS"):
    if _f in {f.name for f in matplotlib.font_manager.fontManager.ttflist}:
        plt.rcParams["font.sans-serif"] = [_f]
        break
plt.rcParams["axes.unicode_minus"] = False


def _plot_candles(ax, df: pd.DataFrame, lo: int, hi: int) -> None:
    """在 ax 上手绘 [lo,hi) 区间的 candlestick。"""
    seg = df.iloc[lo:hi]
    x = np.arange(len(seg))
    op = seg["open"].to_numpy(float)
    cl = seg["close"].to_numpy(float)
    hi_ = seg["high"].to_numpy(float)
    lo_ = seg["low"].to_numpy(float)
    up = cl >= op
    # 影线
    ax.vlines(x, lo_, hi_, color="k", linewidth=0.6, alpha=0.5)
    # 实体
    body_lo = np.minimum(op, cl)
    body_h = np.abs(cl - op)
    body_h = np.where(body_h < 1e-9, (hi_ - lo_) * 0.05, body_h)  # doji 给一点高度
    ax.bar(x[up], body_h[up], bottom=body_lo[up], width=0.7, color="#d62728", edgecolor="none")
    ax.bar(x[~up], body_h[~up], bottom=body_lo[~up], width=0.7, color="#2ca02c", edgecolor="none")


def plot_overview(result: ZigZagResult, symbol: str, name: str,
                  out_dir: Path, tail: int | None = None) -> Path:
    """全景图：close 线 + ZigZag 连线 + 拐点。"""
    df = result.df
    n = len(df)
    lo = 0 if tail is None else max(0, n - tail)
    close = df["close"].to_numpy(float)

    fig, (ax, axv) = plt.subplots(2, 1, figsize=(16, 8), gridspec_kw={"height_ratios": [3, 1]},
                                  sharex=True)
    x = np.arange(lo, n)
    ax.plot(x, close[lo:n], color="#888", linewidth=0.8, label="close")

    # ZigZag 连线 + 拐点（已确认）
    cps = result.confirmed_pivots
    if len(cps) >= 2:
        xs = [p.index for p in cps if p.index >= lo]
        ys = [p.price for p in cps if p.index >= lo]
        ax.plot(xs, ys, color="#1f77b4", linewidth=1.2, alpha=0.9)
        hi_idx = [p.index for p in cps if p.kind == "high" and p.index >= lo]
        hi_y = [p.price for p in cps if p.kind == "high" and p.index >= lo]
        lo_idx = [p.index for p in cps if p.kind == "low" and p.index >= lo]
        lo_y = [p.price for p in cps if p.kind == "low" and p.index >= lo]
        ax.scatter(hi_idx, hi_y, marker="v", s=22, color="#d62728", zorder=5, label="波峰")
        ax.scatter(lo_idx, lo_y, marker="^", s=22, color="#2ca02c", zorder=5, label="波谷")
    # 暂定极值
    prov = result.provisional
    if prov and prov.index >= lo:
        mk = "v" if prov.kind == "high" else "^"
        col = "#ff7f0e"
        ax.scatter([prov.index], [prov.price], marker=mk, s=50, color=col,
                   edgecolor="k", zorder=6, label="暂定(未确认)")

    ax.set_title(f"{symbol} {name} — ZigZag 全景 (depth={result.config.depth_atr_multiple}×ATR, "
                 f"mode={result.config.reversal_mode}, bars={n}, pivots={result.pivot_count()})")
    ax.legend(loc="best", fontsize=8)
    ax.grid(True, alpha=0.25)

    # 成交量
    vol = df["volume"].to_numpy(float) if "volume" in df.columns else np.zeros(n)
    axv.bar(x, vol[lo:n], width=1.0, color="#4c72b0", alpha=0.5)
    axv.set_ylabel("成交量")
    axv.grid(True, alpha=0.25)

    plt.tight_layout()
    out = out_dir / f"{symbol}_overview.png"
    fig.savefig(out, dpi=110)
    plt.close(fig)
    return out


def plot_closeup(result: ZigZagResult, symbol: str, name: str,
                 out_dir: Path, tail: int = 400) -> Path:
    """特写图：近 tail 根 candlestick + ZigZag。"""
    df = result.df
    n = len(df)
    lo = max(0, n - tail)

    fig, (ax, axv) = plt.subplots(2, 1, figsize=(16, 8), gridspec_kw={"height_ratios": [3, 1]},
                                  sharex=True)
    _plot_candles(ax, df, lo, n)

    # ZigZag 叠加（用绝对索引）
    cps = [p for p in result.confirmed_pivots if p.index >= lo - 1]
    if len(cps) >= 2:
        xs = [p.index - lo for p in cps]
        ys = [p.price for p in cps]
        ax.plot(xs, ys, color="#1f77b4", linewidth=1.3, alpha=0.85)
        for p in cps:
            mk = "v" if p.kind == "high" else "^"
            col = "#d62728" if p.kind == "high" else "#2ca02c"
            ax.scatter([p.index - lo], [p.price], marker=mk, s=45, color=col, zorder=5)
    prov = result.provisional
    if prov and prov.index >= lo:
        mk = "v" if prov.kind == "high" else "^"
        ax.scatter([prov.index - lo], [prov.price], marker=mk, s=70, color="#ff7f0e",
                   edgecolor="k", zorder=6)

    ax.set_xlim(0, n - lo)
    ax.set_title(f"{symbol} {name} — ZigZag 特写 (近{tail}根 candlestick)")
    ax.grid(True, alpha=0.25)

    vol = df["volume"].to_numpy(float) if "volume" in df.columns else np.zeros(n)
    axv.bar(np.arange(n - lo), vol[lo:n], width=0.7, color="#4c72b0", alpha=0.5)
    axv.set_ylabel("成交量")
    axv.set_xlim(0, n - lo)
    axv.grid(True, alpha=0.25)

    plt.tight_layout()
    out = out_dir / f"{symbol}_closeup.png"
    fig.savefig(out, dpi=110)
    plt.close(fig)
    return out


def plot_three_push(result, patterns, symbol, name, out_dir,
                    tail=600, top_n=4) -> list:
    """画 Three Push 模式：K线 + ZigZag + 高亮每个三推模式(P0..P3) + 四维分数。

    只画最近 top_n 个有效模式（总分>=门槛），聚焦 tail 根。
    每个模式用不同颜色高亮其 4 个拐点和 3 段腿。
    """
    from .three_push import ThreePushPattern  # 避免循环导入

    df = result.df
    n = len(df)
    lo = max(0, n - tail)
    cfg = result.config

    fig, ax = plt.subplots(figsize=(16, 8))
    _plot_candles(ax, df, lo, n)

    # 背景灰 ZigZag
    cps = [p for p in result.confirmed_pivots if p.index >= lo - 1]
    if len(cps) >= 2:
        ax.plot([p.index - lo for p in cps], [p.price for p in cps],
                color="#999", linewidth=0.8, alpha=0.5, zorder=2)

    # 高亮模式
    colors = ["#e41a1c", "#377eb8", "#4daf4a", "#984ea3", "#ff7f00"]
    shown = patterns[-top_n:] if len(patterns) > top_n else patterns
    # pivot index -> price 查询表
    piv_price = {p.index: p.price for p in result.confirmed_pivots}
    for k, pat in enumerate(reversed(shown)):
        col = colors[k % len(colors)]
        xs = [pi - lo for pi in pat.pivot_indices]
        ys = [piv_price.get(pi, result.df["close"].iloc[pi]) for pi in pat.pivot_indices]
        ax.plot(xs, ys, color=col, linewidth=2.0, alpha=0.9, zorder=4)
        # 标记 P0..P5
        for j, (x, y) in enumerate(zip(xs, ys)):
            ax.scatter([x], [y], s=70, color=col, edgecolor="k", zorder=6)
            label = f"P{j}"
            if j == 5:  # 第三推极值 = 反转点
                s = pat.score
                label += (f"\n总={s.total:.2f} 门{'✓' if s.hard_gate_passed else '✗'}"
                          f"\n幅={s.amplitude:.2f} 量={s.volume:.2f}"
                          f"\n力={s.momentum:.2f} 时={s.duration:.2f}")
            ax.annotate(label, (x, y), textcoords="offset points",
                        xytext=(0, 12 if pat.reversal_kind == "high" else -22),
                        fontsize=7, color=col, ha="center", weight="bold")

    valid = [p for p in patterns if p.score.hard_gate_passed]
    ax.set_title(f"{symbol} {name} — Three Push (近{tail}根, depth={cfg.depth_atr_multiple}×ATR)\n"
                 f"共{len(patterns)}个模式, {len(valid)}个通过硬门控, 显示最近{len(shown)}个")
    ax.set_xlim(0, n - lo)
    ax.grid(True, alpha=0.25)

    plt.tight_layout()
    out = out_dir / f"{symbol}_threepush.png"
    fig.savefig(out, dpi=110)
    plt.close(fig)
    return [out]
