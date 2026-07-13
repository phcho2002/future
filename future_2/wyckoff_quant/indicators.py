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


def compute_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Compute RSI (Relative Strength Index)."""
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)

    avg_gain = gain.ewm(alpha=1 / period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(50.0)


def add_indicators(df: pd.DataFrame, atr_period: int = 14, rsi_period: int = 14) -> pd.DataFrame:
    """Add ATR, body, wick, RSI and other base indicators to OHLCV DataFrame."""
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
    out["wick_ratio"] = (out["upper_wick"] + out["lower_wick"]) / out["range"].replace(0, np.nan)
    out["upper_wick_ratio"] = out["upper_wick"] / out["range"].replace(0, np.nan)
    out["lower_wick_ratio"] = out["lower_wick"] / out["range"].replace(0, np.nan)
    out["direction"] = np.sign(out["close"] - out["open"]).astype(int)
    out["close_delta"] = out["close"].diff()
    out["rsi"] = compute_rsi(out["close"], period=rsi_period)

    # Volume moving averages
    out["volume_ma5"] = out["volume"].rolling(5, min_periods=1).mean()
    out["volume_ma10"] = out["volume"].rolling(10, min_periods=1).mean()
    out["volume_ma20"] = out["volume"].rolling(20, min_periods=1).mean()

    return out


def body_overlap_ratio(row_a: pd.Series, row_b: pd.Series) -> float:
    """Calculate body overlap ratio between two bars."""
    low_a, high_a = sorted((row_a["open"], row_a["close"]))
    low_b, high_b = sorted((row_b["open"], row_b["close"]))
    overlap = max(0.0, min(high_a, high_b) - max(low_a, low_b))
    denom = max(high_a - low_a, high_b - low_b, 1e-12)
    return float(overlap / denom)


def slope_to_degrees(slope: float) -> float:
    """Convert slope to degrees."""
    return float(np.degrees(np.arctan(slope)))


def linear_regression_slope(values: pd.Series | np.ndarray) -> float:
    """Calculate linear regression slope."""
    y = np.asarray(values, dtype=float)
    if len(y) < 2:
        return 0.0
    x = np.arange(len(y), dtype=float)
    return float(np.polyfit(x, y, 1)[0])
