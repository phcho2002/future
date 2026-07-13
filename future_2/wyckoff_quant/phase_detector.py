"""Wyckoff phase detector — identifies accumulation / distribution phases (A-E)."""

import pandas as pd
import numpy as np

from wyckoff_quant.config import WyckoffConfig
from wyckoff_quant.core.types import WyckoffPhase


def detect_phase(df: pd.DataFrame, config: WyckoffConfig) -> WyckoffPhase:
    """
    Detect Wyckoff phase based on price/volume patterns.

    吸筹(Accumulation)阶段:
        Phase A: PS(初步支撑) + SC(抛售高潮) + AR(自动反弹)
        Phase B: 二次测试(ST) + 区间整理
        Phase C: Spring(弹簧) — 最后的震仓
        Phase D: SOS(强势信号) +  LPS(最后支撑点) — 确认吸筹完成
        Phase E: 突破区间，进入上升趋势

    派发(Distribution)阶段:
        Phase A: PS(初步供应) + BC(购买高潮) + AR(自动回落)
        Phase B: 二次测试(ST) + 区间整理
        Phase C: Upthrust(上冲回落) — 最后的诱多
        Phase D: SOS(弱势信号) + LPS(最后供应点) — 确认派发完成
        Phase E: 跌破区间，进入下降趋势
    """
    if len(df) < config.range_lookback:
        return WyckoffPhase(
            phase="unknown",
            stage="unknown",
            description="数据不足，无法识别阶段"
        )

    recent = df.tail(config.range_lookback).reset_index(drop=True)
    n = len(recent)

    # 1. 识别高潮/极端K线 (Climax / Selling Climax / Buying Climax)
    avg_range = recent["range"].mean()
    avg_volume = recent["volume"].mean()

    climax_bars = []
    for i in range(n):
        row = recent.iloc[i]
        is_large_range = row["range"] > avg_range * 2.0
        is_large_volume = row["volume"] > avg_volume * config.climax_volume_multiplier
        if is_large_range and is_large_volume:
            direction = "bull" if row["close"] > row["open"] else "bear"
            climax_bars.append({
                "idx": i,
                "direction": direction,
                "high": row["high"],
                "low": row["low"],
                "volume": row["volume"],
                "close": row["close"],
            })

    if not climax_bars:
        # 没有明显的高潮，可能处于整理阶段
        return _detect_range_phase(recent, config)

    # 2. 判断是吸筹还是派发
    first_climax = climax_bars[0]
    
    # 寻找自动反弹/回落 (AR)
    ar_found = False
    ar_direction = None
    ar_idx = None

    for i in range(first_climax["idx"] + 1, min(n, first_climax["idx"] + config.ar_min_bars + 5)):
        if i >= n:
            break
        row = recent.iloc[i]
        if first_climax["direction"] == "bear":
            # 抛售高潮后应有自动反弹 (AR)
            if row["close"] > row["open"] and row["close"] > first_climax["close"]:
                ar_found = True
                ar_direction = "up"
                ar_idx = i
                break
        else:
            # 购买高潮后应有自动回落 (AR)
            if row["close"] < row["open"] and row["close"] < first_climax["close"]:
                ar_found = True
                ar_direction = "down"
                ar_idx = i
                break

    # 3. 判断吸筹 vs 派发
    if first_climax["direction"] == "bear":
        stage = "accumulation"
        # 寻找二次测试 (ST)
        st_found = _find_secondary_test(recent, first_climax, ar_idx, "low")
        
        # 寻找 Spring
        spring_found = _find_spring(recent, first_climax["low"], config)
        
        # 寻找 SOS (Sign of Strength)
        sos_found = _find_sos(recent, first_climax, ar_idx, config)

        if sos_found:
            return WyckoffPhase(
                phase="Phase_D",
                stage="accumulation",
                description="Phase D 吸筹确认期 — SOS强势信号出现，准备进入上升趋势"
            )
        elif spring_found:
            return WyckoffPhase(
                phase="Phase_C",
                stage="accumulation",
                description="Phase C 吸筹弹簧期 — Spring震仓测试，最后的吸筹机会"
            )
        elif st_found:
            return WyckoffPhase(
                phase="Phase_B",
                stage="accumulation",
                description="Phase B 吸筹建设期 — 二次测试完成，主力区间吸筹中"
            )
        elif ar_found:
            return WyckoffPhase(
                phase="Phase_A",
                stage="accumulation",
                description="Phase A 吸筹初期 — SC抛售高潮+AR自动反弹已出现"
            )
        else:
            return WyckoffPhase(
                phase="Phase_A",
                stage="accumulation",
                description="Phase A 吸筹初期 — 抛售高潮出现，等待自动反弹确认"
            )
    else:
        stage = "distribution"
        # 寻找二次测试 (ST)
        st_found = _find_secondary_test(recent, first_climax, ar_idx, "high")
        
        # 寻找 Upthrust
        upthrust_found = _find_upthrust(recent, first_climax["high"], config)
        
        # 寻找 SOS (Sign of Weakness)
        sow_found = _find_sow(recent, first_climax, ar_idx, config)

        if sow_found:
            return WyckoffPhase(
                phase="Phase_D",
                stage="distribution",
                description="Phase D 派发确认期 — SOW弱势信号出现，准备进入下降趋势"
            )
        elif upthrust_found:
            return WyckoffPhase(
                phase="Phase_C",
                stage="distribution",
                description="Phase C 派发诱多期 — Upthrust上冲回落，最后的派发机会"
            )
        elif st_found:
            return WyckoffPhase(
                phase="Phase_B",
                stage="distribution",
                description="Phase B 派发建设期 — 二次测试完成，主力区间派发中"
            )
        elif ar_found:
            return WyckoffPhase(
                phase="Phase_A",
                stage="distribution",
                description="Phase A 派发初期 — BC购买高潮+AR自动回落已出现"
            )
        else:
            return WyckoffPhase(
                phase="Phase_A",
                stage="distribution",
                description="Phase A 派发初期 — 购买高潮出现，等待自动回落确认"
            )


