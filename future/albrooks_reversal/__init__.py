"""
Al Brooks Three Push Reversal Quantitative Framework
=====================================================

A comprehensive quantitative trading system based on Al Brooks'
price action methodology for detecting three-push reversal patterns.

Key Components:
- Three Push Structure Detection (swing points, push sequences, momentum decay)
- Exhaustion Analysis (RSI/MACD divergence, ATR contraction)
- Candlestick Pattern Recognition (Pin Bar, Engulfing, Outside Bar)
- Trend Context (EMA20/50/200 alignment)
- Reversal Probability Model (0-100%)
- Risk Metrics (Expected Move, Expected Drawdown, Risk/Reward Ratio)

Author: Quantitative Framework
Methodology: Al Brooks - Three Push Reversal Pattern
"""

__version__ = "1.0.0"
__author__ = "Al Brooks Reversal Framework"

from .engine import ReversalEngine, EngineConfig
from .model import ReversalProbabilityModel
from .risk import RiskCalculator
from .features import (
    SwingDetector,
    ThreePushDetector,
    ExhaustionAnalyzer,
    CandlestickAnalyzer,
    TrendAnalyzer,
)

__all__ = [
    "ReversalEngine",
    "EngineConfig",
    "ReversalProbabilityModel",
    "RiskCalculator",
    "SwingDetector",
    "ThreePushDetector",
    "ExhaustionAnalyzer",
    "CandlestickAnalyzer",
    "TrendAnalyzer",
]
