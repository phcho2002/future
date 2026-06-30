import pandas as pd

from future_quant.config import QuantConfig
from future_quant.core.types import ChannelAnalysis, MarketRegime, PushSet, SignalSide, TradeLevels


def build_trade_levels(
    df: pd.DataFrame,
    side: SignalSide,
    push_set: PushSet,
    market_state,
    config: QuantConfig,
    account_equity: float | None = None,
) -> TradeLevels:
    if side == SignalSide.NONE or len(push_set.pushes) < 2:
        return TradeLevels()

    last = df.iloc[-1]
    entry = float(last["close"])
    third = push_set.pushes[-1]
    pattern_start = push_set.pushes[0].start_idx
    pattern_df = df.iloc[pattern_start : third.end_idx + 1]
    pattern_high = float(pattern_df["high"].max())
    pattern_low = float(pattern_df["low"].min())
    height = pattern_high - pattern_low
    atr = float(last["atr"])
    stop_buffer = (
        config.wide_channel_stop_atr_buffer
        if market_state.regime == MarketRegime.WIDE_CHANNEL
        else config.stop_atr_buffer
    ) * atr

    if side == SignalSide.SHORT:
        stop = pattern_high + stop_buffer
        target_1 = entry - height
        target_2 = entry - 2 * height
    else:
        stop = pattern_low - stop_buffer
        target_1 = entry + height
        target_2 = entry + 2 * height

    position_size = None
    if account_equity is not None:
        risk_cash = account_equity * config.risk_per_trade
        risk_per_unit = abs(entry - stop)
        position_size = risk_cash / risk_per_unit if risk_per_unit > 0 else 0.0

    risk_per_unit = abs(entry - stop)
    reward = abs(target_1 - entry)
    reward_risk = reward / risk_per_unit if risk_per_unit > 0 else 0.0

    return TradeLevels(
        entry=entry,
        stop=stop,
        target_1=target_1,
        target_2=target_2,
        position_size=position_size,
        reward_risk=reward_risk,
    )


def build_breakout_levels(
    df: pd.DataFrame,
    side: SignalSide,
    channel: ChannelAnalysis,
    market_state,
    config: QuantConfig,
    account_equity: float | None = None,
) -> TradeLevels:
    """突破入场的止损/目标：ATR 止损 + 固定 R 倍数目标。

    旧设计（止损=区间另一侧）在突破场景下几何倒挂：突破入场已离区间另一侧
    很远，止损距离 ≈ 2×区间高度，而目标=形态高度仅 ≈ 1×区间高度，导致
    R:R≈0.4（回测验证：胜率 60-78% 仍全亏）。

    新几何让 R:R 由参数决定（默认 2:1）：
      - entry = 突破K线收盘
      - 止损贴在突破K线反侧外移 breakout_stop_atr_buffer*ATR（距离小，~1-1.5 ATR）
      - 目标 = 风险距离 × breakout_target_r（target_1，默认 R:R=2:1）
    """
    if (
        side == SignalSide.NONE
        or channel.upper_line_price is None
        or channel.lower_line_price is None
    ):
        return TradeLevels()

    last = df.iloc[-1]
    entry = float(last["close"])
    atr = float(last["atr"]) or 1e-12
    buffer = config.breakout_stop_atr_buffer * atr

    if side == SignalSide.SHORT:
        # 突破下轨做空：止损贴在突破K线高点上方
        stop = float(last["high"]) + buffer
    else:
        # 突破上轨做多：止损贴在突破K线低点下方
        stop = float(last["low"]) - buffer

    # 方向合理性兜底：止损必须在入场反侧，否则视为无效结构
    if side == SignalSide.LONG and stop >= entry:
        return TradeLevels()
    if side == SignalSide.SHORT and stop <= entry:
        return TradeLevels()

    risk_dist = abs(entry - stop)
    if side == SignalSide.SHORT:
        target_1 = entry - risk_dist * config.breakout_target_r
        target_2 = entry - risk_dist * config.breakout_target2_r
    else:
        target_1 = entry + risk_dist * config.breakout_target_r
        target_2 = entry + risk_dist * config.breakout_target2_r

    position_size = None
    if account_equity is not None:
        risk_cash = account_equity * config.risk_per_trade
        risk_per_unit = abs(entry - stop)
        position_size = risk_cash / risk_per_unit if risk_per_unit > 0 else 0.0

    risk_per_unit = abs(entry - stop)
    reward = abs(target_1 - entry)
    reward_risk = reward / risk_per_unit if risk_per_unit > 0 else 0.0

    return TradeLevels(
        entry=entry,
        stop=stop,
        target_1=target_1,
        target_2=target_2,
        position_size=position_size,
        reward_risk=reward_risk,
    )


def should_stop_trading_for_day(start_equity: float, current_equity: float, config: QuantConfig) -> bool:
    if start_equity <= 0:
        raise ValueError("start_equity must be positive")
    drawdown = (start_equity - current_equity) / start_equity
    return drawdown >= config.daily_loss_stop
