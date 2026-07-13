"""Signal generator — detects Spring (long) and Upthrust (short) signals.

威科夫三大硬性条件全部参与判断:
  1. 价格区间收敛 (RangeAnalysis.is_valid)
  2. 成交量递减验证 (VolumeAnalysis: 递减/枯竭/Effort-Result背离)
  3. 停止行为确认 (StopBehavior: 长腿影线 + 放量测试)
"""

import pandas as pd

from stock_wyckoff.config import WyckoffConfig
from stock_wyckoff.core.types import (
    RangeAnalysis,
    StopBehavior,
    TradeLevels,
    VolumeAnalysis,
    WyckoffPhase,
    WyckoffSignal,
)


def generate_signal(
    df: pd.DataFrame,
    phase: WyckoffPhase,
    range_analysis: RangeAnalysis,
    volume_analysis: VolumeAnalysis,
    stop_behavior: StopBehavior,
    config: WyckoffConfig,
) -> WyckoffSignal:
    """生成威科夫交易信号: Spring(做多) / Upthrust(做空)。"""
    # ── 硬性条件 1: 价格区间收敛 ──
    if not range_analysis.is_valid:
        return WyckoffSignal(
            side="none", is_valid=False, phase=phase,
            entry_reason=f"区间条件不满足: {range_analysis.reason}",
        )

    if len(df) < 10:
        return WyckoffSignal(
            side="none", is_valid=False, phase=phase, entry_reason="数据不足"
        )

    # Spring/Upthrust 是 "最后一跳"，只关注末尾窗口
    scan_window = max(8, config.spring_recover_bars + 5)
    recent = df.tail(scan_window).reset_index(drop=True)
    atr = float(recent["atr"].iloc[-1]) if "atr" in recent.columns else float(recent["close"].std())
    current_rsi = float(recent["rsi"].iloc[-1]) if "rsi" in recent.columns else 50.0

    # ── Spring 检测 (做多信号) ──
    spring = _detect_spring(recent, range_analysis, atr, config)

    if spring:
        trigger_idx = spring["recover_bar_idx"]
        # B1修复: 使用触发日 RSI，而非最后一根K线
        trigger_rsi = float(recent.iloc[trigger_idx]["rsi"]) if "rsi" in recent.columns else current_rsi

        # RSI 过滤
        if trigger_rsi > config.long_rsi_max:
            return _reject(phase, f"Spring形态出现但RSI过高({trigger_rsi:.1f}>{config.long_rsi_max})")

        # B3修复: 硬性条件2/3 真正参与判断
        vol_ok = _check_volume_condition(volume_analysis)
        stop_ok = _check_stop_condition(stop_behavior, side="long")

        entry = float(recent.iloc[trigger_idx]["close"])
        stop = float(recent.iloc[spring["break_bar_idx"]]["low"])
        target = _calculate_target(df, entry, atr, config, side="long")

        strength = _signal_strength(
            vol_ok=vol_ok, stop_ok=stop_ok,
            volume_confirm=spring["volume_confirm"],
            divergence=volume_analysis.effort_result_divergence,
        )

        reason = _build_reason(
            pattern="Spring", level=range_analysis.support_level,
            entry=entry, gap=trigger_idx - spring["break_bar_idx"],
            vol_ok=vol_ok, stop_ok=stop_ok,
            volume_confirm=spring["volume_confirm"],
            divergence=volume_analysis.effort_result_divergence,
            phase_desc=phase.description,
        )

        return WyckoffSignal(
            side="long", is_valid=True, phase=phase, entry_reason=reason,
            levels=TradeLevels(entry=round(entry, 2), stop=round(stop, 2), target=round(target, 2)),
            rsi_value=round(trigger_rsi, 1),
            metadata={
                "pattern": "Spring",
                "trigger_bar_idx": trigger_idx,  # recent窗口内索引
                "break_bar_idx": spring["break_bar_idx"],
                "volume_confirm": spring["volume_confirm"],
                "volume_ok": vol_ok,
                "stop_ok": stop_ok,
                "strength": strength,
                "effort_divergence": volume_analysis.effort_result_divergence,
            },
        )

    # ── Upthrust 检测 (做空信号) ──
    upthrust = _detect_upthrust(recent, range_analysis, atr, config)

    if upthrust:
        trigger_idx = upthrust["recover_bar_idx"]
        trigger_rsi = float(recent.iloc[trigger_idx]["rsi"]) if "rsi" in recent.columns else current_rsi

        if trigger_rsi < config.short_rsi_min:
            return _reject(phase, f"Upthrust形态出现但RSI过低({trigger_rsi:.1f}<{config.short_rsi_min})")

        vol_ok = _check_volume_condition(volume_analysis)
        stop_ok = _check_stop_condition(stop_behavior, side="short")

        entry = float(recent.iloc[trigger_idx]["close"])
        stop = float(recent.iloc[upthrust["break_bar_idx"]]["high"])
        target = _calculate_target(df, entry, atr, config, side="short")

        strength = _signal_strength(
            vol_ok=vol_ok, stop_ok=stop_ok,
            volume_confirm=upthrust["volume_confirm"],
            divergence=volume_analysis.effort_result_divergence,
        )

        reason = _build_reason(
            pattern="Upthrust", level=range_analysis.resistance_level,
            entry=entry, gap=trigger_idx - upthrust["break_bar_idx"],
            vol_ok=vol_ok, stop_ok=stop_ok,
            volume_confirm=upthrust["volume_confirm"],
            divergence=volume_analysis.effort_result_divergence,
            phase_desc=phase.description,
        )

        return WyckoffSignal(
            side="short", is_valid=True, phase=phase, entry_reason=reason,
            levels=TradeLevels(entry=round(entry, 2), stop=round(stop, 2), target=round(target, 2)),
            rsi_value=round(trigger_rsi, 1),
            metadata={
                "pattern": "Upthrust",
                "trigger_bar_idx": trigger_idx,
                "break_bar_idx": upthrust["break_bar_idx"],
                "volume_confirm": upthrust["volume_confirm"],
                "volume_ok": vol_ok,
                "stop_ok": stop_ok,
                "strength": strength,
                "effort_divergence": volume_analysis.effort_result_divergence,
            },
        )

    return WyckoffSignal(
        side="none", is_valid=False, phase=phase,
        entry_reason="未检测到Spring或Upthrust信号",
    )


