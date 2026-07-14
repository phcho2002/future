"""拐点趋势线拟合 —— 用 ZigZag 拐点（而非全部 K 线）拟合趋势线。

修正 future_1/channels.py 的缺陷：它对所有 60 根 K 线的 high/low 做 OLS，
长影线噪声全进去了，边界线乱跳。本模块只用已确认的 Pivot 拐点拟合，
抗噪且语义明确（趋势线就是"连接波段极值的线"）。
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class TrendLine:
    """一条拟合的趋势线。

    Attributes
    ----------
    slope : float
        每根 K 线的价格斜率。
    intercept : float
        在 index=0 处的截距（价格）。
    r2 : float
        拟合优度 [0,1]，越高越线性。
    direction : 'up' | 'down'
        斜率方向。
    """

    slope: float
    intercept: float
    r2: float
    direction: str

    def price_at(self, idx: int) -> float:
        """趋势线在 K 线 idx 处的价格。"""
        return self.intercept + self.slope * idx


def fit_trendline(indices: list[int], prices: list[float]) -> TrendLine | None:
    """对 (index, price) 点集做最小二乘线性拟合。

    至少需要 2 个点。返回 TrendLine 或 None（点不足/共线退化）。
    """
    if len(indices) < 2:
        return None
    x = np.asarray(indices, dtype=float)
    y = np.asarray(prices, dtype=float)
    if np.all(x == x[0]):   # x 全相等，无法拟合
        return None
    # np.polyfit degree=1
    coeffs = np.polyfit(x, y, 1)
    slope, intercept = float(coeffs[0]), float(coeffs[1])
    pred = intercept + slope * x
    ss_res = float(np.sum((y - pred) ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 1e-12 else 1.0
    direction = "up" if slope > 0 else "down"
    return TrendLine(slope=slope, intercept=intercept, r2=float(r2), direction=direction)


def is_price_above(close: float, line_price: float, atr: float, buffer_atr: float) -> bool:
    """close 是否在趋势线上方（含 buffer）。"""
    if not np.isfinite(atr) or atr <= 0:
        return False
    return close > line_price + buffer_atr * atr


def is_price_below(close: float, line_price: float, atr: float, buffer_atr: float) -> bool:
    """close 是否在趋势线下方（含 buffer）。"""
    if not np.isfinite(atr) or atr <= 0:
        return False
    return close < line_price - buffer_atr * atr
