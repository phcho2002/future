"""高周期关键价位 —— 多周期系统的 60m 侧。

在 60m 上识别两类关键价位，合并后供 mtf_signal 按"接近度"筛选：
    1. 成交量加权密集区（复用 levels.detect_volume_zones）—— 大家都在这儿成交
    2. swing 波峰/波谷（复用 swing.find_swing_highs/lows）—— 价格到这儿被挡回去

两者互补：密集区看的是"堆积"，swing 点看的是"转折"。合并后按趋势方向
（做多取下方支撑 / 做空取上方阻力）取最近价位的 zone。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from fakebreak.levels import detect_volume_zones
from fakebreak.swing import find_swing_highs, find_swing_lows
from fakebreak.types import Zone


def detect_swing_zones(
    df: pd.DataFrame,
    atr: float,
    lookback: int = 3,
    n_points: int = 5,
    bin_mult: float = 0.75,
) -> list[Zone]:
    """从最近 swing 高点/低点构造关键价位 Zone。

    swing high → 阻力区（source="swing_high"）
    swing low  → 支撑区（source="swing_low"）

    每个 swing 点展成一个 bin 宽（atr*bin_mult）的价格带，中心就是 swing 价。
    strength 给固定值 0.5——swing zone 不参与成交量排序竞争，仅用于 proximity 筛选；
    合并去重时与 volume zone 比较会自然让位（volume zone 通常 strength 更高）。

    Parameters
    ----------
    df : 高周期（60m）OHLCV DataFrame
    atr : 当前 ATR（决定 zone 宽度）
    lookback : swing 点检测左右窗口（传给 find_swing_highs/lows）
    n_points : 最多取最近几个 swing 点（高+低各 n_points 个）
    bin_mult : zone 半宽 = atr * bin_mult / 2
    """
    if len(df) < 2 * lookback + 1 or not atr or atr <= 0:
        return []

    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)

    sh_idx = find_swing_highs(high, lookback)
    sl_idx = find_swing_lows(low, lookback)

    half_w = max(atr * bin_mult / 2.0, 1e-9)
    zones: list[Zone] = []

    for i in sh_idx[-n_points:]:
        c = float(high[i])
        zones.append(Zone(center=c, lower=c - half_w, upper=c + half_w,
                          strength=0.5, source="swing_high"))
    for i in sl_idx[-n_points:]:
        c = float(low[i])
        zones.append(Zone(center=c, lower=c - half_w, upper=c + half_w,
                          strength=0.5, source="swing_low"))
    return zones


def merge_zones(volume_zones: list[Zone], swing_zones: list[Zone]) -> list[Zone]:
    """合并两类 zone，价位相近（半宽内）的去重，保留 strength 高者。

    合并后按 center 升序排列，方便按"当前价上方/下方"筛选。
    """
    all_zones = list(volume_zones) + list(swing_zones)
    if not all_zones:
        return []

    # 按 center 排序后做相邻去重
    all_zones.sort(key=lambda z: z.center)
    merged: list[Zone] = []
    for z in all_zones:
        if merged and abs(z.center - merged[-1].center) <= (z.upper - z.lower):
            # 价位重叠 → 保留 strength 更高的
            if z.strength > merged[-1].strength:
                merged[-1] = z
        else:
            merged.append(z)
    return merged


def zones_near_price(
    zones: list[Zone],
    price: float,
    atr: float,
    proximity_atr: float = 1.0,
) -> list[Zone]:
    """筛选距当前价 proximity_atr×ATR 以内的 zone（"接近关键位"判定）。

    返回的 zone 列表按与 price 的距离升序。无命中返回空列表。
    """
    if not zones or not atr or atr <= 0:
        return []
    threshold = proximity_atr * atr
    near = [z for z in zones if abs(z.center - price) <= threshold]
    near.sort(key=lambda z: abs(z.center - price))
    return near
