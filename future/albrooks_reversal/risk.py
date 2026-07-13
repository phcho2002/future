"""
Risk Metrics Calculator
=======================
Computes expected move, expected drawdown, and risk/reward ratio
for Al Brooks three-push reversal setups.

Methodology:
- Expected Move: ATR-based projection with pattern-specific multipliers
- Expected Drawdown: Volatility-based worst-case estimate
- Risk/Reward Ratio: Expected reward / expected risk

These metrics enable position sizing and trade management decisions
consistent with Al Brooks' emphasis on risk control.
"""

import numpy as np
from typing import Dict, Optional, Tuple
from dataclasses import dataclass

from .features import ThreePushPattern


# ============================================================================
# Data Structures
# ============================================================================

@dataclass
class RiskMetrics:
    """Complete risk assessment for a reversal setup."""

    # Core metrics
    expected_move: float              # Expected price move (absolute)
    expected_move_pct: float          # Expected price move (percentage)
    expected_drawdown: float          # Worst-case adverse excursion (absolute)
    expected_drawdown_pct: float      # Worst-case adverse excursion (percentage)
    risk_reward_ratio: float          # Reward / Risk ratio

    # Entry/exit levels
    entry_price: float
    target_price: float               # Take-profit target
    stop_loss: float                  # Stop-loss level

    # Direction
    direction: str                    # 'bullish' or 'bearish'

    # Component breakdown
    move_components: Dict[str, float] = None

    def __post_init__(self):
        if self.move_components is None:
            self.move_components = {}

    @property
    def reward_pips(self) -> float:
        """Absolute reward in price units."""
        return abs(self.target_price - self.entry_price)

    @property
    def risk_pips(self) -> float:
        """Absolute risk in price units."""
        return abs(self.entry_price - self.stop_loss)

    @property
    def is_favorable(self) -> bool:
        """Whether the risk/reward ratio meets minimum threshold."""
        return self.risk_reward_ratio >= 1.5

    def summary(self) -> str:
        """One-line summary of risk metrics."""
        return (
            f"Entry: {self.entry_price:.4f} | "
            f"Target: {self.target_price:.4f} | "
            f"Stop: {self.stop_loss:.4f} | "
            f"RR: {self.risk_reward_ratio:.2f}:1 | "
            f"Move: {self.expected_move_pct:.2f}% | "
            f"DD: {self.expected_drawdown_pct:.2f}%"
        )


# ============================================================================
# Risk Calculator
# ============================================================================

