import pandas as pd

from future_quant.config import QuantConfig
from future_quant.core.types import (
    ChannelAnalysis,
    ChannelType,
    MarketState,
    Push,
    PushSet,
    SignalSide,
    TradeLevels,
    TradeSignal,
    TrendDirection,
)
from future_quant.pushes import PushSet
from future_quant.risk import build_breakout_levels, build_trade_levels


def generate_signal(
    df: pd.DataFrame,
    market_state: MarketState,
    channel: ChannelAnalysis,
    push_set: PushSet,
    config: QuantConfig,
    account_equity: float | None = None,
) -> TradeSignal:
    """突破触发 + 加权评分信号模型。

    触发条件（必要门槛）：最近 K 线收盘价突破通道边界（区间上/下轨或楔形
    轨道）——收盘越过边界 ≥ ``breakout_atr_margin``*ATR 且量能达标。
    其余维度（通道形态/推数/突破力度/exhaustion/结构/风报比）全部转为评分，
    总分 ≥ ``signal_score_threshold`` 才出有效信号。

    方向择优：同时评估「顺突破方向」候选；楔形场景额外评估「反三推」反转
    候选，取总分最高者。
    """
    # ---- 必要门槛：通道边界价格可用 ----
    if channel.upper_line_price is None or channel.lower_line_price is None:
        return TradeSignal(entry_reason="no channel boundary available")

    last_idx = len(df) - 1
    last = df.iloc[last_idx]
    close = float(last["close"])
    atr = float(last["atr"]) or 1e-12
    upper = float(channel.upper_line_price)
    lower = float(channel.lower_line_price)

    # ---- 必要门槛：突破判定（收盘 + ATR 余量 + 量能）----
    margin = config.breakout_atr_margin * atr
    volume_ok = _volume_ok(df, last_idx, config)

    breakout_atr_mult = 0.0
    candidates: list[tuple[SignalSide, str, float]] = []  # (side, trigger, breakout_atr_mult)

    if close > upper + margin and volume_ok:
        breakout_atr_mult = (close - upper) / atr
        candidates.append((SignalSide.LONG, "range_breakout", breakout_atr_mult))
    if close < lower - margin and volume_ok:
        bmult = (lower - close) / atr
        candidates.append((SignalSide.SHORT, "range_breakout", bmult))

    # ---- 楔形场景：额外反三推候选（择优）----
    last_push = push_set.pushes[-1] if push_set.pushes else None
    if (
        channel.channel_type
        in (ChannelType.CONVERGING_WEDGE, ChannelType.PARABOLIC_WEDGE, ChannelType.EXPANDING_TRIANGLE)
        and last_push is not None
    ):
        rev_side = SignalSide.SHORT if last_push.direction == TrendDirection.BULL else SignalSide.LONG
        # 反转候选仅在 exhaustion 有支撑时纳入（避免噪声反转）
        if _exhaustion_usable(push_set, config):
            # 反转方向若与某个突破候选一致，跳过重复；否则补充
            if not any(c[0] == rev_side for c in candidates):
                candidates.append((rev_side, "wedge_reversal", breakout_atr_mult))

    if not candidates:
        return TradeSignal(
            entry_reason=f"no breakout (close={close:.2f} vs [{lower:.2f},{upper:.2f}], vol_ok={volume_ok})"
        )

    # ---- 逐候选评分，择优 ----
    push_count = len(push_set.pushes)
    ch_score_base, _ = _score_channel(channel, config)
    push_score = _score_push_count(push_count, config)
    exh_score = _score_exhaustion(push_set, config)

    best: TradeSignal | None = None
    for side, trigger, bmult in candidates:
        # 突破候选用 build_breakout_levels；反转候选用 build_trade_levels
        if trigger == "wedge_reversal":
            levels = build_trade_levels(
                df=df, side=side, push_set=push_set,
                market_state=market_state, config=config, account_equity=account_equity,
            )
        else:
            levels = build_breakout_levels(
                df=df, side=side, channel=channel,
                market_state=market_state, config=config, account_equity=account_equity,
            )
        # 无效结构（如方向兜底失败）直接放弃该候选
        if levels.entry is None or levels.stop is None or levels.target_1 is None:
            continue

        brk_score = _score_breakout(bmult, volume_ok, config)
        struct_score, struct_reason = _score_structure(df, push_set, last_push, config)
        rr_score = _score_reward_risk(levels, config)

        total = ch_score_base + push_score + brk_score + exh_score + struct_score + rr_score
        breakdown = {
            "channel": round(ch_score_base, 1),
            "push_count": round(push_score, 1),
            "breakout": round(brk_score, 1),
            "exhaustion": round(exh_score, 1),
            "structure": round(struct_score, 1),
            "reward_risk": round(rr_score, 1),
        }

        candidate_sig = TradeSignal(
            side=side,
            is_valid=total >= config.signal_score_threshold,
            entry_reason=(
                f"{'scored' if total >= config.signal_score_threshold else 'score'} "
                f"{total:.1f}/{config.signal_score_threshold} [{trigger}, {push_count}推, "
                f"{channel.channel_type.value}]"
            ),
            levels=levels,
            reverse_on_failure=(trigger == "wedge_reversal"),
            metadata={
                "trigger": trigger,
                "boundary_side": "upper" if side == SignalSide.LONG else "lower",
                "score": round(total, 1),
                "score_breakdown": breakdown,
                "push_count": push_count,
                "exhaustion_score": push_set.exhaustion_score,
                "reward_risk": levels.reward_risk,
                "channel_type": channel.channel_type.value,
                "breakout_atr_mult": round(bmult, 2),
                "candidates_evaluated": len(candidates),
            },
        )
        # 择优：取总分最高；同等分优先突破候选（reverse_on_failure=False 更稳健）
        if best is None:
            best = candidate_sig
        else:
            cur_total = float(candidate_sig.metadata["score"])
            best_total = float(best.metadata["score"])
            if cur_total > best_total:
                best = candidate_sig

    if best is None:
        return TradeSignal(entry_reason="all candidates had invalid trade levels")
    if not best.is_valid:
        # 保留评分细节便于诊断
        return TradeSignal(
            entry_reason=(
                f"score {best.metadata['score']:.1f} < {config.signal_score_threshold} "
                f"({best.metadata['score_breakdown']})"
            ),
            metadata=best.metadata,
        )
    return best


