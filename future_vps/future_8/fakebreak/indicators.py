"""技术指标计算（纯 pandas/numpy，不依赖 talib）。

全部函数就地给 df 增加列，返回新的 DataFrame（不修改原 df）：
    - ATR(period)  : Average True Range，Wilder 平滑
    - VOL_MA(period): 成交量简单移动平均

这些指标是趋势闸 / 密集区 / 假突破检测的共同输入。
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def add_indicators(
    df: pd.DataFrame,
    atr_period: int = 14,
    volume_ma_period: int = 20,
) -> pd.DataFrame:
    """给 df 增加 atr / vol_ma 列。

    Parameters
    ----------
    df : DataFrame[open, high, low, close, volume]

    Returns
    -------
    DataFrame （复制，新增 atr / vol_ma 列；行数不足处为 NaN）
    """
    out = df.copy()
    if len(out) == 0:
        out["atr"] = np.nan
        out["vol_ma"] = np.nan
        return out

    out["atr"] = _atr(out, atr_period)
    out["vol_ma"] = out["volume"].rolling(volume_ma_period, min_periods=1).mean()
    return out


def _atr(df: pd.DataFrame, period: int) -> pd.Series:
    """Average True Range，Wilder 平滑法。

    TR = max(high-low, |high-prev_close|, |low-prev_close|)；
    首个 TR 用 high-low，之后用 Wilder 指数平滑。
    """
    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    close = df["close"].to_numpy(dtype=float)
    n = len(df)

    tr = np.empty(n, dtype=float)
    tr[0] = high[0] - low[0]
    for i in range(1, n):
        tr[i] = max(
            high[i] - low[i],
            abs(high[i] - close[i - 1]),
            abs(low[i] - close[i - 1]),
        )

    atr = np.full(n, np.nan)
    if n < period:
        return pd.Series(atr, index=df.index)

    # Wilder 平滑：首个 ATR = 前 period 个 TR 的简单平均
    atr[period - 1] = tr[:period].mean()
    alpha = 1.0 / period
    for i in range(period, n):
        atr[i] = atr[i - 1] * (1 - alpha) + tr[i] * alpha
    return pd.Series(atr, index=df.index)
