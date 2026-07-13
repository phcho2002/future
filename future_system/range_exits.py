"""震荡腿止损 / 止盈设计（全部关键值参数化，便于网格优化）。

设计原则
--------
1. 止损认结构，不认感觉：假突破单止损在刺穿极值外侧；边沿单止损在箱外。
2. 止盈分两段：先兑现一部分（TP1），剩余让利润跑到中轨/对侧或 R 倍数（TP2）。
3. 触发 TP1 后可选保本 + 移动止损，避免回吐。
4. 时间止损：持仓过久且未到最小 R，离场（震荡单不宜久拖）。

所有倍数以「入场 ATR」与「初始风险 R = |entry - stop|」为尺度。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from typing import Any, Literal

import numpy as np

SetupKind = Literal[
    "",
    "failed_break_long",
    "failed_break_short",
    "fade_long",
    "fade_short",
]


@dataclass
class RangeExitConfig:
    """止损止盈参数（优化时主要扫这一组）。"""

    # ── 初始止损 ──
    # 假突破：止损 = 刺穿极值 ± failed_stop_buffer_atr × ATR
    failed_stop_buffer_atr: float = 0.35
    # 边沿回归：止损 = 箱沿 ± fade_stop_buffer_atr × ATR
    fade_stop_buffer_atr: float = 0.30
    # 风险距离钳制（相对 ATR）
    min_stop_atr: float = 0.45
    max_stop_atr: float = 1.60

    # ── 结构失效（可选二次确认离场）──
    # 空单：收盘重新站上箱上沿 + rebreak_atr×ATR → 失效
    # 多单：收盘重新跌破箱下沿 - rebreak_atr×ATR → 失效
    enable_structure_invalidate: bool = True
    rebreak_atr: float = 0.20

    # ── 止盈模式 ──
    # hybrid : TP1=min(结构目标1, entry±tp1_r*R)；TP2=max(结构目标2, entry±tp2_r*R) 方向正确取
    # mid    : 仅中轨
    # opposite: 仅对侧边界
    # r_multiple: 仅 R 倍数
    tp_mode: str = "hybrid"
    # R 倍数止盈
    tp1_r: float = 1.0
    tp2_r: float = 2.0
    # 分批：TP1 平掉的仓位比例，剩余留到 TP2/移动止损
    tp1_fraction: float = 0.50
    # 结构目标
    use_mid_as_tp1: bool = True
    mid_offset_atr: float = 0.05       # 多单目标 mid - offset；空单 mid + offset（略提前）
    use_opposite_as_tp2: bool = True
    opposite_buffer_atr: float = 0.15  # 对侧内侧留缓冲，避免摸边扫损

    # ── 保本 ──
    enable_breakeven: bool = True
    breakeven_trigger_r: float = 0.80  # 浮盈达到该 R 后止损移到成本附近
    breakeven_lock_atr: float = 0.05   # 保本锁定一点点利润（×ATR）

    # ── TP1 后移动止损 ──
    enable_trail_after_tp1: bool = True
    trail_atr: float = 1.00            # 跟踪极值回撤 ATR 倍
    # 未开 TP1 也可在 trail_activate_r 后启动（默认与 tp1 对齐）
    trail_activate_r: float = 1.00

    # ── 时间 ──
    max_hold_bars: int = 24
    # 持仓超过 max_hold_bars * time_stop_frac 且 浮盈 < time_stop_min_r → 时间离场
    enable_time_stop: bool = True
    time_stop_frac: float = 0.50
    time_stop_min_r: float = 0.25

    # ── 最低盈亏比过滤（入场时）──
    # 若 TP1 相对初始止损的 R 不足，则放弃该信号（0=关闭）
    min_reward_risk_tp1: float = 0.80


def exit_params_dict(cfg: RangeExitConfig | None = None) -> dict[str, Any]:
    """导出全部止损止盈参数（写 JSON / 优化网格用）。"""
    return asdict(cfg or RangeExitConfig())


def exit_param_space() -> dict[str, list]:
    """建议优化网格（可按需裁剪）。"""
    return {
        "failed_stop_buffer_atr": [0.25, 0.35, 0.50],
        "fade_stop_buffer_atr": [0.20, 0.30, 0.40],
        "min_stop_atr": [0.35, 0.45, 0.60],
        "max_stop_atr": [1.20, 1.60, 2.00],
        "tp1_r": [0.8, 1.0, 1.2],
        "tp2_r": [1.5, 2.0, 2.5],
        "tp1_fraction": [0.40, 0.50, 0.60],
        "breakeven_trigger_r": [0.6, 0.8, 1.0],
        "trail_atr": [0.8, 1.0, 1.3],
        "max_hold_bars": [16, 24, 32],
        "min_reward_risk_tp1": [0.6, 0.8, 1.0],
    }


@dataclass
class ExitPlan:
    """入场时冻结的出场计划。"""

    side: int
    entry: float
    atr: float
    stop: float
    tp1: float
    tp2: float
    tp1_fraction: float
    initial_risk: float          # R = |entry - stop|
    box_high: float
    box_low: float
    setup: str
    # 诊断
    stop_source: str = ""
    tp1_source: str = ""
    tp2_source: str = ""
    reward_risk_tp1: float = 0.0
    reward_risk_tp2: float = 0.0
    valid: bool = True
    reject_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _clamp_stop(
    side: int,
    entry: float,
    raw_stop: float,
    atr: float,
    cfg: RangeExitConfig,
) -> tuple[float, str]:
    """把止损距离钳在 [min_stop_atr, max_stop_atr] × ATR。"""
    risk = abs(entry - raw_stop)
    min_d = cfg.min_stop_atr * atr
    max_d = cfg.max_stop_atr * atr
    note = "raw"
    if risk < min_d:
        risk = min_d
        note = "min_clamp"
    elif risk > max_d:
        risk = max_d
        note = "max_clamp"
    if side == 1:
        return entry - risk, note
    return entry + risk, note


def _struct_mid(box_h: float, box_l: float, side: int, atr: float, cfg: RangeExitConfig) -> float:
    mid = 0.5 * (box_h + box_l)
    if side == 1:
        return mid - cfg.mid_offset_atr * atr
    return mid + cfg.mid_offset_atr * atr


def _struct_opposite(
    box_h: float, box_l: float, side: int, atr: float, cfg: RangeExitConfig
) -> float:
    if side == 1:
        return box_h - cfg.opposite_buffer_atr * atr
    return box_l + cfg.opposite_buffer_atr * atr


def _r_target(entry: float, side: int, r_mult: float, risk: float) -> float:
    if side == 1:
        return entry + r_mult * risk
    return entry - r_mult * risk


def _pick_closer_tp(side: int, entry: float, a: float, b: float) -> float:
    """多单取更高者中更近的（较小）；空单取更低者中更近的（较大）。"""
    if side == 1:
        cands = [x for x in (a, b) if x > entry]
        return min(cands) if cands else max(a, b)
    cands = [x for x in (a, b) if x < entry]
    return max(cands) if cands else min(a, b)


def _pick_farther_tp(side: int, entry: float, a: float, b: float) -> float:
    if side == 1:
        cands = [x for x in (a, b) if x > entry]
        return max(cands) if cands else max(a, b)
    cands = [x for x in (a, b) if x < entry]
    return min(cands) if cands else min(a, b)


def build_exit_plan(
    side: int,
    setup: str,
    entry: float,
    box_h: float,
    box_l: float,
    pierce_extreme: float,
    atr: float,
    cfg: RangeExitConfig | None = None,
) -> ExitPlan:
    """根据 setup 计算初始止损与 TP1/TP2。"""
    cfg = cfg or RangeExitConfig()
    is_fade = setup.startswith("fade")

    # 1) raw stop
    if is_fade:
        if side == 1:
            raw_stop = box_l - cfg.fade_stop_buffer_atr * atr
        else:
            raw_stop = box_h + cfg.fade_stop_buffer_atr * atr
        stop_src = "fade_box_outer"
    else:
        if side == 1:
            raw_stop = pierce_extreme - cfg.failed_stop_buffer_atr * atr
        else:
            raw_stop = pierce_extreme + cfg.failed_stop_buffer_atr * atr
        stop_src = "failed_pierce_extreme"

    stop, clamp_note = _clamp_stop(side, entry, raw_stop, atr, cfg)
    if clamp_note != "raw":
        stop_src = f"{stop_src}+{clamp_note}"

    risk = abs(entry - stop)
    if risk <= 0 or not np.isfinite(risk):
        return ExitPlan(
            side=side, entry=entry, atr=atr, stop=stop, tp1=entry, tp2=entry,
            tp1_fraction=cfg.tp1_fraction, initial_risk=0.0,
            box_high=box_h, box_low=box_l, setup=setup,
            stop_source=stop_src, valid=False, reject_reason="non_positive_risk",
        )

    # 2) structure targets
    mid_tp = _struct_mid(box_h, box_l, side, atr, cfg)
    opp_tp = _struct_opposite(box_h, box_l, side, atr, cfg)
    r_tp1 = _r_target(entry, side, cfg.tp1_r, risk)
    r_tp2 = _r_target(entry, side, cfg.tp2_r, risk)

    mode = (cfg.tp_mode or "hybrid").lower()
    tp1_src = tp2_src = mode

    if mode == "mid":
        tp1 = mid_tp
        tp2 = opp_tp if cfg.use_opposite_as_tp2 else r_tp2
        tp1_src, tp2_src = "mid", "opposite" if cfg.use_opposite_as_tp2 else "r2"
    elif mode == "opposite":
        tp1 = mid_tp if cfg.use_mid_as_tp1 else r_tp1
        tp2 = opp_tp
        tp1_src, tp2_src = ("mid" if cfg.use_mid_as_tp1 else "r1"), "opposite"
    elif mode == "r_multiple":
        tp1, tp2 = r_tp1, r_tp2
        tp1_src, tp2_src = "r1", "r2"
    else:  # hybrid
        # TP1：中轨与 R1 中更近的那个（更快兑现）
        if cfg.use_mid_as_tp1:
            tp1 = _pick_closer_tp(side, entry, mid_tp, r_tp1)
            tp1_src = "min(mid,r1)"
        else:
            tp1 = r_tp1
            tp1_src = "r1"
        # TP2：对侧与 R2 中更远的那个（更大利润）
        if cfg.use_opposite_as_tp2:
            tp2 = _pick_farther_tp(side, entry, opp_tp, r_tp2)
            tp2_src = "max(opp,r2)"
        else:
            tp2 = r_tp2
            tp2_src = "r2"

    # 保证方向正确：多单 tp > entry，空单 tp < entry
    if side == 1:
        if tp1 <= entry:
            tp1 = r_tp1
            tp1_src = "fallback_r1"
        if tp2 <= tp1:
            tp2 = max(r_tp2, tp1 + 0.2 * risk)
            tp2_src = "fallback_r2"
    else:
        if tp1 >= entry:
            tp1 = r_tp1
            tp1_src = "fallback_r1"
        if tp2 >= tp1:
            tp2 = min(r_tp2, tp1 - 0.2 * risk)
            tp2_src = "fallback_r2"

    rr1 = abs(tp1 - entry) / risk
    rr2 = abs(tp2 - entry) / risk
    valid = True
    reject = ""
    if cfg.min_reward_risk_tp1 > 0 and rr1 < cfg.min_reward_risk_tp1:
        valid = False
        reject = f"rr_tp1={rr1:.2f}<{cfg.min_reward_risk_tp1}"

    return ExitPlan(
        side=side,
        entry=entry,
        atr=atr,
        stop=stop,
        tp1=tp1,
        tp2=tp2,
        tp1_fraction=float(np.clip(cfg.tp1_fraction, 0.05, 0.95)),
        initial_risk=risk,
        box_high=box_h,
        box_low=box_l,
        setup=setup,
        stop_source=stop_src,
        tp1_source=tp1_src,
        tp2_source=tp2_src,
        reward_risk_tp1=rr1,
        reward_risk_tp2=rr2,
        valid=valid,
        reject_reason=reject,
    )


@dataclass
class ExitState:
    """持仓过程中的动态出场状态。"""

    plan: ExitPlan
    stop: float
    tp1_hit: bool = False
    tp2_hit: bool = False
    remaining_frac: float = 1.0
    extreme: float = 0.0          # 持仓期有利极值
    exit_reason: str = ""
    realized_frac: float = 0.0    # 已平仓比例累计


def open_exit_state(plan: ExitPlan) -> ExitState:
    return ExitState(
        plan=plan,
        stop=plan.stop,
        remaining_frac=1.0,
        extreme=plan.entry,
    )


def on_bar_update(
    state: ExitState,
    bar_high: float,
    bar_low: float,
    bar_close: float,
    bars_held: int,
    atr_now: float,
    cfg: RangeExitConfig | None = None,
) -> tuple[ExitState, list[tuple[str, float]]]:
    """推进一根 K 线，返回 (新状态, 平仓事件列表[(reason, fraction)]).

    fraction 为相对「初始满仓」的比例。
    优先级：结构失效 > 止损 > TP1 > TP2 > 移动止损 > 时间。
    """
    cfg = cfg or RangeExitConfig()
    plan = state.plan
    side = plan.side
    events: list[tuple[str, float]] = []

    if state.remaining_frac <= 1e-9:
        return state, events

    # 更新有利极值
    if side == 1:
        state.extreme = max(state.extreme, bar_high)
    else:
        state.extreme = min(state.extreme, bar_low)

    risk = plan.initial_risk
    if side == 1:
        unreal_r = (bar_close - plan.entry) / risk if risk > 0 else 0.0
        best_r = (state.extreme - plan.entry) / risk if risk > 0 else 0.0
    else:
        unreal_r = (plan.entry - bar_close) / risk if risk > 0 else 0.0
        best_r = (plan.entry - state.extreme) / risk if risk > 0 else 0.0

    # 本 bar 先用「进入本 bar 时」的止损判定，避免同根先抬止损再误杀
    stop_for_hit = state.stop

    # ── 1) 结构失效 ──
    if cfg.enable_structure_invalidate:
        if side == -1 and bar_close > plan.box_high + cfg.rebreak_atr * atr_now:
            events.append(("structure_invalidate", state.remaining_frac))
            state.remaining_frac = 0.0
            state.exit_reason = "structure_invalidate"
            return state, events
        if side == 1 and bar_close < plan.box_low - cfg.rebreak_atr * atr_now:
            events.append(("structure_invalidate", state.remaining_frac))
            state.remaining_frac = 0.0
            state.exit_reason = "structure_invalidate"
            return state, events

    # ── 2) 止损（进入本 bar 时的 stop）──
    hit_stop = (side == 1 and bar_low <= stop_for_hit) or (
        side == -1 and bar_high >= stop_for_hit
    )
    if hit_stop:
        events.append(("stop", state.remaining_frac))
        state.remaining_frac = 0.0
        state.exit_reason = "stop"
        return state, events

    # ── 3) TP1 / TP2（有利出场优先于本 bar 抬止损）──
    if not state.tp1_hit:
        hit_tp1 = (side == 1 and bar_high >= plan.tp1) or (
            side == -1 and bar_low <= plan.tp1
        )
        if hit_tp1:
            frac = min(plan.tp1_fraction, state.remaining_frac)
            events.append(("tp1", frac))
            state.remaining_frac -= frac
            state.realized_frac += frac
            state.tp1_hit = True
            if state.remaining_frac <= 1e-9:
                state.exit_reason = "tp1_full"
                return state, events

    if state.remaining_frac > 1e-9:
        hit_tp2 = (side == 1 and bar_high >= plan.tp2) or (
            side == -1 and bar_low <= plan.tp2
        )
        if hit_tp2:
            events.append(("tp2", state.remaining_frac))
            state.remaining_frac = 0.0
            state.tp2_hit = True
            state.exit_reason = "tp2"
            return state, events

    # ── 4) 保本（下一 bar 生效）──
    if cfg.enable_breakeven and best_r >= cfg.breakeven_trigger_r:
        if side == 1:
            be = plan.entry + cfg.breakeven_lock_atr * plan.atr
            state.stop = max(state.stop, be)
        else:
            be = plan.entry - cfg.breakeven_lock_atr * plan.atr
            state.stop = min(state.stop, be)

    # ── 5) 移动止损：默认仅 TP1 之后；trail_activate_r 作额外开关 ──
    trail_on = False
    if cfg.enable_trail_after_tp1 and state.tp1_hit:
        trail_on = True
    elif (not cfg.enable_trail_after_tp1) and best_r >= cfg.trail_activate_r:
        trail_on = True
    if trail_on and cfg.trail_atr > 0:
        if side == 1:
            trail = state.extreme - cfg.trail_atr * atr_now
            state.stop = max(state.stop, trail)
        else:
            trail = state.extreme + cfg.trail_atr * atr_now
            state.stop = min(state.stop, trail)

    # ── 6) 时间止损 ──
    if cfg.enable_time_stop and bars_held >= int(cfg.max_hold_bars * cfg.time_stop_frac):
        if unreal_r < cfg.time_stop_min_r and bars_held >= cfg.max_hold_bars:
            events.append(("time_stop", state.remaining_frac))
            state.remaining_frac = 0.0
            state.exit_reason = "time_stop"
            return state, events
        if bars_held >= cfg.max_hold_bars:
            events.append(("max_hold", state.remaining_frac))
            state.remaining_frac = 0.0
            state.exit_reason = "max_hold"
            return state, events

    return state, events


def merge_exit_into_signal_config(base: Any, exit_cfg: RangeExitConfig) -> Any:
    """可选：把 Exit 参数同步到旧 RangeSignalConfig 字段（兼容）。"""
    if hasattr(base, "max_hold_bars"):
        base.max_hold_bars = exit_cfg.max_hold_bars
    if hasattr(base, "stop_buffer_atr"):
        base.stop_buffer_atr = exit_cfg.failed_stop_buffer_atr
    return base
