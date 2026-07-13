"""
Reversal Probability Model
==========================
Aggregates all feature scores into a unified reversal probability (0-100%).

The model combines four signal dimensions:
1. Three Push Structure Score (weight: 0.35)
2. Exhaustion Score (weight: 0.30)
3. Candlestick Confirmation Score (weight: 0.20)
4. Trend Context Score (weight: 0.15)

Each sub-score is 0-1. The weighted sum is passed through a calibrated
sigmoid to produce the final probability.

Mathematical Foundation:
------------------------
P(reversal) = σ(β₀ + β₁·S_structure + β₂·S_exhaustion + β₃·S_candle + β₄·S_trend)

Where σ(x) = 1/(1+e^(-x)) is the sigmoid function.

β₀ (bias) calibrates the base rate
β₁-β₄ are feature weights

The sigmoid output is scaled to 0-100% for direct interpretation.
"""

import numpy as np
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field

from .features import ThreePushPattern


def _sigmoid(x: float) -> float:
    """Numerically stable sigmoid."""
    return 1.0 / (1.0 + np.exp(-np.clip(x, -50, 50)))


@dataclass
class ReversalProbabilityResult:
    """Complete reversal probability assessment."""
    probability: float                    # 0-100%
    direction: str                        # 'bullish' or 'bearish'
    confidence: str                       # 'low', 'medium', 'high', 'very_high'

    # Sub-scores (0-1)
    structure_score: float
    exhaustion_score: float
    candle_score: float
    trend_score: float

    # Feature details
    pattern_info: Optional[Dict] = None
    exhaustion_info: Optional[Dict] = None
    candle_info: Optional[Dict] = None
    trend_info: Optional[Dict] = None

    # Metadata
    raw_logit: float = 0.0
    calibration_applied: bool = True


