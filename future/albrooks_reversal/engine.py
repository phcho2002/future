"""
Al Brooks Three Push Reversal Engine
====================================
Main orchestrator integrating all components of the reversal detection system.

Usage:
    import numpy as np
    from albrooks_reversal import ReversalEngine

    engine = ReversalEngine()
    result = engine.analyze(high, low, close, open_=open_)
    # result contains:
    #   - reversal probability (0-100%)
    #   - risk metrics (expected move, drawdown, RR ratio)
    #   - detailed feature breakdowns

The engine processes OHLC data arrays and outputs a comprehensive
reversal assessment suitable for both live trading and backtesting.
"""

import numpy as np
from typing import Dict, List, Optional, Tuple, Union
from dataclasses import dataclass, field
import warnings

from .features import (
    SwingDetector,
    ThreePushDetector,
    ExhaustionAnalyzer,
    CandlestickAnalyzer,
    TrendAnalyzer,
    ThreePushPattern,
)
from .model import ReversalProbabilityModel, ReversalProbabilityResult
from .risk import RiskCalculator, RiskMetrics


@dataclass
class ReversalSignal:
    """
    Complete reversal analysis output.

    Contains everything needed for a trading decision:
    - Probability assessment
    - Risk metrics
    - Feature breakdowns
    - Entry/exit levels
    """
    # Core signal
    probability: float                     # 0-100%
    direction: str                         # 'bullish' or 'bearish'
    confidence: str                        # 'low', 'medium', 'high', 'very_high'

    # Risk metrics
    entry_price: float
    target_price: float
    stop_loss: float
    expected_move: float
    expected_move_pct: float
    expected_drawdown: float
    expected_drawdown_pct: float
    risk_reward_ratio: float

    # Feature scores (0-1)
    structure_score: float
    exhaustion_score: float
    candle_score: float
    trend_score: float

    # Pattern details
    pattern_detected: bool
    push_count: int = 0
    wedge_type: str = ''
    momentum_decay: float = 0.0

    # Detailed breakdowns
    exhaustion_detail: Dict = field(default_factory=dict)
    candle_detail: Dict = field(default_factory=dict)
    trend_detail: Dict = field(default_factory=dict)

    # Metadata
    bar_index: int = -1
    timestamp: Optional[int] = None

    def is_actionable(self, min_probability: float = 60.0, min_rr: float = 1.5) -> bool:
        """Whether this signal meets minimum criteria for action."""
        return (
            self.pattern_detected and
            self.probability >= min_probability and
            self.risk_reward_ratio >= min_rr
        )

    def summary(self) -> str:
        """One-line signal summary."""
        return (
            f"[{self.confidence.upper()}] {self.direction.upper()} Reversal | "
            f"Prob: {self.probability:.1f}% | "
            f"RR: {self.risk_reward_ratio:.2f}:1 | "
            f"Target: {self.target_price:.4f} | "
            f"Stop: {self.stop_loss:.4f}"
        )


@dataclass
class EngineConfig:
    """Configuration for the ReversalEngine."""

    # Swing detector
    swing_window: int = 5
    swing_min_strength: float = 0.3

    # Three push detector
    min_pushes: int = 2
    max_pushes: int = 5
    momentum_decay_threshold: float = 0.15

    # Exhaustion
    rsi_period: int = 14
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    atr_period: int = 14
    divergence_lookback: int = 20

    # Candlestick
    pin_bar_ratio: float = 2.0
    body_pct_threshold: float = 0.003

    # Trend
    ema_periods: Tuple[int, int, int] = (20, 50, 200)

    # Probability model
    model_weights: Optional[Dict[str, float]] = None
    model_bias: float = -2.0
    model_calibration: float = 4.5

    # Risk
    atr_multiplier: float = 2.0
    max_rr_ratio: float = 10.0


