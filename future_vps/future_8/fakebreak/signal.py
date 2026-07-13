"""假突破反转信号检测 —— 系统核心。

移植 future_2/wyckoff_quant/signal_generator.py 的 Spring/Upthrust 逻辑，
关键位从 range_analysis.support_level 换成成交量密集区（levels.Zone）。

信号管线：
    indicators → trend(趋势闸) → levels(密集区) → Spring/Upthrust 检测

做空（Upthrust）：左侧明显下跌 + 反弹 + 向上假突破阻力（刺穿后收回 + 阴线放量）
做多（Spring）：  左侧明显上涨 + 回落 + 向下假跌破支撑（刺破后收回 + 阳线放量）
"""
from __future__ import annotations

import pandas as pd

from fakebreak.config import FakeBreakConfig
from fakebreak.indicators import add_indicators
from fakebreak.levels import (
    detect_volume_zones,
    nearest_resistance,
    nearest_support,
    nearest_below,
    nearest_above,
)
from fakebreak.trend import detect_trend
from fakebreak.types import Signal, SignalSide, TradeLevels, Zone


def generate_signal(df: pd.DataFrame, config: FakeBreakConfig | None = None) -> Signal:
    """主入口：对一段 K 线判定假突破反转信号。

    ``df`` 是升序的 OHLCV DataFrame（最新一根在末尾）。返回有效信号或 none。
    """
    cfg = config or FakeBreakConfig()

    if len(df) < max(cfg.swing_window, cfg.zone_window, cfg.signal_lookback, cfg.atr_period + 5):
        return Signal.none("数据不足")

    # 1. 指标
    df_i = add_indicators(
        df,
        atr_period=cfg.atr_period,
        volume_ma_period=cfg.volume_ma_period,
    )
    atr = float(df_i["atr"].iloc[-1])
    if not atr or atr <= 0 or atr != atr:
        return Signal.none("ATR 无效")
    vol_ma = float(df_i["vol_ma"].iloc[-1])

    # 2. 趋势闸（swing 结构）
    trend = detect_trend(
        df_i,
        atr=atr,
        swing_lookback=cfg.swing_lookback,
        n_swings=cfg.n_swings,
        swing_window=cfg.swing_window,
        pullback_atr=cfg.pullback_atr,
    )

    # 3. 密集区
    zones = detect_volume_zones(
        df_i,
        atr=atr,
        zone_window=cfg.zone_window,
        zone_bin_mult=cfg.zone_bin_mult,
        zone_min_vol_ratio=cfg.zone_min_vol_ratio,
    )
    if not zones:
        return Signal.none(f"无明显密集区；趋势: {trend.reason}")

    cur = float(df_i["close"].iloc[-1])

    # ── 空头侧：Upthrust ──
    if trend.short_eligible:
        resistance = nearest_resistance(zones, cur)
        if resistance is not None:
            sig = _detect_upthrust(df_i, resistance, zones, atr, vol_ma, cfg)
            if sig is not None:
                return sig
        # 阻力位不存在时无法判定假突破，继续

    # ── 多头侧：Spring ──
    if trend.long_eligible:
        support = nearest_support(zones, cur)
        if support is not None:
            sig = _detect_spring(df_i, support, zones, atr, vol_ma, cfg)
            if sig is not None:
                return sig

    return Signal.none(f"无假突破触发；趋势: {trend.reason}")


def _detect_upthrust(
    df_i: pd.DataFrame,
    resistance: Zone,
    zones: list[Zone],
    atr: float,
    vol_ma: float,
    cfg: FakeBreakConfig,
) -> Signal | None:
    """Upthrust（做空）：假突破阻力后失败。

    遍历 signal_lookback 窗口内每个刺穿阻力的 break_bar，追踪其后 post_break_bars
    根，按三档失败特征打分，返回 failure_score 最高的达标 setup。
    """
    recent = df_i.tail(cfg.signal_lookback).reset_index(drop=True)
    n = len(recent)
    level = resistance.center
    break_threshold = level + cfg.break_atr * atr

    best: Signal | None = None
    for i in range(n - 1):
        break_bar = recent.iloc[i]
        break_high = float(break_bar["high"])
        if break_high < break_threshold:
            continue

        # 在 break_bar 之后 post_break_bars 根内，找第一根收盘收回阻力下的 recover_bar
        end = min(i + cfg.post_break_bars + 1, n)
        recover_idx = -1
        for j in range(i + 1, end):
            if float(recent.iloc[j]["close"]) < level:
                recover_idx = j
                break
        if recover_idx < 0:
            continue  # 没收回，不构成失败

        recover_bar = recent.iloc[recover_idx]
        entry = float(recover_bar["close"])
        stop = break_high  # 假突破K极值点止损
        target = _downside_target(nearest_below(zones, resistance), entry, atr, cfg)
        if stop <= entry or target >= entry:
            continue

        # ── 三档打分 ──
        score, tags = _score_failure_short(recent, i, recover_idx, end, level, break_high, atr, cfg)
        if score < cfg.min_failure_score:
            continue

        vol = float(recover_bar["volume"])
        volume_confirm = (vol_ma > 0) and (vol > vol_ma * cfg.recover_volume_ratio)

        sig = Signal(
            side=SignalSide.SHORT,
            is_valid=True,
            levels=TradeLevels(entry=round(entry, 2), stop=round(stop, 2), target=round(target, 2)),
            pattern="Upthrust",
            reason=(
                f"Upthrust/上冲回落 — 假突破阻力区[{resistance.lower:.1f}-{resistance.upper:.1f}]"
                f"(中心{level:.1f})后第{recover_idx-i}根收回，"
                f"刺穿高{break_high:.1f}，失败强度{score:.0f}[{'+'.join(tags)}]，"
                f"{'放量' if volume_confirm else '缩量'}确认"
            ),
            zone=resistance,
            volume_confirm=volume_confirm,
            failure_score=score,
            metadata={"break_bar_idx": i, "recover_idx": recover_idx, "tags": tags},
        )
        if best is None or score > best.failure_score:
            best = sig
    return best