class RiskCalculator:
    """
    Calculates risk metrics for three-push reversal setups.

    Expected Move is derived from:
    1. ATR-based projection (base estimate)
    2. Pattern amplitude (push distances)
    3. Volatility regime adjustment

    Expected Drawdown considers:
    1. Recent volatility (ATR)
    2. Pattern failure rate
    3. Market noise level

    Risk/Reward Ratio = Expected Move / Expected Drawdown

    Parameters
    ----------
    atr_period : int, default 14
        Period for ATR calculation.
    atr_multiplier : float, default 2.0
        Multiplier on ATR for expected move (base).
    max_rr_ratio : float, default 10.0
        Cap on risk/reward ratio to prevent unrealistic values.
    min_rr_ratio : float, default 0.2
        Floor on risk/reward ratio.
    volatility_lookback : int, default 50
        Bars for volatility regime calculation.
    """

    def __init__(
        self,
        atr_period: int = 14,
        atr_multiplier: float = 2.0,
        max_rr_ratio: float = 10.0,
        min_rr_ratio: float = 0.2,
        volatility_lookback: int = 50,
    ):
        self.atr_period = atr_period
        self.atr_multiplier = atr_multiplier
        self.max_rr_ratio = max_rr_ratio
        self.min_rr_ratio = min_rr_ratio
        self.volatility_lookback = volatility_lookback

    def calculate(
        self,
        high: np.ndarray,
        low: np.ndarray,
        close: np.ndarray,
        pattern: Optional[ThreePushPattern],
        probability: float,
    ) -> RiskMetrics:
        """
        Calculate complete risk metrics.

        Parameters
        ----------
        high, low, close : np.ndarray
            Price arrays.
        pattern : ThreePushPattern or None
            Detected pattern (None = use recent data only).
        probability : float
            Reversal probability (0-100). Higher probability = tighter risk.

        Returns
        -------
        RiskMetrics with all calculations.
        """
        n = len(close)
        atr = self._calculate_atr(high, low, close)

        # Current price (entry)
        if pattern is not None:
            entry_idx = pattern.completion_idx
            entry_price = close[entry_idx]
        else:
            entry_idx = n - 1
            entry_price = close[-1]

        # Determine direction
        direction = 'neutral'
        if pattern is not None:
            direction = pattern.direction

        # Calculate expected move
        expected_move, expected_move_pct = self._expected_move(
            high, low, close, atr, pattern, entry_idx, entry_price, direction
        )

        # Calculate expected drawdown
        expected_dd, expected_dd_pct = self._expected_drawdown(
            high, low, close, atr, pattern, entry_idx, entry_price,
            probability, direction
        )

        # Calculate target and stop
        target_price, stop_loss = self._calculate_levels(
            entry_price, expected_move, expected_dd, direction
        )

        # Risk/Reward ratio
        rr_ratio = self._risk_reward_ratio(
            expected_move, expected_dd, probability
        )

        # Cap/clamp
        rr_ratio = max(self.min_rr_ratio, min(self.max_rr_ratio, rr_ratio))

        return RiskMetrics(
            expected_move=round(expected_move, 6),
            expected_move_pct=round(expected_move_pct * 100, 3),
            expected_drawdown=round(expected_dd, 6),
            expected_drawdown_pct=round(expected_dd_pct * 100, 3),
            risk_reward_ratio=round(rr_ratio, 2),
            entry_price=round(entry_price, 6),
            target_price=round(target_price, 6),
            stop_loss=round(stop_loss, 6),
            direction=direction,
            move_components={},
        )

    def _calculate_atr(
        self, high: np.ndarray, low: np.ndarray, close: np.ndarray
    ) -> np.ndarray:
        """Calculate ATR."""
        n = len(close)
        tr = np.zeros(n)
        for i in range(1, n):
            tr[i] = max(
                high[i] - low[i],
                abs(high[i] - close[i - 1]),
                abs(low[i] - close[i - 1]),
            )
        tr[0] = high[0] - low[0]

        # RMA (Wilder's smoothing) for ATR
        atr = np.zeros(n)
        atr[self.atr_period] = np.mean(tr[1:self.atr_period + 1])
        for i in range(self.atr_period + 1, n):
            atr[i] = (atr[i - 1] * (self.atr_period - 1) + tr[i]) / self.atr_period

        atr[:self.atr_period] = atr[self.atr_period]
        return atr

    def _expected_move(
        self,
        high: np.ndarray,
        low: np.ndarray,
        close: np.ndarray,
        atr: np.ndarray,
        pattern: Optional[ThreePushPattern],
        entry_idx: int,
        entry_price: float,
        direction: str,
    ) -> Tuple[float, float]:
        """
        Calculate expected absolute and percentage move.

        Components:
        1. ATR base: atr_multiplier * current ATR
        2. Pattern amplitude: average push distance if pattern exists
        3. Volatility regime adjustment
        4. Pattern quality adjustment
        """
        current_atr = atr[min(entry_idx, len(atr) - 1)]

        # Base: ATR projection
        base_move = self.atr_multiplier * current_atr

        # Pattern amplitude (if available)
        pattern_amp = base_move
        if pattern is not None and pattern.pushes:
            push_distances = [p.distance for p in pattern.pushes]
            avg_push = np.mean(push_distances)
            # Last push is the exhaustion push - target is reversal of that
            last_push = push_distances[-1]
            # Expected move = average of avg push and last push
            pattern_amp = 0.6 * avg_push + 0.4 * last_push

        # Blend: 60% pattern, 40% ATR
        if pattern is not None:
            raw_move = 0.6 * pattern_amp + 0.4 * base_move
        else:
            raw_move = base_move

        # Volatility regime adjustment
        vol_mult = self._volatility_regime(high, low, close, entry_idx)
        expected_move = raw_move * vol_mult

        # Direction-aware (no negative expected moves)
        expected_move = abs(expected_move)

        # Percentage
        expected_move_pct = expected_move / entry_price if entry_price > 0 else 0.0

        return expected_move, expected_move_pct

    def _expected_drawdown(
        self,
        high: np.ndarray,
        low: np.ndarray,
        close: np.ndarray,
        atr: np.ndarray,
        pattern: Optional[ThreePushPattern],
        entry_idx: int,
        entry_price: float,
        probability: float,
        direction: str,
    ) -> Tuple[float, float]:
        """
        Calculate expected drawdown (worst-case adverse excursion).

        Factors:
        1. ATR-based noise level
        2. Recent swing volatility
        3. Pattern failure adjustment
        4. Probability adjustment (higher prob = tighter DD)
        """
        current_atr = atr[min(entry_idx, len(atr) - 1)]

        # Base drawdown: 1.0-1.5x ATR (based on probability)
        prob_factor = max(0.5, 1.5 - probability / 100.0)  # 1.5 at 0%, 0.5 at 100%
        base_dd = prob_factor * current_atr

        # Recent volatility component
        lookback = min(self.volatility_lookback, entry_idx)
        if lookback >= 10:
            recent_highs = high[max(0, entry_idx - lookback):entry_idx + 1]
            recent_lows = low[max(0, entry_idx - lookback):entry_idx + 1]
            recent_range = np.max(recent_highs) - np.min(recent_lows)
            vol_component = recent_range * 0.1  # 10% of recent range
        else:
            vol_component = base_dd * 0.5

        # Pattern quality adjustment
        pattern_factor = 1.0
        if pattern is not None:
            # Better pattern = less drawdown
            quality = (pattern.momentum_decay + pattern.overlap_score + pattern.symmetry_score) / 3
            pattern_factor = max(0.6, 1.4 - quality)  # 0.6-1.4 range

        expected_dd = (0.5 * base_dd + 0.3 * vol_component + 0.2 * base_dd) * pattern_factor
        expected_dd = abs(expected_dd)
        expected_dd_pct = expected_dd / entry_price if entry_price > 0 else 0.0

        return expected_dd, expected_dd_pct

    def _risk_reward_ratio(
        self,
        expected_move: float,
        expected_drawdown: float,
        probability: float,
    ) -> float:
        """
        Calculate risk/reward ratio.

        RR = Expected Reward / Expected Risk

        Where:
        - Expected Reward = probability_adjusted_move
        - Expected Risk = expected_drawdown
        """
        if expected_drawdown == 0:
            return 0.0

        # Adjust expected move by probability
        prob_adjusted_move = expected_move * (probability / 100.0)

        # Also account for the chance of full drawdown
        prob_adjusted_risk = expected_drawdown * (1.0 - probability / 100.0)

        if prob_adjusted_risk == 0:
            return expected_move / expected_drawdown

        return prob_adjusted_move / prob_adjusted_risk

    def _calculate_levels(
        self,
        entry_price: float,
        expected_move: float,
        expected_drawdown: float,
        direction: str,
    ) -> Tuple[float, float]:
        """Calculate target and stop-loss levels."""
        if direction == 'bullish':
            target_price = entry_price + expected_move
            stop_loss = entry_price - expected_drawdown
        elif direction == 'bearish':
            target_price = entry_price - expected_move
            stop_loss = entry_price + expected_drawdown
        else:
            # Neutral: use symmetric levels
            target_price = entry_price + expected_move
            stop_loss = entry_price - expected_drawdown

        return target_price, stop_loss

    def _volatility_regime(
        self,
        high: np.ndarray,
        low: np.ndarray,
        close: np.ndarray,
        idx: int,
    ) -> float:
        """
        Determine volatility regime multiplier.

        Returns:
        - > 1.0: High volatility (larger moves expected)
        - < 1.0: Low volatility (smaller moves expected)
        - = 1.0: Normal volatility
        """
        n = len(close)
        lookback = min(self.volatility_lookback, idx)

        if lookback < self.atr_period * 2:
            return 1.0

        # Calculate current vs historical ATR
        atr = self._calculate_atr(high, low, close)
        current_atr = atr[idx]

        historical_atr = np.mean(atr[max(0, idx - lookback):idx])

        if historical_atr == 0:
            return 1.0

        ratio = current_atr / historical_atr

        # Map ratio to multiplier
        # ratio < 1.0: low vol → multiplier < 1.0 (smaller moves)
        # ratio > 1.0: high vol → multiplier > 1.0 (larger moves)
        # Clamp to reasonable range
        return max(0.5, min(2.0, ratio))

    def position_size(
        self,
        account_equity: float,
        risk_per_trade_pct: float,
        entry_price: float,
        stop_loss: float,
        direction: str,
    ) -> Dict[str, float]:
        """
        Calculate position size based on risk management rules.

        Parameters
        ----------
        account_equity : float
            Total account equity.
        risk_per_trade_pct : float
            Percentage of equity to risk per trade (e.g., 1.0 = 1%).
        entry_price : float
            Planned entry price.
        stop_loss : float
            Stop-loss price level.
        direction : str
            'bullish' or 'bearish'.

        Returns
        -------
        Dict with position_size, risk_amount, units.
        """
        risk_amount = account_equity * (risk_per_trade_pct / 100.0)
        risk_per_unit = abs(entry_price - stop_loss)

        if risk_per_unit == 0:
            return {
                'position_size': 0.0,
                'risk_amount': risk_amount,
                'units': 0.0,
                'risk_per_unit': risk_per_unit,
            }

        units = risk_amount / risk_per_unit
        position_size = units * entry_price

        return {
            'position_size': round(position_size, 2),
            'risk_amount': round(risk_amount, 2),
            'units': round(units, 6),
            'risk_per_unit': round(risk_per_unit, 6),
        }
