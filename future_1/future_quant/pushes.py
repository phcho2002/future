import pandas as pd

from future_quant.config import QuantConfig
from future_quant.core.types import Push, PushSet, TrendDirection
from future_quant.indicators import bar_overlap_ratio


def detect_pushes(df: pd.DataFrame, config: QuantConfig) -> PushSet:
    scan_start = max(0, len(df) - config.push_scan_bars)
    scan = df.iloc[scan_start:].copy()
    if len(scan) < config.push_min_bars:
        return PushSet()

    pushes: list[Push] = []
    pullbacks: list[tuple[int, int]] = []
    i = 1
    while i < len(scan):
        direction = _close_direction(scan.iloc[i - 1], scan.iloc[i])
        if direction == TrendDirection.UNKNOWN:
            i += 1
            continue

        start = i - 1
        end = i
        while end + 1 < len(scan) and _close_direction(scan.iloc[end], scan.iloc[end + 1]) == direction:
            end += 1

        if end - start + 1 >= config.push_min_bars:
            push = _build_push(scan, scan_start, start, end, direction)
            if push.atr_multiple >= config.push_min_atr:
                pushes.append(push)

        i = max(end + 1, i + 1)

    filtered_pushes, filtered_pullbacks = _link_pushes_with_pullbacks(df, pushes, config)
    score, details = _score_exhaustion(filtered_pushes, config)
    return PushSet(
        pushes=filtered_pushes[-3:],
        pullbacks=filtered_pullbacks[-2:],
        exhaustion_score=score,
        exhaustion_details=details,
    )


def _close_direction(prev: pd.Series, current: pd.Series) -> TrendDirection:
    if current["close"] > prev["close"]:
        return TrendDirection.BULL
    if current["close"] < prev["close"]:
        return TrendDirection.BEAR
    return TrendDirection.UNKNOWN


def _build_push(scan: pd.DataFrame, offset: int, start: int, end: int, direction: TrendDirection) -> Push:
    segment = scan.iloc[start : end + 1]
    start_price = float(segment.iloc[0]["close"])
    end_price = float(segment.iloc[-1]["close"])
    net_move = end_price - start_price
    bars = len(segment)
    atr = float(segment["atr"].mean()) or 1e-12
    body = segment["body"].fillna(0)
    wick_sum = (segment["upper_wick"] + segment["lower_wick"]).replace(0, 1e-12)
    strong_body_ratio = float((body > 2 * wick_sum).mean())
    if direction == TrendDirection.BULL:
        long_wick_ratio = float((segment["upper_wick"] >= body.replace(0, 1e-12)).mean())
    else:
        long_wick_ratio = float((segment["lower_wick"] >= body.replace(0, 1e-12)).mean())
    overlaps = [
        bar_overlap_ratio(segment.iloc[j - 1], segment.iloc[j])
        for j in range(1, len(segment))
    ]
    overlap_ratio = float(sum(x > 0.20 for x in overlaps) / len(overlaps)) if overlaps else 0.0
    return Push(
        start_idx=offset + start,
        end_idx=offset + end,
        direction=direction,
        start_price=start_price,
        end_price=end_price,
        net_move=net_move,
        atr_multiple=abs(net_move) / atr,
        bars=bars,
        avg_move_per_bar=abs(net_move) / bars,
        strong_body_ratio=strong_body_ratio,
        long_wick_ratio=long_wick_ratio,
        volume_mean=float(segment["volume"].mean()),
        slope=net_move / bars,
        overlap_ratio=overlap_ratio,
    )


def _link_pushes_with_pullbacks(
    df: pd.DataFrame,
    pushes: list[Push],
    config: QuantConfig,
) -> tuple[list[Push], list[tuple[int, int]]]:
    if len(pushes) < 3:
        return pushes, []

    linked: list[Push] = [pushes[0]]
    pullbacks: list[tuple[int, int]] = []
    for prev, current in zip(pushes, pushes[1:]):
        if current.direction != prev.direction:
            # Only reset the sequence if this opposite move is itself a
            # meaningful reversal push (>= push_min_bars and push_min_atr).
            # A single counter-trend bar used to discard the whole linked
            # sequence; that was far too aggressive and destroyed valid
            # three-push structures.
            if current.bars >= config.push_min_bars and current.atr_multiple >= config.push_min_atr:
                linked = [current]
                pullbacks = []
            continue
        pullback = df.iloc[prev.end_idx + 1 : current.start_idx]
        if _is_meaningful_pullback(pullback, prev, config):
            pullbacks.append((prev.end_idx + 1, current.start_idx - 1))
            linked.append(current)
        else:
            linked[-1] = current
    return linked, pullbacks


def _is_meaningful_pullback(pullback: pd.DataFrame, prev: Push, config: QuantConfig) -> bool:
    if pullback.empty:
        return False
    atr = float(pullback["atr"].mean()) or 1e-12
    if prev.direction == TrendDirection.BULL:
        move = prev.end_price - float(pullback["close"].min())
        has_reverse_bar = bool((pullback["close"] < pullback["open"]).any())
    else:
        move = float(pullback["close"].max()) - prev.end_price
        has_reverse_bar = bool((pullback["close"] > pullback["open"]).any())
    return has_reverse_bar and move >= config.pullback_min_atr * atr


def _score_exhaustion(pushes: list[Push], config: QuantConfig) -> tuple[float | None, dict]:
    if len(pushes) < 3:
        return None, {"reason": "need three linked pushes"}

    p1, p2, p3 = pushes[-3:]
    prior_strength = (_push_strength(p1) + _push_strength(p2)) / 2
    third_strength = _push_strength(p3)
    strength_ratio = third_strength / prior_strength if prior_strength else 1.0

    checks = {
        "distance_decay": abs(p3.net_move) <= abs(p1.net_move) * config.exhaustion_distance_ratio,
        "avg_bar_decay": p3.avg_move_per_bar <= p1.avg_move_per_bar * config.exhaustion_avg_bar_ratio,
        "strong_body_decay": p3.strong_body_ratio <= p1.strong_body_ratio * config.exhaustion_strong_body_ratio,
        "volume_dry_up": p3.volume_mean <= ((p1.volume_mean + p2.volume_mean) / 2) * config.exhaustion_volume_ratio,
        "long_wick_pressure": p3.long_wick_ratio >= config.long_wick_ratio_threshold,
        "slope_decay": abs(p3.slope) <= abs(p1.slope) * config.exhaustion_slope_ratio,
        "wide_overlap": p3.overlap_ratio >= config.bar_overlap_threshold,
    }
    score = sum(checks.values()) / len(checks)
    details = {
        **checks,
        "passed_checks": int(sum(checks.values())),
        "total_checks": len(checks),
        "third_strength": third_strength,
        "prior_strength": prior_strength,
        "strength_ratio": strength_ratio,
    }
    return score, details


def _push_strength(push: Push) -> float:
    return (
        push.atr_multiple * 0.35
        + push.avg_move_per_bar * 0.25
        + push.strong_body_ratio * 0.20
        + max(0.0, 1.0 - push.long_wick_ratio) * 0.10
        + max(0.0, 1.0 - push.overlap_ratio) * 0.10
    )
