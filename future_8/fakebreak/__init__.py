"""fakebreak — 假突破反转策略核心包。

信号管线：indicators → trend(趋势闸) → levels(密集区) → signal(Spring/Upthrust)
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
