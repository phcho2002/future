"""Tests for the ATR-normalized slope and price-level-invariant channel typing.

The bug being prevented: raw-price linear-regression slopes made gold (~600)
    and rebar (~3000) classify into different channel types for identical
    shapes. After normalization by ATR they must classify identically.
"""

import pandas as pd

from future_quant.channels import analyze_channel
from future_quant.config import QuantConfig
from future_quant.core.types import PushSet
from future_quant.indicators import add_indicators, normalized_slope
import numpy as np


def test_normalized_slope_is_scale_invariant():
    """The same shape at two price levels must yield the same normalized slope."""
    base = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0])
    scaled = base * 100.0  # same shape, 100x the price level
    # ATR scales with price too, so normalized slope must match.
    assert abs(normalized_slope(base, atr=0.5) - normalized_slope(scaled, atr=50.0)) < 1e-9


def test_channel_type_invariant_to_price_level():
    """Two series with identical relative shape but very different absolute
    prices must be assigned the same channel type."""
    rng = np.random.default_rng(42)
    wiggle = rng.uniform(-0.1, 0.1, 60)

    def make_df(level: float) -> pd.DataFrame:
        # A converging shape: highs decelerate, lows accelerate.
        t = np.arange(60)
        highs = level + 5.0 - 0.05 * t + wiggle
        lows = level - 5.0 + 0.05 * t + wiggle
        closes = (highs + lows) / 2
        opens = closes - 0.01
        return pd.DataFrame(
            {"open": opens, "high": highs, "low": lows, "close": closes, "volume": 100.0}
        )

    cfg = QuantConfig()
    cheap = add_indicators(make_df(10.0), atr_period=cfg.atr_period)
    expensive = add_indicators(make_df(3000.0), atr_period=cfg.atr_period)

    cheap_ch = analyze_channel(cheap, PushSet(), cfg)
    expensive_ch = analyze_channel(expensive, PushSet(), cfg)

    assert cheap_ch.channel_type == expensive_ch.channel_type, (
        f"price-level leakage: cheap={cheap_ch.channel_type} "
        f"vs expensive={expensive_ch.channel_type}"
    )
