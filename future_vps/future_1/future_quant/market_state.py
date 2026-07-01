import pandas as pd

from future_quant.config import QuantConfig
from future_quant.core.types import MarketRegime, MarketState, TrendDirection
from future_quant.indicators import rolling_adjacent_overlap


def detect_market_state(df: pd.DataFrame, config: QuantConfig) -> MarketState:
    if len(df) < max(config.trend_window, config.range_window):
        return MarketState(MarketRegime.UNKNOWN, TrendDirection.UNKNOWN, reason="not enough bars")

    recent = df.tail(config.trend_window)
    hh_count = int((recent["high"].diff() > 0).sum())
    hl_count = int((recent["low"].diff() > 0).sum())
    ll_count = int((recent["low"].diff() < 0).sum())
    lh_count = int((recent["high"].diff() < 0).sum())

    bull = hh_count >= config.trend_min_count and hl_count >= config.trend_min_count
    bear = ll_count >= config.trend_min_count and lh_count >= config.trend_min_count

    range_inside_count = _range_inside_count(df, config.range_window)
    overlap_ratio = rolling_adjacent_overlap(
        df,
        window=config.overlap_filter_window,
        threshold=config.body_overlap_threshold,
    )

    if range_inside_count >= config.range_min_inside_count:
        return MarketState(
            regime=MarketRegime.TRADING_RANGE,
            direction=TrendDirection.SIDEWAYS,
            hh_count=hh_count,
            hl_count=hl_count,
            ll_count=ll_count,
            lh_count=lh_count,
            overlap_ratio=overlap_ratio,
            range_inside_count=range_inside_count,
            allow_wedge_reversal=True,
            reason="fixed range with broad open/close overlap",
        )

    if bull or bear:
        direction = TrendDirection.BULL if bull else TrendDirection.BEAR
        if overlap_ratio <= config.narrow_channel_overlap_ratio:
            regime = MarketRegime.NARROW_CHANNEL
            allow = False
            reason = "strong narrow channel; wedge reversal filtered"
        elif overlap_ratio >= config.wide_channel_overlap_ratio:
            regime = MarketRegime.WIDE_CHANNEL
            allow = True
            reason = "overlapping wide channel; wedge reversal priority increased"
        else:
            regime = MarketRegime.BULL_TREND if bull else MarketRegime.BEAR_TREND
            allow = overlap_ratio > config.overlap_filter_ratio
            reason = "trend with moderate overlap"
        return MarketState(
            regime=regime,
            direction=direction,
            hh_count=hh_count,
            hl_count=hl_count,
            ll_count=ll_count,
            lh_count=lh_count,
            overlap_ratio=overlap_ratio,
            range_inside_count=range_inside_count,
            allow_wedge_reversal=allow,
            reason=reason,
        )

    return MarketState(
        regime=MarketRegime.UNKNOWN,
        direction=TrendDirection.UNKNOWN,
        hh_count=hh_count,
        hl_count=hl_count,
        ll_count=ll_count,
        lh_count=lh_count,
        overlap_ratio=overlap_ratio,
        range_inside_count=range_inside_count,
        allow_wedge_reversal=overlap_ratio > config.overlap_filter_ratio,
        reason="no confirmed trend or fixed range",
    )


def _range_inside_count(df: pd.DataFrame, window: int) -> int:
    recent = df.tail(window)
    if len(recent) < window:
        return 0
    high = float(recent["high"].max())
    low = float(recent["low"].min())
    inside_open = recent["open"].between(low, high)
    inside_close = recent["close"].between(low, high)
    return int((inside_open & inside_close).sum())
