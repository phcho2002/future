"""Signal generator — detects Spring (long) and Upthrust (short) signals."""

import pandas as pd
import numpy as np

from wyckoff_quant.config import WyckoffConfig
from wyckoff_quant.core.types import WyckoffSignal, WyckoffPhase, TradeLevels
from wyckoff_quant.range_analyzer import find_recent_support_test, find_recent_resistance_test


def generate_signal(
    df: pd.DataFrame,
    phase: WyckoffPhase,
    range_analysis,
    volume_analysis,
    stop_behavior,
    config: WyckoffConfig,
) -> WyckoffSignal:
    """
    Generate Wyckoff trading signals:
    - LONG (Spring): 假跌破支撑后迅速收回
    - SHORT (Upthrust): 假突破压力后迅速回落
    """
    # 基础条件检查
    if not range_analysis.is_valid:
        return WyckoffSignal(
            side="none",
            is_valid=False,
            phase=phase,
            entry_reason=f"区间条件不满足: {range_analysis.reason}"
        )

    if len(df) < 10:
        return WyckoffSignal(
            side="none",
            is_valid=False,
            phase=phase,
            entry_reason="数据不足"
        )

    recent = df.tail(10).reset_index(drop=True)
    n = len(recent)
    atr = recent["atr"].iloc[-1] if "atr" in recent.columns else recent["close"].std()
    current_rsi = recent["rsi"].iloc[-1] if "rsi" in recent.columns else 50.0

    # ── Spring 检测 (做多信号) ──
    spring_signal = _detect_spring(
        recent, range_analysis, volume_analysis, stop_behavior, atr, config
    )

    if spring_signal:
        # RSI 过滤
        if current_rsi > config.long_rsi_max:
            return WyckoffSignal(
                side="none",
                is_valid=False,
                phase=phase,
                entry_reason=f"Spring形态出现但RSI过高({current_rsi:.1f} > {config.long_rsi_max})，不满足超卖条件"
            )

        entry = spring_signal["entry"]
        stop = spring_signal["stop"]
        target = _calculate_target(recent, stop, entry, atr, config, "long")

        return WyckoffSignal(
            side="long",
            is_valid=True,
            phase=phase,
            entry_reason=spring_signal["reason"],
            levels=TradeLevels(
                entry=round(entry, 2),
                stop=round(stop, 2),
                target=round(target, 2)
            ),
            rsi_value=round(current_rsi, 1),
            metadata={
                "pattern": "Spring",
                "break_bar_idx": spring_signal["break_bar_idx"],
                "recover_bar_idx": spring_signal["recover_bar_idx"],
                "volume_confirm": spring_signal["volume_confirm"]
            }
        )

    # ── Upthrust 检测 (做空信号) ──
    upthrust_signal = _detect_upthrust(
        recent, range_analysis, volume_analysis, stop_behavior, atr, config
    )

    if upthrust_signal:
        # RSI 过滤
        if current_rsi < config.short_rsi_min:
            return WyckoffSignal(
                side="none",
                is_valid=False,
                phase=phase,
                entry_reason=f"Upthrust形态出现但RSI过低({current_rsi:.1f} < {config.short_rsi_min})，不满足超买条件"
            )

        entry = upthrust_signal["entry"]
        stop = upthrust_signal["stop"]
        target = _calculate_target(recent, stop, entry, atr, config, "short")

        return WyckoffSignal(
            side="short",
            is_valid=True,
            phase=phase,
            entry_reason=upthrust_signal["reason"],
            levels=TradeLevels(
                entry=round(entry, 2),
                stop=round(stop, 2),
                target=round(target, 2)
            ),
            rsi_value=round(current_rsi, 1),
            metadata={
                "pattern": "Upthrust",
                "break_bar_idx": upthrust_signal["break_bar_idx"],
                "recover_bar_idx": upthrust_signal["recover_bar_idx"],
                "volume_confirm": upthrust_signal["volume_confirm"]
            }
        )

    # 没有信号
    return WyckoffSignal(
        side="none",
        is_valid=False,
        phase=phase,
        entry_reason="未检测到Spring或Upthrust信号"
    )


