"""
Feature Calculator Module
=========================
Core signal processing components for the Al Brooks Three Push Reversal system.

Algorithms implement detection of:
1. Three push structure (swing points → push sequences → momentum decay)
2. Exhaustion signals (RSI divergence, MACD divergence, ATR contraction)
3. Candlestick patterns (Pin Bar, Engulfing, Outside Bar)
4. Trend context (EMA20/50/200 alignment and distance)
"""

import numpy as np
from typing import Tuple, List, Dict, Optional, NamedTuple
from dataclasses import dataclass, field


# ============================================================================
# Data Structures
# ============================================================================

@dataclass
class SwingPoint:
    """A detected swing high or low point with metadata."""
    index: int
    price: float
    swing_type: str          # 'high' or 'low'
    strength: float = 1.0     # Normalized 0-1, based on surrounding volatility


@dataclass
class Push:
    """A single push within a multi-push pattern."""
    start_idx: int
    end_idx: int
    start_price: float
    end_price: float
    distance: float           # Absolute price distance
    distance_pct: float       # Percentage distance
    slope: float              # Price change per bar
    duration: int             # Number of bars in this push


@dataclass
class ThreePushPattern:
    """Complete three-push reversal pattern."""
    direction: str            # 'bullish' (3 pushes down → reversal up) or 'bearish' (3 pushes up → reversal down)
    push_count: int           # Actual number of pushes (≥2, typically 3)
    pushes: List[Push]        # List of pushes in chronological order
    completion_idx: int       # Bar index where pattern completed
    wedge_type: str           # 'contracting', 'expanding', 'parallel'
    # Structure scores
    momentum_decay: float     # 0-1, rate of momentum decrease
    overlap_score: float      # 0-1, how well pushes overlap (wedge quality)
    symmetry_score: float     # 0-1, time symmetry between pushes


@dataclass
class DivergenceSignal:
    """RSI or MACD divergence signal."""
    divergence_type: str      # 'bullish' or 'bearish'
    strength: float           # 0-1, divergence strength
    price_peak_idx: int       # Index of price extreme
    indicator_peak_idx: int   # Index of indicator extreme
    price_delta: float        # Price change between peaks
    indicator_delta: float    # Indicator change between peaks


@dataclass
class CandlestickPattern:
    """Detected candlestick pattern."""
    pattern_type: str         # 'pin_bar', 'engulfing', 'outside_bar'
    direction: str            # 'bullish' or 'bearish'
    quality: float            # 0-1, pattern quality score
    index: int                # Bar index


# ============================================================================
# Helper Functions
# ============================================================================

def _ema(data: np.ndarray, period: int) -> np.ndarray:
    """Compute Exponential Moving Average."""
    alpha = 2.0 / (period + 1)
    result = np.zeros_like(data)
    result[0] = data[0]
    for i in range(1, len(data)):
        result[i] = alpha * data[i] + (1 - alpha) * result[i - 1]
    return result


def _sma(data: np.ndarray, period: int) -> np.ndarray:
    """Compute Simple Moving Average."""
    result = np.zeros_like(data)
    for i in range(len(data)):
        if i < period - 1:
            result[i] = np.mean(data[:i + 1])
        else:
            result[i] = np.mean(data[i - period + 1:i + 1])
    return result


def _rsi(close: np.ndarray, period: int = 14) -> np.ndarray:
    """Compute Relative Strength Index using Wilder's smoothing."""
    delta = np.diff(close, prepend=close[0])
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)

    avg_gain = np.zeros_like(close)
    avg_loss = np.zeros_like(close)

    # Initial SMA
    avg_gain[period] = np.mean(gain[1:period + 1])
    avg_loss[period] = np.mean(loss[1:period + 1])

    # Wilder's smoothing
    for i in range(period + 1, len(close)):
        avg_gain[i] = (avg_gain[i - 1] * (period - 1) + gain[i]) / period
        avg_loss[i] = (avg_loss[i - 1] * (period - 1) + loss[i]) / period

    # Fill pre-period with NaN-equivalent
    avg_gain[:period] = np.nan
    avg_loss[:period] = np.nan

    rs = np.divide(avg_gain, avg_loss, out=np.zeros_like(avg_gain), where=avg_loss != 0)
    rsi_vals = 100.0 - (100.0 / (1.0 + rs))
    rsi_vals[:period] = np.nan
    return rsi_vals


