"""Tests for the breakout-triggered, score-based signal model.

The model changed from "wedge reversal with multiple hard gates" to
"breakout triggers the signal; everything else is scored". These tests pin
down the new behaviour:
  - a clean range breakout produces a valid signal with trigger=range_breakout
  - no breakout (price inside the channel) -> no signal
  - low reward:risk only lowers the score, never hard-rejects
  - candidate selection picks the higher-scoring direction
  - the reward:risk score maps linearly across its anchors
"""

import numpy as np
import pandas as pd

from future_quant.config import QuantConfig
from future_quant.core.types import (
    ChannelAnalysis,
    ChannelType,
    MarketRegime,
    MarketState,
    Push,
    PushSet,
    SignalSide,
    TradeLevels,
    TrendDirection,
)
from future_quant.indicators import add_indicators
from future_quant.signals import (
    _score_reward_risk,
    _volume_ok,
    generate_signal,
)


def _state(allow=True):
    return MarketState(
        regime=MarketRegime.TRADING_RANGE,
        direction=TrendDirection.SIDEWAYS,
        allow_wedge_reversal=allow,
        reason="test",
    )


def _channel(ct=ChannelType.PARALLEL, upper=102.0, lower=98.0):
    return ChannelAnalysis(
        channel_type=ct,
        upper_line_price=upper,
        lower_line_price=lower,
        reason="test",
    )


def _at_df(n=30, price=100.0, last_close=100.0, volume=100.0):
    rng = np.random.default_rng(0)
    base = price + np.cumsum(rng.uniform(-0.2, 0.2, n))
    df = pd.DataFrame(
        {
            "open": base,
            "high": base + 0.5,
            "low": base - 0.5,
            "close": base + 0.1,
            "volume": float(volume),
        }
    )
    # Force the last close + volume so the breakout is deterministic.
    df.loc[df.index[-1], "close"] = last_close
    df.loc[df.index[-1], "volume"] = float(volume)
    return add_indicators(df, atr_period=14)


def _b(i, end_price=100.0, direction=TrendDirection.BULL) -> Push:
    return Push(
        start_idx=i * 5,
        end_idx=i * 5 + 4,
        direction=direction,
        start_price=99.0,
        end_price=end_price,
        net_move=end_price - 99.0,
        atr_multiple=4.0,
        bars=5,
        avg_move_per_bar=0.2,
        strong_body_ratio=0.6,
        long_wick_ratio=0.1,
        volume_mean=100.0,
        slope=0.2,
        overlap_ratio=0.3,
    )


def _pushset(n=3):
    return PushSet(
        pushes=[_b(0), _b(1), _b(2)],
        exhaustion_score=0.90,
        exhaustion_details={"passed_checks": 7, "total_checks": 7},
    )


# --------------------------------------------------------- breakout trigger
def test_range_breakout_up_produces_long_signal():
    """Close clearly above the upper boundary + volume -> LONG range_breakout."""
    # last_close 110 vs upper 102: clearly a breakout on volume.
    df = _at_df(n=30, last_close=110.0, volume=500.0)
    sig = generate_signal(df, _state(), _channel(upper=102.0, lower=98.0), _pushset(), QuantConfig())
    assert sig.is_valid
    assert sig.side == SignalSide.LONG
    assert sig.metadata["trigger"] == "range_breakout"
    assert sig.metadata["boundary_side"] == "upper"


def test_range_breakout_down_produces_short_signal():
    """Close clearly below the lower boundary + volume -> SHORT range_breakout."""
    df = _at_df(n=30, last_close=90.0, volume=500.0)
    sig = generate_signal(df, _state(), _channel(upper=102.0, lower=98.0), _pushset(), QuantConfig())
    assert sig.is_valid
    assert sig.side == SignalSide.SHORT
    assert sig.metadata["trigger"] == "range_breakout"
    assert sig.metadata["boundary_side"] == "lower"


def test_no_breakout_inside_channel_gives_no_signal():
    """Price sitting inside the channel -> no breakout, no signal."""
    df = _at_df(n=30, last_close=100.0, volume=500.0)
    sig = generate_signal(df, _state(), _channel(upper=102.0, lower=98.0), _pushset(), QuantConfig())
    assert not sig.is_valid
    assert "no breakout" in sig.entry_reason


def test_breakout_without_volume_is_not_triggered():
    """A breakout-sized close but on thin volume must not trigger (volume gate).

    Base volume is 100 across the series; the last bar is crushed to 10 so it
    falls below the recent average * breakout_volume_ratio (1.0).
    """
    df = _at_df(n=30, last_close=110.0, volume=100.0)
    df.loc[df.index[-1], "volume"] = 10.0
    sig = generate_signal(df, _state(), _channel(upper=102.0, lower=98.0), _pushset(), QuantConfig())
    assert not sig.is_valid
    assert "no breakout" in sig.entry_reason