class ReversalProbabilityModel:
    """
    Multi-factor reversal probability model.

    Aggregates structure, exhaustion, candlestick, and trend features
    into a calibrated probability estimate (0-100%).

    Parameters
    ----------
    weights : Dict[str, float]
        Feature weights. Default weights based on empirical analysis
        of Al Brooks' methodology:
        - structure: 0.35 (the three-push pattern is primary)
        - exhaustion: 0.30 (divergence confirms momentum loss)
        - candle: 0.20 (price action confirmation)
        - trend: 0.15 (contextual factor)

    bias : float, default -2.0
        Sigmoid bias term. Negative bias means the model is conservative
        — strong evidence is needed for high probability.

    calibration_factor : float, default 4.0
        Scales the weighted sum before sigmoid. Higher = steeper transition
        between low and high probability.

    min_probability : float, default 0.0
        Floor for output probability.

    max_probability : float, default 100.0
        Ceiling for output probability.
    """

    # Default weights derived from Al Brooks methodology analysis
    DEFAULT_WEIGHTS = {
        'structure': 0.35,
        'exhaustion': 0.30,
        'candle': 0.20,
        'trend': 0.15,
    }

    def __init__(
        self,
        weights: Optional[Dict[str, float]] = None,
        bias: float = -2.0,
        calibration_factor: float = 4.5,
        min_probability: float = 0.0,
        max_probability: float = 100.0,
    ):
        self.weights = weights or self.DEFAULT_WEIGHTS.copy()
        self.bias = bias
        self.calibration_factor = calibration_factor
        self.min_probability = min_probability
        self.max_probability = max_probability

        # Validate weights
        total = sum(self.weights.values())
        if abs(total - 1.0) > 0.01:
            # Normalize
            for k in self.weights:
                self.weights[k] /= total

    def calculate(
        self,
        structure_score: float,
        exhaustion_score: float,
        candle_score: float,
        trend_score: float,
        pattern: Optional[ThreePushPattern] = None,
        exhaustion_info: Optional[Dict] = None,
        candle_info: Optional[Dict] = None,
        trend_info: Optional[Dict] = None,
    ) -> ReversalProbabilityResult:
        """
        Calculate reversal probability from feature scores.

        Parameters
        ----------
        structure_score : float (0-1)
            Aggregate score from ThreePushDetector.
        exhaustion_score : float (0-1)
            Composite exhaustion score from ExhaustionAnalyzer.
        candle_score : float (0-1)
            Composite candlestick score from CandlestickAnalyzer.
        trend_score : float (0-1)
            Trend context score from TrendAnalyzer.
        pattern : ThreePushPattern, optional
            The detected pattern (for direction and metadata).
        exhaustion_info, candle_info, trend_info : Dict, optional
            Detailed feature outputs for metadata.

        Returns
        -------
        ReversalProbabilityResult with full assessment.
        """
        # Apply interaction effects
        adjusted_scores = self._apply_interactions(
            structure_score, exhaustion_score, candle_score, trend_score
        )

        # Weighted sum (logit)
        logit = self.bias + self.calibration_factor * (
            self.weights['structure'] * adjusted_scores['structure'] +
            self.weights['exhaustion'] * adjusted_scores['exhaustion'] +
            self.weights['candle'] * adjusted_scores['candle'] +
            self.weights['trend'] * adjusted_scores['trend']
        )

        # Convert to probability
        raw_prob = _sigmoid(logit) * 100.0

        # Apply calibration curve
        probability = self._calibrate(raw_prob)

        # Clamp
        probability = max(self.min_probability, min(self.max_probability, probability))

        # Determine direction
        direction = 'neutral'
        if pattern is not None:
            direction = pattern.direction

        # Confidence level
        confidence = self._confidence_level(probability, adjusted_scores)

        return ReversalProbabilityResult(
            probability=round(probability, 2),
            direction=direction,
            confidence=confidence,
            structure_score=round(structure_score, 4),
            exhaustion_score=round(exhaustion_score, 4),
            candle_score=round(candle_score, 4),
            trend_score=round(trend_score, 4),
            pattern_info=self._pattern_summary(pattern) if pattern else None,
            exhaustion_info=exhaustion_info,
            candle_info=candle_info,
            trend_info=trend_info,
            raw_logit=round(logit, 4),
            calibration_applied=True,
        )

    def _apply_interactions(
        self,
        structure: float,
        exhaustion: float,
        candle: float,
        trend: float,
    ) -> Dict[str, float]:
        """
        Apply feature interaction effects.

        Key interactions:
        1. Structure × Exhaustion: Strong structure + divergence = multiplicative effect
        2. Candle × Exhaustion: Confirmation candle at divergence point = boost
        3. Structure × Trend: Counter-trend patterns at key levels = extra weight
        4. Penalty for conflicting signals
        """
        adjusted = {
            'structure': structure,
            'exhaustion': exhaustion,
            'candle': candle,
            'trend': trend,
        }

        # Boost: strong structure + strong exhaustion
        if structure > 0.5 and exhaustion > 0.5:
            boost = (structure * exhaustion) * 0.15
            adjusted['structure'] = min(1.0, structure + boost * 0.5)
            adjusted['exhaustion'] = min(1.0, exhaustion + boost * 0.5)

        # Boost: candle confirmation at exhaustion
        if exhaustion > 0.4 and candle > 0.4:
            boost = (exhaustion * candle) * 0.12
            adjusted['candle'] = min(1.0, candle + boost)

        # Boost: structure + favorable trend context
        if structure > 0.4 and trend > 0.5:
            boost = (structure * trend) * 0.10
            adjusted['trend'] = min(1.0, trend + boost)

        # Penalty: conflicting signals (e.g., strong structure but weak everything else)
        if structure > 0.6 and exhaustion < 0.2 and candle < 0.2:
            adjusted['structure'] *= 0.7  # Reduce structure weight when unconfirmed

        return adjusted

    def _calibrate(self, raw_prob: float) -> float:
        """
        Apply calibration mapping to convert raw sigmoid output
        to well-calibrated probability.

        This corrects for the sigmoid's tendency to produce
        probabilities clustered near 0 or 100.
        """
        # Platt scaling-like calibration
        # Maps raw probability to calibrated space
        # Based on empirical observation of three-push reversal frequencies
        a = 0.85  # Slope
        b = 5.0   # Intercept shift (percentage points)

        calibrated = a * raw_prob + b * (1.0 - raw_prob / 100.0)

        return calibrated

    def _confidence_level(
        self, probability: float, scores: Dict[str, float]
    ) -> str:
        """
        Determine confidence level based on probability and score agreement.
        """
        # Score agreement (how consistent the sub-scores are)
        score_values = [scores[k] for k in scores]
        mean_score = np.mean(score_values)
        std_score = np.std(score_values)

        # High agreement = low variance across sub-scores
        agreement = 1.0 - min(1.0, std_score * 2)

        if probability >= 75 and agreement > 0.6:
            return 'very_high'
        elif probability >= 65 and agreement > 0.4:
            return 'high'
        elif probability >= 50:
            return 'medium'
        else:
            return 'low'

    def _pattern_summary(self, pattern: ThreePushPattern) -> Dict:
        """Create a summary dict from a ThreePushPattern."""
        return {
            'direction': pattern.direction,
            'push_count': pattern.push_count,
            'wedge_type': pattern.wedge_type,
            'momentum_decay': round(pattern.momentum_decay, 4),
            'overlap_score': round(pattern.overlap_score, 4),
            'symmetry_score': round(pattern.symmetry_score, 4),
            'completion_bar': pattern.completion_idx,
            'pushes': [
                {
                    'distance_pct': round(p.distance_pct * 100, 3),
                    'duration': p.duration,
                    'slope': round(p.slope, 6),
                }
                for p in pattern.pushes
            ],
        }

    def batch_calculate(
        self,
        scores_batch: List[Tuple[float, float, float, float]],
        patterns: Optional[List[Optional[ThreePushPattern]]] = None,
    ) -> List[ReversalProbabilityResult]:
        """Calculate reversal probability for a batch of score sets."""
        results = []
        for i, (s, e, c, t) in enumerate(scores_batch):
            pat = patterns[i] if patterns and i < len(patterns) else None
            results.append(self.calculate(s, e, c, t, pattern=pat))
        return results

    def explain(self, result: ReversalProbabilityResult) -> str:
        """
        Generate a human-readable explanation of the probability assessment.

        Parameters
        ----------
        result : ReversalProbabilityResult
            The probability result to explain.

        Returns
        -------
        Multi-line explanation string.
        """
        lines = []
        lines.append(f"═══ Al Brooks Three Push Reversal Assessment ═══")
        lines.append(f"Reversal Probability: {result.probability:.1f}%")
        lines.append(f"Direction: {result.direction.upper()}")
        lines.append(f"Confidence: {result.confidence.upper()}")
        lines.append("")

        lines.append("── Feature Breakdown ──")
        lines.append(f"  Structure Score:     {result.structure_score:.3f}  (weight: {self.weights['structure']:.0%})")
        lines.append(f"  Exhaustion Score:    {result.exhaustion_score:.3f}  (weight: {self.weights['exhaustion']:.0%})")
        lines.append(f"  Candlestick Score:   {result.candle_score:.3f}  (weight: {self.weights['candle']:.0%})")
        lines.append(f"  Trend Context Score: {result.trend_score:.3f}  (weight: {self.weights['trend']:.0%})")
        lines.append("")

        if result.pattern_info:
            pi = result.pattern_info
            lines.append("── Pattern Details ──")
            lines.append(f"  Push Count:     {pi['push_count']}")
            lines.append(f"  Wedge Type:     {pi['wedge_type']}")
            lines.append(f"  Momentum Decay: {pi['momentum_decay']:.3f}")
            lines.append(f"  Symmetry:       {pi['symmetry_score']:.3f}")
            lines.append("  Pushes:")
            for i, p in enumerate(pi['pushes']):
                lines.append(f"    Push {i+1}: {p['distance_pct']:.2f}% over {p['duration']} bars")
            lines.append("")

        if result.exhaustion_info:
            ei = result.exhaustion_info
            lines.append("── Exhaustion Details ──")
            lines.append(f"  RSI Divergence:     {ei.get('rsi_divergence_strength', 'N/A'):.3f}" if isinstance(ei.get('rsi_divergence_strength'), float) else f"  RSI Divergence:     {ei.get('rsi_divergence_strength', 'N/A')}")
            lines.append(f"  MACD Divergence:    {ei.get('macd_divergence_strength', 'N/A')}" if not isinstance(ei.get('macd_divergence_strength'), float) else f"  MACD Divergence:    {ei.get('macd_divergence_strength', 'N/A'):.3f}")
            lines.append(f"  ATR Contraction:    {ei.get('atr_contraction', 'N/A')}" if not isinstance(ei.get('atr_contraction'), float) else f"  ATR Contraction:    {ei.get('atr_contraction', 'N/A'):.3f}")
            lines.append("")

        if result.candle_info:
            ci = result.candle_info
            lines.append("── Candlestick Details ──")
            lines.append(f"  Pin Bar:       {ci.get('pin_bar_score', 'N/A')}" if not isinstance(ci.get('pin_bar_score'), float) else f"  Pin Bar:       {ci.get('pin_bar_score', 'N/A'):.3f}")
            lines.append(f"  Engulfing:     {ci.get('engulfing_score', 'N/A')}" if not isinstance(ci.get('engulfing_score'), float) else f"  Engulfing:     {ci.get('engulfing_score', 'N/A'):.3f}")
            lines.append(f"  Outside Bar:   {ci.get('outside_bar_score', 'N/A')}" if not isinstance(ci.get('outside_bar_score'), float) else f"  Outside Bar:   {ci.get('outside_bar_score', 'N/A'):.3f}")
            lines.append("")

        return "\n".join(lines)
