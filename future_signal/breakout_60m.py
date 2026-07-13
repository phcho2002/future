"""60-minute Donchian breakout with volume confirmation (state machine)."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .config import SignalConfig


def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat(
        [(high - low).abs(), (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1.0 / period, adjust=False).mean()


def _normalize_oi(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for cand in ("open_interest", "oi", "hold", "close_oi", "position"):
        if cand in out.columns:
            out["open_interest"] = pd.to_numeric(out[cand], errors="coerce")
            break
    return out


@dataclass
class BreakoutSnapshot:
    symbol: str
    direction: int              # +1 / -1 / 0
    strength: float             # 0~1
    price: float
    atr: float
    daily_vol: float
    bar_time: str
    reason: str
    vol_ratio: float = 1.0
    oi_delta: float | None = None
    entry_level: float | None = None
    exit_level: float | None = None
    bars_in_trade: int = 0


def estimate_daily_vol_from_60m(close: pd.Series, bars_per_day: float = 8.0) -> float:
    r = np.log(close.astype(float)).diff().dropna()
    if len(r) < 30:
        return 0.012
    lam = 0.94
    var = float(r.iloc[0] ** 2)
    for x in r.iloc[1:]:
        var = lam * var + (1.0 - lam) * float(x) ** 2
    sigma_bar = var ** 0.5
    return float(max(0.004, min(0.08, sigma_bar * (bars_per_day ** 0.5))))


def _strength(
    break_dist: float,
    atr_i: float,
    vol_ratio: float,
    oi_boost: float,
    is_fresh_entry: bool,
    cfg: SignalConfig,
) -> float:
    atr_term = 0.0
    if atr_i > 0:
        atr_term = min(1.0, break_dist / max(atr_i, 1e-9) / 1.5)
    vol_term = min(1.0, max(0.0, (vol_ratio - 1.0) / 1.5))
    base = 0.45 * atr_term + 0.40 * vol_term + 0.15
    if not is_fresh_entry:
        base *= 0.80
    base += oi_boost
    return float(min(cfg.strength_ceil, max(cfg.strength_floor, base)))


def evaluate_breakout(
    symbol: str,
    df: pd.DataFrame,
    cfg: SignalConfig | None = None,
) -> BreakoutSnapshot:
    """Donchian 状态机（向量友好的逐步回放，取最后一根状态）。

    规则（海龟式）：
      - 空仓时：收盘突破前 N 根高点 + 放量 → 多；跌破前 N 根低点 + 放量 → 空
      - 持多时：收盘跌破前 M 根低点 → 平仓；若同时触发空头突破可反手
      - 持空时：收盘升破前 M 根高点 → 平仓；若同时触发多头突破可反手

    突破幅度需 ≥ min_break_atr × ATR；放量 ≥ vol_factor × MA(volume)。
    """
    cfg = cfg or SignalConfig()
    empty = BreakoutSnapshot(
        symbol=symbol, direction=0, strength=0.0, price=0.0, atr=0.0,
        daily_vol=0.012, bar_time="", reason="no_data",
    )
    need = cfg.donchian_entry + cfg.atr_period + 5
    if df is None or len(df) < need:
        return empty

    df = _normalize_oi(df)
    df = df.copy()
    for c in ("open", "high", "low", "close", "volume"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["open", "high", "low", "close"]).reset_index(drop=True)
    if len(df) < need:
        return empty

    atr = _atr(df, cfg.atr_period)
    vol_ma = df["volume"].rolling(cfg.vol_ma, min_periods=5).mean()
    prev_high = df["high"].shift(1).rolling(cfg.donchian_entry).max()
    prev_low = df["low"].shift(1).rolling(cfg.donchian_entry).min()
    exit_high = df["high"].shift(1).rolling(cfg.donchian_exit).max()
    exit_low = df["low"].shift(1).rolling(cfg.donchian_exit).min()

    pos = 0
    entry_i = -1
    last_reason = "flat"
    last_fresh = False
    last_break_dist = 0.0

    start = cfg.donchian_entry + 1
    for i in range(start, len(df)):
        close = float(df.at[i, "close"])
        atr_i = float(atr.iloc[i]) if np.isfinite(atr.iloc[i]) else 0.0
        vol_m = float(vol_ma.iloc[i]) if np.isfinite(vol_ma.iloc[i]) and vol_ma.iloc[i] > 0 else 0.0
        vol_ratio = float(df.at[i, "volume"]) / vol_m if vol_m > 0 else 1.0
        vol_ok = vol_ratio >= cfg.vol_factor

        hi = float(prev_high.iloc[i]) if np.isfinite(prev_high.iloc[i]) else np.nan
        lo = float(prev_low.iloc[i]) if np.isfinite(prev_low.iloc[i]) else np.nan
        ex_hi = float(exit_high.iloc[i]) if np.isfinite(exit_high.iloc[i]) else np.nan
        ex_lo = float(exit_low.iloc[i]) if np.isfinite(exit_low.iloc[i]) else np.nan

        long_lvl = hi * (1.0 + cfg.breakout_buffer) if np.isfinite(hi) else np.nan
        short_lvl = lo * (1.0 - cfg.breakout_buffer) if np.isfinite(lo) else np.nan

        long_break = (
            np.isfinite(long_lvl)
            and atr_i > 0
            and close > long_lvl
            and (close - long_lvl) >= cfg.min_break_atr * atr_i
            and vol_ok
        )
        short_break = (
            np.isfinite(short_lvl)
            and atr_i > 0
            and close < short_lvl
            and (short_lvl - close) >= cfg.min_break_atr * atr_i
            and vol_ok
        )

        if pos == 0:
            if long_break and not short_break:
                pos = 1
                entry_i = i
                last_reason = f"long_entry vol={vol_ratio:.2f}x"
                last_fresh = True
                last_break_dist = close - long_lvl
            elif short_break and not long_break:
                pos = -1
                entry_i = i
                last_reason = f"short_entry vol={vol_ratio:.2f}x"
                last_fresh = True
                last_break_dist = short_lvl - close
            else:
                last_reason = "flat"
                last_fresh = False
                last_break_dist = 0.0
        elif pos == 1:
            exit_long = np.isfinite(ex_lo) and close < ex_lo
            if short_break:
                pos = -1
                entry_i = i
                last_reason = f"reverse_short vol={vol_ratio:.2f}x"
                last_fresh = True
                last_break_dist = short_lvl - close if np.isfinite(short_lvl) else 0.0
            elif exit_long:
                pos = 0
                entry_i = -1
                last_reason = "long_exit"
                last_fresh = False
                last_break_dist = 0.0
            else:
                last_reason = "long_hold"
                last_fresh = False
                last_break_dist = max(0.0, close - (long_lvl if np.isfinite(long_lvl) else close))
        else:  # pos == -1
            exit_short = np.isfinite(ex_hi) and close > ex_hi
            if long_break:
                pos = 1
                entry_i = i
                last_reason = f"reverse_long vol={vol_ratio:.2f}x"
                last_fresh = True
                last_break_dist = close - long_lvl if np.isfinite(long_lvl) else 0.0
            elif exit_short:
                pos = 0
                entry_i = -1
                last_reason = "short_exit"
                last_fresh = False
                last_break_dist = 0.0
            else:
                last_reason = "short_hold"
                last_fresh = False
                last_break_dist = max(0.0, (short_lvl if np.isfinite(short_lvl) else close) - close)

    # 最后一根快照
    i = len(df) - 1
    close = float(df.at[i, "close"])
    atr_i = float(atr.iloc[i]) if np.isfinite(atr.iloc[i]) else 0.0
    vol_m = float(vol_ma.iloc[i]) if np.isfinite(vol_ma.iloc[i]) and vol_ma.iloc[i] > 0 else 0.0
    vol_ratio = float(df.at[i, "volume"]) / vol_m if vol_m > 0 else 1.0
    hi = float(prev_high.iloc[i]) if np.isfinite(prev_high.iloc[i]) else np.nan
    lo = float(prev_low.iloc[i]) if np.isfinite(prev_low.iloc[i]) else np.nan
    ex_hi = float(exit_high.iloc[i]) if np.isfinite(exit_high.iloc[i]) else np.nan
    ex_lo = float(exit_low.iloc[i]) if np.isfinite(exit_low.iloc[i]) else np.nan

    oi_delta = None
    oi_boost = 0.0
    if "open_interest" in df.columns and i >= 1:
        a, b = df["open_interest"].iloc[i], df["open_interest"].iloc[i - 1]
        if pd.notna(a) and pd.notna(b):
            oi_delta = float(a - b)
            if cfg.prefer_oi_increase and oi_delta > 0:
                oi_boost = 0.10

    bar_time = ""
    if "datetime" in df.columns:
        bar_time = str(pd.Timestamp(df.at[i, "datetime"]))

    daily_vol = estimate_daily_vol_from_60m(df["close"])
    bars_in = (i - entry_i) if (pos != 0 and entry_i >= 0) else 0

    if pos == 0:
        strength = 0.0
    else:
        strength = _strength(
            last_break_dist, atr_i, vol_ratio, oi_boost, last_fresh, cfg
        )

    return BreakoutSnapshot(
        symbol=symbol,
        direction=int(pos),
        strength=strength,
        price=close,
        atr=atr_i,
        daily_vol=daily_vol,
        bar_time=bar_time,
        reason=last_reason,
        vol_ratio=vol_ratio,
        oi_delta=oi_delta,
        entry_level=(hi if pos == 1 else lo) if pos != 0 else None,
        exit_level=(ex_lo if pos == 1 else ex_hi) if pos != 0 else None,
        bars_in_trade=bars_in,
    )
