"""核心数据类型。"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class SignalSide(str, Enum):
    """信号方向。NONE 表示无信号。"""

    NONE = "none"
    LONG = "long"
    SHORT = "short"


@dataclass
class Zone:
    """成交密集区（支撑/阻力）。

    source 标记来源，多周期系统合并密集区与 swing 点时区分：
        "volume"     — 成交量加权密集区（levels.detect_volume_zones）
        "swing_high" — swing 波峰构造的阻力区（htf_zones.detect_swing_zones）
        "swing_low"  — swing 波谷构造的支撑区
    """

    center: float
    lower: float
    upper: float
    strength: float = 0.0  # 成交量占比 0~1；swing zone 给固定值不参与排序竞争
    source: str = "volume"

    def __repr__(self) -> str:
        return f"Zone({self.center:.2f} [{self.lower:.2f}-{self.upper:.2f}] s={self.strength:.2f} {self.source})"


@dataclass
class TradeLevels:
    """一笔交易的入场/止损/目标价位。"""

    entry: float
    stop: float
    target: float

    @property
    def reward_risk(self) -> float:
        """盈亏比 = 潜在盈利 / 潜在亏损（绝对值）。"""
        risk = abs(self.entry - self.stop)
        reward = abs(self.target - self.entry)
        return reward / risk if risk > 0 else 0.0


@dataclass
class Signal:
    """假突破反转信号。"""

    side: SignalSide
    is_valid: bool
    levels: TradeLevels | None = None
    pattern: str = ""        # "Spring" / "Upthrust" / ""
    reason: str = ""         # 人类可读的触发原因
    zone: Zone | None = None  # 触发所依据的密集区
    volume_confirm: bool = False
    failure_score: float = 0.0   # 突破失败强度(0-100)：快速反击+不创新极值+2B
    metadata: dict = field(default_factory=dict)

    @classmethod
    def none(cls, reason: str = "无信号") -> Signal:
        return cls(side=SignalSide.NONE, is_valid=False, reason=reason)


@dataclass
class PatternResult:
    """15m K 线形态检测结果（多周期入场的低周期确认）。

    name:   "engulfing"（吞没）/ "merged"（合并K实体）/
            "ltf_spring"（15m假跌破支撑后收回做多）/
            "ltf_upthrust"（15m假突破阻力后收回做空）
    bar_idx: 形态确认根在 df 中的位置（末根索引）
    entry:  形态确认根的收盘价
    stop_ref: 止损参考价（假突破模式=刺穿极值，供 mtf_signal 组装止损）
    failure_score: 假突破失败强度(0-100)；纯形态模式为 0
    reason: 人类可读的命中描述
    """

    side: SignalSide
    name: str
    bar_idx: int
    entry: float
    reason: str = ""
    stop_ref: float = 0.0
    failure_score: float = 0.0