def _detect_spring(
    df_i: pd.DataFrame,
    support: Zone,
    zones: list[Zone],
    atr: float,
    vol_ma: float,
    cfg: FakeBreakConfig,
) -> Signal | None:
    """Spring（做多）：假跌破支撑后失败。镜像于 _detect_upthrust。"""
    recent = df_i.tail(cfg.signal_lookback).reset_index(drop=True)
    n = len(recent)
    level = support.center
    break_threshold = level - cfg.break_atr * atr

    best: Signal | None = None
    for i in range(n - 1):
        break_bar = recent.iloc[i]
        break_low = float(break_bar["low"])
        if break_low > break_threshold:
            continue

        end = min(i + cfg.post_break_bars + 1, n)
        recover_idx = -1
        for j in range(i + 1, end):
            if float(recent.iloc[j]["close"]) > level:
                recover_idx = j
                break
        if recover_idx < 0:
            continue

        recover_bar = recent.iloc[recover_idx]
        entry = float(recover_bar["close"])
        stop = break_low
        target = _upside_target(nearest_above(zones, support), entry, atr, cfg)
        if stop >= entry or target <= entry:
            continue

        score, tags = _score_failure_long(recent, i, recover_idx, end, level, break_low, atr, cfg)
        if score < cfg.min_failure_score:
            continue

        vol = float(recover_bar["volume"])
        volume_confirm = (vol_ma > 0) and (vol > vol_ma * cfg.recover_volume_ratio)

        sig = Signal(
            side=SignalSide.LONG,
            is_valid=True,
            levels=TradeLevels(entry=round(entry, 2), stop=round(stop, 2), target=round(target, 2)),
            pattern="Spring",
            reason=(
                f"Spring/弹簧效应 — 假跌破支撑区[{support.lower:.1f}-{support.upper:.1f}]"
                f"(中心{level:.1f})后第{recover_idx-i}根收回，"
                f"刺破低{break_low:.1f}，失败强度{score:.0f}[{'+'.join(tags)}]，"
                f"{'放量' if volume_confirm else '缩量'}确认"
            ),
            zone=support,
            volume_confirm=volume_confirm,
            failure_score=score,
            metadata={"break_bar_idx": i, "recover_idx": recover_idx, "tags": tags},
        )
        if best is None or score > best.failure_score:
            best = sig
    return best


# ── 突破失败三档打分 ──
def _score_failure_short(
    recent: pd.DataFrame,
    break_idx: int,
    recover_idx: int,
    end: int,
    level: float,
    break_high: float,
    atr: float,
    cfg: FakeBreakConfig,
) -> tuple[float, list[str]]:
    """做空侧突破失败打分。返回 (总分, 命中档位标签列表)。

    A 快速反击：break 后第1根就是明显反向K且收回 → score_fast_recoil
    B 不创新高：break_idx 之后到 end 内，无 K 线 high 超过 break_high → score_no_new_extreme
    C 2B 跌破突破点：recover 不仅收回 level，收盘还跌破 (level - beyond_atr*ATR) → score_2b_beyond
    """
    score = 0.0
    tags: list[str] = []

    # A 快速反击：第1根就收回 + 明显阴线实体
    if recover_idx == break_idx + 1:
        rb = recent.iloc[recover_idx]
        body = float(rb["open"]) - float(rb["close"])  # 阴线实体（正数）
        if body >= cfg.recoil_atr * atr:
            score += cfg.score_fast_recoil
            tags.append("快速反击")

    # B 不创新高：后续窗口内没有 high 超过 break_high
    post = recent.iloc[break_idx + 1:end]
    if len(post) > 0 and float(post["high"].max()) <= break_high:
        score += cfg.score_no_new_extreme
        tags.append("不创新高")

    # C 2B 跌破突破点：收盘跌破突破起始点 level 一定深度
    beyond_threshold = level - cfg.beyond_atr * atr
    if float(recent.iloc[recover_idx]["close"]) <= beyond_threshold:
        score += cfg.score_2b_beyond
        tags.append("2B跌破")

    return score, tags


def _score_failure_long(
    recent: pd.DataFrame,
    break_idx: int,
    recover_idx: int,
    end: int,
    level: float,
    break_low: float,
    atr: float,
    cfg: FakeBreakConfig,
) -> tuple[float, list[str]]:
    """做多侧突破失败打分（镜像于 _score_failure_short）。"""
    score = 0.0
    tags: list[str] = []

    # A 快速反击：第1根就收回 + 明显阳线实体
    if recover_idx == break_idx + 1:
        rb = recent.iloc[recover_idx]
        body = float(rb["close"]) - float(rb["open"])  # 阳线实体（正数）
        if body >= cfg.recoil_atr * atr:
            score += cfg.score_fast_recoil
            tags.append("快速反击")

    # B 不创新低：后续窗口内没有 low 低于 break_low
    post = recent.iloc[break_idx + 1:end]
    if len(post) > 0 and float(post["low"].min()) >= break_low:
        score += cfg.score_no_new_extreme
        tags.append("不创新低")

    # C 2B 涨破突破点：收盘涨破 level 一定深度
    beyond_threshold = level + cfg.beyond_atr * atr
    if float(recent.iloc[recover_idx]["close"]) >= beyond_threshold:
        score += cfg.score_2b_beyond
        tags.append("2B涨破")

    return score, tags


# ── 目标位辅助 ──
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
