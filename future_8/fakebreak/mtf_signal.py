"""多周期信号 —— 60m 定关键位 + 15m 找形态。

与单周期 generate_signal（假突破）并行的另一条信号路径：

    60m:  趋势闸(swing) + 关键价位(密集区+swing点) → 价格接近关键位？
    15m:  吞没形态(2根) 或 合并K实体(n根) → 入场确认

核心前提（与单周期一致）：
    - 做多 = 上升趋势 + 回落中 → 接近下方支撑 → 15m 出阳线形态
    - 做空 = 下降趋势 + 反弹中 → 接近上方阻力 → 15m 出阴线形态
"""
from __future__ import annotations

import pandas as pd

from fakebreak.config import FakeBreakConfig
from fakebreak.htf_zones import (
    detect_swing_zones,
    merge_zones,
    zones_near_price,
)
from fakebreak.indicators import add_indicators
from fakebreak.levels import (
    detect_volume_zones,
    nearest_above,
    nearest_below,
    nearest_resistance,
    nearest_support,
)
from fakebreak.patterns import (
    detect_engulfing,
    detect_merged_body,
    detect_ltf_spring,
    detect_ltf_upthrust,
)
from fakebreak.trend import detect_trend
from fakebreak.types import Signal, SignalSide, TradeLevels, Zone


def generate_mtf_signal(
    df_60m: pd.DataFrame,
    df_15m: pd.DataFrame,
    config: FakeBreakConfig | None = None,
) -> Signal:
    """多周期主入口：60m 定趋势+关键位，15m 找入场形态。

    Parameters
    ----------
    df_60m : 60m OHLCV DataFrame（升序，最新在末尾）
    df_15m : 15m OHLCV DataFrame（升序，最新在末尾）
    config : FakeBreakConfig（用其中 MTF + 形态 + 通用参数）

    Returns
    -------
    Signal —— 有效信号或 none(reason)
    """
    cfg = config or FakeBreakConfig()

    # ── 数据充足性 ──
    min_60m = max(cfg.swing_window, cfg.zone_window, cfg.atr_period + 5)
    if len(df_60m) < min_60m or len(df_15m) < max(cfg.merge_bars_n, 2):
        return Signal.none(f"数据不足 60m={len(df_60m)} 15m={len(df_15m)}")

    # ── 1. 60m 指标 + 趋势 ──
    df_h = add_indicators(df_60m, atr_period=cfg.atr_period, volume_ma_period=cfg.volume_ma_period)
    atr = float(df_h["atr"].iloc[-1])
    if not atr or atr <= 0 or atr != atr:
        return Signal.none("60m ATR 无效")
    vol_ma = float(df_h["vol_ma"].iloc[-1])

    trend = detect_trend(
        df_h, atr=atr,
        swing_lookback=cfg.swing_lookback, n_swings=cfg.n_swings,
        swing_window=cfg.swing_window, pullback_atr=cfg.pullback_atr,
    )

    # ── 2. 60m 关键价位（密集区 + swing点）──
    vol_zones = detect_volume_zones(
        df_h, atr=atr,
        zone_window=cfg.zone_window, zone_bin_mult=cfg.zone_bin_mult,
        zone_min_vol_ratio=cfg.zone_min_vol_ratio,
    )
    swing_zones = []
    if cfg.use_swing_zones:
        swing_zones = detect_swing_zones(
            df_h, atr=atr,
            lookback=cfg.swing_lookback, n_points=cfg.swing_zone_n,
            bin_mult=cfg.zone_bin_mult,
        )
    zones = merge_zones(vol_zones, swing_zones)
    if not zones:
        return Signal.none(f"无关键价位；趋势: {trend.reason}")

    # ── 3. 15m 当前价接近关键位 ──
    cur = float(df_15m["close"].iloc[-1])
    near = zones_near_price(zones, cur, atr, proximity_atr=cfg.proximity_atr)
    if not near:
        return Signal.none(f"价格{cur:.1f}未接近关键位(>{cfg.proximity_atr}ATR)；趋势: {trend.reason}")

    # ── 4. 按趋势方向 + 接近的 zone 判定多/空 ──
    sig_long = _try_long(df_15m, near, zones, atr, vol_ma, trend, cfg)
    if sig_long is not None:
        return sig_long

    sig_short = _try_short(df_15m, near, zones, atr, vol_ma, trend, cfg)
    if sig_short is not None:
        return sig_short

    return Signal.none(f"接近关键位但15m无形态确认；趋势: {trend.reason}")


