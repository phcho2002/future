"""fakebreak — 假突破反转策略核心包。

主路径（单周期）：indicators → trend → levels → signal(Spring/Upthrust)

多周期(60m+15m) 策略作为旁支保留在 mtf_signal.py / patterns.py / htf_zones.py，
不在此处强导入——需要时显式 ``from fakebreak.mtf_signal import generate_mtf_signal``。
"""
from fakebreak.config import (
    FakeBreakConfig,
    load_config,
    load_universe,
    candidate_symbols,
    TIER1,
    TIER2,
    CANDIDATES,
    EXCLUDED,
)
from fakebreak.types import Signal, TradeLevels, SignalSide, Zone

__all__ = [
    "FakeBreakConfig",
    "load_config",
    "load_universe",
    "candidate_symbols",
    "TIER1",
    "TIER2",
    "CANDIDATES",
    "EXCLUDED",
    "Signal",
    "TradeLevels",
    "SignalSide",
    "Zone",
]