def test_no_boundary_prices_gives_no_signal():
    """Missing channel boundaries -> immediate rejection."""
    df = _at_df()
    ch = ChannelAnalysis(channel_type=ChannelType.UNKNOWN, reason="no boundary")
    sig = generate_signal(df, _state(), ch, _pushset(), QuantConfig())
    assert not sig.is_valid
    assert "boundary" in sig.entry_reason


# --------------------------------------------------------- reward:risk scoring
def test_reward_risk_score_is_linear_and_saturates():
    """R:R maps linearly across [min_score, full] anchors and saturates beyond."""
    cfg = QuantConfig()
    max_score = cfg.reward_risk_score_max
    lo = cfg.reward_risk_min_score
    hi = cfg.reward_risk_full

    def make_levels(rr):
        return TradeLevels(entry=100.0, stop=99.0, target_1=100.0 + rr, reward_risk=rr)

    assert _score_reward_risk(make_levels(lo), cfg) == 0.0
    # midpoint -> half of max
    mid = (lo + hi) / 2
    assert abs(_score_reward_risk(make_levels(mid), cfg) - max_score / 2) < 1e-9
    # at full -> max
    assert abs(_score_reward_risk(make_levels(hi), cfg) - max_score) < 1e-9
    # beyond full saturates at max
    assert abs(_score_reward_risk(make_levels(hi + 5), cfg) - max_score) < 1e-9


def test_low_reward_risk_is_not_hard_rejected(monkeypatch):
    """A poor R:R must only lower the score, not hard-reject (no hard floor)."""
    df = _at_df(n=30, last_close=110.0, volume=500.0)

    # Force build_breakout_levels to return a tiny reward_risk.
    import future_quant.signals as signals_mod

    def fake_levels(**kwargs):
        return TradeLevels(entry=100.0, stop=99.9, target_1=100.05, reward_risk=0.5)

    monkeypatch.setattr(signals_mod, "build_breakout_levels", fake_levels)
    monkeypatch.setattr(signals_mod, "build_trade_levels", fake_levels)

    sig = generate_signal(df, _state(), _channel(upper=102.0, lower=98.0), _pushset(), QuantConfig())
    # Even with R:R 0.5 the signal can still be valid if other dims clear the
    # threshold — the key assertion is the rr score is 0, not a hard rejection.
    assert sig.metadata.get("score_breakdown", {}).get("reward_risk") == 0.0


# --------------------------------------------------------- volume helper
def test_volume_ok_threshold():
    df = _at_df(n=30, volume=100.0)
    idx = df.index[-1]
    # average of lookback ~100, last bar 100 -> just meets ratio 1.0
    assert _volume_ok(df, idx, QuantConfig())
    # drop last bar volume below average -> fails
    df.loc[idx, "volume"] = 10.0
    assert not _volume_ok(df, idx, QuantConfig())


# --------------------------------------------------------- candidate selection
def test_candidate_selection_picks_higher_score(monkeypatch):
    """When both a breakout and reversal candidate exist, the higher score wins.

    We force the breakout levels to be excellent (high R:R) and the reversal
    levels poor, then assert the chosen side is the breakout direction.
    """
    df = _at_df(n=30, last_close=110.0, volume=500.0)

    import future_quant.signals as signals_mod
    from future_quant.core.types import ChannelType as CT

    def good_levels(**kwargs):
        return TradeLevels(entry=100.0, stop=95.0, target_1=115.0, reward_risk=3.0)

    def bad_levels(**kwargs):
        return TradeLevels(entry=100.0, stop=99.9, target_1=100.05, reward_risk=0.2)

    state = {
        "breakout": 0,
        "reversal": 0,
    }

    def fake_breakout(**kwargs):
        state["breakout"] += 1
        return good_levels()

    def fake_reversal(**kwargs):
        state["reversal"] += 1
        return bad_levels()

    monkeypatch.setattr(signals_mod, "build_breakout_levels", fake_breakout)
    monkeypatch.setattr(signals_mod, "build_trade_levels", fake_reversal)

    # Converging wedge + a bullish last push -> reversal candidate (SHORT) added
    # alongside the LONG breakout. Breakout has higher R:R -> must win.
    ps = PushSet(
        pushes=[_b(0), _b(1), _b(2, direction=TrendDirection.BULL)],
        exhaustion_score=0.90,
        exhaustion_details={"passed_checks": 7, "total_checks": 7},
    )
    ch = _channel(ct=CT.CONVERGING_WEDGE, upper=102.0, lower=98.0)
    sig = generate_signal(df, _state(), ch, ps, QuantConfig())
    assert sig.side == SignalSide.LONG
    assert sig.metadata["trigger"] == "range_breakout"
    assert sig.metadata["candidates_evaluated"] >= 1
