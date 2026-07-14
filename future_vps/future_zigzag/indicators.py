"""指标层：True Range 与 Wilder's RMA ATR。

关键修正：future_1/indicators.py:45 用 ``tr.rolling(n).mean()``（SMA）算 ATR，
与 TradingView / MT4 / 原始 Wilder 定义全部不一致，导致 atr_multiple 阈值
只能对这套 SMA 校准、和任何外部 ATR 参考都对不上。

这里改用 Wilder's RMA（等价于 alpha=1/period 的 EMA），与所有标准平台一致。
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def true_range(df: pd.DataFrame) -> pd.Series:
    """标准 True Range = max(H-L, |H-前收|, |L-前收|)。

    Parameters
    ----------
    df : 含 high / low / close 列的 DataFrame。
    """
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    prev_close = df["close"].shift(1).astype(float)

    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    tr.name = "tr"
    return tr


def wilder_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Wilder's ATR（RMA 平滑）。

    RMA 等价于 ``alpha = 1/period``、``adjust=False`` 的 EMA：

        ATR[t] = (ATR[t-1] * (period-1) + TR[t]) / period

    首根有效 TR 作为种子（等价于 Wilder 原始的前 N 根简单平均作为起始值）。
    与 TradingView `ta.atr(14)` / MT4 `iATR` 数值一致。

    返回的 Series 长度与 df 相同；前 ``period-1`` 根因种子递推会偏小但仍有效，
    调用方若需严格可避开前 ``period`` 根。
    """
    tr = true_range(df)
    # ewm + adjust=False + alpha=1/period == Wilder RMA
    atr = tr.ewm(alpha=1.0 / period, adjust=False).mean()
    atr.name = "atr"
    return atr


def add_atr(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """返回带 ``tr`` / ``atr`` 列的副本。"""
    out = df.copy()
    out["tr"] = true_range(out)
    out["atr"] = wilder_atr(out, period=period)
    return out


def atr_multiple(price_move: float, atr_value: float) -> float:
    """把一个价格位移换算成 ATR 倍数；ATR<=0 时返回 nan。"""
    if not np.isfinite(atr_value) or atr_value <= 0:
        return float("nan")
    return float(price_move) / float(atr_value)
