"""技术指标计算（移植自 future_bb/indicators.py）。

包含 ATR、EMA、SMA、detect_box（箱体蓄势）、detect_wedge（收敛楔形蓄势）。
策略核心依赖这些指标来判定蓄势形态和突破。
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def SMA(data: pd.Series, period: int) -> pd.Series:
    """简单移动平均。"""
    return data.rolling(window=period).mean()


def EMA(data: pd.Series, period: int) -> pd.Series:
    """指数移动平均（adjust=False，与 future_bb 一致）。"""
    return data.ewm(span=period, adjust=False).mean()


def ATR(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """平均真实波幅。

    TR = max(当日高低差, |当日高 - 昨收|, |当日低 - 昨收|)
    ATR = TR 的 SMA。
    """
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(window=period).mean()


def detect_box(
    high: pd.Series,
    low: pd.Series,
    period: int = 20,
    range_max: float = 0.12,
    touch_min: int = 2,
    touch_tol: float = 0.01,
) -> pd.Series:
    """箱体蓄势形态检测：波幅小 + 上下边界各自被触碰 >= touch_min 次。

    条件（全部满足）:
      1. 近 period 根波幅 (最高-最低)/最低 <= range_max
      2. 上边界（区间最高）被触碰 >= touch_min 次
      3. 下边界（区间最低）被触碰 >= touch_min 次

    Returns:
        布尔序列（True 表示该根 K 线处于合格箱体中）
    """
    hh = high.rolling(period).max()
    ll = low.rolling(period).min()
    rng_pct = (hh - ll) / ll

    # 上边界触碰：high 接近区间最高（在容差内）
    up_touch = ((hh - high).abs() / hh < touch_tol).rolling(period).sum()
    # 下边界触碰：low 接近区间最低
    dn_touch = ((low - ll).abs() / ll < touch_tol).rolling(period).sum()

    is_box = (rng_pct <= range_max) & (up_touch >= touch_min) & (dn_touch >= touch_min)
    return is_box


def detect_wedge(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 20,
    atr_shrink: float = 0.8,
    range_shrink: float = 0.8,
) -> pd.Series:
    """收敛楔形检测：波动率收敛 + 波幅收窄。

    判定:
      1. 近 period 根 ATR 均值 < 前 period 根 ATR 均值 × atr_shrink（波动收敛）
      2. 近 period 根后半段波幅 < 前半段波幅 × range_shrink（波幅收窄）

    Returns:
        布尔序列
    """
    atr = ATR(high, low, close, 14)
    atr_now = atr.rolling(period).mean()
    atr_pre = atr.shift(period).rolling(period).mean()

    half = period // 2
    hh2 = high.rolling(half).max().shift(half)   # 前半段最高
    ll2 = low.rolling(half).min().shift(half)    # 前半段最低
    rng_pre = hh2 - ll2
    hh1 = high.rolling(half).max()               # 后半段最高
    ll1 = low.rolling(half).min()                # 后半段最低
    rng_now = hh1 - ll1

    shrink_ok = (atr_now < atr_pre * atr_shrink) & (rng_now < rng_pre * range_shrink)
    return shrink_ok.fillna(False)


def is_limit_up(close: float, prev_close: float, pct: float = 0.10) -> bool:
    """是否涨停（收盘涨幅 >= pct）。"""
    if prev_close <= 0:
        return False
    return close >= prev_close * (1 + pct - 1e-6)
