import numpy as np
import pandas as pd


REQUIRED_COLUMNS = ("open", "high", "low", "close")


def normalize_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize common Chinese/AkShare OHLCV names to internal names."""
    mapping = {
        "日期": "datetime",
        "时间": "datetime",
        "开盘": "open",
        "最高": "high",
        "最低": "low",
        "收盘": "close",
        "成交量": "volume",
        "成交额": "amount",
    }
    out = df.rename(columns={k: v for k, v in mapping.items() if k in df.columns}).copy()
    for col in REQUIRED_COLUMNS:
        if col not in out.columns:
            raise ValueError(f"missing required OHLC column: {col}")
        out[col] = pd.to_numeric(out[col], errors="coerce")
    if "volume" not in out.columns:
        out["volume"] = 0.0
    out["volume"] = pd.to_numeric(out["volume"], errors="coerce").fillna(0.0)
    if "datetime" in out.columns:
        out["datetime"] = pd.to_datetime(out["datetime"], errors="coerce")
    return out.dropna(subset=list(REQUIRED_COLUMNS)).reset_index(drop=True)


def add_indicators(df: pd.DataFrame, atr_period: int = 14) -> pd.DataFrame:
    out = normalize_ohlcv(df)
    prev_close = out["close"].shift(1)
    tr = pd.concat(
        [
            out["high"] - out["low"],
            (out["high"] - prev_close).abs(),
            (out["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    out["tr"] = tr
    out["atr"] = tr.rolling(atr_period, min_periods=1).mean()
    out["body"] = (out["close"] - out["open"]).abs()
    out["range"] = (out["high"] - out["low"]).replace(0, np.nan)
    out["upper_wick"] = out["high"] - out[["open", "close"]].max(axis=1)
    out["lower_wick"] = out[["open", "close"]].min(axis=1) - out["low"]
    out["direction"] = np.sign(out["close"] - out["open"]).astype(int)
    out["close_delta"] = out["close"].diff()
    return out


def body_overlap_ratio(row_a: pd.Series, row_b: pd.Series) -> float:
    low_a, high_a = sorted((row_a["open"], row_a["close"]))
    low_b, high_b = sorted((row_b["open"], row_b["close"]))
    overlap = max(0.0, min(high_a, high_b) - max(low_a, low_b))
    denom = max(high_a - low_a, high_b - low_b, 1e-12)
    return float(overlap / denom)


def bar_overlap_ratio(row_a: pd.Series, row_b: pd.Series) -> float:
    overlap = max(0.0, min(row_a["high"], row_b["high"]) - max(row_a["low"], row_b["low"]))
    denom = max(row_a["high"] - row_a["low"], row_b["high"] - row_b["low"], 1e-12)
    return float(overlap / denom)


def rolling_adjacent_overlap(df: pd.DataFrame, window: int, threshold: float) -> float:
    if len(df) < 2:
        return 0.0
    recent = df.tail(window)
    hits = 0
    total = 0
    for i in range(1, len(recent)):
        total += 1
        if body_overlap_ratio(recent.iloc[i - 1], recent.iloc[i]) > threshold:
            hits += 1
    return hits / total if total else 0.0


def slope_to_degrees(slope: float) -> float:
    return float(np.degrees(np.arctan(slope)))


def linear_regression_slope(values: pd.Series | np.ndarray) -> float:
    y = np.asarray(values, dtype=float)
    if len(y) < 2:
        return 0.0
    x = np.arange(len(y), dtype=float)
    return float(np.polyfit(x, y, 1)[0])


def normalized_slope(values: pd.Series | np.ndarray, atr: float) -> float:
    """Price-slope normalized by ATR so the resulting angle is comparable across
    instruments at very different price levels (e.g. gold ~600 vs rebar ~3000).

    The raw ``linear_regression_slope`` is in price-units-per-bar; dividing by
    ATR converts it into ATR-units-per-bar, an instrument-agnostic momentum
    measure. ``slope_to_degrees`` can then be applied meaningfully.
    """
    denom = float(atr) if atr and atr > 0 else 1e-12
    return linear_regression_slope(values) / denom
