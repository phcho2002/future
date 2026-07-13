from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class AnalysisRow:
    symbol: str
    date: str
    cross_count: int
    golden_close: float
    volume_value: float
    volume_ratio_prev: float | None
    volume_ratio_ma20: float | None
    note: str


@dataclass(frozen=True)
class Signal:
    symbol: str
    signal_type: str
    date: str
    trigger_price: float
    confidence: str
    reason: str


def add_indicators(df: pd.DataFrame, fast: int = 8, slow: int = 21) -> pd.DataFrame:
    out = df.copy()
    out["sma_fast"] = out["close"].rolling(fast, min_periods=fast).mean()
    out["sma_slow"] = out["close"].rolling(slow, min_periods=slow).mean()
    out["vol_ma20"] = out["volume"].rolling(20, min_periods=20).mean()
    out["diff"] = out["sma_fast"] - out["sma_slow"]
    out["cross"] = ""

    prev = out["diff"].shift(1)
    golden = (prev <= 0) & (out["diff"] > 0)
    death = (prev >= 0) & (out["diff"] < 0)
    out.loc[golden, "cross"] = "golden"
    out.loc[death, "cross"] = "death"
    return out


def is_double_volume(df: pd.DataFrame, idx: int) -> bool:
    if idx < 0 or idx >= len(df):
        return False
    volume = df.at[idx, "volume"]
    prev_volume = df.at[idx - 1, "volume"] if idx > 0 else None
    vol_ma20 = df.at[idx, "vol_ma20"]

    by_prev = prev_volume is not None and volume >= prev_volume * 2.0
    by_ma = pd.notna(vol_ma20) and volume >= vol_ma20 * 1.5
    return bool(by_prev or by_ma)


def volume_ratios(df: pd.DataFrame, idx: int) -> tuple[float | None, float | None]:
    if idx < 0 or idx >= len(df):
        return None, None
    prev = df.at[idx - 1, "volume"] if idx > 0 else None
    vol_ma20 = df.at[idx, "vol_ma20"]
    ratio_prev = float(df.at[idx, "volume"] / prev) if prev and prev > 0 else None
    ratio_ma20 = float(df.at[idx, "volume"] / vol_ma20) if pd.notna(vol_ma20) and vol_ma20 > 0 else None
    return ratio_prev, ratio_ma20


def _alternating(crosses: pd.Series) -> bool:
    values = crosses.tolist()
    return all(values[i] != values[i - 1] for i in range(1, len(values)))


def _has_winding_center(df: pd.DataFrame, idx: int, lookback: int = 60, band: float = 0.05) -> tuple[bool, int]:
    start = max(0, idx - lookback + 1)
    window = df.iloc[start : idx + 1]
    crosses = window[window["cross"].isin(["golden", "death"])]["cross"]
    if len(crosses) < 2 or not _alternating(crosses):
        return False, int(len(crosses))
    if crosses.iloc[-1] != "golden":
        return False, int(len(crosses))

    first_cross_pos = crosses.index[0]
    winding = df.loc[first_cross_pos:idx]
    lower = winding["sma_slow"] * (1 - band)
    upper = winding["sma_slow"] * (1 + band)
    in_band = winding["close"].between(lower, upper, inclusive="both")
    return bool(in_band.all()), int(len(crosses))


def _is_breakout(df: pd.DataFrame, idx: int) -> bool:
    if idx < 5:
        return False
    row = df.loc[idx]
    prior_high = df.loc[idx - 5 : idx - 1, "high"].max()
    return bool(row["close"] > row["sma_slow"] and row["close"] > prior_high)


def _volume_confirmation(df: pd.DataFrame, idx: int) -> tuple[int | None, str | None]:
    if is_double_volume(df, idx):
        return idx, "突破当日倍量"
    if is_double_volume(df, idx - 1):
        return idx - 1, "突破前一日倍量"
    next_idx = idx + 1
    if next_idx < len(df) and is_double_volume(df, next_idx):
        bullish = df.at[next_idx, "close"] > df.at[next_idx, "open"]
        continued = df.at[next_idx, "close"] > df.at[idx, "close"]
        if bullish and continued:
            return next_idx, "补量确认"
    return None, None


def analyze_daily_data(symbol: str, daily: pd.DataFrame) -> tuple[list[AnalysisRow], list[Signal]]:
    df = add_indicators(daily)
    analyses: list[AnalysisRow] = []
    signals: list[Signal] = []

    for idx, row in df[df["cross"] == "golden"].iterrows():
        has_winding, cross_count = _has_winding_center(df, int(idx))
        if not has_winding or not _is_breakout(df, int(idx)):
            continue

        volume_idx, volume_note = _volume_confirmation(df, int(idx))
        if volume_idx is None or volume_note is None:
            continue

        ratio_prev, ratio_ma20 = volume_ratios(df, volume_idx)
        analyses.append(
            AnalysisRow(
                symbol=symbol,
                date=row["date"].strftime("%Y-%m-%d"),
                cross_count=cross_count,
                golden_close=float(row["close"]),
                volume_value=float(df.at[volume_idx, "volume"]),
                volume_ratio_prev=ratio_prev,
                volume_ratio_ma20=ratio_ma20,
                note=volume_note,
            )
        )

        signal_date_idx = volume_idx if volume_note == "补量确认" else int(idx)
        confidence = "高" if volume_note == "突破当日倍量" else "中"
        reason = (
            f"前60日均线缠绕且交叉{cross_count}次；最近交叉为金叉，"
            f"收盘价{row['close']:.2f}站上SMA_Slow并突破金叉前5日最高价；{volume_note}"
        )
        signals.append(
            Signal(
                symbol=symbol,
                signal_type="买入",
                date=df.at[signal_date_idx, "date"].strftime("%Y-%m-%d"),
                trigger_price=float(df.at[signal_date_idx, "close"]),
                confidence=confidence,
                reason=reason,
            )
        )

    return analyses, signals
