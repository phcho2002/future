"""Volatility estimation helpers."""

from __future__ import annotations

import numpy as np
import pandas as pd


def daily_returns_from_close(close: pd.Series) -> pd.Series:
    return close.astype(float).pct_change().dropna()


def ewma_vol(returns: pd.Series, lam: float = 0.94) -> float:
    """最新一步 EWMA 日波动（标准差）。"""
    r = returns.dropna().astype(float)
    if len(r) < 5:
        return float("nan")
    var = float(r.iloc[0] ** 2)
    for x in r.iloc[1:]:
        var = lam * var + (1.0 - lam) * float(x) ** 2
    return var ** 0.5


def realized_vol(returns: pd.Series, lookback: int = 20) -> float:
    r = returns.dropna().astype(float).tail(lookback)
    if len(r) < 5:
        return float("nan")
    return float(r.std(ddof=1))


def estimate_daily_vol(
    close: pd.Series,
    lookback: int = 20,
    lam: float = 0.94,
    method: str = "ewma",
) -> float:
    rets = daily_returns_from_close(close)
    if method == "realized":
        return realized_vol(rets, lookback=lookback)
    # 默认 EWMA，但只在 lookback 窗口上估计更稳
    return ewma_vol(rets.tail(max(lookback, 60)), lam=lam)


def clip_vol(vol: float, floor: float, cap: float) -> float:
    if not np.isfinite(vol) or vol <= 0:
        return floor
    return float(min(max(vol, floor), cap))
