"""市场环境闸：TREND / RANGE / NEUTRAL。

参数由系统给定（可调），默认面向国内期货 60m：
  - RANGE：ADX 低 + 波幅收敛（适合假突破反转/边沿回归）
  - TREND：ADX 抬升或通道展开（适合二次突破主信号）
  - NEUTRAL：中间带，不做新开仓（已有持仓可继续由止损管理）
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import numpy as np
import pandas as pd


class Regime(str, Enum):
    TREND = "TREND"
    RANGE = "RANGE"
    NEUTRAL = "NEUTRAL"


@dataclass
class RegimeConfig:
    """环境闸参数（作者选定默认值）。"""

    adx_period: int = 14
    ema_period: int = 50
    # ADX
    adx_range_max: float = 22.0       # ≤ 此值偏向震荡
    adx_trend_min: float = 26.0       # ≥ 此值偏向趋势
    # 波幅：近 lookback 根 (H-L)/mid
    width_lookback: int = 40
    width_range_max: float = 0.032    # ≤ 3.2% 箱体偏窄 → 震荡
    width_trend_min: float = 0.055    # ≥ 5.5% 通道展开 → 趋势
    # ATR 压缩：近 ATR / 慢 ATR
    atr_fast: int = 14
    atr_slow: int = 40
    atr_compress_max: float = 0.88    # fast/slow ≤ 此 → 压缩
    atr_expand_min: float = 1.15      # fast/slow ≥ 此 → 扩张
    # 最少 K 线
    min_bars: int = 80


@dataclass
class RegimeSnapshot:
    regime: Regime
    adx: float
    width: float
    atr_ratio: float
    ema: float
    price: float
    reason: str
    # 子开关
    allow_trend_entry: bool
    allow_range_entry: bool


def _true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev = close.shift(1)
    return pd.concat(
        [(high - low).abs(), (high - prev).abs(), (low - prev).abs()],
        axis=1,
    ).max(axis=1)


def _atr(high, low, close, period: int) -> pd.Series:
    tr = _true_range(high, low, close)
    return tr.ewm(alpha=1.0 / period, adjust=False).mean()


def _adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    up = high.diff()
    down = -low.diff()
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    tr = _true_range(high, low, close)
    atr = tr.ewm(alpha=1.0 / period, adjust=False).mean()
    plus_di = 100 * pd.Series(plus_dm, index=high.index).ewm(
        alpha=1.0 / period, adjust=False
    ).mean() / atr.replace(0, np.nan)
    minus_di = 100 * pd.Series(minus_dm, index=high.index).ewm(
        alpha=1.0 / period, adjust=False
    ).mean() / atr.replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1.0 / period, adjust=False).mean()


def classify_regime(
    df: pd.DataFrame,
    cfg: RegimeConfig | None = None,
) -> RegimeSnapshot:
    """用 60m（或任意）OHLCV 判定当前环境。"""
    cfg = cfg or RegimeConfig()
    empty = RegimeSnapshot(
        Regime.NEUTRAL, 0.0, 0.0, 1.0, 0.0, 0.0,
        "no_data", False, False,
    )
    if df is None or len(df) < cfg.min_bars:
        return empty

    work = df.copy()
    for c in ("open", "high", "low", "close"):
        work[c] = pd.to_numeric(work[c], errors="coerce")
    work = work.dropna(subset=["open", "high", "low", "close"]).reset_index(drop=True)
    if len(work) < cfg.min_bars:
        return empty

    high, low, close = work["high"], work["low"], work["close"]
    adx_s = _adx(high, low, close, cfg.adx_period)
    atr_f = _atr(high, low, close, cfg.atr_fast)
    atr_s = _atr(high, low, close, cfg.atr_slow)
    ema = close.ewm(span=cfg.ema_period, adjust=False).mean()

    i = len(work) - 1
    adx = float(adx_s.iloc[i]) if np.isfinite(adx_s.iloc[i]) else 0.0
    price = float(close.iloc[i])
    ema_i = float(ema.iloc[i]) if np.isfinite(ema.iloc[i]) else price
    atr_fast_i = float(atr_f.iloc[i]) if np.isfinite(atr_f.iloc[i]) else 0.0
    atr_slow_i = float(atr_s.iloc[i]) if np.isfinite(atr_s.iloc[i]) else 1.0
    atr_ratio = atr_fast_i / atr_slow_i if atr_slow_i > 0 else 1.0

    window = work.tail(cfg.width_lookback)
    hh, ll = float(window["high"].max()), float(window["low"].min())
    mid = 0.5 * (hh + ll) if (hh + ll) > 0 else price
    width = (hh - ll) / mid if mid > 0 else 0.0

    # 打分：trend_score vs range_score
    trend_pts = 0
    range_pts = 0
    bits: list[str] = []

    if adx >= cfg.adx_trend_min:
        trend_pts += 2
        bits.append(f"ADX={adx:.1f}≥{cfg.adx_trend_min}")
    elif adx <= cfg.adx_range_max:
        range_pts += 2
        bits.append(f"ADX={adx:.1f}≤{cfg.adx_range_max}")
    else:
        bits.append(f"ADX={adx:.1f} mid")

    if width >= cfg.width_trend_min:
        trend_pts += 1
        bits.append(f"width={width:.2%} wide")
    elif width <= cfg.width_range_max:
        range_pts += 1
        bits.append(f"width={width:.2%} tight")

    if atr_ratio >= cfg.atr_expand_min:
        trend_pts += 1
        bits.append(f"ATR↑{atr_ratio:.2f}")
    elif atr_ratio <= cfg.atr_compress_max:
        range_pts += 1
        bits.append(f"ATR↓{atr_ratio:.2f}")

    if trend_pts >= 3 and trend_pts > range_pts:
        regime = Regime.TREND
    elif range_pts >= 3 and range_pts > trend_pts:
        regime = Regime.RANGE
    elif trend_pts >= 2 and range_pts <= 1:
        regime = Regime.TREND
    elif range_pts >= 2 and trend_pts <= 1:
        regime = Regime.RANGE
    else:
        regime = Regime.NEUTRAL

    allow_trend = regime == Regime.TREND
    allow_range = regime == Regime.RANGE
    # NEUTRAL：允许已有趋势持仓维持，但不新开震荡；趋势新开也关
    reason = f"{regime.value} ({' | '.join(bits)}; t={trend_pts} r={range_pts})"

    return RegimeSnapshot(
        regime=regime,
        adx=adx,
        width=width,
        atr_ratio=atr_ratio,
        ema=ema_i,
        price=price,
        reason=reason,
        allow_trend_entry=allow_trend,
        allow_range_entry=allow_range,
    )
