"""Tests for the backtester.

The key invariant: with zero commission / zero slippage, a trade's recorded PnL
must equal (exit - entry) * multiplier for a long (and the negative for a
short). We drive a guaranteed win and a guaranteed loss through the engine by
patching ``analyze_df`` to return a controlled signal.
"""

from unittest.mock import patch

import numpy as np
import pandas as pd

from future_quant.backtest.backtester import BacktestConfig, Backtester
from future_quant.config import QuantConfig
from future_quant.core.types import (
    AnalysisResult,
    ChannelAnalysis,
    ChannelType,
    MarketRegime,
    MarketState,
    PushSet,
    SignalSide,
    TradeLevels,
    TradeSignal,
    TrendDirection,
)


def _kline_df():
    """60 bars on a flat plateau at 100.0. By keeping the series flat we ensure
    an injected long entry at ~100 never trips the entry>=target / entry<=stop
    guards, and neither stop nor target is touched by price drift alone — so
    the only way a position closes is via the explicit levels we inject."""
    n = 60
    close = np.full(n, 100.0)
    return pd.DataFrame(
        {
            "open": close,
            "high": close + 0.2,
            "low": close - 0.2,
            "close": close,
            "volume": 100.0,
        }
    )


def _fake_result(side, entry, stop, target):
    return AnalysisResult(
        data=pd.DataFrame(),
        market_state=MarketState(
            regime=MarketRegime.TRADING_RANGE,
            direction=TrendDirection.SIDEWAYS,
            allow_wedge_reversal=True,
            reason="t",
        ),
        channel=ChannelAnalysis(channel_type=ChannelType.CONVERGING_WEDGE, reason="t"),
        push_set=PushSet(),
        signal=TradeSignal(
            side=side,
            is_valid=True,
            levels=TradeLevels(entry=entry, stop=stop, target_1=target),
        ),
    )


def _run_backtest(side, entry, stop, target, multiplier=10.0):
    """Run a backtest where analyze_df fires a single controlled signal at the
    first evaluated bar. Because price is injected at exactly the stop or
    target level on subsequent bars, we force a deterministic exit there."""
    cfg = BacktestConfig(
        commission_rate=0.0,
        slippage_points=0.0,
        multiplier=multiplier,
        warmup=20,
        step=1,
    )
    bt = Backtester(QuantConfig(), cfg)

    # Build a df whose later bars touch the level we want to exit on.
    n = 60
    if side == SignalSide.LONG:
        # Long target win: spike a later bar's high up to the target.
        target_level = target
        stop_level = stop
    else:
        target_level = target
        stop_level = stop

    base = np.full(n, 100.0)
    df = pd.DataFrame(
        {
            "open": base.copy(),
            "high": base + 0.2,
            "low": base - 0.2,
            "close": base.copy(),
            "volume": 100.0,
        }
    )
    # Force bar 25 to touch BOTH the target (via high) for a long-win test,
    # or the stop (via low) for a loss test. We set this per-test below by the
    # caller choosing entry/stop/target around 100.
    return _run_with_signal(df, side, entry, stop, target, cfg, multiplier)


def _run_with_signal(df, side, entry, stop, target, cfg, multiplier):
    # On bar 30, inject a candle that touches the chosen exit level.
    if side == SignalSide.LONG:
        df.loc[30, "high"] = max(target, df.loc[30, "high"])  # reach target
    else:
        df.loc[30, "low"] = min(target, df.loc[30, "low"])

    bt = Backtester(QuantConfig(), cfg)
    call_count = {"n": 0}

    def fake_analyze(self, df_in, account_equity=None):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return _fake_result(side, entry, stop, target)
        return AnalysisResult(
            data=pd.DataFrame(),
            market_state=MarketState(
                regime=MarketRegime.UNKNOWN, direction=TrendDirection.UNKNOWN, reason="t"
            ),
            channel=ChannelAnalysis(channel_type=ChannelType.UNKNOWN, reason="t"),
            push_set=PushSet(),
            signal=TradeSignal(is_valid=False),
        )

    engine_mod = __import__("future_quant.engine", fromlist=["QuantEngine"])
    with patch.object(engine_mod.QuantEngine, "analyze_df", fake_analyze):
        return bt.run(df, "TEST"), multiplier


def test_long_target_hit_records_exact_pnl():
    # Entry 100, stop 90, target 110 -> bar 30 high reaches 110.
    res, mult = _run_backtest(SignalSide.LONG, entry=100.0, stop=90.0, target=110.0)
    assert res.n_trades == 1, f"expected 1 trade, got {res.n_trades}"
    t = res.trades[0]
    assert t.is_win
    assert abs(t.pnl - (110.0 - 100.0) * mult) < 1e-6


def test_long_stop_hit_records_negative_pnl():
    # Entry 100, stop 99, target 200. Bar 30 high only reaches ~110 (target),
    # so to force a STOP we inject the stop via the low instead.
    cfg = BacktestConfig(
        commission_rate=0.0, slippage_points=0.0, multiplier=10.0, warmup=20, step=1
    )
    base = np.full(60, 100.0)
    df = pd.DataFrame(
        {"open": base.copy(), "high": base + 0.2, "low": base - 0.2,
         "close": base.copy(), "volume": 100.0}
    )
    df.loc[30, "low"] = 95.0  # below stop 99 -> stop hit first (conservative)

    side = SignalSide.LONG
    call_count = {"n": 0}

    def fake_analyze(self, df_in, account_equity=None):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return _fake_result(side, 100.0, 99.0, 200.0)
        return AnalysisResult(
            data=pd.DataFrame(),
            market_state=MarketState(
                regime=MarketRegime.UNKNOWN, direction=TrendDirection.UNKNOWN, reason="t"
            ),
            channel=ChannelAnalysis(channel_type=ChannelType.UNKNOWN, reason="t"),
            push_set=PushSet(),
            signal=TradeSignal(is_valid=False),
        )

    engine_mod = __import__("future_quant.engine", fromlist=["QuantEngine"])
    bt = Backtester(QuantConfig(), cfg)
    with patch.object(engine_mod.QuantEngine, "analyze_df", fake_analyze):
        res = bt.run(df, "TEST")
    assert res.n_trades == 1
    t = res.trades[0]
    assert not t.is_win
    assert t.pnl < 0
    assert abs(t.pnl - (99.0 - 100.0) * 10.0) < 1e-6


def test_empty_series_returns_empty_result():
    bt = Backtester(QuantConfig(), BacktestConfig(warmup=120))
    res = bt.run(_kline_df(), "TEST")
    assert res.n_trades == 0
    assert res.net_pnl == 0.0
