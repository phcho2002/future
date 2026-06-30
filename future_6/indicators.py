"""
indicators.py
=============
基础技术指标，全部向量化、无未来函数。
"""
from __future__ import annotations

import pandas as pd


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Wilder ATR。

    Parameters
    ----------
    df : DataFrame 需包含 high, low, close
    period : ATR 周期

    Returns
    -------
    pd.Series
    """
    high = df["high"]
    low = df["low"]
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [(high - low).abs(), (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1.0 / period, adjust=False).mean()


def round_brick_size(raw_size: float, price: float, min_size: float = 0.0001) -> float:
    """把原始砖块大小圆整到合适的价格精度。

    规则：
      - 价格 >= 10000：圆整到 1 的整数倍
      - 价格 >= 1000：圆整到 0.1
      - 价格 >= 100：圆整到 0.01
      - 价格 >= 10：圆整到 0.001
      - 其它：圆整到 0.0001
    同时保证 >= min_size。
    """
    if price >= 10000:
        tick = 1.0
    elif price >= 1000:
        tick = 0.1
    elif price >= 100:
        tick = 0.01
    elif price >= 10:
        tick = 0.001
    else:
        tick = 0.0001

    rounded = max(round(raw_size / tick) * tick, min_size)
    # 保留合理小数位，避免浮点脏尾数
    decimals = max(0, int(-10_000_000_000_000_000_000 // tick))  # fallback
    if tick == 1.0:
        decimals = 0
    elif tick == 0.1:
        decimals = 1
    elif tick == 0.01:
        decimals = 2
    elif tick == 0.001:
        decimals = 3
    else:
        decimals = 4
    return round(rounded, decimals)
