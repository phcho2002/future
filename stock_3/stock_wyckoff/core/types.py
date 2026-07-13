from dataclasses import dataclass, field
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class WyckoffPhase:
    """威科夫阶段识别结果"""

    phase: str = "unknown"          # "Phase_A", "Phase_B", "Phase_C", "Phase_D", "Phase_E"
    stage: str = "unknown"            # "accumulation" / "distribution" / "unknown"
    description: str = ""           # 中文描述


@dataclass(frozen=True)
class RangeAnalysis:
    """价格区间收敛分析结果"""

    support_level: float = 0.0       # 支撑位（反复测试的低点）
    resistance_level: float = 0.0    # 阻力位（反复测试的高点）
    test_count: int = 0              # 测试次数
    contraction_ratio: float = 0.0   # 波动幅度收敛比率
    core_price: float = 0.0          # 核心价位（放量K线实体中点或开盘价）
    is_valid: bool = False
    reason: str = ""                 # 无效原因


@dataclass(frozen=True)
class VolumeAnalysis:
    """成交量分析结果"""

    volume_trend: str = "unknown"          # "decreasing" / "increasing" / "flat"
    climax_volume: float = 0.0             # 高潮成交量
    avg_recent_volume: float = 0.0         # 近期平均成交量
    effort_result_divergence: bool = False # 努力与结果背离
    volume_decline_ratio: float = 0.0      # 成交量递减比率


@dataclass(frozen=True)
class StopBehavior:
    """停止行为确认结果"""

    has_stop: bool = False
    stop_bar_idx: int | None = None
    wick_ratio: float = 0.0
    volume_ratio: float = 0.0    # 相对前5日均量倍数
    direction: str = ""        # "upper" / "lower"
    description: str = ""        # 中文描述


@dataclass(frozen=True)
class TradeLevels:
    """交易价位"""

    entry: float | None = None
    stop: float | None = None        # 极值点（被反复测试的价位）
    target: float | None = None      # 上方首次放量高量柱对应的价位


@dataclass(frozen=True)
class WyckoffSignal:
    """威科夫交易信号"""

    side: str = "none"                # "long" / "short" / "none"
    is_valid: bool = False
    phase: WyckoffPhase = field(default_factory=WyckoffPhase)
    entry_reason: str = ""            # 威科夫术语解释
    levels: TradeLevels = field(default_factory=TradeLevels)
    rsi_value: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AnalysisResult:
    """完整分析结果"""

    data: pd.DataFrame = field(default_factory=pd.DataFrame)
    phase: WyckoffPhase = field(default_factory=WyckoffPhase)
    range_analysis: RangeAnalysis = field(default_factory=RangeAnalysis)
    volume_analysis: VolumeAnalysis = field(default_factory=VolumeAnalysis)
    stop_behavior: StopBehavior = field(default_factory=StopBehavior)
    signal: WyckoffSignal = field(default_factory=WyckoffSignal)