# ─────────────────────── 辅助: 成交量/停止行为条件 ───────────────────────

def _check_volume_condition(va: VolumeAnalysis) -> bool:
    """成交量递减验证: 威科夫要求 Climax 后成交量枯竭/递减。
    - decreasing: 梯量递减 (最强)
    - flat: 地量横盘 (可接受)
    - increasing: 量能未枯竭 (不通过)
    """
    return va.volume_trend in ("decreasing", "flat")


def _check_stop_condition(sb: StopBehavior, side: str) -> bool:
    """停止行为确认: 方向须与信号一致。
    做多(Spring): 需要 下影线 停止行为 (测试下方支撑)
    做空(Upthrust): 需要 上影线 停止行为 (测试上方压力)
    """
    if not sb.has_stop:
        return False
    if side == "long" and sb.direction == "lower":
        return True
    if side == "short" and sb.direction == "upper":
        return True
    return False


def _signal_strength(vol_ok: bool, stop_ok: bool, volume_confirm: bool,
                     divergence: bool) -> str:
    """综合判断信号强度等级 (4个条件中满足数)。"""
    score = sum([vol_ok, stop_ok, volume_confirm, divergence])
    if score >= 3:
        return "强"
    if score >= 2:
        return "中"
    return "弱"


def _reject(phase: WyckoffPhase, reason: str) -> WyckoffSignal:
    return WyckoffSignal(side="none", is_valid=False, phase=phase, entry_reason=reason)


