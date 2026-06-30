"""
indicators.py
==============
技术指标计算，全部向量化、无未来函数：
- EMA
- ATR (Wilder)
- 成交量均线
- 交叉次数 / 均线密集度
- 局部极值 (底部抬高 / 高点降低)
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from typing import Optional


# ---------------------------------------------------------------------
# 基础指标
# ---------------------------------------------------------------------
def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Wilder ATR"""
    high = df["high"]
    low = df["low"]
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [(high - low).abs(), (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    # Wilder 平滑 == alpha=1/period 的 EMA
    return tr.ewm(alpha=1.0 / period, adjust=False).mean()


def vol_ma(volume: pd.Series, period: int) -> pd.Series:
    return volume.rolling(period, min_periods=max(2, period // 2)).mean()


# ---------------------------------------------------------------------
# 缠绕带判定
# ---------------------------------------------------------------------
def count_crossings(fast: pd.Series, slow: pd.Series) -> pd.Series:
    """
    滚动统计：截至当前根，前 twist_window 根内 fast/slow 的交叉次数。
    交叉 = (fast-slow) 符号变化次数。
    返回与输入等长的 Series，每个位置代表"以此根为终点的窗口内交叉次数"。
    窗口长度由调用方在 rolling 时指定。
    """
    diff = fast - slow
    sign = np.sign(diff)
    # 符号翻转(忽略 0) -> 计为一次交叉
    flip = (sign.diff().fillna(0) != 0) & (sign != 0)
    return flip.astype(int)


def twist_crossing_roll(fast: pd.Series, slow: pd.Series, window: int) -> pd.Series:
    """前 window 根内的交叉次数(含当前根)"""
    flips = count_crossings(fast, slow)
    return flips.rolling(window, min_periods=window).sum()


def twist_density(fast: pd.Series, slow: pd.Series, close: pd.Series, window: int) -> pd.Series:
    """
    均线密集度 = window 内 |fast-slow|/close 的最大值，越小越密集。
    用于过滤"伪缠绕"(均线虽交叉但价差很大)。
    """
    rel = (fast - slow).abs() / close
    return rel.rolling(window, min_periods=window).max()


# ---------------------------------------------------------------------
# 加分项：底部抬高 / 高点降低
# ---------------------------------------------------------------------
def _local_extrema(series: pd.Series, lookback: int) -> pd.Series:
    """
    标记局部极小/极大位置(向后看 lookback, 向前看 lookback)。
    返回 -1(极小) / 1(极大) / 0。向量化实现。
    """
    arr = series.to_numpy(dtype=float)
    n = len(arr)
    flags = np.zeros(n, dtype=int)
    if n < 2 * lookback + 1:
        return pd.Series(flags, index=series.index)
    c = arr[lookback : n - lookback]
    from numpy.lib.stride_tricks import sliding_window_view

    win = sliding_window_view(arr, 2 * lookback + 1)
    win_min = win.min(axis=1)
    win_max = win.max(axis=1)
    is_min = (c <= win_min) & ~np.isnan(c)
    is_max = (c >= win_max) & ~np.isnan(c)
    # 若同时既是最小又是最大(全相等窗口), 视作非极值
    both = is_min & is_max
    is_min = is_min & ~both
    is_max = is_max & ~both
    flags[lookback : n - lookback] = np.where(is_max, 1, np.where(is_min, -1, 0))
    return pd.Series(flags, index=series.index)


def _rolling_extrema_monotone(ext_flags: np.ndarray, vals: np.ndarray, window: int, want_up: bool) -> np.ndarray:
    """
    滚动判断: 在 [i-window+1 .. i] 窗口内的同向极值序列中,
    是否存在相邻的递增(want_up=True)/递减(want_up=False)。
    """
    n = len(vals)
    out = np.zeros(n, dtype=bool)
    if n < 2:
        return out
    w = min(window, n)
    for i in range(w - 1, n):
        seg_ext = ext_flags[i - w + 1 : i + 1]
        seg_vals = vals[i - w + 1 : i + 1]
        if want_up:
            idx = np.where(seg_ext == -1)[0]
        else:
            idx = np.where(seg_ext == 1)[0]
        if len(idx) >= 2:
            dv = np.diff(seg_vals[idx])
            cond = np.any(dv > 0) if want_up else np.any(dv < 0)
            if cond:
                out[i] = True
    return out


def has_higher_lows(low: pd.Series, window: int, lookback: int = 5) -> pd.Series:
    """
    在 [i-window, i] 区间内，是否存在"底部抬高"：
    存在至少两个递增的局部低点 (后低点 > 前低点)。
    """
    ext = _local_extrema(low, lookback).to_numpy()
    out = _rolling_extrema_monotone(ext, low.to_numpy(dtype=float), window, want_up=True)
    return pd.Series(out, index=low.index)


def has_lower_highs(high: pd.Series, window: int, lookback: int = 5) -> pd.Series:
    """与 has_higher_lows 镜像：高点降低"""
    ext = _local_extrema(high, lookback).to_numpy()
    out = _rolling_extrema_monotone(ext, high.to_numpy(dtype=float), window, want_up=False)
    return pd.Series(out, index=high.index)


# ---------------------------------------------------------------------
# 一次性装配
# ---------------------------------------------------------------------
def attach_indicators(df: pd.DataFrame, p: dict, precomputed: Optional[dict] = None) -> pd.DataFrame:
    """
    给 K 线 df 附加策略所需指标列。p 为 strategy 配置字典。
    precomputed: 可选, 提前算好的 {'higher_low':Series,'lower_high':Series},
                 用于网格优化时复用(这两个只依赖 twist_window/swing_lookback, 与 ema 无关)。
    """
    out = df.copy()
    f, s = p["ema_fast"], p["ema_slow"]
    out["ema_fast"] = ema(out["close"], f)
    out["ema_slow"] = ema(out["close"], s)

    tw = p["twist_window"]
    out["twist_cross"] = twist_crossing_roll(out["ema_fast"], out["ema_slow"], tw)
    out["twist_density"] = twist_density(out["ema_fast"], out["ema_slow"], out["close"], tw)

    out["vol_ma"] = vol_ma(out["volume"], p["vol_ma"])
    out["body_ratio"] = ((out["close"] - out["open"]).abs() / out["close"]).fillna(0.0)
    out["prev_volume"] = out["volume"].shift(1)

    if p.get("bonus_higher_low", True):
        out["higher_low"] = precomputed["higher_low"] if precomputed else has_higher_lows(out["low"], tw, p.get("swing_lookback", 5))
    if p.get("bonus_lower_high", True):
        out["lower_high"] = precomputed["lower_high"] if precomputed else has_lower_highs(out["high"], tw, p.get("swing_lookback", 5))
    return out


if __name__ == "__main__":
    from data_loader import load_klines, load_config

    cfg = load_config()
    df = load_klines("AU0", cfg)
    dfi = attach_indicators(df, cfg["strategy"])
    cols = ["datetime", "close", "ema_fast", "ema_slow", "twist_cross", "body_ratio"]
    print(dfi[cols].tail(8).to_string(index=False))
    print("higher_low bars:", int(dfi["higher_low"].sum()))
    print("lower_high bars:", int(dfi["lower_high"].sum()))
