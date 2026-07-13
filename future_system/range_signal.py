"""震荡旁路完整逻辑（30m 执行，60m 环境闸在 runner 层）。

主信号：假突破反转（Failed Break）
辅信号：箱体边沿回归（Range Fade）
止损止盈：见 range_exits.RangeExitConfig（参数化，可优化）
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Literal

import numpy as np
import pandas as pd

from future_signal.indicators_min import ATR, detect_box, detect_wedge

from .range_exits import (
    ExitPlan,
    ExitState,
    RangeExitConfig,
    build_exit_plan,
    exit_params_dict,
    open_exit_state,
    on_bar_update,
)
from .regime import _atr

SetupKind = Literal[
    "",
    "failed_break_long",
    "failed_break_short",
    "fade_long",
    "fade_short",
]


@dataclass
class RangeSignalConfig:
    # ── 形态 ──
    box_lookback: int = 30
    box_range_max: float = 0.04
    box_touch_min: int = 2
    box_touch_tol: float = 0.006
    wedge_atr_shrink: float = 0.85
    wedge_range_shrink: float = 0.85
    require_pattern: bool = True

    # ── 假突破 ──
    pierce_atr: float = 0.25
    recover_within: int = 5
    min_failure_score: float = 40.0
    score_fast_recoil: float = 40.0
    score_close_inside: float = 30.0
    score_reject_body: float = 30.0
    recoil_body_atr: float = 0.35
    beyond_start_atr: float = 0.20

    # ── 边沿回归 ──
    enable_fade: bool = True
    edge_atr: float = 0.40
    fade_min_body_atr: float = 0.15
    fade_strength_scale: float = 0.75

    # ── 量能 / 指标 ──
    vol_ma: int = 20
    vol_confirm: float = 1.10
    atr_period: int = 14
    min_bars: int = 100

    # ── 强度 ──
    strength_floor: float = 0.30
    strength_ceil: float = 0.90

    # ── 止损止盈（嵌套，优化主战场）──
    exits: RangeExitConfig | None = None

    def __post_init__(self) -> None:
        if self.exits is None:
            self.exits = RangeExitConfig()

    @property
    def max_hold_bars(self) -> int:
        return self.exits.max_hold_bars if self.exits else 24


@dataclass
class RangeSnapshot:
    symbol: str
    direction: int
    strength: float
    price: float
    atr: float
    daily_vol: float
    bar_time: str
    reason: str
    setup: SetupKind = ""
    box_high: float | None = None
    box_low: float | None = None
    # 出场计划
    entry: float | None = None
    stop: float | None = None
    tp1: float | None = None
    tp2: float | None = None
    tp1_fraction: float = 0.5
    initial_risk: float | None = None
    reward_risk_tp1: float | None = None
    reward_risk_tp2: float | None = None
    stop_source: str = ""
    tp1_source: str = ""
    tp2_source: str = ""
    # 动态
    current_stop: float | None = None
    tp1_hit: bool = False
    remaining_frac: float = 1.0
    failure_score: float = 0.0
    order_now: bool = False
    is_hold: bool = False
    bars_in_trade: int = 0
    pattern_ok: bool = False
    exit_params: dict[str, Any] | None = None

    def levels_dict(self) -> dict[str, Any]:
        return {
            "entry": self.entry,
            "stop": self.stop,
            "tp1": self.tp1,
            "tp2": self.tp2,
            "current_stop": self.current_stop,
            "tp1_fraction": self.tp1_fraction,
            "initial_risk": self.initial_risk,
            "rr_tp1": self.reward_risk_tp1,
            "rr_tp2": self.reward_risk_tp2,
            "stop_source": self.stop_source,
            "tp1_source": self.tp1_source,
            "tp2_source": self.tp2_source,
            "tp1_hit": self.tp1_hit,
            "remaining_frac": self.remaining_frac,
        }


@dataclass
class _Event:
    idx: int
    side: int
    setup: SetupKind
    box_high: float
    box_low: float
    plan: ExitPlan
    score: float
    strength: float
    entry: float


def _daily_vol(close: pd.Series, bars_per_day: float = 10.0) -> float:
    r = np.log(close.astype(float)).diff().dropna()
    if len(r) < 20:
        return 0.012
    return float(max(0.004, min(0.08, float(r.tail(80).std()) * (bars_per_day ** 0.5))))


def _prep(df: pd.DataFrame) -> pd.DataFrame:
    work = df.copy()
    for c in ("open", "high", "low", "close", "volume"):
        if c in work.columns:
            work[c] = pd.to_numeric(work[c], errors="coerce")
        elif c == "volume":
            work["volume"] = 1.0
    return work.dropna(subset=["open", "high", "low", "close"]).reset_index(drop=True)


def _add_structure(work: pd.DataFrame, cfg: RangeSignalConfig) -> pd.DataFrame:
    n = cfg.box_lookback
    high, low, close = work["high"], work["low"], work["close"]
    work["atr"] = _atr(high, low, close, cfg.atr_period)
    work["atr_sma"] = ATR(high, low, close, cfg.atr_period)
    work["vol_ma"] = work["volume"].rolling(cfg.vol_ma, min_periods=5).mean()
    work["box_high"] = high.shift(1).rolling(n).max()
    work["box_low"] = low.shift(1).rolling(n).min()
    work["box_mid"] = 0.5 * (work["box_high"] + work["box_low"])
    work["box_width"] = (work["box_high"] - work["box_low"]) / work["box_mid"].replace(0, np.nan)

    is_box = detect_box(
        high, low, period=n,
        range_max=cfg.box_range_max,
        touch_min=cfg.box_touch_min,
        touch_tol=cfg.box_touch_tol,
    )
    is_wedge = detect_wedge(
        high, low, close, period=n,
        atr_shrink=cfg.wedge_atr_shrink,
        range_shrink=cfg.wedge_range_shrink,
    )
    work["pattern_ok"] = (is_box | is_wedge).fillna(False)
    if not cfg.require_pattern:
        work["pattern_ok"] = work["pattern_ok"] | (
            work["box_width"].notna() & (work["box_width"] <= cfg.box_range_max)
        )
    return work


def _failure_score(
    work: pd.DataFrame,
    pierce_i: int,
    recover_i: int,
    side: int,
    box_h: float,
    box_l: float,
    cfg: RangeSignalConfig,
) -> float:
    atr = float(work.at[recover_i, "atr"])
    if not np.isfinite(atr) or atr <= 0:
        atr = float(work.at[recover_i, "atr_sma"]) if np.isfinite(work.at[recover_i, "atr_sma"]) else 1.0

    ph = float(work.at[pierce_i, "high"])
    pl = float(work.at[pierce_i, "low"])
    ro = float(work.at[recover_i, "open"])
    rc = float(work.at[recover_i, "close"])
    body = abs(rc - ro)
    score = 0.0

    if recover_i - pierce_i <= 1:
        score += cfg.score_fast_recoil
    elif recover_i - pierce_i <= 2:
        score += cfg.score_fast_recoil * 0.6

    if side == -1 and rc < box_h:
        score += cfg.score_close_inside
        if rc < box_h - cfg.beyond_start_atr * atr:
            score += 10.0
    if side == 1 and rc > box_l:
        score += cfg.score_close_inside
        if rc > box_l + cfg.beyond_start_atr * atr:
            score += 10.0

    if side == -1 and rc < ro and body >= cfg.recoil_body_atr * atr:
        score += cfg.score_reject_body
    elif side == -1 and rc < ro:
        score += cfg.score_reject_body * 0.5
    if side == 1 and rc > ro and body >= cfg.recoil_body_atr * atr:
        score += cfg.score_reject_body
    elif side == 1 and rc > ro:
        score += cfg.score_reject_body * 0.5

    vol_ma = work.at[recover_i, "vol_ma"]
    vol = work.at[recover_i, "volume"]
    if np.isfinite(vol_ma) and vol_ma > 0 and vol / vol_ma >= cfg.vol_confirm:
        score += 8.0

    if side == -1:
        depth = (ph - box_h) / atr
    else:
        depth = (box_l - pl) / atr
    if depth >= cfg.pierce_atr * 1.5:
        score += 5.0

    return float(min(100.0, score))


def _strength_from_score(
    score: float,
    atr: float,
    pierce_dist: float,
    vol_ratio: float,
    cfg: RangeSignalConfig,
    is_fade: bool,
) -> float:
    atr_term = min(1.0, pierce_dist / max(atr, 1e-9) / 1.2)
    vol_term = min(1.0, max(0.0, (vol_ratio - 1.0) / 1.2))
    score_term = min(1.0, score / 100.0)
    base = 0.40 * score_term + 0.30 * atr_term + 0.20 * vol_term + 0.10
    if is_fade:
        base *= cfg.fade_strength_scale
    return float(min(cfg.strength_ceil, max(cfg.strength_floor, base)))


def scan_events(work: pd.DataFrame, cfg: RangeSignalConfig) -> list[_Event]:
    """全历史扫描假突破 + 边沿入场；出场计划由 RangeExitConfig 生成。"""
    assert cfg.exits is not None
    xcfg = cfg.exits
    events: list[_Event] = []
    n = len(work)
    start = cfg.box_lookback + 2
    i = start
    while i < n:
        if not bool(work.at[i, "pattern_ok"]):
            i += 1
            continue
        box_h = float(work.at[i, "box_high"])
        box_l = float(work.at[i, "box_low"])
        atr = float(work.at[i, "atr"])
        if not (np.isfinite(box_h) and np.isfinite(box_l) and np.isfinite(atr) and atr > 0):
            i += 1
            continue
        width = float(work.at[i, "box_width"]) if np.isfinite(work.at[i, "box_width"]) else 1.0
        if width > cfg.box_range_max:
            i += 1
            continue

        hi = float(work.at[i, "high"])
        lo = float(work.at[i, "low"])
        cl = float(work.at[i, "close"])
        op = float(work.at[i, "open"])
        vol_ma = work.at[i, "vol_ma"]
        vol_r = (
            float(work.at[i, "volume"]) / float(vol_ma)
            if np.isfinite(vol_ma) and vol_ma > 0
            else 1.0
        )

        pierced_up = hi >= box_h + cfg.pierce_atr * atr
        pierced_dn = lo <= box_l - cfg.pierce_atr * atr
        fired = False

        if pierced_up:
            for k in range(i, min(i + cfg.recover_within + 1, n)):
                ck = float(work.at[k, "close"])
                if ck < box_h:
                    score = _failure_score(work, i, k, -1, box_h, box_l, cfg)
                    if score >= cfg.min_failure_score:
                        extreme = float(work["high"].iloc[i : k + 1].max())
                        entry = ck
                        plan = build_exit_plan(
                            -1, "failed_break_short", entry, box_h, box_l, extreme, atr, xcfg
                        )
                        if plan.valid:
                            dist = extreme - box_h
                            st = _strength_from_score(score, atr, dist, vol_r, cfg, False)
                            events.append(
                                _Event(
                                    k, -1, "failed_break_short", box_h, box_l,
                                    plan, score, st, entry,
                                )
                            )
                        i = k + 1
                        fired = True
                        break
            if fired:
                continue

        if pierced_dn:
            for k in range(i, min(i + cfg.recover_within + 1, n)):
                ck = float(work.at[k, "close"])
                if ck > box_l:
                    score = _failure_score(work, i, k, 1, box_h, box_l, cfg)
                    if score >= cfg.min_failure_score:
                        extreme = float(work["low"].iloc[i : k + 1].min())
                        entry = ck
                        plan = build_exit_plan(
                            1, "failed_break_long", entry, box_h, box_l, extreme, atr, xcfg
                        )
                        if plan.valid:
                            dist = box_l - extreme
                            st = _strength_from_score(score, atr, dist, vol_r, cfg, False)
                            events.append(
                                _Event(
                                    k, 1, "failed_break_long", box_h, box_l,
                                    plan, score, st, entry,
                                )
                            )
                        i = k + 1
                        fired = True
                        break
            if fired:
                continue

        if cfg.enable_fade:
            body = abs(cl - op)
            dist_up = box_h - cl
            dist_dn = cl - box_l
            if (
                0 <= dist_up <= cfg.edge_atr * atr
                and cl <= box_h
                and cl < op
                and body >= cfg.fade_min_body_atr * atr
            ):
                entry = cl
                plan = build_exit_plan(
                    -1, "fade_short", entry, box_h, box_l, box_h, atr, xcfg
                )
                if plan.valid:
                    score = 45.0 + min(
                        20.0, (1.0 - dist_up / max(cfg.edge_atr * atr, 1e-9)) * 20
                    )
                    st = _strength_from_score(
                        score, atr, cfg.edge_atr * atr - dist_up, vol_r, cfg, True
                    )
                    events.append(
                        _Event(i, -1, "fade_short", box_h, box_l, plan, score, st, entry)
                    )
                i += 1
                continue
            if (
                0 <= dist_dn <= cfg.edge_atr * atr
                and cl >= box_l
                and cl > op
                and body >= cfg.fade_min_body_atr * atr
            ):
                entry = cl
                plan = build_exit_plan(
                    1, "fade_long", entry, box_h, box_l, box_l, atr, xcfg
                )
                if plan.valid:
                    score = 45.0 + min(
                        20.0, (1.0 - dist_dn / max(cfg.edge_atr * atr, 1e-9)) * 20
                    )
                    st = _strength_from_score(
                        score, atr, cfg.edge_atr * atr - dist_dn, vol_r, cfg, True
                    )
                    events.append(
                        _Event(i, 1, "fade_long", box_h, box_l, plan, score, st, entry)
                    )
                i += 1
                continue

        i += 1
    return events


def _simulate_hold(
    work: pd.DataFrame,
    events: list[_Event],
    cfg: RangeSignalConfig,
) -> tuple[int, _Event | None, ExitState | None, int, bool, str]:
    """回放持仓（含分批止盈 / 保本 / 移动止损 / 时间）。"""
    if not events:
        return 0, None, None, 0, False, "no_events"

    assert cfg.exits is not None
    xcfg = cfg.exits
    by_i = {e.idx: e for e in events}
    n = len(work)
    pos = 0
    ev: _Event | None = None
    st: ExitState | None = None
    entry_i = -1
    last_order = False
    last_reason = "flat"

    for i in range(n):
        hi = float(work.at[i, "high"])
        lo = float(work.at[i, "low"])
        cl = float(work.at[i, "close"])
        atr_i = float(work.at[i, "atr"]) if np.isfinite(work.at[i, "atr"]) else 0.0
        order_now = False

        if pos != 0 and st is not None and ev is not None:
            bars_held = i - entry_i
            st, exits = on_bar_update(st, hi, lo, cl, bars_held, atr_i, xcfg)
            if st.remaining_frac <= 1e-9:
                pos = 0
                last_reason = st.exit_reason or "exit"
                ev = None
                st = None
                entry_i = -1

        if i in by_i:
            new_e = by_i[i]
            # 若仍有仓：先当反手/刷新（简化：直接替换）
            pos = new_e.side
            ev = new_e
            st = open_exit_state(new_e.plan)
            entry_i = i
            order_now = True
            last_reason = f"entry_{new_e.setup}"

        if i == n - 1:
            last_order = order_now

    if pos == 0 or ev is None or st is None:
        reason = last_reason if last_reason not in ("flat",) else "range_flat"
        if reason.startswith("entry_"):
            reason = "flat"
        return 0, None, None, 0, False, reason

    bars = n - 1 - entry_i
    reason = last_reason if last_order else f"hold_{ev.setup}"
    return pos, ev, st, bars, last_order, reason


def evaluate_range_signal(
    symbol: str,
    df: pd.DataFrame,
    cfg: RangeSignalConfig | None = None,
) -> RangeSnapshot:
    """震荡信号主入口。"""
    cfg = cfg or RangeSignalConfig()
    empty = RangeSnapshot(
        symbol=symbol, direction=0, strength=0.0, price=0.0, atr=0.0,
        daily_vol=0.012, bar_time="", reason="no_data",
        exit_params=exit_params_dict(cfg.exits),
    )
    if df is None or len(df) < cfg.min_bars:
        return empty

    work = _prep(df)
    if len(work) < cfg.min_bars:
        return empty
    work = _add_structure(work, cfg)

    i = len(work) - 1
    price = float(work.at[i, "close"])
    atr = float(work.at[i, "atr"]) if np.isfinite(work.at[i, "atr"]) else 0.0
    box_h = float(work.at[i, "box_high"]) if np.isfinite(work.at[i, "box_high"]) else None
    box_l = float(work.at[i, "box_low"]) if np.isfinite(work.at[i, "box_low"]) else None
    pattern_ok = bool(work.at[i, "pattern_ok"])
    bar_time = ""
    if "datetime" in work.columns:
        bar_time = str(pd.Timestamp(work.at[i, "datetime"]))
    dvol = _daily_vol(work["close"])
    xparams = exit_params_dict(cfg.exits)

    events = scan_events(work, cfg)
    direction, ev, st, bars_in, order_now, reason = _simulate_hold(work, events, cfg)

    if direction == 0 or ev is None or st is None:
        if box_h is None or box_l is None:
            reason = "no_box"
        elif not pattern_ok and cfg.require_pattern:
            reason = "no_pattern"
        elif reason in ("stop", "tp1_full", "tp2", "time_stop", "max_hold", "structure_invalidate"):
            reason = f"flat_after_{reason}"
        elif not events:
            reason = "no_setup"
        return RangeSnapshot(
            symbol=symbol,
            direction=0,
            strength=0.0,
            price=price,
            atr=atr,
            daily_vol=dvol,
            bar_time=bar_time,
            reason=reason,
            box_high=box_h,
            box_low=box_l,
            pattern_ok=pattern_ok,
            exit_params=xparams,
        )

    plan = ev.plan
    return RangeSnapshot(
        symbol=symbol,
        direction=int(direction),
        strength=float(ev.strength),
        price=price,
        atr=atr,
        daily_vol=dvol,
        bar_time=bar_time,
        reason=reason,
        setup=ev.setup,
        box_high=ev.box_high,
        box_low=ev.box_low,
        entry=plan.entry,
        stop=plan.stop,
        tp1=plan.tp1,
        tp2=plan.tp2,
        tp1_fraction=plan.tp1_fraction,
        initial_risk=plan.initial_risk,
        reward_risk_tp1=plan.reward_risk_tp1,
        reward_risk_tp2=plan.reward_risk_tp2,
        stop_source=plan.stop_source,
        tp1_source=plan.tp1_source,
        tp2_source=plan.tp2_source,
        current_stop=st.stop,
        tp1_hit=st.tp1_hit,
        remaining_frac=st.remaining_frac,
        failure_score=ev.score,
        order_now=order_now,
        is_hold=not order_now,
        bars_in_trade=bars_in,
        pattern_ok=pattern_ok,
        exit_params=xparams,
    )


def explain_range_state(
    symbol: str, df: pd.DataFrame, cfg: RangeSignalConfig | None = None
) -> dict:
    cfg = cfg or RangeSignalConfig()
    work = _add_structure(_prep(df), cfg)
    events = scan_events(work, cfg)
    i = len(work) - 1
    snap = evaluate_range_signal(symbol, df, cfg)
    return {
        "symbol": symbol,
        "bars": len(work),
        "pattern_ok": bool(work.at[i, "pattern_ok"]),
        "box_high": float(work.at[i, "box_high"]) if np.isfinite(work.at[i, "box_high"]) else None,
        "box_low": float(work.at[i, "box_low"]) if np.isfinite(work.at[i, "box_low"]) else None,
        "n_events": len(events),
        "exit_params": exit_params_dict(cfg.exits),
        "levels": snap.levels_dict(),
        "snapshot_reason": snap.reason,
        "direction": snap.direction,
        "setup": snap.setup,
    }