# ---------------------------------------------------------------- scoring
def _score_channel(channel: ChannelAnalysis, config: QuantConfig) -> tuple[float, str]:
    """通道形态得分：楔形最高，平行区间次之，扩散再次，未定型最低。"""
    ct = channel.channel_type
    if ct in (ChannelType.CONVERGING_WEDGE, ChannelType.PARABOLIC_WEDGE):
        return config.channel_wedge_score, "wedge"
    if ct == ChannelType.PARALLEL:
        return config.channel_parallel_score, "parallel"
    if ct == ChannelType.EXPANDING_TRIANGLE:
        return config.channel_expanding_score, "expanding"
    if ct == ChannelType.THREE_PUSH_NON_WEDGE:
        return config.channel_three_push_score, "three_push_non_wedge"
    return config.channel_unknown_score, "unknown"


def _score_push_count(push_count: int, config: QuantConfig) -> float:
    """推数得分：3推满分，2推需其他维度补偿，<2 推无分。"""
    if push_count >= 3:
        return config.push_count_three_score
    if push_count == 2:
        return config.push_count_two_score
    return 0.0


def _score_breakout(breakout_atr_mult: float, volume_ok: bool, config: QuantConfig) -> float:
    """突破力度得分（提权维度）。

    按 breakout_margin/ATR 的倍数在 [breakout_min_atr, breakout_full_atr] 区间线性
    映射到 [0, breakout_score_max]；volume 达标再加成。
    """
    lo = config.breakout_min_atr
    hi = config.breakout_full_atr
    if hi <= lo:
        ratio = 1.0
    else:
        ratio = max(0.0, min(1.0, (breakout_atr_mult - lo) / (hi - lo)))
    score = ratio * config.breakout_score_max
    if volume_ok:
        score += config.breakout_volume_bonus * config.breakout_score_max
    return min(score, config.breakout_score_max)


def _score_exhaustion(push_set: PushSet, config: QuantConfig) -> float:
    """exhaustion 得分：综合分 × 满分，passed_checks 需达标。"""
    passed = push_set.exhaustion_details.get("passed_checks", 0)
    if passed < config.exhaustion_min_checks_for_score:
        return 0.0
    base = push_set.exhaustion_score or 0.0
    return max(0.0, min(1.0, base)) * config.exhaustion_score_max


