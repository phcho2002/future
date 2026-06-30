"""
renko.py
========
Renko Chart 生成与基于砖块序列的指标。

核心约定：
- 每块砖大小 = brick_size（由 ATR 定制）
- 延续：收盘价比上一砖收盘同向移动 >= 1 块砖
- 反转：收盘价比上一砖收盘反向移动 >= 2 块砖
- 每块砖记录：open / close / direction / 源 bar datetime / 源 bar index / volume
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def build_renko(
    df: pd.DataFrame,
    brick_size: float,
    reversal_mult: float = 2.0,
    max_bricks_per_bar: int = 20,
) -> pd.DataFrame:
    """从时间序列 K 线生成 Renko 砖块序列。

    Parameters
    ----------
    df : DataFrame[datetime, open, high, low, close, volume]
    brick_size : 每块砖的价格梯度
    reversal_mult : 反转所需砖数，默认 2
    max_bricks_per_bar : 单根 K 线最多生成砖数上限

    Returns
    -------
    DataFrame 列:
        brick_idx, datetime, source_idx, open, close, high, low,
        direction, volume, body, top, bottom
    """
    if len(df) < 2 or brick_size <= 0:
        return pd.DataFrame()

    closes = df["close"].to_numpy(dtype=float)
    datetimes = df["datetime"].to_numpy()
    volumes = df["volume"].to_numpy(dtype=float) if "volume" in df.columns else np.zeros(len(df))

    last_close = float(closes[0])
    direction = 0  # 0=未定向, 1=up, -1=down
    bricks = []

    reversal_threshold = reversal_mult * brick_size

    for i in range(1, len(closes)):
        c = float(closes[i])
        dt = datetimes[i]
        vol = float(volumes[i])

        if direction == 0:
            # 首块砖：任一方向移动 1 砖即定型
            up_dist = c - last_close
            down_dist = last_close - c
            if up_dist >= brick_size:
                n = min(int(up_dist // brick_size), max_bricks_per_bar)
                for k in range(n):
                    o = last_close + k * brick_size
                    brick_close = o + brick_size
                    bricks.append({
                        "source_idx": i,
                        "datetime": dt,
                        "open": o,
                        "close": brick_close,
                        "high": brick_close,
                        "low": o,
                        "direction": 1,
                        "volume": vol / n if n > 0 else vol,
                    })
                last_close += n * brick_size
                direction = 1
                continue
            if down_dist >= brick_size:
                n = min(int(down_dist // brick_size), max_bricks_per_bar)
                for k in range(n):
                    o = last_close - k * brick_size
                    brick_close = o - brick_size
                    bricks.append({
                        "source_idx": i,
                        "datetime": dt,
                        "open": o,
                        "close": brick_close,
                        "high": o,
                        "low": brick_close,
                        "direction": -1,
                        "volume": vol / n if n > 0 else vol,
                    })
                last_close -= n * brick_size
                direction = -1
                continue

        elif direction == 1:
            # 先检查上涨延续
            up_dist = c - last_close
            if up_dist >= brick_size:
                n = min(int(up_dist // brick_size), max_bricks_per_bar)
                for k in range(n):
                    o = last_close + k * brick_size
                    brick_close = o + brick_size
                    bricks.append({
                        "source_idx": i,
                        "datetime": dt,
                        "open": o,
                        "close": brick_close,
                        "high": brick_close,
                        "low": o,
                        "direction": 1,
                        "volume": vol / n if n > 0 else vol,
                    })
                last_close += n * brick_size
                continue

            # 再检查下跌反转（需 2 砖）
            down_dist = last_close - c
            if down_dist >= reversal_threshold:
                effective_dist = down_dist - brick_size
                n = min(max(1, int(effective_dist // brick_size) + 1), max_bricks_per_bar)
                for k in range(n):
                    o = last_close - k * brick_size
                    brick_close = o - brick_size
                    bricks.append({
                        "source_idx": i,
                        "datetime": dt,
                        "open": o,
                        "close": brick_close,
                        "high": o,
                        "low": brick_close,
                        "direction": -1,
                        "volume": vol / n if n > 0 else vol,
                    })
                last_close -= n * brick_size
                direction = -1
                continue

        elif direction == -1:
            # 先检查下跌延续
            down_dist = last_close - c
            if down_dist >= brick_size:
                n = min(int(down_dist // brick_size), max_bricks_per_bar)
                for k in range(n):
                    o = last_close - k * brick_size
                    brick_close = o - brick_size
                    bricks.append({
                        "source_idx": i,
                        "datetime": dt,
                        "open": o,
                        "close": brick_close,
                        "high": o,
                        "low": brick_close,
                        "direction": -1,
                        "volume": vol / n if n > 0 else vol,
                    })
                last_close -= n * brick_size
                continue

            # 再检查上涨反转（需 2 砖）
            up_dist = c - last_close
            if up_dist >= reversal_threshold:
                effective_dist = up_dist - brick_size
                n = min(max(1, int(effective_dist // brick_size) + 1), max_bricks_per_bar)
                for k in range(n):
                    o = last_close + k * brick_size
                    brick_close = o + brick_size
                    bricks.append({
                        "source_idx": i,
                        "datetime": dt,
                        "open": o,
                        "close": brick_close,
                        "high": brick_close,
                        "low": o,
                        "direction": 1,
                        "volume": vol / n if n > 0 else vol,
                    })
                last_close += n * brick_size
                direction = 1
                continue

    if not bricks:
        return pd.DataFrame()

    renko = pd.DataFrame(bricks)
    renko.insert(0, "brick_idx", range(len(renko)))
    renko["body"] = renko["close"] - renko["open"]
    renko["top"] = renko[["open", "close"]].max(axis=1)
    renko["bottom"] = renko[["open", "close"]].min(axis=1)
    return renko


def rsi_on_bricks(renko: pd.DataFrame, period: int = 14) -> pd.Series:
    """对 Renko close 序列计算标准 RSI。

    Parameters
    ----------
    renko : Renko DataFrame
    period : RSI 周期（砖块数）

    Returns
    -------
    pd.Series, 与 renko 等长
    """
    if len(renko) < period + 1:
        return pd.Series(np.nan, index=renko.index)

    closes = renko["close"].to_numpy(dtype=float)
    delta = np.diff(closes)
    gains = np.where(delta > 0, delta, 0.0)
    losses = np.where(delta < 0, -delta, 0.0)

    # 第一个 delta 对应 renko 的第 2 行，因此索引对齐到 renko.index[1:]
    idx = renko.index[1:]
    avg_gain = pd.Series(gains, index=idx).ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    avg_loss = pd.Series(losses, index=idx).ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    rsi = rsi.reindex(renko.index)
    return rsi


def brick_trend_state(renko: pd.DataFrame, lookback: int = 10) -> dict:
    """返回最近若干砖的趋势状态摘要。

    Returns
    -------
    dict: {
        last_close, last_direction, consecutive_same_dir,
        recent_up_count, recent_down_count, swing_high, swing_low
    }
    """
    tail = renko.tail(lookback)
    directions = tail["direction"].to_numpy()
    last_dir = int(directions[-1]) if len(directions) else 0

    consecutive = 0
    for d in reversed(directions):
        if d == last_dir:
            consecutive += 1
        else:
            break

    return {
        "last_close": float(renko["close"].iloc[-1]),
        "last_direction": last_dir,
        "consecutive_same_dir": consecutive,
        "recent_up_count": int((directions == 1).sum()),
        "recent_down_count": int((directions == -1).sum()),
        "swing_high": float(renko["top"].tail(lookback).max()),
        "swing_low": float(renko["bottom"].tail(lookback).min()),
    }