def _detect_spring(recent, range_analysis, volume_analysis, stop_behavior, atr, config):
    """
    Detect Spring pattern:
    1. Price breaks below support (false breakdown)
    2. Recovers back above support within 1-3 bars
    3. Recovery on bullish volume
    """
    n = len(recent)
    support = range_analysis.support_level

    # 寻找假跌破
    for i in range(n - 1):
        break_bar = recent.iloc[i]
        
        # 条件1: 价格瞬间击穿支撑位（低于支撑 - 0.3*ATR）
        break_threshold = support - config.spring_break_atr * atr
        if break_bar["low"] > break_threshold:
            continue

        # 条件2: 在1-3根K线内收回支撑位上方
        for j in range(i + 1, min(i + config.spring_recover_bars + 1, n)):
            recover_bar = recent.iloc[j]
            
            # 收盘价收回支撑上方
            if recover_bar["close"] > support:
                # 条件3: 收回时阳线且成交量放大
                is_bullish = recover_bar["close"] > recover_bar["open"]
                
                volume_ma = recent["volume"].rolling(5, min_periods=1).mean()
                avg_volume = volume_ma.iloc[max(0, j - 3):j].mean() if j > 0 else recover_bar["volume"]
                volume_confirm = recover_bar["volume"] > avg_volume * config.recover_volume_ratio if avg_volume > 0 else False

                if is_bullish and volume_confirm:
                    return {
                        "entry": recover_bar["close"],
                        "stop": break_bar["low"],  # 极值点作为止损
                        "break_bar_idx": i,
                        "recover_bar_idx": j,
                        "volume_confirm": True,
                        "reason": (
                            f"Spring/弹簧效应 — 价格假跌破支撑位{support:.2f}后迅速收回，"
                            f"最低探至{break_bar['low']:.2f}，第{j-i}根K线阳线放量收回，"
                            f"确认主力吸筹意图，符合威科夫Phase C末期做多条件"
                        )
                    }
                elif is_bullish:
                    # 成交量不够但形态符合
                    return {
                        "entry": recover_bar["close"],
                        "stop": break_bar["low"],
                        "break_bar_idx": i,
                        "recover_bar_idx": j,
                        "volume_confirm": False,
                        "reason": (
                            f"Spring/弹簧效应(弱) — 价格假跌破支撑位{support:.2f}后收回，"
                            f"但收回时成交量未明显放大，信号强度一般"
                        )
                    }

    return None


def _detect_upthrust(recent, range_analysis, volume_analysis, stop_behavior, atr, config):
    """
    Detect Upthrust pattern:
    1. Price breaks above resistance (false breakout)
    2. Falls back below resistance within 1-3 bars
    3. Fall on bearish volume
    """
    n = len(recent)
    resistance = range_analysis.resistance_level

    # 寻找假突破
    for i in range(n - 1):
        break_bar = recent.iloc[i]
        
        # 条件1: 价格瞬间突破阻力位（高于阻力 + 0.3*ATR）
        break_threshold = resistance + config.upthrust_break_atr * atr
        if break_bar["high"] < break_threshold:
            continue

        # 条件2: 在1-3根K线内回落至阻力位下方
        for j in range(i + 1, min(i + config.upthrust_recover_bars + 1, n)):
            recover_bar = recent.iloc[j]
            
            # 收盘价回落至阻力下方
            if recover_bar["close"] < resistance:
                # 条件3: 回落时阴线且成交量放大
                is_bearish = recover_bar["close"] < recover_bar["open"]
                
                volume_ma = recent["volume"].rolling(5, min_periods=1).mean()
                avg_volume = volume_ma.iloc[max(0, j - 3):j].mean() if j > 0 else recover_bar["volume"]
                volume_confirm = recover_bar["volume"] > avg_volume * config.recover_volume_ratio if avg_volume > 0 else False

                if is_bearish and volume_confirm:
                    return {
                        "entry": recover_bar["close"],
                        "stop": break_bar["high"],  # 极值点作为止损
                        "break_bar_idx": i,
                        "recover_bar_idx": j,
                        "volume_confirm": True,
                        "reason": (
                            f"Upthrust/上冲回落 — 价格假突破阻力位{resistance:.2f}后迅速回落，"
                            f"最高冲至{break_bar['high']:.2f}，第{j-i}根K线阴线放量回落，"
                            f"确认主力派发意图，符合威科夫Phase C末期做空条件"
                        )
                    }
                elif is_bearish:
                    return {
                        "entry": recover_bar["close"],
                        "stop": break_bar["high"],
                        "break_bar_idx": i,
                        "recover_bar_idx": j,
                        "volume_confirm": False,
                        "reason": (
                            f"Upthrust/上冲回落(弱) — 价格假突破阻力位{resistance:.2f}后回落，"
                            f"但回落时成交量未明显放大，信号强度一般"
                        )
                    }

    return None


def _calculate_target(recent, stop, entry, atr, config, side):
    """Calculate target price based on ATR multiple and recent structure."""
    if side == "long":
        # 目标位: 上方首次放量高量柱对应的价位
        # 或基于ATR的倍数
        atr_target = entry + config.target_atr_multiple * atr
        
        # 寻找上方最近的放量高量柱
        volume_threshold = recent["volume"].quantile(0.7)
        high_volume_bars = recent[recent["volume"] >= volume_threshold]
        
        if len(high_volume_bars) > 0:
            # 使用放量K线的高点作为目标参考
            structure_target = high_volume_bars["high"].max()
            # 取ATR目标和结构目标的较大值
            return max(atr_target, structure_target)
        else:
            return atr_target
    else:
        # 做空目标位
        atr_target = entry - config.target_atr_multiple * atr
        
        volume_threshold = recent["volume"].quantile(0.7)
        high_volume_bars = recent[recent["volume"] >= volume_threshold]
        
        if len(high_volume_bars) > 0:
            structure_target = high_volume_bars["low"].min()
            return min(atr_target, structure_target)
        else:
            return atr_target
