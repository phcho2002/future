"""
plot.py
=======
为单个品种绘制 Renko 图并标记信号。
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import matplotlib
matplotlib.use("Agg")
import matplotlib.font_manager as fm
import matplotlib.pyplot as plt
import pandas as pd

from signals import Signal


# 尝试使用系统自带中文字体，避免标题/标签出现方框
_CJK_FONTS = ["Microsoft YaHei", "SimHei", "SimSun", "WenQuanYi Micro Hei"]
_ch_font = next((f for f in _CJK_FONTS if f in [font.name for font in fm.fontManager.ttflist]), None)
if _ch_font:
    plt.rcParams["font.sans-serif"] = [_ch_font] + plt.rcParams.get("font.sans-serif", [])
plt.rcParams["axes.unicode_minus"] = False


def plot_renko_for_symbol(
    symbol: str,
    name: str,
    renko: pd.DataFrame,
    brick_size: float,
    signals: list[Signal],
    output_path: str,
    max_bars: int = 120,
    show_volume: bool = True,
) -> None:
    """绘制 Renko 图：上涨绿色，下跌红色，标记支撑/阻力/信号。

    若 show_volume=True（默认）且 renko 含 volume 列，则在主图下方叠加成交量柱状图。
    """
    if renko.empty:
        return

    tail = renko.tail(max_bars).reset_index(drop=True)
    has_volume = show_volume and "volume" in tail.columns and tail["volume"].notna().any()

    if has_volume:
        fig, axes = plt.subplots(
            nrows=2,
            ncols=1,
            figsize=(14, 9),
            gridspec_kw={"height_ratios": [4, 1]},
            sharex=True,
        )
        ax_price, ax_vol = axes
    else:
        fig, ax_price = plt.subplots(figsize=(14, 7))
        ax_vol = None

    for i, row in tail.iterrows():
        color = "#2e7d32" if row["direction"] == 1 else "#c62828"
        # 每块砖画一个小矩形
        x = i
        y = min(row["open"], row["close"])
        height = abs(row["close"] - row["open"])
        rect = plt.Rectangle((x - 0.4, y), 0.8, height, facecolor=color, edgecolor="black", linewidth=0.5)
        ax_price.add_patch(rect)

    # 标记信号位置
    signal_markers = {
        "zone_breakout_up": "^",
        "zone_breakout_down": "v",
        "zone_rejection_up": ">",
        "zone_rejection_down": "<",
        "trend_pullback_long": "P",
        "trend_pullback_short": "X",
    }

    for sig in signals:
        # 信号发生在最新砖，直接标在最后一块
        x = len(tail) - 1
        y = tail["close"].iloc[-1]
        marker = signal_markers.get(sig.signal_type, "*")
        ax_price.scatter(x, y, s=220, c="gold", marker=marker, edgecolors="black", linewidths=1.2, zorder=5)
        ax_price.annotate(
            sig.signal_type,
            (x, y),
            textcoords="offset points",
            xytext=(12, 12),
            fontsize=8,
            color="navy",
            bbox=dict(boxstyle="round,pad=0.2", fc="yellow", alpha=0.6),
        )

    ax_price.set_title(f"{symbol} {name} | Renko (brick={brick_size:.4f})")
    ax_price.set_ylabel("Price")
    ax_price.grid(True, alpha=0.3)

    # 留出足够边距
    ax_price.set_xlim(-1, len(tail))
    price_min = tail["bottom"].min()
    price_max = tail["top"].max()
    pad = (price_max - price_min) * 0.1 if price_max > price_min else brick_size * 5
    ax_price.set_ylim(price_min - pad, price_max + pad)

    if ax_vol is not None:
        # 成交量柱状图：按砖块方向着色
        vol_colors = ["#2e7d32" if d == 1 else "#c62828" for d in tail["direction"]]
        ax_vol.bar(tail.index, tail["volume"], color=vol_colors, edgecolor="black", linewidth=0.3, alpha=0.85)
        ax_vol.set_xlabel("Brick index")
        ax_vol.set_ylabel("Volume")
        ax_vol.grid(True, alpha=0.3, axis="y")
        ax_vol.set_ylim(0, tail["volume"].max() * 1.15 if tail["volume"].max() > 0 else 1)

    plt.tight_layout()
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(output_path, dpi=120)
    plt.close(fig)