def _score_structure(
    df: pd.DataFrame, push_set: PushSet, last_push: Push | None, config: QuantConfig
) -> tuple[float, str]:
    """结构加分：三推极值确认 + 反转K线质量，各占一半。"""
    if last_push is None:
        return 0.0, "no_push"
    half = config.structure_score_max / 2.0
    parts = []

    # 三推极值确认（3推时才有意义；2推给半分）
    if len(push_set.pushes) >= 3 and _third_push_confirms_extreme(df, last_push, push_set, config):
        parts.append(half)
        parts.append("extreme_ok")
    elif len(push_set.pushes) < 3:
        parts.append(half * 0.5)
        parts.append("n/a_2push")
    else:
        parts.append("no_extreme")

    # 反转K线质量：last_push 附近是否存在强反转体作为代理。
    seg = df.iloc[max(0, last_push.end_idx - 2): min(len(df), last_push.end_idx + 3)]
    if not seg.empty:
        atr = float(seg["atr"].mean()) or 1e-12
        strong = bool(
            (seg["body"].fillna(0) >= config.reversal_body_atr_ratio * atr).any()
        )
        if strong:
            parts.append(half)
            parts.append("reversal_strong")
    return sum(p for p in parts if isinstance(p, (int, float))), " ".join(
        p for p in parts if isinstance(p, str)
    )


def _score_reward_risk(levels: TradeLevels, config: QuantConfig) -> float:
    """风报比得分（完全评分项，无硬底线）：R:R 在 [min, full] 线性映射到满分。"""
    rr = levels.reward_risk
    if rr is None:
        return 0.0
    lo = config.reward_risk_min_score
    hi = config.reward_risk_full
    if hi <= lo:
        ratio = 1.0 if rr >= hi else 0.0
    else:
        ratio = max(0.0, min(1.0, (rr - lo) / (hi - lo)))
    return ratio * config.reward_risk_score_max


def _volume_ok(df: pd.DataFrame, idx: int, config: QuantConfig) -> bool:
    """最近一根 K 线的量能是否 ≥ 近期均值的 ``breakout_volume_ratio`` 倍。"""
    lookback = df.iloc[max(0, idx - config.breakout_volume_lookback): idx]
    if lookback.empty:
        return True
    avg_vol = float(lookback["volume"].mean()) or 1e-12
    return float(df.iloc[idx]["volume"]) >= avg_vol * config.breakout_volume_ratio


def _exhaustion_usable(push_set: PushSet, config: QuantConfig) -> bool:
    """楔形反转候选是否值得纳入（exhaustion 须有基本支撑）。"""
    passed = push_set.exhaustion_details.get("passed_checks", 0)
    return passed >= config.exhaustion_min_checks_for_score


def _third_push_confirms_extreme(
    df: pd.DataFrame,
    third: Push,
    push_set: PushSet,
    config: QuantConfig,
) -> bool:
    """The third push must reach a new pattern extreme.

    For a bearish reversal (third push up) the third push's high must be at or
    above the pattern high; for a bullish reversal its low must be at or below
    the pattern low. A small ``third_push_extreme_tolerance`` slack is allowed
    so a marginally lower high / higher low still qualifies.
    """
    pattern_start = push_set.pushes[0].start_idx
    pattern_end = third.end_idx
    seg = df.iloc[pattern_start : pattern_end + 1]
    if seg.empty:
        return False
    pattern_high = float(seg["high"].max())
    pattern_low = float(seg["low"].min())
    tol = config.third_push_extreme_tolerance

    # Use the third push's own high/low (not just its close) so a push that
    # spiked to a new extreme but closed off it still counts.
    third_seg = df.iloc[third.start_idx : third.end_idx + 1]
    if third_seg.empty:
        return False
    third_high = float(third_seg["high"].max())
    third_low = float(third_seg["low"].min())

    if third.direction == TrendDirection.BULL:
        # third push is bullish -> bearish reversal; need new pattern high
        return third_high >= pattern_high * (1 - tol)
    # third push is bearish -> bullish reversal; need new pattern low
    return third_low <= pattern_low * (1 + tol)
