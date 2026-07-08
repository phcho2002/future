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
    """成交密集区（支撑/阻力）。"""

    center: float
    lower: float
    upper: float
    strength: float  # 成交量占比 0~1

    def __repr__(self) -> str:
        return f"Zone({self.center:.2f} [{self.lower:.2f}-{self.upper:.2f}] s={self.strength:.2f})"


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
