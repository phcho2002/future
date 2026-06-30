from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import pandas as pd


class TrendDirection(str, Enum):
    BULL = "bull"
    BEAR = "bear"
    SIDEWAYS = "sideways"
    UNKNOWN = "unknown"


class MarketRegime(str, Enum):
    BULL_TREND = "bull_trend"
    BEAR_TREND = "bear_trend"
    NARROW_CHANNEL = "narrow_channel"
    WIDE_CHANNEL = "wide_channel"
    TRADING_RANGE = "trading_range"
    UNKNOWN = "unknown"


class ChannelType(str, Enum):
    PARALLEL = "parallel_channel"
    CONVERGING_WEDGE = "converging_wedge"
    EXPANDING_TRIANGLE = "expanding_triangle"
    PARABOLIC_WEDGE = "parabolic_wedge"
    THREE_PUSH_NON_WEDGE = "three_push_non_wedge"
    UNKNOWN = "unknown"


class SignalSide(str, Enum):
    LONG = "long"
    SHORT = "short"
    NONE = "none"


@dataclass(frozen=True)
class MarketState:
    regime: MarketRegime
    direction: TrendDirection
    hh_count: int = 0
    hl_count: int = 0
    ll_count: int = 0
    lh_count: int = 0
    overlap_ratio: float = 0.0
    range_inside_count: int = 0
    allow_wedge_reversal: bool = False
    reason: str = ""


@dataclass(frozen=True)
class ChannelAnalysis:
    channel_type: ChannelType
    upper_slope: float | None = None
    lower_slope: float | None = None
    slope_diff_deg: float | None = None
    reason: str = ""
    # 突破判定边界价格：由 channels.py 用窗口内 high/low 回归拟合后取末点值。
    # 上轨（阻力）/ 下轨（支撑），供 signals.py 判定收盘是否突破。
    upper_line_price: float | None = None
    lower_line_price: float | None = None


@dataclass(frozen=True)
class Push:
    start_idx: int
    end_idx: int
    direction: TrendDirection
    start_price: float
    end_price: float
    net_move: float
    atr_multiple: float
    bars: int
    avg_move_per_bar: float
    strong_body_ratio: float
    long_wick_ratio: float
    volume_mean: float
    slope: float
    overlap_ratio: float


@dataclass(frozen=True)
class PushSet:
    pushes: list[Push] = field(default_factory=list)
    pullbacks: list[tuple[int, int]] = field(default_factory=list)
    exhaustion_score: float | None = None
    exhaustion_details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TradeLevels:
    entry: float | None = None
    stop: float | None = None
    target_1: float | None = None
    target_2: float | None = None
    position_size: float | None = None
    reward_risk: float | None = None


@dataclass(frozen=True)
class TradeSignal:
    side: SignalSide = SignalSide.NONE
    is_valid: bool = False
    entry_reason: str = ""
    levels: TradeLevels = field(default_factory=TradeLevels)
    reverse_on_failure: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AnalysisResult:
    data: pd.DataFrame
    market_state: MarketState
    channel: ChannelAnalysis
    push_set: PushSet
    signal: TradeSignal
