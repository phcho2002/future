"""Range analyzer — detects price range contraction and repeated tests of key levels."""

import pandas as pd
import numpy as np

from stock_wyckoff.config import WyckoffConfig
from stock_wyckoff.core.types import RangeAnalysis


def analyze_range(df: pd.DataFrame, config: WyckoffConfig) -> RangeAnalysis:
    """
    Analyze price range for Wyckoff patterns:
    1. Identify support/resistance levels
    2. Count repeated tests of core price level
    3. Detect range contraction (volatility narrowing)
    """
    if len(df) < config.range_lookback:
        return RangeAnalysis(
            is_valid=False,
            reason=f"数据不足: {len(df)} < {config.range_lookback}"
        )

    recent = df.tail(config.range_lookback).reset_index(drop=True)
    n = len(recent)

    # 1. 确定核心价位 (Core Price)
    # 使用近期放量K线的实体中点作为核心价位
    volume_threshold = recent["volume"].quantile(0.7)
    high_volume_bars = recent[recent["volume"] >= volume_threshold]
    
    if len(high_volume_bars) > 0:
        # 放量K线实体中点
        core_price = ((high_volume_bars["open"] + high_volume_bars["close"]) / 2).mean()
    else:
        core_price = recent["close"].mean()

    # 2. 确定支撑和阻力位
    # 使用近期低点/高点聚类来识别关键价位
    support_level = _find_cluster_level(recent["low"].values, direction="low")
    resistance_level = _find_cluster_level(recent["high"].values, direction="high")

    # 3. 计算测试次数
    # 收盘价频繁测试核心价位但未能有效脱离
    atr = recent["atr"].iloc[-1] if "atr" in recent.columns else recent["close"].std()
    tolerance = max(atr * config.test_tolerance_atr, core_price * 0.001)
    
    test_count = 0
    for i in range(n):
        close = recent.iloc[i]["close"]
        if abs(close - core_price) <= tolerance:
            test_count += 1

    # 4. 检测波动幅度收敛
    # 将lookback分为两半，比较波动幅度
    half = n // 2
    first_half_range = recent.iloc[:half]["high"].max() - recent.iloc[:half]["low"].min()
    second_half_range = recent.iloc[half:]["high"].max() - recent.iloc[half:]["low"].min()
    
    if first_half_range > 0:
        contraction_ratio = second_half_range / first_half_range
    else:
        contraction_ratio = 1.0

    # 5. 验证条件
    is_valid = (
        test_count >= config.min_test_count
        and contraction_ratio <= config.max_contraction_ratio
        and support_level > 0
        and resistance_level > support_level
    )

    reason = ""
    if not is_valid:
        if test_count < config.min_test_count:
            reason = f"测试次数不足: {test_count} < {config.min_test_count}"
        elif contraction_ratio > config.max_contraction_ratio:
            reason = f"波动未收敛: {contraction_ratio:.2f} > {config.max_contraction_ratio}"
        elif support_level <= 0 or resistance_level <= support_level:
            reason = "支撑/阻力位识别失败"

    return RangeAnalysis(
        support_level=round(support_level, 2),
        resistance_level=round(resistance_level, 2),
        test_count=test_count,
        contraction_ratio=round(contraction_ratio, 3),
        core_price=round(core_price, 2),
        is_valid=is_valid,
        reason=reason
    )


def _find_cluster_level(values: np.ndarray, direction: str = "low") -> float:
    """
    Find clustered price level using histogram/density approach.
    For lows: find the most frequently tested support level.
    For highs: find the most frequently tested resistance level.
    """
    if len(values) < 3:
        return float(values[-1]) if len(values) > 0 else 0.0

    # 排序后寻找最密集的价位区间
    sorted_vals = np.sort(values)
    
    # 使用滑动窗口找到最密集的价位
    best_level = sorted_vals[len(sorted_vals) // 2]
    min_cluster_width = float('inf')
    
    # 取中间50%的数据寻找聚类中心
    start_idx = len(sorted_vals) // 4
    end_idx = len(sorted_vals) * 3 // 4
    
    for i in range(start_idx, end_idx):
        # 找到包含约30%数据的价位区间
        target_count = max(3, len(sorted_vals) // 3)
        if i + target_count < len(sorted_vals):
            cluster_width = sorted_vals[i + target_count] - sorted_vals[i]
            if cluster_width < min_cluster_width:
                min_cluster_width = cluster_width
                best_level = sorted_vals[i:i + target_count].mean()

    return float(best_level)


def find_recent_support_test(df: pd.DataFrame, support_level: float, lookback: int = 10) -> dict:
    """Find the most recent test of support level and return details."""
    recent = df.tail(lookback).reset_index(drop=True)
    
    for i in range(len(recent) - 1, -1, -1):
        row = recent.iloc[i]
        # 价格触及或接近支撑位
        if row["low"] <= support_level * 1.002:
            return {
                "idx": i,
                "low": row["low"],
                "close": row["close"],
                "volume": row["volume"],
                "recovered": row["close"] > support_level
            }
    
    return {}


def find_recent_resistance_test(df: pd.DataFrame, resistance_level: float, lookback: int = 10) -> dict:
    """Find the most recent test of resistance level and return details."""
    recent = df.tail(lookback).reset_index(drop=True)
    
    for i in range(len(recent) - 1, -1, -1):
        row = recent.iloc[i]
        # 价格触及或接近阻力位
        if row["high"] >= resistance_level * 0.998:
            return {
                "idx": i,
                "high": row["high"],
                "close": row["close"],
                "volume": row["volume"],
                "rejected": row["close"] < resistance_level
            }
    
    return {}