class ReversalEngine:
    """
    Main engine for Al Brooks Three Push Reversal analysis.

    Integrates swing detection, pattern recognition, exhaustion analysis,
    candlestick patterns, trend context, probability modeling, and risk metrics
    into a unified analysis pipeline.

    Parameters
    ----------
    config : EngineConfig, optional
        Engine configuration. Uses sensible defaults if omitted.

    Examples
    --------
    >>> engine = ReversalEngine()
    >>> # Assume OHLC data as numpy arrays
    >>> result = engine.analyze(high, low, close, open_)
    >>> print(f"Reversal Probability: {result.probability:.1f}%")
    >>> print(f"R/R Ratio: {result.risk_reward_ratio:.2f}")
    """

    def __init__(self, config: Optional[EngineConfig] = None):
        self.config = config or EngineConfig()
        cfg = self.config

        # Initialize components
        self.swing_detector = SwingDetector(
            window=cfg.swing_window,
            min_strength=cfg.swing_min_strength,
        )

        self.push_detector = ThreePushDetector(
            min_pushes=cfg.min_pushes,
            max_pushes=cfg.max_pushes,
            momentum_decay_threshold=cfg.momentum_decay_threshold,
        )

        self.exhaustion_analyzer = ExhaustionAnalyzer(
            rsi_period=cfg.rsi_period,
            macd_fast=cfg.macd_fast,
            macd_slow=cfg.macd_slow,
            macd_signal=cfg.macd_signal,
            atr_period=cfg.atr_period,
            divergence_lookback=cfg.divergence_lookback,
        )

        self.candle_analyzer = CandlestickAnalyzer(
            pin_bar_ratio=cfg.pin_bar_ratio,
            body_pct_threshold=cfg.body_pct_threshold,
        )

        self.trend_analyzer = TrendAnalyzer(
            ema_periods=cfg.ema_periods,
        )

        self.probability_model = ReversalProbabilityModel(
            weights=cfg.model_weights,
            bias=cfg.model_bias,
            calibration_factor=cfg.model_calibration,
        )

        self.risk_calculator = RiskCalculator(
            atr_period=cfg.atr_period,
            atr_multiplier=cfg.atr_multiplier,
            max_rr_ratio=cfg.max_rr_ratio,
        )

    def analyze(
        self,
        high: np.ndarray,
        low: np.ndarray,
        close: np.ndarray,
        open_: Optional[np.ndarray] = None,
        volume: Optional[np.ndarray] = None,
    ) -> ReversalSignal:
        """
        Run complete reversal analysis on price data.

        Parameters
        ----------
        high : np.ndarray
            High prices.
        low : np.ndarray
            Low prices.
        close : np.ndarray
            Close prices.
        open_ : np.ndarray, optional
            Open prices. If None, uses close shifted by 1.
        volume : np.ndarray, optional
            Volume data (reserved for future use).

        Returns
        -------
        ReversalSignal with complete analysis.

        Raises
        ------
        ValueError
            If input arrays have different lengths or are too short.
        """
        # Validate inputs
        self._validate_inputs(high, low, close, open_)

        n = len(close)

        # Use close as open if not provided (approximation)
        if open_ is None:
            open_ = np.roll(close, 1)
            open_[0] = close[0]

        # 1. Detect swing points
        swing_highs, swing_lows = self.swing_detector.detect(high, low, close)

        # 2. Detect three-push patterns
        patterns = self.push_detector.detect(
            swing_highs, swing_lows, high, low, close
        )

        # Select the most recent/best pattern
        best_pattern = self._select_best_pattern(patterns)

        # 3. Calculate structure score from pattern
        structure_score = self._calculate_structure_score(best_pattern)

        # 4. Analyze exhaustion
        exhaustion_info = self.exhaustion_analyzer.analyze(
            high, low, close, best_pattern
        )
        exhaustion_score = exhaustion_info['composite_exhaustion']

        # 5. Analyze candlestick patterns
        candle_info = self.candle_analyzer.analyze(
            open_, high, low, close, best_pattern
        )
        candle_score = candle_info['composite_candle']

        # 6. Analyze trend context
        trend_info = self.trend_analyzer.analyze(close, best_pattern)
        trend_score = trend_info['trend_context_score']

        # 7. Calculate reversal probability
        prob_result = self.probability_model.calculate(
            structure_score=structure_score,
            exhaustion_score=exhaustion_score,
            candle_score=candle_score,
            trend_score=trend_score,
            pattern=best_pattern,
            exhaustion_info=exhaustion_info,
            candle_info=candle_info,
            trend_info=trend_info,
        )

        # 8. Calculate risk metrics
        risk_metrics = self.risk_calculator.calculate(
            high, low, close, best_pattern, prob_result.probability
        )

        # 9. Assemble final signal
        signal = self._assemble_signal(
            prob_result, risk_metrics, best_pattern,
            exhaustion_info, candle_info, trend_info,
        )

        return signal

    def analyze_batch(
        self,
        high: np.ndarray,
        low: np.ndarray,
        close: np.ndarray,
        open_: Optional[np.ndarray] = None,
        window_size: int = 200,
        step_size: int = 1,
    ) -> List[ReversalSignal]:
        """
        Run analysis in a rolling window across the data.

        Useful for backtesting and historical analysis.

        Parameters
        ----------
        high, low, close, open_ : np.ndarray
            Full price history.
        window_size : int
            Number of bars in each analysis window.
        step_size : int
            Bars between consecutive analyses.

        Returns
        -------
        List of ReversalSignal objects, one per window position.
        """
        n = len(close)
        signals = []

        for end_idx in range(window_size, n + 1, step_size):
            start_idx = max(0, end_idx - window_size)

            try:
                signal = self.analyze(
                    high[start_idx:end_idx],
                    low[start_idx:end_idx],
                    close[start_idx:end_idx],
                    open_[start_idx:end_idx] if open_ is not None else None,
                )
                signal.bar_index = end_idx - 1
                signals.append(signal)
            except Exception as e:
                warnings.warn(f"Analysis failed at index {end_idx}: {e}")
                continue

        return signals

    def get_actionable_signals(
        self,
        signals: List[ReversalSignal],
        min_probability: float = 60.0,
        min_rr: float = 1.5,
    ) -> List[ReversalSignal]:
        """Filter a list of signals to only actionable ones."""
        return [
            s for s in signals
            if s.is_actionable(min_probability, min_rr)
        ]

    # ── Internal Methods ──────────────────────────────────────────────

    def _validate_inputs(
        self,
        high: np.ndarray,
        low: np.ndarray,
        close: np.ndarray,
        open_: Optional[np.ndarray],
    ) -> None:
        """Validate input arrays."""
        n = len(close)

        if n < 50:
            raise ValueError(
                f"Need at least 50 bars of data, got {n}. "
                "The three-push pattern requires sufficient history."
            )

        if len(high) != n or len(low) != n:
            raise ValueError(
                f"Array length mismatch: close={n}, high={len(high)}, low={len(low)}"
            )

        if open_ is not None and len(open_) != n:
            raise ValueError(
                f"Array length mismatch: close={n}, open={len(open_)}"
            )

        # Check for NaN
        for name, arr in [('close', close), ('high', high), ('low', low)]:
            if np.any(~np.isfinite(arr)):
                raise ValueError(f"Array '{name}' contains NaN or Inf values")

    def _select_best_pattern(
        self, patterns: List[ThreePushPattern]
    ) -> Optional[ThreePushPattern]:
        """
        Select the best pattern from detected ones.

        Criteria (in order):
        1. Most recently completed
        2. Highest quality (momentum_decay × overlap × symmetry)
        """
        if not patterns:
            return None

        # Score each pattern
        def pattern_score(p: ThreePushPattern) -> float:
            recency = p.completion_idx  # Higher = more recent
            quality = (
                0.4 * p.momentum_decay +
                0.3 * p.overlap_score +
                0.3 * p.symmetry_score
            )
            # Normalize recency contribution
            return recency * 0.3 + quality * 100 * 0.7

        return max(patterns, key=pattern_score)

    def _calculate_structure_score(
        self, pattern: Optional[ThreePushPattern]
    ) -> float:
        """Calculate structure score from pattern."""
        if pattern is None:
            return 0.05  # Small baseline

        # Push count bonus (3 is ideal, 2 is early, 4-5 still valid)
        if pattern.push_count == 3:
            count_score = 1.0
        elif pattern.push_count == 2:
            count_score = 0.6
        elif pattern.push_count >= 4:
            count_score = 0.8
        else:
            count_score = 0.3

        # Wedge type bonus
        wedge_bonus = {
            'contracting': 0.2,  # Classic wedge = strongest
            'parallel': 0.1,     # Channel = valid
            'expanding': -0.1,   # Broadening = weaker
        }.get(pattern.wedge_type, 0.0)

        # Combine
        base = (
            0.40 * count_score +
            0.25 * pattern.momentum_decay +
            0.20 * pattern.overlap_score +
            0.15 * pattern.symmetry_score +
            wedge_bonus
        )

        return max(0.0, min(1.0, base))

    def _assemble_signal(
        self,
        prob_result: ReversalProbabilityResult,
        risk_metrics: RiskMetrics,
        pattern: Optional[ThreePushPattern],
        exhaustion_info: Dict,
        candle_info: Dict,
        trend_info: Dict,
    ) -> ReversalSignal:
        """Assemble all results into a ReversalSignal."""

        pattern_detected = pattern is not None

        return ReversalSignal(
            probability=prob_result.probability,
            direction=prob_result.direction,
            confidence=prob_result.confidence,
            entry_price=risk_metrics.entry_price,
            target_price=risk_metrics.target_price,
            stop_loss=risk_metrics.stop_loss,
            expected_move=risk_metrics.expected_move,
            expected_move_pct=risk_metrics.expected_move_pct,
            expected_drawdown=risk_metrics.expected_drawdown,
            expected_drawdown_pct=risk_metrics.expected_drawdown_pct,
            risk_reward_ratio=risk_metrics.risk_reward_ratio,
            structure_score=prob_result.structure_score,
            exhaustion_score=prob_result.exhaustion_score,
            candle_score=prob_result.candle_score,
            trend_score=prob_result.trend_score,
            pattern_detected=pattern_detected,
            push_count=pattern.push_count if pattern else 0,
            wedge_type=pattern.wedge_type if pattern else '',
            momentum_decay=pattern.momentum_decay if pattern else 0.0,
            exhaustion_detail=exhaustion_info,
            candle_detail=candle_info,
            trend_detail=trend_info,
            bar_index=pattern.completion_idx if pattern else -1,
        )