def _macd(close: np.ndarray, fast: int = 12, slow: int = 26, signal: int = 9) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Compute MACD line, signal line, and histogram."""
    ema_fast = _ema(close, fast)
    ema_slow = _ema(close, slow)
    macd_line = ema_fast - ema_slow
    signal_line = _ema(macd_line, signal)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def _atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> np.ndarray:
    """Compute Average True Range."""
    tr = np.zeros_like(close)
    for i in range(1, len(close)):
        tr[i] = max(
            high[i] - low[i],
            abs(high[i] - close[i - 1]),
            abs(low[i] - close[i - 1])
        )
    tr[0] = high[0] - low[0]
    return _sma(tr, period)  # Using SMA for ATR (standard method uses RMA, SMA is close approximation)


def _find_peaks(data: np.ndarray, window: int) -> np.ndarray:
    """Find local maxima in a 1D array using a sliding window."""
    peaks = np.zeros(len(data), dtype=bool)
    for i in range(window, len(data) - window):
        if data[i] == np.max(data[i - window:i + window + 1]):
            # Check not flat
            if data[i] > data[i - 1] and data[i] > data[i + 1]:
                peaks[i] = True
    return peaks


def _find_troughs(data: np.ndarray, window: int) -> np.ndarray:
    """Find local minima in a 1D array using a sliding window."""
    troughs = np.zeros(len(data), dtype=bool)
    for i in range(window, len(data) - window):
        if data[i] == np.min(data[i - window:i + window + 1]):
            if data[i] < data[i - 1] and data[i] < data[i + 1]:
                troughs[i] = True
    return troughs


def _sigmoid(x: float) -> float:
    """Sigmoid activation function."""
    return 1.0 / (1.0 + np.exp(-np.clip(x, -50, 50)))


# ============================================================================
# 1. Swing Point Detector
# ============================================================================

class SwingDetector:
    """
    Detects significant swing highs and lows in price data.

    A swing point is a local extremum that represents a meaningful
    turning point in price action. These form the foundation of
    three-push pattern recognition.

    Parameters
    ----------
    window : int, default 5
        Number of bars on each side to check for local extremum.
        Larger values find more significant (longer-term) swings.
    min_strength : float, default 0.3
        Minimum relative amplitude to consider a swing valid.
    """

    def __init__(self, window: int = 5, min_strength: float = 0.3):
        self.window = window
        self.min_strength = min_strength

    def detect(self, high: np.ndarray, low: np.ndarray, close: np.ndarray) -> Tuple[List[SwingPoint], List[SwingPoint]]:
        """
        Detect swing highs and lows.

        Parameters
        ----------
        high, low, close : np.ndarray
            Price arrays for OHLC data.

        Returns
        -------
        Tuple of (swing_highs, swing_lows)
        """
        n = len(close)
        window = min(self.window, n // 4)  # Adaptive window for short series

        # Find raw peaks and troughs
        peak_mask = _find_peaks(high, max(window, 2))
        trough_mask = _find_troughs(low, max(window, 2))

        # Calculate ATR for strength normalization
        atr_vals = _atr(high, low, close, 14)
        median_atr = np.nanmedian(atr_vals)

        swing_highs = []
        swing_lows = []

        for i in range(n):
            if peak_mask[i]:
                strength = self._calculate_swing_strength(high, low, i, median_atr, atr_vals)
                if strength >= self.min_strength:
                    swing_highs.append(SwingPoint(
                        index=i, price=high[i],
                        swing_type='high', strength=strength
                    ))

            if trough_mask[i]:
                strength = self._calculate_swing_strength(high, low, i, median_atr, atr_vals)
                if strength >= self.min_strength:
                    swing_lows.append(SwingPoint(
                        index=i, price=low[i],
                        swing_type='low', strength=strength
                    ))

        return swing_highs, swing_lows

    def _calculate_swing_strength(
        self, high: np.ndarray, low: np.ndarray,
        idx: int, median_atr: float, atr_vals: np.ndarray
    ) -> float:
        """Calculate normalized swing strength based on ATR."""
        if median_atr == 0 or np.isnan(median_atr):
            return 0.5

        local_range = high[idx] - low[idx]
        if idx < len(atr_vals) and not np.isnan(atr_vals[idx]):
            strength = min(local_range / (atr_vals[idx] * 2), 1.0)
        else:
            strength = min(local_range / (median_atr * 2), 1.0)
        return max(0.0, strength)


# ============================================================================
# 2. Three Push Pattern Detector
# ============================================================================

class ThreePushDetector:
    """
    Detects three-push reversal patterns from swing point sequences.

    A "push" is a directional move between two consecutive swing points.
    Three pushes in the same direction, with each showing diminishing momentum,
    signal an impending reversal.

    Al Brooks identifies several three-push variations:
    - Wedge (contracting): Each push is shorter, forming a wedge/triangle
    - Broadening: Each push is longer (less common, more dangerous)
    - Parallel: Equal-sized pushes in a channel

    Parameters
    ----------
    min_pushes : int, default 2
        Minimum pushes required (2 for early detection, 3 for confirmed).
    max_pushes : int, default 5
        Maximum pushes to consider.
    momentum_decay_threshold : float, default 0.2
        Required momentum decay rate for valid pattern.
    """

    def __init__(
        self,
        min_pushes: int = 2,
        max_pushes: int = 5,
        momentum_decay_threshold: float = 0.2,
    ):
        self.min_pushes = min_pushes
        self.max_pushes = max_pushes
        self.momentum_decay_threshold = momentum_decay_threshold

    def detect(
        self,
        swing_highs: List[SwingPoint],
        swing_lows: List[SwingPoint],
        high: np.ndarray,
        low: np.ndarray,
        close: np.ndarray,
    ) -> List[ThreePushPattern]:
        """
        Detect three-push patterns from swing sequences.

        Returns
        -------
        List of detected ThreePushPattern objects.
        """
        patterns = []

        # Merge and sort all swing points
        all_swings = sorted(swing_highs + swing_lows, key=lambda s: s.index)

        if len(all_swings) < 4:
            return patterns

        # Detect bearish patterns (3 pushes UP → reversal DOWN)
        bearish = self._detect_directional(
            all_swings, direction='bearish',
            compare_swings=swing_highs,  # Looking at push highs
        )
        patterns.extend(bearish)

        # Detect bullish patterns (3 pushes DOWN → reversal UP)
        bullish = self._detect_directional(
            all_swings, direction='bullish',
            compare_swings=swing_lows,  # Looking at push lows
        )
        patterns.extend(bullish)

        return patterns

    def _detect_directional(
        self,
        all_swings: List[SwingPoint],
        direction: str,
        compare_swings: List[SwingPoint],
    ) -> List[ThreePushPattern]:
        """
        Detect directional three-push patterns.

        For bearish: looking for 3+ consecutive higher swing highs
        (each push is a rally to a new high, followed by a pullback)

        For bullish: looking for 3+ consecutive lower swing lows
        (each push is a decline to a new low, followed by a bounce)
        """
        patterns = []

        if direction == 'bearish':
            target_type = 'high'
            compare_fn = lambda a, b: a.price > b.price  # Higher highs
        else:
            target_type = 'low'
            compare_fn = lambda a, b: a.price < b.price  # Lower lows

        # Filter to target-type swings only
        target_swings = [s for s in compare_swings if s.swing_type == target_type]
        target_swings.sort(key=lambda s: s.index)

        if len(target_swings) < self.min_pushes + 1:
            return patterns

        # Slide through swings looking for push sequences
        for i in range(len(target_swings) - self.min_pushes):
            for push_count in range(self.min_pushes, min(self.max_pushes + 1, len(target_swings) - i)):
                push_swings = target_swings[i:i + push_count + 1]

                # Verify directional consistency
                consistent = all(
                    compare_fn(push_swings[j + 1], push_swings[j])
                    for j in range(len(push_swings) - 1)
                )
                if not consistent:
                    continue

                # Build push objects
                pushes = self._build_pushes(push_swings, all_swings, direction)

                if len(pushes) < self.min_pushes:
                    continue

                # Calculate pattern quality metrics
                momentum_decay = self._calc_momentum_decay(pushes)
                overlap_score = self._calc_overlap(pushes)
                symmetry_score = self._calc_symmetry(pushes)
                wedge_type = self._classify_wedge(pushes)

                # Validate pattern quality
                if momentum_decay >= self.momentum_decay_threshold:
                    patterns.append(ThreePushPattern(
                        direction=direction,
                        push_count=len(pushes),
                        pushes=pushes,
                        completion_idx=push_swings[-1].index,
                        wedge_type=wedge_type,
                        momentum_decay=momentum_decay,
                        overlap_score=overlap_score,
                        symmetry_score=symmetry_score,
                    ))

        return patterns

    def _build_pushes(
        self,
        target_swings: List[SwingPoint],
        all_swings: List[SwingPoint],
        direction: str,
    ) -> List[Push]:
        """Build Push objects by finding the alternating swing points between target swings."""
        pushes = []

        for j in range(len(target_swings) - 1):
            start_swing = target_swings[j]
            end_swing = target_swings[j + 1]

            # In a bearish setup, pushes go from swing low to swing high
            # In a bullish setup, pushes go from swing high to swing low
            if direction == 'bearish':
                # Push: low → high rally
                # Find the swing low that started this push
                push_start = start_swing.index
                push_start_price = start_swing.price

                # The best start is actually the intervening swing low
                intervening = [
                    s for s in all_swings
                    if start_swing.index < s.index < end_swing.index
                    and s.swing_type == 'low'
                ]
                if intervening:
                    push_start = intervening[-1].index
                    push_start_price = intervening[-1].price
            else:
                # Push: high → low decline
                push_start = start_swing.index
                push_start_price = start_swing.price

                intervening = [
                    s for s in all_swings
                    if start_swing.index < s.index < end_swing.index
                    and s.swing_type == 'high'
                ]
                if intervening:
                    push_start = intervening[-1].index
                    push_start_price = intervening[-1].price

            duration = end_swing.index - push_start
            if duration <= 0:
                continue

            distance = abs(end_swing.price - push_start_price)
            distance_pct = distance / push_start_price if push_start_price > 0 else 0.0
            slope = (end_swing.price - push_start_price) / duration

            if direction == 'bearish':
                slope = abs(slope) if slope > 0 else 0.001
            else:
                slope = abs(slope) if slope < 0 else 0.001

            pushes.append(Push(
                start_idx=push_start,
                end_idx=end_swing.index,
                start_price=push_start_price,
                end_price=end_swing.price,
                distance=distance,
                distance_pct=distance_pct,
                slope=slope,
                duration=duration,
            ))

        return pushes

    def _calc_momentum_decay(self, pushes: List[Push]) -> float:
        """
        Calculate momentum decay across pushes.

        Returns normalized 0-1 score where higher = more decay (more exhaustion).
        """
        if len(pushes) < 2:
            return 0.0

        slopes = [p.slope for p in pushes]
        distances = [p.distance_pct for p in pushes]

        # Calculate rate of change in both slope and distance
        slope_changes = []
        for i in range(1, len(slopes)):
            if slopes[i - 1] > 0:
                change = (slopes[i - 1] - slopes[i]) / slopes[i - 1]
                slope_changes.append(change)

        distance_changes = []
        for i in range(1, len(distances)):
            if distances[i - 1] > 0:
                change = (distances[i - 1] - distances[i]) / distances[i - 1]
                distance_changes.append(change)

        if not slope_changes and not distance_changes:
            return 0.0

        avg_slope_decay = np.mean(slope_changes) if slope_changes else 0.0
        avg_dist_decay = np.mean(distance_changes) if distance_changes else 0.0

        # Combine and normalize to 0-1
        combined = 0.6 * avg_slope_decay + 0.4 * avg_dist_decay
        return _sigmoid(combined * 5)  # Scale factor to spread distribution

    def _calc_overlap(self, pushes: List[Push]) -> float:
        """
        Calculate push overlap (wedge quality).

        High overlap = contracting wedge (classic three push)
        Low/no overlap = broadening (dangerous, lower probability)
        """
        if len(pushes) < 2:
            return 0.5

        # Use distance ratios to determine contracting vs expanding
        distances = [p.distance for p in pushes]
        ratios = []
        for i in range(1, len(distances)):
            if distances[i - 1] > 0:
                ratios.append(distances[i] / distances[i - 1])

        if not ratios:
            return 0.5

        avg_ratio = np.mean(ratios)

        # < 1.0 = contracting (good), > 1.0 = expanding (bad)
        # Normalize: 0.5 ratio = very contracting = score 1.0
        #            1.5 ratio = expanding = score 0.0
        score = max(0.0, min(1.0, 2.0 - avg_ratio))
        return score

    def _calc_symmetry(self, pushes: List[Push]) -> float:
        """Calculate time symmetry between pushes."""
        if len(pushes) < 2:
            return 0.5

        durations = [p.duration for p in pushes]
        if len(durations) < 2:
            return 0.5

        # Coefficient of variation of durations (lower = more symmetric)
        mean_dur = np.mean(durations)
        if mean_dur == 0:
            return 0.5

        std_dur = np.std(durations)
        cv = std_dur / mean_dur

        # cv < 0.3 = highly symmetric = score 1.0
        # cv > 1.0 = very asymmetric = score 0.0
        score = max(0.0, min(1.0, 1.5 - cv))
        return score

    def _classify_wedge(self, pushes: List[Push]) -> str:
        """Classify the type of three-push pattern."""
        if len(pushes) < 2:
            return 'unknown'

        distances = [p.distance for p in pushes]
        ratios = []
        for i in range(1, len(distances)):
            if distances[i - 1] > 0:
                ratios.append(distances[i] / distances[i - 1])

        if not ratios:
            return 'unknown'

        avg_ratio = np.mean(ratios)

        if avg_ratio < 0.85:
            return 'contracting'
        elif avg_ratio > 1.15:
            return 'expanding'
        else:
            return 'parallel'


# ============================================================================
# 3. Exhaustion Analyzer (RSI/MACD Divergence + ATR Contraction)
# ============================================================================

class ExhaustionAnalyzer:
    """
    Analyzes momentum exhaustion through RSI divergence, MACD divergence,
    and ATR contraction.

    These signals confirm that a three-push pattern is reaching exhaustion
    and a reversal is imminent.

    Parameters
    ----------
    rsi_period : int, default 14
    macd_fast : int, default 12
    macd_slow : int, default 26
    macd_signal : int, default 9
    atr_period : int, default 14
    divergence_lookback : int, default 20
        Bars to look back for divergence detection.
    """

    def __init__(
        self,
        rsi_period: int = 14,
        macd_fast: int = 12,
        macd_slow: int = 26,
        macd_signal: int = 9,
        atr_period: int = 14,
        divergence_lookback: int = 20,
    ):
        self.rsi_period = rsi_period
        self.macd_fast = macd_fast
        self.macd_slow = macd_slow
        self.macd_signal = macd_signal
        self.atr_period = atr_period
        self.divergence_lookback = divergence_lookback

    def analyze(
        self,
        high: np.ndarray,
        low: np.ndarray,
        close: np.ndarray,
        pattern: Optional[ThreePushPattern] = None,
    ) -> Dict[str, float]:
        """
        Run full exhaustion analysis.

        Returns
        -------
        Dict with keys:
            rsi_divergence_strength   : 0-1
            macd_divergence_strength   : 0-1
            atr_contraction           : 0-1
            composite_exhaustion      : 0-1 (weighted average)
        """
        # Calculate indicators
        rsi_vals = _rsi(close, self.rsi_period)
        macd_line, signal_line, macd_hist = _macd(
            close, self.macd_fast, self.macd_slow, self.macd_signal
        )
        atr_vals = _atr(high, low, close, self.atr_period)

        # Detect divergences
        rsi_div = self._detect_rsi_divergence(close, rsi_vals, pattern)
        macd_div = self._detect_macd_divergence(close, macd_hist, pattern)

        # ATR contraction
        atr_contraction = self._calculate_atr_contraction(atr_vals, pattern)

        # Composite score
        composite = (
            0.40 * rsi_div +
            0.35 * macd_div +
            0.25 * atr_contraction
        )

        return {
            'rsi_divergence_strength': round(rsi_div, 4),
            'macd_divergence_strength': round(macd_div, 4),
            'atr_contraction': round(atr_contraction, 4),
            'composite_exhaustion': round(composite, 4),
        }

    def _detect_rsi_divergence(
        self,
        close: np.ndarray,
        rsi_vals: np.ndarray,
        pattern: Optional[ThreePushPattern],
    ) -> float:
        """
        Detect RSI divergence near the pattern completion point.

        Regular bullish divergence: Price makes lower low, RSI makes higher low
        Regular bearish divergence: Price makes higher high, RSI makes lower high
        """
        if pattern is None:
            # Scan recent bars for any divergence
            return self._scan_divergence(close, rsi_vals, lookback=self.divergence_lookback)

        idx = pattern.completion_idx
        lookback = min(self.divergence_lookback, idx)

        if lookback < 5:
            return 0.0

        if pattern.direction == 'bearish':
            # Looking for bearish divergence: higher price highs, lower RSI highs
            return self._scan_divergence(
                close[max(0, idx - lookback):idx + 1],
                rsi_vals[max(0, idx - lookback):idx + 1],
                lookback=lookback,
                divergence_type='bearish',
            )
        else:
            # Looking for bullish divergence: lower price lows, higher RSI lows
            return self._scan_divergence(
                close[max(0, idx - lookback):idx + 1],
                rsi_vals[max(0, idx - lookback):idx + 1],
                lookback=lookback,
                divergence_type='bullish',
            )

    def _scan_divergence(
        self,
        price_segment: np.ndarray,
        rsi_segment: np.ndarray,
        lookback: int,
        divergence_type: str = 'auto',
    ) -> float:
        """
        Scan a price/RSI segment for divergence.

        Returns divergence strength 0-1.
        """
        n = len(price_segment)
        if n < 5:
            return 0.0

        # Find peaks/troughs in both series
        window = max(2, n // 6)

        if divergence_type in ('bearish', 'auto'):
            price_peaks = _find_peaks(price_segment, window)
            rsi_peaks = _find_peaks(rsi_segment, window)

            # Get last two peaks in each
            price_peak_indices = np.where(price_peaks)[0]
            rsi_peak_indices = np.where(rsi_peaks)[0]

            if len(price_peak_indices) >= 2 and len(rsi_peak_indices) >= 2:
                # Check if last two price peaks are rising
                p1, p2 = price_peak_indices[-2], price_peak_indices[-1]
                r1, r2 = rsi_peak_indices[-2], rsi_peak_indices[-1]

                price_rising = price_segment[p2] > price_segment[p1]
                rsi_falling = rsi_segment[r2] < rsi_segment[r1]

                if price_rising and rsi_falling:
                    # Calculate divergence strength
                    price_change = (price_segment[p2] - price_segment[p1]) / price_segment[p1]
                    rsi_change = (rsi_segment[r1] - rsi_segment[r2]) / rsi_segment[r1]
                    strength = min(1.0, abs(price_change * rsi_change) * 50)
                    return strength

        if divergence_type in ('bullish', 'auto'):
            price_troughs = _find_troughs(price_segment, window)
            rsi_troughs = _find_troughs(rsi_segment, window)

            price_trough_indices = np.where(price_troughs)[0]
            rsi_trough_indices = np.where(rsi_troughs)[0]

            if len(price_trough_indices) >= 2 and len(rsi_trough_indices) >= 2:
                p1, p2 = price_trough_indices[-2], price_trough_indices[-1]
                r1, r2 = rsi_trough_indices[-2], rsi_trough_indices[-1]

                price_falling = price_segment[p2] < price_segment[p1]
                rsi_rising = rsi_segment[r2] > rsi_segment[r1]

                if price_falling and rsi_rising:
                    price_change = (price_segment[p1] - price_segment[p2]) / price_segment[p1]
                    rsi_change = (rsi_segment[r2] - rsi_segment[r1]) / rsi_segment[r1]
                    strength = min(1.0, abs(price_change * rsi_change) * 50)
                    return strength

        return 0.0

    def _detect_macd_divergence(
        self,
        close: np.ndarray,
        macd_hist: np.ndarray,
        pattern: Optional[ThreePushPattern],
    ) -> float:
        """
        Detect MACD histogram divergence.

        Similar logic to RSI divergence but using MACD histogram.
        """
        if pattern is None:
            return self._scan_divergence(
                close[-min(self.divergence_lookback, len(close)):],
                macd_hist[-min(self.divergence_lookback, len(close)):],
                lookback=min(self.divergence_lookback, len(close)),
            )

        idx = pattern.completion_idx
        lookback = min(self.divergence_lookback, idx)
        if lookback < 5:
            return 0.0

        div_type = 'bearish' if pattern.direction == 'bearish' else 'bullish'
        return self._scan_divergence(
            close[max(0, idx - lookback):idx + 1],
            macd_hist[max(0, idx - lookback):idx + 1],
            lookback=lookback,
            divergence_type=div_type,
        )

    def _calculate_atr_contraction(
        self,
        atr_vals: np.ndarray,
        pattern: Optional[ThreePushPattern],
    ) -> float:
        """
        Calculate ATR contraction rate as an exhaustion signal.

        Volatility contraction during a push sequence indicates
        decreasing conviction in the trend.
        """
        n = len(atr_vals)
        if n < self.atr_period * 2:
            return 0.0

        if pattern is not None and pattern.pushes:
            # Measure ATR change during the push sequence
            start_idx = max(0, pattern.pushes[0].start_idx)
            end_idx = min(n - 1, pattern.completion_idx)

            if end_idx - start_idx < 5:
                return 0.0

            # Split into halves
            mid = (start_idx + end_idx) // 2
            first_half_atr = np.nanmean(atr_vals[start_idx:mid])
            second_half_atr = np.nanmean(atr_vals[mid:end_idx + 1])

            if first_half_atr > 0:
                contraction = (first_half_atr - second_half_atr) / first_half_atr
                return max(0.0, min(1.0, contraction * 5))
        else:
            # General ATR contraction check
            recent_atr = np.nanmean(atr_vals[-self.atr_period:])
            prior_atr = np.nanmean(atr_vals[-2 * self.atr_period:-self.atr_period])

            if prior_atr > 0:
                contraction = (prior_atr - recent_atr) / prior_atr
                return max(0.0, min(1.0, contraction * 5))

        return 0.0


# ============================================================================
# 4. Candlestick Pattern Analyzer
# ============================================================================

class CandlestickAnalyzer:
    """
    Detects key candlestick reversal patterns:
    - Pin Bar (Hammer / Shooting Star)
    - Engulfing (Bullish / Bearish)
    - Outside Bar

    Parameters
    ----------
    pin_bar_ratio : float, default 2.0
        Minimum wick-to-body ratio for a pin bar.
    body_pct_threshold : float, default 0.003
        Minimum body size as percentage of price for engulfing validity.
    """

    def __init__(
        self,
        pin_bar_ratio: float = 2.0,
        body_pct_threshold: float = 0.003,
    ):
        self.pin_bar_ratio = pin_bar_ratio
        self.body_pct_threshold = body_pct_threshold

    def analyze(
        self,
        open_: np.ndarray,
        high: np.ndarray,
        low: np.ndarray,
        close: np.ndarray,
        pattern: Optional[ThreePushPattern] = None,
    ) -> Dict[str, float]:
        """
        Analyze candlestick patterns at the current bar or pattern completion.

        Returns
        -------
        Dict with keys:
            pin_bar_score       : 0-1
            engulfing_score     : 0-1
            outside_bar_score   : 0-1
            composite_candle    : 0-1 (weighted average)
        """
        idx = -1
        expected_dir = None

        if pattern is not None:
            idx = pattern.completion_idx
            expected_dir = pattern.direction  # 'bullish' or 'bearish'

        pin_bar = self._detect_pin_bar(open_, high, low, close, idx, expected_dir)
        engulfing = self._detect_engulfing(open_, high, low, close, idx, expected_dir)
        outside = self._detect_outside_bar(open_, high, low, close, idx, expected_dir)

        composite = 0.40 * pin_bar + 0.35 * engulfing + 0.25 * outside

        return {
            'pin_bar_score': round(pin_bar, 4),
            'engulfing_score': round(engulfing, 4),
            'outside_bar_score': round(outside, 4),
            'composite_candle': round(composite, 4),
        }

    def _detect_pin_bar(
        self,
        open_: np.ndarray,
        high: np.ndarray,
        low: np.ndarray,
        close: np.ndarray,
        idx: int,
        expected_dir: Optional[str] = None,
    ) -> float:
        """Detect pin bar (hammer / shooting star) at given index."""
        i = idx % len(close) if idx < 0 else idx

        body_high = max(open_[i], close[i])
        body_low = min(open_[i], close[i])
        body = body_high - body_low
        upper_wick = high[i] - body_high
        lower_wick = body_low - low[i]
        total_range = high[i] - low[i]

        if total_range == 0:
            return 0.0

        # Hammer: long lower wick, small upper wick, at bottom of move
        # Shooting star: long upper wick, small lower wick, at top of move

        # Check for hammer (bullish reversal)
        if lower_wick >= self.pin_bar_ratio * body and upper_wick <= 0.3 * body:
            wick_ratio = lower_wick / max(body, 0.0001)
            quality = min(1.0, wick_ratio / (self.pin_bar_ratio * 3))
            # Stronger if at the lower end of recent range
            position_score = self._position_score(close, i, 'bottom')
            if expected_dir is None or expected_dir == 'bullish':
                return quality * 0.7 + position_score * 0.3

        # Check for shooting star (bearish reversal)
        if upper_wick >= self.pin_bar_ratio * body and lower_wick <= 0.3 * body:
            wick_ratio = upper_wick / max(body, 0.0001)
            quality = min(1.0, wick_ratio / (self.pin_bar_ratio * 3))
            position_score = self._position_score(close, i, 'top')
            if expected_dir is None or expected_dir == 'bearish':
                return quality * 0.7 + position_score * 0.3

        return 0.0

    def _detect_engulfing(
        self,
        open_: np.ndarray,
        high: np.ndarray,
        low: np.ndarray,
        close: np.ndarray,
        idx: int,
        expected_dir: Optional[str] = None,
    ) -> float:
        """Detect bullish/bearish engulfing pattern."""
        i = idx % len(close) if idx < 0 else idx
        if i < 1:
            return 0.0

        curr_open, curr_close = open_[i], close[i]
        prev_open, prev_close = open_[i - 1], close[i - 1]

        curr_body = abs(curr_close - curr_open)
        prev_body = abs(prev_close - prev_open)

        min_body = self.body_pct_threshold * close[i]
        if curr_body < min_body or prev_body < min_body:
            return 0.0

        # Bullish engulfing
        if (prev_close < prev_open and curr_close > curr_open and
                curr_open <= prev_close and curr_close >= prev_open):
            engulf_ratio = curr_body / max(prev_body, 0.0001)
            quality = min(1.0, engulf_ratio / 3.0)
            if expected_dir is None or expected_dir == 'bullish':
                return quality

        # Bearish engulfing
        if (prev_close > prev_open and curr_close < curr_open and
                curr_open >= prev_close and curr_close <= prev_open):
            engulf_ratio = curr_body / max(prev_body, 0.0001)
            quality = min(1.0, engulf_ratio / 3.0)
            if expected_dir is None or expected_dir == 'bearish':
                return quality

        return 0.0

    def _detect_outside_bar(
        self,
        open_: np.ndarray,
        high: np.ndarray,
        low: np.ndarray,
        close: np.ndarray,
        idx: int,
        expected_dir: Optional[str] = None,
    ) -> float:
        """Detect outside bar pattern."""
        i = idx % len(close) if idx < 0 else idx
        if i < 1:
            return 0.0

        curr_range = high[i] - low[i]
        prev_range = high[i - 1] - low[i - 1]

        if curr_range > prev_range and high[i] > high[i - 1] and low[i] < low[i - 1]:
            # Outside bar confirmed
            range_ratio = curr_range / max(prev_range, 0.0001)
            quality = min(1.0, range_ratio / 2.5)

            # Direction from close position
            body_high = max(open_[i], close[i])
            body_low = min(open_[i], close[i])

            if close[i] < open_[i]:
                # Bearish outside bar
                if expected_dir is None or expected_dir == 'bearish':
                    return quality
            else:
                # Bullish outside bar
                if expected_dir is None or expected_dir == 'bullish':
                    return quality

            # Return partial score if direction doesn't match perfectly
            return quality * 0.5

        return 0.0

    def _position_score(self, close: np.ndarray, idx: int, position: str) -> float:
        """Score candle position relative to recent range (top or bottom)."""
        lookback = min(20, idx)
        if lookback < 5:
            return 0.5

        segment = close[max(0, idx - lookback):idx + 1]
        range_high = np.max(segment)
        range_low = np.min(segment)
        range_total = range_high - range_low

        if range_total == 0:
            return 0.5

        relative_pos = (close[idx] - range_low) / range_total

        if position == 'top':
            return min(1.0, relative_pos * 2)  # Higher score near top
        else:  # bottom
            return min(1.0, (1.0 - relative_pos) * 2)  # Higher score near bottom


# ============================================================================
# 5. Trend Context Analyzer
# ============================================================================

class TrendAnalyzer:
    """
    Analyzes trend context using EMA20, EMA50, and EMA200.

    Provides context for reversal probability:
    - Counter-trend reversals are more reliable near strong support/resistance
    - Trend alignment affects expected move and risk/reward

    Parameters
    ----------
    ema_periods : Tuple[int, int, int], default (20, 50, 200)
        EMA periods for short, medium, and long-term trend.
    """

    def __init__(self, ema_periods: Tuple[int, int, int] = (20, 50, 200)):
        self.ema_periods = ema_periods

    def analyze(
        self,
        close: np.ndarray,
        pattern: Optional[ThreePushPattern] = None,
    ) -> Dict[str, float]:
        """
        Analyze trend context.

        Returns
        -------
        Dict with keys:
            ema20_value, ema50_value, ema200_value
            ema_alignment_score         : 0-1, how well aligned EMAs are
            trend_strength              : 0-1, trend momentum
            price_vs_ema20              : normalized distance
            trend_context_score         : 0-1, composite trend context
            trend_direction             : -1 (downtrend) to +1 (uptrend)
        """
        ema20 = _ema(close, 20)
        ema50 = _ema(close, 50)
        ema200 = _ema(close, 200)

        idx = -1 if pattern is None else pattern.completion_idx

        e20 = ema20[idx]
        e50 = ema50[idx]
        e200 = ema200[idx]
        price = close[idx]

        # Alignment score
        alignment = self._ema_alignment(e20, e50, e200)

        # Trend direction and strength
        trend_direction, trend_strength = self._trend_momentum(ema20, ema50, idx)

        # Price vs EMA20 distance
        price_dist = self._price_distance(price, e20, e200)

        # Trend context for reversal
        context_score = self._trend_context(
            pattern, trend_direction, price, e20, e50, e200
        )

        return {
            'ema20_value': round(float(e20), 4),
            'ema50_value': round(float(e50), 4),
            'ema200_value': round(float(e200), 4),
            'ema_alignment_score': round(alignment, 4),
            'trend_strength': round(trend_strength, 4),
            'price_vs_ema20': round(price_dist, 4),
            'trend_context_score': round(context_score, 4),
            'trend_direction': round(trend_direction, 4),
        }

    def _ema_alignment(self, e20: float, e50: float, e200: float) -> float:
        """Score how well EMAs are aligned (bullish or bearish stack)."""
        if e20 > e50 > e200:
            # Perfect bullish alignment
            e20_50 = (e20 - e50) / e50
            e50_200 = (e50 - e200) / e200
            return min(1.0, (e20_50 + e50_200) * 20)
        elif e20 < e50 < e200:
            # Perfect bearish alignment
            e20_50 = (e50 - e20) / e50
            e50_200 = (e200 - e50) / e200
            return min(1.0, (e20_50 + e50_200) * 20)
        else:
            # Mixed / no clear alignment
            return 0.1

    def _trend_momentum(
        self, ema20: np.ndarray, ema50: np.ndarray, idx: int
    ) -> Tuple[float, float]:
        """Calculate trend direction and strength from EMA slope."""
        lookback = min(20, idx)
        if lookback < 2:
            return 0.0, 0.0

        # EMA20 slope over lookback period
        start_idx = max(0, idx - lookback)
        e20_change = ema20[idx] - ema20[start_idx]
        e20_pct = e20_change / ema20[start_idx] if ema20[start_idx] > 0 else 0

        # EMA50 slope
        e50_change = ema50[idx] - ema50[start_idx]
        e50_pct = e50_change / ema50[start_idx] if ema50[start_idx] > 0 else 0

        # Combined
        combined = 0.7 * e20_pct + 0.3 * e50_pct

        direction = np.tanh(combined * 100)  # -1 to +1
        strength = min(1.0, abs(combined) * 200)

        return float(direction), float(strength)

    def _price_distance(self, price: float, e20: float, e200: float) -> float:
        """Calculate normalized price distance from EMA20 (relative to EMA200 range)."""
        if e200 == 0:
            return 0.0
        return (price - e20) / e200

    def _trend_context(
        self,
        pattern: Optional[ThreePushPattern],
        trend_direction: float,
        price: float,
        e20: float,
        e50: float,
        e200: float,
    ) -> float:
        """
        Calculate trend context score for reversal.

        High score = favorable conditions for the expected reversal direction.
        """
        if pattern is None:
            return 0.5

        # For bullish reversal (three pushes DOWN), we want:
        # - Price at/near support (EMA200 or below EMA20)
        # - Or oversold relative to trend
        if pattern.direction == 'bullish':
            score = 0.0

            # Price below EMA20 in uptrend = pullback to support (good for reversal up)
            if trend_direction > 0.3 and price < e20:
                score += 0.4
            # Price near EMA200 = major support (good for reversal up)
            if e200 > 0 and abs(price - e200) / e200 < 0.03:
                score += 0.4
            # Deeply oversold
            if price < e20 and price < e50:
                score += 0.2

            return min(1.0, score)

        # For bearish reversal (three pushes UP), we want:
        # - Price at/near resistance (above EMA20)
        # - Or overbought relative to trend
        else:
            score = 0.0

            if trend_direction < -0.3 and price > e20:
                score += 0.4
            if e200 > 0 and abs(price - e200) / e200 < 0.03:
                score += 0.4
            if price > e20 and price > e50:
                score += 0.2

            return min(1.0, score)