def _detect_range_phase(df: pd.DataFrame, config: WyckoffConfig) -> WyckoffPhase:
    """当没有明显高潮时，基于价格区间特征判断阶段。"""
    recent = df.tail(config.range_lookback)
    
    # 检查是否处于明显的区间整理
    highs = recent["high"].values
    lows = recent["low"].values
    price_range = highs.max() - lows.min()
    avg_price = recent["close"].mean()
    
    if price_range / avg_price < 0.03:  # 波动小于3%
        return WyckoffPhase(
            phase="Phase_B",
            stage="unknown",
            description="Phase B 区间整理期 — 波动收窄，等待方向选择"
        )
    
    # 检查趋势方向
    first_half = recent.iloc[:len(recent)//2]["close"].mean()
    second_half = recent.iloc[len(recent)//2:]["close"].mean()
    
    if second_half > first_half * 1.02:
        return WyckoffPhase(
            phase="Phase_B",
            stage="accumulation",
            description="Phase B 吸筹建设期 — 价格重心上移，可能处于吸筹阶段"
        )
    elif second_half < first_half * 0.98:
        return WyckoffPhase(
            phase="Phase_B",
            stage="distribution",
            description="Phase B 派发建设期 — 价格重心下移，可能处于派发阶段"
        )
    else:
        return WyckoffPhase(
            phase="unknown",
            stage="unknown",
            description="阶段不明 — 需要更多价格行为确认"
        )


def _find_secondary_test(df: pd.DataFrame, climax: dict, ar_idx: int | None, test_level: str) -> bool:
    """寻找二次测试 (Secondary Test)。"""
    if ar_idx is None:
        return False
    
    n = len(df)
    recent = df.iloc[ar_idx:]
    
    if test_level == "low":
        # 吸筹: 二次测试应接近SC低点，但成交量减小
        target = climax["low"]
        for i in range(len(recent)):
            row = recent.iloc[i]
            if row["low"] <= target * 1.005 and row["volume"] < climax["volume"] * 0.8:
                return True
    else:
        # 派发: 二次测试应接近BC高点，但成交量减小
        target = climax["high"]
        for i in range(len(recent)):
            row = recent.iloc[i]
            if row["high"] >= target * 0.995 and row["volume"] < climax["volume"] * 0.8:
                return True
    
    return False


def _find_spring(df: pd.DataFrame, sc_low: float, config: WyckoffConfig) -> bool:
    """寻找 Spring (弹簧效应) — 价格瞬间跌破支撑后迅速收回。"""
    recent = df.tail(10)
    n = len(recent)
    
    for i in range(n - 1):
        row = recent.iloc[i]
        # 价格瞬间跌破SC低点
        if row["low"] < sc_low * 0.995:
            # 检查是否在1-3根K线内收回
            for j in range(i + 1, min(i + config.spring_recover_bars + 1, n)):
                recover = recent.iloc[j]
                if recover["close"] > sc_low:
                    return True
    return False


def _find_upthrust(df: pd.DataFrame, bc_high: float, config: WyckoffConfig) -> bool:
    """寻找 Upthrust (上冲回落) — 价格瞬间突破压力后迅速回落。"""
    recent = df.tail(10)
    n = len(recent)
    
    for i in range(n - 1):
        row = recent.iloc[i]
        # 价格瞬间突破BC高点
        if row["high"] > bc_high * 1.005:
            # 检查是否在1-3根K线内回落
            for j in range(i + 1, min(i + config.upthrust_recover_bars + 1, n)):
                recover = recent.iloc[j]
                if recover["close"] < bc_high:
                    return True
    return False


def _find_sos(df: pd.DataFrame, climax: dict, ar_idx: int | None, config: WyckoffConfig) -> bool:
    """寻找 SOS (Sign of Strength) — 强势信号。"""
    if ar_idx is None:
        return False
    
    recent = df.iloc[ar_idx:]
    if len(recent) < 3:
        return False
    
    # SOS: 价格突破AR高点，伴随放量
    ar_high = df.iloc[ar_idx]["high"] if ar_idx < len(df) else climax["high"]
    
    for i in range(2, len(recent)):
        row = recent.iloc[i]
        prev = recent.iloc[i-1]
        if row["close"] > ar_high and row["volume"] > prev["volume"] * 1.2:
            return True
    return False


def _find_sow(df: pd.DataFrame, climax: dict, ar_idx: int | None, config: WyckoffConfig) -> bool:
    """寻找 SOW (Sign of Weakness) — 弱势信号。"""
    if ar_idx is None:
        return False
    
    recent = df.iloc[ar_idx:]
    if len(recent) < 3:
        return False
    
    # SOW: 价格跌破AR低点，伴随放量
    ar_low = df.iloc[ar_idx]["low"] if ar_idx < len(df) else climax["low"]
    
    for i in range(2, len(recent)):
        row = recent.iloc[i]
        prev = recent.iloc[i-1]
        if row["close"] < ar_low and row["volume"] > prev["volume"] * 1.2:
            return True
    return False
