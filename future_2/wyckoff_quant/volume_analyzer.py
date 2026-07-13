"""Volume analyzer — validates volume decline after climax (Effort vs Result divergence)."""

import pandas as pd
import numpy as np

from wyckoff_quant.config import WyckoffConfig
from wyckoff_quant.core.types import VolumeAnalysis


def analyze_volume(df: pd.DataFrame, config: WyckoffConfig) -> VolumeAnalysis:
    """
    Analyze volume patterns for Wyckoff Method:
    1. Detect volume decline after climax (exhaustion)
    2. Identify Effort vs Result divergence
    """
    if len(df) < config.volume_lookback + 5:
        return VolumeAnalysis(
            volume_trend="unknown",
            effort_result_divergence=False,
            volume_decline_ratio=0.0
        )

    recent = df.tail(config.volume_lookback + 10).reset_index(drop=True)
    n = len(recent)

    # 1. 识别高潮成交量 (Climax Volume)
    volume_ma = recent["volume"].rolling(5, min_periods=1).mean()
    climax_mask = recent["volume"] > volume_ma * config.climax_volume_multiplier
    climax_volumes = recent.loc[climax_mask, "volume"]
    
    climax_volume = climax_volumes.max() if len(climax_volumes) > 0 else recent["volume"].max()
    
    # 2. 检测成交量趋势
    first_half = recent.iloc[:n//2]["volume"].mean()
    second_half = recent.iloc[n//2:]["volume"].mean()
    
    if first_half > 0:
        volume_decline_ratio = second_half / first_half
    else:
        volume_decline_ratio = 1.0

    if volume_decline_ratio < config.volume_decline_threshold:
        volume_trend = "decreasing"
    elif volume_decline_ratio > 1.2:
        volume_trend = "increasing"
    else:
        volume_trend = "flat"

    # 3. Effort vs Result 背离检测
    # 原理：价格到达相同位置，所需成交量越来越小
    effort_result_divergence = _detect_effort_result_divergence(
        recent, config.effort_result_lookback
    )

    # 4. 近期平均成交量
    avg_recent_volume = recent.iloc[-config.volume_lookback:]["volume"].mean()

    return VolumeAnalysis(
        volume_trend=volume_trend,
        climax_volume=round(climax_volume, 2),
        avg_recent_volume=round(avg_recent_volume, 2),
        effort_result_divergence=effort_result_divergence,
        volume_decline_ratio=round(volume_decline_ratio, 3)
    )


def _detect_effort_result_divergence(df: pd.DataFrame, lookback: int) -> bool:
    """
    Detect Effort vs Result divergence:
    - Price reaches similar levels but with decreasing volume
    - This indicates weakening opposition (selling or buying pressure)
    """
    if len(df) < lookback * 2:
        return False

    recent = df.tail(lookback * 2).reset_index(drop=True)
    n = len(recent)
    half = n // 2

    # 比较前后两半：价格范围相似但成交量递减
    first_half = recent.iloc[:half]
    second_half = recent.iloc[half:]

    # 价格范围比较
    first_range = first_half["high"].max() - first_half["low"].min()
    second_range = second_half["high"].max() - second_half["low"].min()
    
    # 成交量比较
    first_volume = first_half["volume"].mean()
    second_volume = second_half["volume"].mean()

    # 背离条件：价格范围相似（±30%）但成交量显著减少（< 70%）
    if first_range > 0 and first_volume > 0:
        range_similar = abs(second_range - first_range) / first_range < 0.3
        volume_decreased = second_volume / first_volume < 0.7
        return range_similar and volume_decreased
    
    return False


def check_volume_at_level(df: pd.DataFrame, level: float, tolerance: float = 0.01) -> dict:
    """
    Check volume behavior when price approaches a specific level.
    Returns volume statistics for bars near the level.
    """
    mask = (
        (df["low"] <= level * (1 + tolerance)) & 
        (df["high"] >= level * (1 - tolerance))
    )
    
    near_level = df[mask]
    
    if len(near_level) < 2:
        return {
            "count": len(near_level),
            "avg_volume": near_level["volume"].mean() if len(near_level) > 0 else 0,
            "volume_trend": "unknown"
        }
    
    # 检查这些K线的成交量是否递减
    volumes = near_level["volume"].values
    if len(volumes) >= 3:
        first_avg = np.mean(volumes[:len(volumes)//2])
        second_avg = np.mean(volumes[len(volumes)//2:])
        
        if first_avg > 0:
            decline_ratio = second_avg / first_avg
            if decline_ratio < 0.7:
                volume_trend = "decreasing"
            elif decline_ratio > 1.3:
                volume_trend = "increasing"
            else:
                volume_trend = "flat"
        else:
            volume_trend = "unknown"
    else:
        volume_trend = "unknown"
    
    return {
        "count": len(near_level),
        "avg_volume": near_level["volume"].mean(),
        "volume_trend": volume_trend,
        "decline_ratio": second_avg / first_avg if len(volumes) >= 3 and first_avg > 0 else 1.0
    }