def _try_long(
    df_15m: pd.DataFrame,
    near: list[Zone],
    zones: list[Zone],
    atr: float,
    vol_ma: float,
    trend,
    cfg: FakeBreakConfig,
) -> Signal | None:
    """做多：上升趋势+回落中，接近下方支撑，15m 出入场确认。"""
    if not trend.long_eligible:
        return None
    cur = float(df_15m["close"].iloc[-1])
    # 在接近的 zone 里取下方的（支撑）
    support_candidates = [z for z in near if z.center < cur]
    if not support_candidates:
        return None
    support = max(support_candidates, key=lambda z: z.center)  # 最近的支撑

    pat = _detect_pattern(df_15m, SignalSide.LONG, atr, support, None, cfg)
    if pat is None:
        return None

    entry = pat.entry
    # 假突破模式：止损放刺破低点（更紧）；形态模式：止损放支撑区下沿
    stop = pat.stop_ref if pat.stop_ref > 0 else support.lower
    target = _upside_target(nearest_above(zones, support), entry, atr, cfg)
    if stop >= entry or target <= entry:
        return None

    vol = float(df_15m["volume"].iloc[-1])
    volume_confirm = (vol_ma > 0) and (vol > vol_ma * cfg.recover_volume_ratio)

    return Signal(
        side=SignalSide.LONG,
        is_valid=True,
        levels=TradeLevels(entry=round(entry, 2), stop=round(stop, 2), target=round(target, 2)),
        pattern=f"MTF-{pat.name}",
        reason=(
            f"MTF做多 — 60m上升趋势+回落，接近支撑[{support.lower:.1f}-{support.upper:.1f}]"
            f"({support.source})，15m{pat.reason}，{'放量' if volume_confirm else '缩量'}确认"
        ),
        zone=support,
        volume_confirm=volume_confirm,
        failure_score=pat.failure_score,
        metadata={"pattern": pat.name, "zone_source": support.source, "trend": trend.reason},
    )


def _try_short(
    df_15m: pd.DataFrame,
    near: list[Zone],
    zones: list[Zone],
    atr: float,
    vol_ma: float,
    trend,
    cfg: FakeBreakConfig,
) -> Signal | None:
    """做空：下降趋势+反弹中，接近上方阻力，15m 出入场确认。"""
    if not trend.short_eligible:
        return None
    cur = float(df_15m["close"].iloc[-1])
    resistance_candidates = [z for z in near if z.center > cur]
    if not resistance_candidates:
        return None
    resistance = min(resistance_candidates, key=lambda z: z.center)  # 最近的阻力

    pat = _detect_pattern(df_15m, SignalSide.SHORT, atr, None, resistance, cfg)
    if pat is None:
        return None

    entry = pat.entry
    stop = pat.stop_ref if pat.stop_ref > 0 else resistance.upper
    target = _downside_target(nearest_below(zones, resistance), entry, atr, cfg)
    if stop <= entry or target >= entry:
        return None

    vol = float(df_15m["volume"].iloc[-1])
    volume_confirm = (vol_ma > 0) and (vol > vol_ma * cfg.recover_volume_ratio)

    return Signal(
        side=SignalSide.SHORT,
        is_valid=True,
        levels=TradeLevels(entry=round(entry, 2), stop=round(stop, 2), target=round(target, 2)),
        pattern=f"MTF-{pat.name}",
        reason=(
            f"MTF做空 — 60m下降趋势+反弹，接近阻力[{resistance.lower:.1f}-{resistance.upper:.1f}]"
            f"({resistance.source})，15m{pat.reason}，{'放量' if volume_confirm else '缩量'}确认"
        ),
        zone=resistance,
        volume_confirm=volume_confirm,
        failure_score=pat.failure_score,
        metadata={"pattern": pat.name, "zone_source": resistance.source, "trend": trend.reason},
    )


def _detect_pattern(
    df_15m: pd.DataFrame,
    side: SignalSide,
    atr: float,
    support: Zone | None,
    resistance: Zone | None,
    cfg: FakeBreakConfig,
):
    """按 ltf_confirm_mode 分派低周期入场确认。

    - "fakebreak"（默认，推荐）：对 60m zone 边界做刺穿→收回→失败打分。
      确认力度强，过滤噪音，交易频率与单周期假突破相当。
    - "pattern"：吞没(2根) + 合并K实体(n根)，满足任一即可。门槛低，交易多。
    """
    mode = getattr(cfg, "ltf_confirm_mode", "fakebreak")

    if mode == "fakebreak":
        if side == SignalSide.LONG and support is not None:
            return detect_ltf_spring(df_15m, support, atr, cfg)
        if side == SignalSide.SHORT and resistance is not None:
            return detect_ltf_upthrust(df_15m, resistance, atr, cfg)
        return None

    # pattern 模式：吞没优先，未命中再查合并K实体
    eng = detect_engulfing(df_15m, min_body_atr=cfg.engulf_min_body_atr, atr=atr)
    if eng is not None and eng.side == side:
        return eng
    merged = detect_merged_body(df_15m, n=cfg.merge_bars_n)
    if merged is not None and merged.side == side:
        return merged
    return None


# ── 目标位辅助（从 signal.py 复制，保持多周期模块自包含）──
def _downside_target(lower_zone: Zone | None, entry: float, atr: float, cfg: FakeBreakConfig) -> float:
    """做空目标：取 ATR 目标与下方密集区中心的较低者（更保守 = 更早止盈）。"""
    atr_target = entry - cfg.target_atr * atr
    if lower_zone is not None:
        return min(atr_target, lower_zone.center)
    return atr_target


def _upside_target(upper_zone: Zone | None, entry: float, atr: float, cfg: FakeBreakConfig) -> float:
    """做多目标：取 ATR 目标与上方密集区中心的较高者。"""
    atr_target = entry + cfg.target_atr * atr
    if upper_zone is not None:
        return max(atr_target, upper_zone.center)
    return atr_target