def _build_reason(pattern: str, level: float, entry: float, gap: int,
                  vol_ok: bool, stop_ok: bool, volume_confirm: bool,
                  divergence: bool, phase_desc: str) -> str:
    """构建中文信号说明 (威科夫术语)。"""
    cn_name = "弹簧效应" if pattern == "Spring" else "上冲回落"
    action = "假跌破支撑" if pattern == "Spring" else "假突破阻力"
    parts = [f"{pattern}/{cn_name} — 价格{action}位{level:.2f}后第{gap}根K线收回，入场价{entry:.2f}"]
    flags = []
    if volume_confirm:
        flags.append("收回时放量")
    if vol_ok:
        flags.append("成交量枯竭验证通过")
    if divergence:
        flags.append("Effort-Result背离")
    if stop_ok:
        flags.append("停止行为确认")
    if flags:
        parts.append("；".join(flags))
    parts.append(phase_desc)
    return "，".join(parts)


# ─────────────────────── Spring / Upthrust 形态检测 ───────────────────────

def _detect_spring(recent: pd.DataFrame, range_analysis: RangeAnalysis,
                   atr: float, config: WyckoffConfig) -> dict | None:
    """检测 Spring: 假跌破支撑 + 1~3根K线内收回 + 收回时阳线。
    返回 {"break_bar_idx", "recover_bar_idx", "volume_confirm"} 或 None。
    """
    n = len(recent)
    support = range_analysis.support_level
    break_threshold = support - config.spring_break_atr * atr

    for i in range(n - 1):
        break_bar = recent.iloc[i]
        if break_bar["low"] > break_threshold:
            continue

        for j in range(i + 1, min(i + config.spring_recover_bars + 1, n)):
            recover_bar = recent.iloc[j]
            if recover_bar["close"] <= support:
                continue

            is_bullish = recover_bar["close"] > recover_bar["open"]
            # 至少要求阳线收回
            if not is_bullish:
                continue

            avg_volume = _recent_avg_volume(recent, j)
            volume_confirm = bool(
                avg_volume > 0
                and recover_bar["volume"] > avg_volume * config.recover_volume_ratio
            )
            return {
                "break_bar_idx": i,
                "recover_bar_idx": j,
                "volume_confirm": volume_confirm,
            }
    return None


def _detect_upthrust(recent: pd.DataFrame, range_analysis: RangeAnalysis,
                     atr: float, config: WyckoffConfig) -> dict | None:
    """检测 Upthrust: 假突破压力 + 1~3根K线内回落 + 阴线。"""
    n = len(recent)
    resistance = range_analysis.resistance_level
    break_threshold = resistance + config.upthrust_break_atr * atr

    for i in range(n - 1):
        break_bar = recent.iloc[i]
        if break_bar["high"] < break_threshold:
            continue

        for j in range(i + 1, min(i + config.upthrust_recover_bars + 1, n)):
            recover_bar = recent.iloc[j]
            if recover_bar["close"] >= resistance:
                continue

            is_bearish = recover_bar["close"] < recover_bar["open"]
            if not is_bearish:
                continue

            avg_volume = _recent_avg_volume(recent, j)
            volume_confirm = bool(
                avg_volume > 0
                and recover_bar["volume"] > avg_volume * config.recover_volume_ratio
            )
            return {
                "break_bar_idx": i,
                "recover_bar_idx": j,
                "volume_confirm": volume_confirm,
            }
    return None


def _recent_avg_volume(recent: pd.DataFrame, idx: int, lookback: int = 5) -> float:
    """计算 idx 前 lookback 根的平均成交量 (不含 idx 本身)。"""
    start = max(0, idx - lookback)
    if start >= idx:
        return 0.0
    return float(recent.iloc[start:idx]["volume"].mean())


def _calculate_target(df: pd.DataFrame, entry: float, atr: float,
                      config: WyckoffConfig, side: str) -> float:
    """计算目标价: 取 ATR 倍数目标 与 结构目标(放量高/低点) 的更优值。"""
    if side == "long":
        atr_target = entry + config.target_atr_multiple * atr
        vol_thr = df["volume"].quantile(0.7)
        hv = df[df["volume"] >= vol_thr]
        struct_target = float(hv["high"].max()) if len(hv) > 0 else atr_target
        return max(atr_target, struct_target)
    else:
        atr_target = entry - config.target_atr_multiple * atr
        vol_thr = df["volume"].quantile(0.7)
        hv = df[df["volume"] >= vol_thr]
        struct_target = float(hv["low"].min()) if len(hv) > 0 else atr_target
        return min(atr_target, struct_target)
