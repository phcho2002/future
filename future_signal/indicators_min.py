"""Minimal indicators for second-breakout engine (from future_bb/indicators.py)."""

from __future__ import annotations

import pandas as pd


def EMA(data: pd.Series, period: int) -> pd.Series:
    return data.ewm(span=period, adjust=False).mean()


def ATR(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(window=period).mean()


def detect_box(
    high: pd.Series,
    low: pd.Series,
    period: int = 30,
    range_max: float = 0.02,
    touch_min: int = 2,
    touch_tol: float = 0.005,
) -> pd.Series:
    hh = high.rolling(period).max()
    ll = low.rolling(period).min()
    rng_pct = (hh - ll) / ll
    up_touch = ((hh - high).abs() / hh < touch_tol).rolling(period).sum()
    dn_touch = ((low - ll).abs() / ll < touch_tol).rolling(period).sum()
    return (rng_pct <= range_max) & (up_touch >= touch_min) & (dn_touch >= touch_min)


def detect_wedge(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 30,
    atr_shrink: float = 0.8,
    range_shrink: float = 0.8,
) -> pd.Series:
    atr = ATR(high, low, close, 14)
    atr_now = atr.rolling(period).mean()
    atr_pre = atr.shift(period).rolling(period).mean()
    half = period // 2
    hh2 = high.rolling(half).max().shift(half)
    ll2 = low.rolling(half).min().shift(half)
    rng_pre = hh2 - ll2
    hh1 = high.rolling(half).max()
    ll1 = low.rolling(half).min()
    rng_now = hh1 - ll1
    shrink_ok = (atr_now < atr_pre * atr_shrink) & (rng_now < rng_pre * range_shrink)
    return shrink_ok.fillna(False)
