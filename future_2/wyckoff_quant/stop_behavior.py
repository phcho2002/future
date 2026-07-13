"""Stop behavior detector — identifies stopping action (long wick + volume climax)."""

import pandas as pd
import numpy as np

from wyckoff_quant.config import WyckoffConfig
from wyckoff_quant.core.types import StopBehavior


def detect_stop_behavior(df: pd.DataFrame, config: WyckoffConfig) -> StopBehavior:
    """
    Detect stopping action bars:
    - Long upper or lower wick (shadow)
    - Volume significantly above average (1.5x+)
    - Price does not continue in the direction of the wick afterward
    
    This indicates smart money testing market depth (Test).
    """
    if len(df) < config.stop_lookback + 5:
        return StopBehavior(
            has_stop=False,
            description="数据不足，无法检测停止行为"
        )

    recent = df.tail(config.stop_lookback).reset_index(drop=True)
    n = len(recent)

    # 计算前5日平均成交量
    volume_ma5 = recent["volume"].rolling(5, min_periods=1).mean()

    best_stop = None
    best_score = 0.0

    for i in range(n):
        row = recent.iloc[i]
        
        # 跳过无成交量的K线
        if row["volume"] <= 0 or row["range"] <= 0:
            continue

        # 计算前5日均量（排除当前K线）
        start_idx = max(0, i - 5)
        if start_idx < i:
            prev_volumes = recent.iloc[start_idx:i]["volume"]
            avg_prev_volume = prev_volumes.mean() if len(prev_volumes) > 0 else row["volume"]
        else:
            avg_prev_volume = row["volume"]

        # 成交量放大检查
        if avg_prev_volume > 0:
            volume_ratio = row["volume"] / avg_prev_volume
        else:
            volume_ratio = 1.0

        if volume_ratio < config.stop_volume_multiplier:
            continue

        # 影线检查
        upper_wick_ratio = row["upper_wick"] / row["range"] if row["range"] > 0 else 0
        lower_wick_ratio = row["lower_wick"] / row["range"] if row["range"] > 0 else 0

        # 上影线停止行为（测试上方压力）
        if upper_wick_ratio >= config.wick_ratio_threshold:
            # 检查随后价格是否未继续上涨（确认是测试而非突破）
            if i + 1 < n:
                next_close = recent.iloc[i + 1]["close"]
                if next_close < row["high"]:
                    score = upper_wick_ratio * volume_ratio
                    if score > best_score:
                        best_score = score
                        best_stop = {
                            "idx": i,
                            "wick_ratio": upper_wick_ratio,
                            "volume_ratio": volume_ratio,
                            "direction": "upper",
                            "description": f"上影线停止行为 — 长腿测试上方压力，成交量放大{volume_ratio:.1f}倍"
                        }

        # 下影线停止行为（测试下方支撑）
        if lower_wick_ratio >= config.wick_ratio_threshold:
            # 检查随后价格是否未继续下跌（确认是测试而非突破）
            if i + 1 < n:
                next_close = recent.iloc[i + 1]["close"]
                if next_close > row["low"]:
                    score = lower_wick_ratio * volume_ratio
                    if score > best_score:
                        best_score = score
                        best_stop = {
                            "idx": i,
                            "wick_ratio": lower_wick_ratio,
                            "volume_ratio": volume_ratio,
                            "direction": "lower",
                            "description": f"下影线停止行为 — 长腿测试下方支撑，成交量放大{volume_ratio:.1f}倍"
                        }

    if best_stop is None:
        return StopBehavior(
            has_stop=False,
            description="未检测到明显的停止行为"
        )

    return StopBehavior(
        has_stop=True,
        stop_bar_idx=best_stop["idx"],
        wick_ratio=round(best_stop["wick_ratio"], 3),
        volume_ratio=round(best_stop["volume_ratio"], 2),
        direction=best_stop["direction"],
        description=best_stop["description"]
    )


def is_test_bar(row: pd.Series, avg_volume: float, config: WyckoffConfig) -> dict:
    """
    Check if a single bar is a test bar (stop action).
    Returns details if it is, empty dict otherwise.
    """
    if row["volume"] <= 0 or row["range"] <= 0:
        return {}

    volume_ratio = row["volume"] / avg_volume if avg_volume > 0 else 1.0
    
    if volume_ratio < config.stop_volume_multiplier:
        return {}

    upper_wick_ratio = row["upper_wick"] / row["range"] if row["range"] > 0 else 0
    lower_wick_ratio = row["lower_wick"] / row["range"] if row["range"] > 0 else 0

    result = {}

    if upper_wick_ratio >= config.wick_ratio_threshold:
        result = {
            "direction": "upper",
            "wick_ratio": upper_wick_ratio,
            "volume_ratio": volume_ratio,
            "body_direction": "bull" if row["close"] > row["open"] else "bear"
        }

    if lower_wick_ratio >= config.wick_ratio_threshold:
        if lower_wick_ratio > upper_wick_ratio or not result:
            result = {
                "direction": "lower",
                "wick_ratio": lower_wick_ratio,
                "volume_ratio": volume_ratio,
                "body_direction": "bull" if row["close"] > row["open"] else "bear"
            }

    return result
