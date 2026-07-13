"""假突破 → 真突破 主信号（复刻 future_bb SecondBreakoutEngine）。

入场规则（用户约定）
------------------
1. **主仓**：仅在「二次真突破」下单（FAILED → 再次突破 + EMA 顺势）。
2. **可选试探小仓**：首次突破（PRIMED → BROKEN）可开小仓；失败确认则平掉；
   若走出二次真突破，则升级为满仓强度。

首次突破默认不强制开仓（由 ``allow_first_breakout_probe`` 控制）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd

from .config import SignalConfig
from .indicators_min import ATR, EMA, detect_box, detect_wedge

EntryTier = Literal["none", "first_probe", "second_full"]


@dataclass
class BreakoutSnapshot:
    symbol: str
    direction: int
    strength: float
    price: float
    atr: float
    daily_vol: float
    bar_time: str
    reason: str
    vol_ratio: float = 1.0
    oi_delta: float | None = None
    entry_level: float | None = None
    exit_level: float | None = None
    bars_in_trade: int = 0
    engine_state: str = "IDLE"
    is_fresh_entry: bool = False
    # 入场档位：none / first_probe / second_full
    entry_tier: EntryTier = "none"
    # 是否本根需要下单（开/加仓事件），hold 为 False
    order_now: bool = False
    order_side: int = 0          # +1 开多/-1 开空/0 无
    order_kind: str = ""         # first_probe | second_full | ""
    # 状态机监控
    pattern_resistance: float | None = None
    pattern_support: float | None = None
    hist_signal_count: int = 0       # 二次突破历史次数
    hist_first_count: int = 0        # 首次突破历史次数
    last_signal_bars_ago: int | None = None
    last_signal_side: int | None = None


def estimate_daily_vol_from_60m(close: pd.Series, bars_per_day: float = 8.0) -> float:
    r = np.log(close.astype(float)).diff().dropna()
    if len(r) < 30:
        return 0.012
    lam = 0.94
    var = float(r.iloc[0] ** 2)
    for x in r.iloc[1:]:
        var = lam * var + (1.0 - lam) * float(x) ** 2
    return float(max(0.004, min(0.08, (var ** 0.5) * (bars_per_day ** 0.5))))


class SecondBreakoutEngine:
    """蓄势 → 首次突破(可试探) → 失败确认 → 二次真突破(主仓)。"""

    IDLE = "IDLE"
    PRIMED_LONG = "PRIMED_LONG"
    BROKEN_LONG = "BROKEN_LONG"
    FAILED_LONG = "FAILED_LONG"
    PRIMED_SHORT = "PRIMED_SHORT"
    BROKEN_SHORT = "BROKEN_SHORT"
    FAILED_SHORT = "FAILED_SHORT"

    def __init__(self, df: pd.DataFrame, cfg: SignalConfig):
        self.cfg = cfg
        self.df = df.reset_index(drop=True).copy()
        self.n = len(self.df)
        self.state = self.IDLE
        self.pattern_level: dict | None = None
        self.breakout_idx: int | None = None
        self.fail_count = 0
        self._primed_short = True
        # (idx, side, level, atr, kind)  kind: first | second | fail_exit
        self.events: list[tuple[int, int, float, float, str]] = []
        self.signals: list[tuple[int, int, float, float]] = []  # 仅二次，兼容旧测试
        self._compute_patterns()

    def _compute_patterns(self) -> None:
        cfg = self.cfg
        df = self.df
        high, low, close = df["high"], df["low"], df["close"]
        n = cfg.pattern_lookback

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
        df["setup_pattern"] = (is_box | is_wedge).fillna(False)
        df["resistance_level"] = high.rolling(n).max().shift(1)
        df["support_level"] = low.rolling(n).min().shift(1)
        df["atr"] = ATR(high, low, close, cfg.atr_period)
        df["ema_trend"] = EMA(close, cfg.ema_trend_period)
        vol_ma = df["volume"].rolling(cfg.vol_ma, min_periods=5).mean()
        df["vol_ratio"] = df["volume"] / vol_ma.replace(0, np.nan)
        self.df = df

    def run(self) -> list[tuple[int, int, float, float]]:
        for i in range(self.n):
            self._step(i)
        return self.signals

    def _reset(self) -> None:
        self.state = self.IDLE
        self.pattern_level = None
        self.breakout_idx = None
        self.fail_count = 0
        self._primed_short = True

    def _pattern_expired(self, i: int) -> bool:
        return i - (self.breakout_idx or i) > self.cfg.pattern_lookback * 2

    def _emit(self, i: int, side: int, level: float, atr: float, kind: str) -> None:
        self.events.append((i, side, level, atr, kind))
        if kind == "second":
            self.signals.append((i, side, level, atr))

    def _step(self, i: int) -> None:
        cfg = self.cfg
        df = self.df
        if i < cfg.pattern_lookback:
            return

        row = df.iloc[i]
        close = float(row["close"])
        atr = float(row["atr"]) if np.isfinite(row["atr"]) else 0.0
        has_pattern = bool(row["setup_pattern"])
        resistance = (
            float(row["resistance_level"])
            if np.isfinite(row["resistance_level"])
            else 0.0
        )
        support = (
            float(row["support_level"])
            if np.isfinite(row["support_level"])
            else 0.0
        )
        ema = float(row["ema_trend"]) if np.isfinite(row["ema_trend"]) else 0.0
        gap = i - self.breakout_idx if self.breakout_idx is not None else 0
        thr = cfg.breakout_threshold_2nd

        if self.state in (
            self.BROKEN_LONG, self.FAILED_LONG,
            self.BROKEN_SHORT, self.FAILED_SHORT,
        ):
            if gap > cfg.second_break_max_gap:
                # 超时：若仍有首次试探仓，发出平仓事件
                if self.state in (self.BROKEN_LONG, self.BROKEN_SHORT) and cfg.allow_first_breakout_probe:
                    side = 1 if self.state == self.BROKEN_LONG else -1
                    lvl = (
                        self.pattern_level["resistance"]
                        if side == 1
                        else self.pattern_level["support"]
                    ) if self.pattern_level else close
                    self._emit(i, side, float(lvl), atr, "fail_exit")
                self._reset()

        if self.state == self.IDLE:
            if has_pattern and resistance > 0 and support > 0:
                self.pattern_level = {"resistance": resistance, "support": support}
                self.state = self.PRIMED_LONG
                self._primed_short = True

        elif self.state == self.PRIMED_LONG:
            if not has_pattern and self._pattern_expired(i):
                self._reset()
                return
            if resistance > 0 and support > 0:
                self.pattern_level = {"resistance": resistance, "support": support}
            res = self.pattern_level["resistance"] if self.pattern_level else resistance
            sup = self.pattern_level["support"] if self.pattern_level else support

            broke_up = close > res * (1 + thr)
            broke_dn = close < sup * (1 - thr)
            if broke_up:
                self.state = self.BROKEN_LONG
                self.breakout_idx = i
                self.fail_count = 0
                self._primed_short = False
                # 首次突破：可选试探小仓（需 EMA 顺势，避免逆势扎针）
                if cfg.allow_first_breakout_probe and (ema <= 0 or close > ema):
                    self._emit(i, 1, res, atr, "first")
            elif broke_dn and getattr(self, "_primed_short", True):
                self.state = self.BROKEN_SHORT
                self.breakout_idx = i
                self.fail_count = 0
                self.pattern_level = {"resistance": resistance, "support": support}
                if cfg.allow_first_breakout_probe and (ema <= 0 or close < ema):
                    self._emit(i, -1, sup, atr, "first")

        elif self.state == self.BROKEN_LONG:
            assert self.pattern_level is not None
            if close < self.pattern_level["resistance"]:
                self.fail_count += 1
                if self.fail_count >= cfg.fail_confirm_bars:
                    self.state = self.FAILED_LONG
                    # 假突破确认失败 → 平掉试探仓
                    if cfg.allow_first_breakout_probe:
                        self._emit(
                            i, 1, self.pattern_level["resistance"], atr, "fail_exit"
                        )
            else:
                self.fail_count = 0

        elif self.state == self.BROKEN_SHORT:
            assert self.pattern_level is not None
            if close > self.pattern_level["support"]:
                self.fail_count += 1
                if self.fail_count >= cfg.fail_confirm_bars:
                    self.state = self.FAILED_SHORT
                    if cfg.allow_first_breakout_probe:
                        self._emit(
                            i, -1, self.pattern_level["support"], atr, "fail_exit"
                        )
            else:
                self.fail_count = 0

        elif self.state == self.FAILED_LONG:
            assert self.pattern_level is not None
            if self.breakout_idx is not None and i - self.breakout_idx < cfg.second_break_min_gap:
                return
            if close > self.pattern_level["resistance"] * (1 + thr):
                if ema > 0 and close > ema:
                    self._emit(i, 1, self.pattern_level["resistance"], atr, "second")
                self._reset()

        elif self.state == self.FAILED_SHORT:
            assert self.pattern_level is not None
            if self.breakout_idx is not None and i - self.breakout_idx < cfg.second_break_min_gap:
                return
            if close < self.pattern_level["support"] * (1 - thr):
                if ema > 0 and close < ema:
                    self._emit(i, -1, self.pattern_level["support"], atr, "second")
                self._reset()


def _simulate_positions(
    df: pd.DataFrame,
    events: list[tuple[int, int, float, float, str]],
    cfg: SignalConfig,
) -> tuple[int, float, float, int, bool, str, EntryTier, bool, int, str]:
    """回放事件，得到最后一根持仓状态。

    Returns
    -------
    direction, entry_level, stop, bars_in, is_fresh, reason,
    entry_tier, order_now, order_side, order_kind
    """
    atr_s = (
        df["atr"]
        if "atr" in df.columns
        else ATR(df["high"], df["low"], df["close"], cfg.atr_period)
    )
    n = len(df)
    # idx -> list of events that bar (order: fail_exit before first/second)
    by_i: dict[int, list[tuple[int, float, float, str]]] = {}
    for idx, side, level, atr, kind in events:
        by_i.setdefault(idx, []).append((side, level, atr, kind))

    pos = 0
    tier: EntryTier = "none"
    entry_level = 0.0
    stop = 0.0
    entry_i = -1
    extreme = 0.0
    last_order_now = False
    last_order_side = 0
    last_order_kind = ""
    last_reason = "flat"

    for i in range(n):
        close = float(df.at[i, "close"])
        high = float(df.at[i, "high"])
        low = float(df.at[i, "low"])
        atr_i = float(atr_s.iloc[i]) if np.isfinite(atr_s.iloc[i]) else 0.0
        order_now = False
        order_side = 0
        order_kind = ""

        # ---- 止损（试探仓用更紧的 stop 参数）----
        if pos != 0:
            if pos == 1:
                extreme = max(extreme, high)
                if tier == "first_probe":
                    # 试探：止损不抬到移动止损那么宽，贴边界/紧 ATR
                    trail = extreme - cfg.first_probe_stop_atr * atr_i if atr_i > 0 else stop
                    stop = max(stop, trail) if cfg.first_probe_use_trail else stop
                else:
                    trail = extreme - cfg.trail_atr_mult * atr_i if atr_i > 0 else stop
                    stop = max(stop, trail)
                if close < stop or low < stop:
                    pos = 0
                    tier = "none"
                    entry_i = -1
                    last_reason = "stop_exit"
            else:
                extreme = min(extreme, low)
                if tier == "first_probe":
                    trail = extreme + cfg.first_probe_stop_atr * atr_i if atr_i > 0 else stop
                    stop = min(stop, trail) if cfg.first_probe_use_trail else stop
                else:
                    trail = extreme + cfg.trail_atr_mult * atr_i if atr_i > 0 else stop
                    stop = min(stop, trail)
                if close > stop or high > stop:
                    pos = 0
                    tier = "none"
                    entry_i = -1
                    last_reason = "stop_exit"

        # ---- 事件 ----
        for side, level, atr_sig, kind in by_i.get(i, []):
            atr_use = atr_sig if atr_sig > 0 else atr_i
            if kind == "fail_exit":
                if pos != 0 and tier == "first_probe":
                    pos = 0
                    tier = "none"
                    entry_i = -1
                    last_reason = "first_probe_fail_exit"
                continue

            if kind == "first":
                if not cfg.allow_first_breakout_probe:
                    continue
                # 已有二次满仓则忽略
                if pos != 0 and tier == "second_full":
                    continue
                pos = int(side)
                tier = "first_probe"
                entry_level = float(level)
                entry_i = i
                order_now = True
                order_side = pos
                order_kind = "first_probe"
                last_reason = "first_probe_entry"
                if pos == 1:
                    stop = entry_level - cfg.first_probe_stop_atr * atr_use
                    extreme = high
                else:
                    stop = entry_level + cfg.first_probe_stop_atr * atr_use
                    extreme = low
                continue

            if kind == "second":
                # 主仓：二次突破满仓（可从试探升级）
                was_probe = pos != 0 and tier == "first_probe" and pos == int(side)
                pos = int(side)
                tier = "second_full"
                entry_level = float(level)
                entry_i = i
                order_now = True
                order_side = pos
                order_kind = "second_full_add" if was_probe else "second_full"
                last_reason = "second_breakout_entry"
                if pos == 1:
                    stop = entry_level - cfg.initial_stop_atr * atr_use
                    extreme = high
                else:
                    stop = entry_level + cfg.initial_stop_atr * atr_use
                    extreme = low

        if i == n - 1:
            last_order_now = order_now
            last_order_side = order_side
            last_order_kind = order_kind

    is_fresh = pos != 0 and entry_i == n - 1
    bars = (n - 1 - entry_i) if pos != 0 and entry_i >= 0 else 0
    if pos == 0:
        reason = last_reason if last_reason in ("stop_exit", "first_probe_fail_exit") else "flat"
        # 若最后一根刚好出场，reason 已设；否则 flat
        if not is_fresh and entry_i < 0:
            reason = "flat" if last_reason not in ("stop_exit", "first_probe_fail_exit") else last_reason
            # 非最后一根出场则最终是 flat
            reason = "flat"
        tier = "none"
    elif is_fresh:
        reason = last_reason
    else:
        reason = "first_probe_hold" if tier == "first_probe" else "second_breakout_hold"

    return (
        pos, entry_level, stop, bars, is_fresh, reason, tier,
        last_order_now, last_order_side, last_order_kind,
    )


def _strength(
    direction: int,
    tier: EntryTier,
    is_fresh: bool,
    price: float,
    entry_level: float,
    atr: float,
    vol_ratio: float,
    cfg: SignalConfig,
) -> float:
    if direction == 0 or tier == "none":
        return 0.0
    dist = abs(price - entry_level)
    atr_term = min(1.0, dist / max(atr, 1e-9) / 1.2) if atr > 0 else 0.3
    vol_term = (
        min(1.0, max(0.0, (vol_ratio - 1.0) / 1.2)) if np.isfinite(vol_ratio) else 0.3
    )
    base = 0.50 * atr_term + 0.30 * vol_term + 0.20
    if tier == "second_full":
        base = max(base, 0.55 if is_fresh else 0.45)
        if not is_fresh:
            base *= 0.90
    else:
        # 首次试探：质量分再乘 scale（仓位层用 strength 控风险预算）
        base = max(base, 0.40 if is_fresh else 0.35)
        base *= cfg.first_probe_strength_scale
        # 保证有一个很小但非零的 strength
        base = max(base, cfg.strength_floor * cfg.first_probe_strength_scale)
    return float(min(cfg.strength_ceil, max(0.05, base)))


def evaluate_second_breakout(
    symbol: str,
    df: pd.DataFrame,
    cfg: SignalConfig | None = None,
) -> BreakoutSnapshot:
    """主信号：二次突破满仓；可选首次突破试探小仓。"""
    cfg = cfg or SignalConfig()
    empty = BreakoutSnapshot(
        symbol=symbol, direction=0, strength=0.0, price=0.0, atr=0.0,
        daily_vol=0.012, bar_time="", reason="no_data", engine_state="IDLE",
    )
    min_bars = cfg.pattern_lookback + cfg.atr_period + cfg.second_break_max_gap + 30
    if df is None or len(df) < min_bars:
        return empty

    work = df.copy()
    for c in ("open", "high", "low", "close", "volume"):
        work[c] = pd.to_numeric(work[c], errors="coerce")
    work = work.dropna(subset=["open", "high", "low", "close"]).reset_index(drop=True)
    if len(work) < min_bars:
        return empty

    engine = SecondBreakoutEngine(work, cfg)
    engine.run()
    work = engine.df

    (
        direction, entry_level, stop, bars_in, is_fresh, reason, tier,
        order_now, order_side, order_kind,
    ) = _simulate_positions(work, engine.events, cfg)

    i = len(work) - 1
    close = float(work.at[i, "close"])
    atr_i = float(work.at[i, "atr"]) if np.isfinite(work.at[i, "atr"]) else 0.0
    vol_ratio = (
        float(work.at[i, "vol_ratio"]) if np.isfinite(work.at[i, "vol_ratio"]) else 1.0
    )
    bar_time = ""
    if "datetime" in work.columns:
        bar_time = str(pd.Timestamp(work.at[i, "datetime"]))

    strength = _strength(
        direction, tier, is_fresh, close, entry_level, atr_i, vol_ratio, cfg
    )
    daily_vol = estimate_daily_vol_from_60m(work["close"])

    pl = engine.pattern_level or {}
    last_ago = None
    last_side = None
    seconds = [e for e in engine.events if e[4] == "second"]
    firsts = [e for e in engine.events if e[4] == "first"]
    if seconds:
        last_idx, last_side, _, _, _ = seconds[-1]
        last_ago = i - last_idx

    return BreakoutSnapshot(
        symbol=symbol,
        direction=int(direction),
        strength=strength,
        price=close,
        atr=atr_i,
        daily_vol=daily_vol,
        bar_time=bar_time,
        reason=reason,
        vol_ratio=vol_ratio,
        entry_level=entry_level if direction != 0 else None,
        exit_level=stop if direction != 0 else None,
        bars_in_trade=bars_in,
        engine_state=engine.state,
        is_fresh_entry=is_fresh,
        entry_tier=tier,
        order_now=order_now,
        order_side=order_side,
        order_kind=order_kind,
        pattern_resistance=pl.get("resistance"),
        pattern_support=pl.get("support"),
        hist_signal_count=len(seconds),
        hist_first_count=len(firsts),
        last_signal_bars_ago=last_ago,
        last_signal_side=last_side,
    )
