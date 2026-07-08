"""成交密集区：成交量加权价格直方图 + 局部峰值提取。

借鉴 future_6/signals.py 的 detect_zones 算法（直方图 + 局部峰值），做两处改造：
    1. 统计对象从 Renko 砖块 close 频数 → 原始 K 线**成交量加权**直方图
       （np.histogram(weights=volume)），这才是真正的"成交量密集堆积区"。
    2. bin 宽从 brick_size*bin_mult → atr*bin_mult，脱离 Renko 依赖。

输出：按强度（成交量占比）降序排列的 Zone 列表，每个 Zone 是一个价格带。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from fakebreak.types import Zone


def detect_volume_zones(
    df: pd.DataFrame,
    atr: float,
    zone_window: int = 60,
    zone_bin_mult: float = 0.75,
    zone_min_vol_ratio: float = 0.10,
) -> list[Zone]:
    """识别成交量加权密集区。

    Parameters
    ----------
    df : DataFrame[open, high, low, close, volume]
    atr : 当前 ATR 值，用于决定 bin 宽
    zone_window : 回看根数
    zone_bin_mult : bin 宽 = ATR * 此值
    zone_min_vol_ratio : 密集区成交量占总量比下限

    Returns
    -------
    list[Zone]  按 strength 降序
    """
    if len(df) < 5 or not atr or atr <= 0:
        return []

    recent = df.tail(zone_window)
    vols = recent["volume"].to_numpy(dtype=float)
    if vols.sum() <= 0:
        return []

    # 典型价 = (H+L+C)/3，比纯 close 更能代表一根 K 线的成交重心
    prices = (
        recent["high"].to_numpy(dtype=float)
        + recent["low"].to_numpy(dtype=float)
        + recent["close"].to_numpy(dtype=float)
    ) / 3.0

    bin_width = max(atr * zone_bin_mult, 1e-9)
    lo = prices.min() - bin_width
    hi = prices.max() + bin_width
    # 防止价格全相等（bins 退化）
    if hi - lo <= bin_width:
        bin_width = max(hi - lo, 1e-9)
        hi = lo + bin_width * 2
    bins = np.arange(lo, hi + bin_width, bin_width)
    if len(bins) < 3:
        return []

    # ★ 核心改造：weights=volume，统计每个价位带的累计成交量
    vol_per_bin, edges = np.histogram(prices, bins=bins, weights=vols)
    total_vol = vol_per_bin.sum()
    if total_vol <= 0:
        return []

    centers = (edges[:-1] + edges[1:]) / 2.0
    zones: list[Zone] = []
    for i in range(1, len(vol_per_bin) - 1):
        v = float(vol_per_bin[i])
        if v < total_vol * zone_min_vol_ratio:
            continue
        # 局部峰值：不低于左右邻居（允许等高，取连续 plateau 第一个）
        if v < vol_per_bin[i - 1] or v < vol_per_bin[i + 1]:
            continue
        zones.append(
            Zone(
                center=float(centers[i]),
                lower=float(edges[i]),
                upper=float(edges[i + 1]),
                strength=v / total_vol,
            )
        )

    zones.sort(key=lambda z: z.strength, reverse=True)
    return zones


def nearest_resistance(zones: list[Zone], price: float) -> Zone | None:
    """返回当前价上方的最近密集区（阻力）。"""
    above = [z for z in zones if z.center > price]
    if not above:
        return None
    return min(above, key=lambda z: z.center - price)


def nearest_support(zones: list[Zone], price: float) -> Zone | None:
    """返回当前价下方的最近密集区（支撑）。"""
    below = [z for z in zones if z.center < price]
    if not below:
        return None
    return max(below, key=lambda z: z.center)


def nearest_below(zones: list[Zone], zone: Zone) -> Zone | None:
    """返回某 zone 下方的最近密集区（做空目标参考）。"""
    below = [z for z in zones if z.center < zone.lower]
    if not below:
        return None
    return max(below, key=lambda z: z.center)


def nearest_above(zones: list[Zone], zone: Zone) -> Zone | None:
    """返回某 zone 上方的最近密集区（做多目标参考）。"""
    above = [z for z in zones if z.center > zone.upper]
    if not above:
        return None
    return min(above, key=lambda z: z.center)
