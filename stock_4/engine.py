"""二次突破状态机（假突破→真突破）—— A 股日线做多单向版。

移植自 future_bb/backtest_30m.py 的 SecondBreakoutEngine，简化为仅做多
（A 股不可做空），删除所有 PRIMED_SHORT / BROKEN_SHORT / FAILED_SHORT 逻辑。

四阶段状态流转:
    IDLE       : 无蓄势形态，等待
    PRIMED     : 检测到蓄势形态（箱体或收敛楔形），记录阻力位，等首次向上突破
    BROKEN     : 已首次向上突破（可能是假突破），等失败（收盘跌回阻力位下方）
    FAILED     : 假突破确认（跌回形态内 >= fail_confirm_bars 根），等二次向上突破
                 → 收盘再次突破阻力位 + close > EMA200 → 产出做多入场信号 → IDLE
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

import indicators as ind


@dataclass
class StrategyParams:
    """策略参数（从 config.yaml 的 strategy 段加载）。"""

    pattern_lookback: int = 20
    box_range_max: float = 0.12
    box_touch_min: int = 2
    box_touch_tol: float = 0.01
    wedge_atr_shrink: float = 0.8
    wedge_range_shrink: float = 0.8
    breakout_threshold: float = 0.01
    fail_confirm_bars: int = 2
    second_break_max_gap: int = 20
    second_break_min_gap: int = 2
    ema_period: int = 200
    atr_period: int = 14
    use_ma_filter: bool = False
    ma_short: int = 20
    ma_long: int = 60

    @classmethod
    def from_dict(cls, d: dict) -> "StrategyParams":
        known = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class Signal:
    """入场信号（做多）。"""

    idx: int              # 二次突破确认的 K 线序号
    date: object          # 该 K 线的日期（pd.Timestamp 或 datetime）
    close: float          # 该 K 线收盘价
    resistance: float     # 突破阻力位
    atr: float            # 该 K 线 ATR
    ema200: float         # 该 K 线 EMA200
    box_range_pct: float  # 蓄势期波幅（用于信号质量评估）

    @property
    def side(self) -> int:
        return 1  # 仅做多


class SecondBreakoutEngine:
    """逐 K 线状态机：跟踪"蓄势→首次突破→失败→二次突破"四阶段，产出做多信号。

    用法:
        engine = SecondBreakoutEngine(df, params)
        signals = engine.run()
    """

    # 状态常量
    IDLE = "IDLE"
    PRIMED = "PRIMED"       # 蓄势形态就位，等首次突破
    BROKEN = "BROKEN"       # 已首次突破，等失败确认
    FAILED = "FAILED"       # 假突破确认，等二次突破 → 入场

    def __init__(self, df: pd.DataFrame, params: StrategyParams):
        self.df = df.reset_index(drop=True).copy()
        self.n = len(df)
        self.p = params
        self.state = self.IDLE
        self.resistance: float | None = None   # 形态阻力位
        self.support: float | None = None      # 形态支撑位（记录用）
        self.breakout_idx: int | None = None   # 首次突破的 K 线序号
        self.fail_count: int = 0               # 回到形态内的连续根数
        self.signals: list[Signal] = []

        self._compute_patterns()

    def _compute_patterns(self):
        """向量化预计算蓄势形态、形态边界、ATR、EMA、MA。"""
        df = self.df
        high, low, close = df["high"], df["low"], df["close"]
        N = self.p.pattern_lookback

        # 蓄势形态：箱体 OR 收敛楔形
        is_box = ind.detect_box(
            high, low, period=N,
            range_max=self.p.box_range_max, touch_min=self.p.box_touch_min,
            touch_tol=self.p.box_touch_tol,
        )
        is_wedge = ind.detect_wedge(
            high, low, close, period=N,
            atr_shrink=self.p.wedge_atr_shrink, range_shrink=self.p.wedge_range_shrink,
        )
        df["setup_pattern"] = (is_box | is_wedge).fillna(False)

        # 形态边界：蓄势窗口内最高（阻力）/最低（支撑），shift(1) 不含当日
        df["resistance_level"] = high.rolling(N).max().shift(1)
        df["support_level"] = low.rolling(N).min().shift(1)
        # 蓄势波幅（信号质量评估用）
        df["box_range_pct"] = (df["resistance_level"] - df["support_level"]) / df["support_level"]

        # ATR / EMA
        df["atr"] = ind.ATR(high, low, close, self.p.atr_period)
        df["ema200"] = ind.EMA(close, self.p.ema_period)

        # 可选 MA 多头排列过滤
        if self.p.use_ma_filter:
            df["ma_short"] = ind.SMA(close, self.p.ma_short)
            df["ma_long"] = ind.SMA(close, self.p.ma_long)

        self.df = df

    def run(self) -> list[Signal]:
        """逐 K 线推进状态机，返回入场信号列表。"""
        for i in range(self.n):
            self._step(i)
        return self.signals

    def _step(self, i: int):
        df = self.df
        p = self.p
        if i < p.pattern_lookback:
            return
        row = df.iloc[i]
        close = float(row["close"])
        atr = float(row["atr"]) if np.isfinite(row["atr"]) else 0.0
        has_pattern = bool(row["setup_pattern"])
        resistance = float(row["resistance_level"]) if np.isfinite(row["resistance_level"]) else 0.0
        ema200 = float(row["ema200"]) if np.isfinite(row.get("ema200", np.nan)) else 0.0
        gap = i - self.breakout_idx if self.breakout_idx is not None else 0

        # ---- 超时回退（首次突破后太久没二次突破）----
        if self.state in (self.BROKEN, self.FAILED):
            if gap > p.second_break_max_gap:
                self._reset()
                # 不 return，当前 K 线可能同时是新蓄势形态

        if self.state == self.IDLE:
            # 检测蓄势形态，进入 PRIMED
            if has_pattern and resistance > 0:
                self.resistance = resistance
                self.support = (
                    float(row["support_level"])
                    if np.isfinite(row["support_level"])
                    else 0.0
                )
                self.state = self.PRIMED

        elif self.state == self.PRIMED:
            # 等待首次向上突破
            if not has_pattern and self._pattern_expired(i):
                self._reset()
                return
            if close > self.resistance * (1 + p.breakout_threshold):
                self.state = self.BROKEN
                self.breakout_idx = i
                self.fail_count = 0

        elif self.state == self.BROKEN:
            # 等待失败：收盘跌回阻力位下方，连续 fail_confirm_bars 根
            if close < self.resistance:
                self.fail_count += 1
                if self.fail_count >= p.fail_confirm_bars:
                    self.state = self.FAILED
            else:
                self.fail_count = 0   # 中途又站上，重新计数

        elif self.state == self.FAILED:
            # 等二次向上突破 → 入场做多
            if i - self.breakout_idx < p.second_break_min_gap:
                return
            if close > self.resistance * (1 + p.breakout_threshold):
                # 趋势过滤：EMA200 > 0 且 close 在 EMA200 之上
                trend_ok = ema200 > 0 and close > ema200
                # 可选 MA 多头排列过滤
                ma_ok = True
                if p.use_ma_filter:
                    ma_s = float(row.get("ma_short", 0))
                    ma_l = float(row.get("ma_long", 0))
                    ma_ok = close > ma_s > ma_l
                if trend_ok and ma_ok:
                    box_rng = (
                        float(row["box_range_pct"])
                        if np.isfinite(row.get("box_range_pct", np.nan))
                        else 0.0
                    )
                    self.signals.append(
                        Signal(
                            idx=i,
                            date=row["date"],
                            close=close,
                            resistance=self.resistance,
                            atr=atr,
                            ema200=ema200,
                            box_range_pct=box_rng,
                        )
                    )
                self._reset()

    def _pattern_expired(self, i: int) -> bool:
        """蓄势形态是否过期（超过 2 倍窗口未突破）。"""
        ref = self.breakout_idx if self.breakout_idx is not None else i
        return i - ref > self.p.pattern_lookback * 2

    def _reset(self):
        self.state = self.IDLE
        self.resistance = None
        self.support = None
        self.breakout_idx = None
        self.fail_count = 0
