"""
signals.py
==========
基于 Renko 砖块序列的信号识别：
- 水平密集成交区（支撑/阻力）
- 遇阻回落 / 遇阻回升 / 突破
- 趋势确认后的回撤再启动
- RSI 超买超卖风险警示
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

from renko import rsi_on_bricks


@dataclass
class Signal:
    symbol: str
    name: str
    datetime: pd.Timestamp
    close: float
    brick_size: float
    signal_type: str
    direction: int  # 1 多头, -1 空头, 0 中性/警示
    strength_score: float
    rsi_brick: Optional[float]
    nearest_support: Optional[float]
    nearest_resistance: Optional[float]
    risk_note: str
    detail: str


def detect_zones(
    renko: pd.DataFrame,
    brick_size: float,
    lookback_bricks: int = 60,
    bin_mult: float = 1.5,
    min_cluster_bricks: int = 4,
) -> list[dict]:
    """在 Renko close 序列上识别水平密集成交区。

    Returns
    -------
    list[dict]: 每个区包含 center, lower, upper, count, strength
    """
    if len(renko) < lookback_bricks:
        return []

    tail = renko.tail(lookback_bricks)
    prices = tail["close"].to_numpy(dtype=float)
    if len(prices) < min_cluster_bricks:
        return []

    bin_width = max(brick_size * bin_mult, 1e-9)
    min_p = prices.min() - bin_width
    max_p = prices.max() + bin_width
    bins = np.arange(min_p, max_p + bin_width, bin_width)
    if len(bins) < 3:
        return []

    counts, edges = np.histogram(prices, bins=bins)
    centers = (edges[:-1] + edges[1:]) / 2.0

    # 局部峰值（频数 >= min_cluster_bricks，且不低于左右邻居）
    zones = []
    for i in range(1, len(counts) - 1):
        c = int(counts[i])
        if c < min_cluster_bricks:
            continue
        if c < counts[i - 1] or c < counts[i + 1]:
            continue
        zones.append({
            "center": float(centers[i]),
            "lower": float(edges[i]),
            "upper": float(edges[i + 1]),
            "count": c,
            "strength": float(c) / len(prices),
        })

    # 按强度排序
    zones.sort(key=lambda z: z["strength"], reverse=True)
    return zones


def _nearest_zones(zones: list[dict], price: float) -> tuple[Optional[float], Optional[float]]:
    """返回 (nearest_support, nearest_resistance)。"""
    supports = [z["center"] for z in zones if z["center"] < price]
    resistances = [z["center"] for z in zones if z["center"] > price]
    support = max(supports) if supports else None
    resistance = min(resistances) if resistances else None
    return support, resistance


def _in_zone(price: float, zone: dict, tolerance: float) -> bool:
    """价格是否在密集区（含容差）内。"""
    return zone["lower"] - tolerance <= price <= zone["upper"] + tolerance


def zone_signals(
    renko: pd.DataFrame,
    zones: list[dict],
    brick_size: float,
    touch_tolerance_bricks: float = 0.8,
    breakout_confirm_bricks: int = 2,
) -> list[dict]:
    """基于密集区生成突破 / 遇阻信号。

    逻辑：
      - 当前价（最近收盘）位于某密集区：若此前接近该区后反向出现 N 块砖 → rejection
      - 当前价明显越过某密集区，且后续有 N 块同向确认砖 → breakout

    Returns
    -------
    list[dict]: 信号摘要列表
    """
    if len(renko) < breakout_confirm_bricks + 3 or not zones:
        return []

    signals = []
    tolerance = brick_size * touch_tolerance_bricks
    last_close = float(renko["close"].iloc[-1])
    last_dir = int(renko["direction"].iloc[-1])
    prev_dir = int(renko["direction"].iloc[-2]) if len(renko) >= 2 else 0

    for z in zones:
        center = z["center"]
        # 突破：最近收盘在区外，且突破方向持续
        if last_close > z["upper"] and last_dir == 1:
            # 确认前价格在区内或下方，现在向上突破
            recent_before = renko.tail(breakout_confirm_bricks + 3).head(3)
            was_inside_or_below = any(
                r["close"] <= z["upper"] + tolerance for _, r in recent_before.iterrows()
            )
            confirm_tail = renko.tail(breakout_confirm_bricks)
            if was_inside_or_below and all(confirm_tail["direction"] == 1):
                signals.append({
                    "type": "zone_breakout_up",
                    "direction": 1,
                    "zone": z,
                    "strength": z["strength"] + 0.1 * min(breakout_confirm_bricks, confirm_tail.shape[0]),
                    "detail": f"向上突破密集区 {z['lower']:.4f}-{z['upper']:.4f}，"
                              f"连续 {breakout_confirm_bricks} 块上涨确认",
                })

        elif last_close < z["lower"] and last_dir == -1:
            recent_before = renko.tail(breakout_confirm_bricks + 3).head(3)
            was_inside_or_above = any(
                r["close"] >= z["lower"] - tolerance for _, r in recent_before.iterrows()
            )
            confirm_tail = renko.tail(breakout_confirm_bricks)
            if was_inside_or_above and all(confirm_tail["direction"] == -1):
                signals.append({
                    "type": "zone_breakout_down",
                    "direction": -1,
                    "zone": z,
                    "strength": z["strength"] + 0.1 * min(breakout_confirm_bricks, confirm_tail.shape[0]),
                    "detail": f"向下突破密集区 {z['lower']:.4f}-{z['upper']:.4f}，"
                              f"连续 {breakout_confirm_bricks} 块下跌确认",
                })

        # 遇阻：最近 3 块砖在区附近反向
        tail3 = renko.tail(3)
        if _in_zone(last_close, z, tolerance) or _in_zone(float(tail3["close"].iloc[-2]), z, tolerance):
            if prev_dir != 0 and last_dir == -prev_dir:
                # 前两砖进入/接触区，最新一砖反向
                approach = renko.tail(4).head(2)
                approach_in_zone = any(
                    _in_zone(r["close"], z, tolerance) for _, r in approach.iterrows()
                )
                if approach_in_zone:
                    sig_type = "zone_rejection_down" if last_dir == -1 else "zone_rejection_up"
                    signals.append({
                        "type": sig_type,
                        "direction": last_dir,
                        "zone": z,
                        "strength": z["strength"],
                        "detail": f"在密集区 {z['lower']:.4f}-{z['upper']:.4f} 附近"
                                  f"遇阻{'回落' if last_dir == -1 else '回升'}",
                    })

    return signals


def trend_pullback_signals(
    renko: pd.DataFrame,
    confirm_bricks: int = 4,
    pullback_max_ratio: float = 0.5,
    entry_follow_bricks: int = 2,
    min_trend_bricks: int = 3,
) -> list[dict]:
    """趋势确认后，回调不超过前期 1/2，再出 N 块同向砖发出信号。

    实现：
      - 从最近砖向前扫描，寻找一段同向趋势（至少 min_trend_bricks 块同向）。
      - 找到趋势起点（该波段第一块同向砖的 open）与趋势极值（最高点/最低点）。
      - 之后价格回撤，计算回撤比例；若 <= pullback_max_ratio 则合格。
      - 回撤结束后，最近连续 entry_follow_bricks 块回到原方向 → 信号。
    """
    if len(renko) < confirm_bricks + entry_follow_bricks + min_trend_bricks + 2:
        return []

    directions = renko["direction"].to_numpy()
    closes = renko["close"].to_numpy(dtype=float)
    n = len(directions)

    signals = []

    def _scan_trend(start_idx: int, wanted_dir: int):
        """从 start_idx 向前找一段同向趋势，返回 (trend_start_idx, extreme_idx, extreme_price)。"""
        idx = start_idx
        # 找到连续同向砖的起点
        while idx >= 0 and directions[idx] == wanted_dir:
            idx -= 1
        trend_start = idx + 1
        if start_idx - trend_start + 1 < min_trend_bricks:
            return None
        segment = renko.iloc[trend_start : start_idx + 1]
        if wanted_dir == 1:
            extreme_price = float(segment["top"].max())
            extreme_idx = int(segment["top"].idxmax())
        else:
            extreme_price = float(segment["bottom"].min())
            extreme_idx = int(segment["bottom"].idxmin())
        return trend_start, extreme_idx, extreme_price

    # 检查多头趋势回撤再启动
    last_dirs = directions[-entry_follow_bricks:]
    if len(last_dirs) == entry_follow_bricks and all(last_dirs == 1):
        entry_end_idx = n - 1
        # 找刚结束的回撤：前面应有下跌砖
        pullback_start = entry_end_idx
        while pullback_start >= 0 and directions[pullback_start] != -1:
            pullback_start -= 1
        if pullback_start >= 0:
            # 回撤结束点：最后一个 -1 的位置
            pullback_end = pullback_start
            while pullback_end >= 0 and directions[pullback_end] == -1:
                pullback_end -= 1
            pullback_end += 1
            trend_info = _scan_trend(pullback_end - 1, 1)
            if trend_info:
                trend_start, extreme_idx, extreme_price = trend_info
                trend_start_price = float(renko.iloc[trend_start]["open"])
                advance = extreme_price - trend_start_price
                pullback_low = float(renko.iloc[pullback_end : pullback_start + 1]["bottom"].min())
                retracement = (extreme_price - pullback_low) / advance if advance > 0 else 1.0
                if 0 <= retracement <= pullback_max_ratio:
                    signals.append({
                        "type": "trend_pullback_long",
                        "direction": 1,
                        "strength": 1.0 - retracement,
                        "detail": f"上涨趋势确认后回撤 {retracement:.1%}，"
                                  f"近 {entry_follow_bricks} 块上涨砖重启",
                    })

    # 空头趋势回撤再启动
    if len(last_dirs) == entry_follow_bricks and all(last_dirs == -1):
        entry_end_idx = n - 1
        pullback_start = entry_end_idx
        while pullback_start >= 0 and directions[pullback_start] != 1:
            pullback_start -= 1
        if pullback_start >= 0:
            pullback_end = pullback_start
            while pullback_end >= 0 and directions[pullback_end] == 1:
                pullback_end -= 1
            pullback_end += 1
            trend_info = _scan_trend(pullback_end - 1, -1)
            if trend_info:
                trend_start, extreme_idx, extreme_price = trend_info
                trend_start_price = float(renko.iloc[trend_start]["open"])
                decline = trend_start_price - extreme_price
                pullback_high = float(renko.iloc[pullback_end : pullback_start + 1]["top"].max())
                retracement = (pullback_high - extreme_price) / decline if decline > 0 else 1.0
                if 0 <= retracement <= pullback_max_ratio:
                    signals.append({
                        "type": "trend_pullback_short",
                        "direction": -1,
                        "strength": 1.0 - retracement,
                        "detail": f"下跌趋势确认后回撤 {retracement:.1%}，"
                                  f"近 {entry_follow_bricks} 块下跌砖重启",
                    })

    return signals


def _local_extrema(close: pd.Series) -> tuple[list[int], list[int]]:
    """返回 (peaks, troughs) 索引列表。"""
    arr = close.to_numpy(dtype=float)
    n = len(arr)
    peaks = []
    troughs = []
    for i in range(1, n - 1):
        if arr[i] > arr[i - 1] and arr[i] > arr[i + 1]:
            peaks.append(i)
        elif arr[i] < arr[i - 1] and arr[i] < arr[i + 1]:
            troughs.append(i)
    return peaks, troughs


def wm_pattern_signals(
    renko: pd.DataFrame,
    brick_size: float,
    lookback_bricks: int = 120,
    min_swing_bricks: int = 4,
    neckline_breakout_bricks: float = 1.0,
    max_tilt_bricks: float = 1.0,
) -> list[dict]:
    """识别 Renko 砖块序列上的 W 底 / M 顶形态，并在突破颈线时生成信号。

    规则：
      - W 底：两个低点 L1 < L2（右底抬高），中间有一个明显峰值作为颈线；
        最新砖收盘向上突破颈线 ≥ 1 砖 → long 信号。
      - M 顶：两个高点 H1 > H2（右顶降低），中间有一个明显谷值作为颈线；
        最新砖收盘向下突破颈线 ≥ 1 砖 → short 信号。
      - 形态必须在 lookback_bricks 范围内，且两底/两顶之间至少间隔 min_swing_bricks。
    """

    tail = renko.tail(min(lookback_bricks, len(renko))).reset_index(drop=True)
    closes = tail["close"].to_numpy(dtype=float)
    n = len(closes)
    if n < 10:
        return []

    peaks, troughs = _local_extrema(tail["close"])
    last_close = closes[-1]
    last_idx = n - 1
    prev_close = closes[-2] if n >= 2 else last_close

    best_w = None  # 记录最新且 freshest 的 W 底
    best_m = None  # 记录最新且 freshest 的 M 顶

    # ---- W 底：在两个 trough 之间找一个 peak 作为颈线 ----
    for i in range(len(troughs)):
        for j in range(i + 1, len(troughs)):
            l1_idx = troughs[i]
            l2_idx = troughs[j]
            if l2_idx - l1_idx < min_swing_bricks:
                continue

            l1 = closes[l1_idx]
            l2 = closes[l2_idx]
            # 右底抬高
            if l2 <= l1 + 1e-9:
                continue

            # 两底之间必须有一个 peak
            mid_peaks = [p for p in peaks if l1_idx < p < l2_idx]
            if not mid_peaks:
                continue
            # 颈线取中间最高 peak
            neck_idx = max(mid_peaks, key=lambda p: closes[p])
            neckline = closes[neck_idx]

            # 突破：最新收盘在颈线上方 ≥ breakout_bricks 块
            breakout_level = neckline + neckline_breakout_bricks * brick_size
            if last_close < breakout_level:
                continue
            # 突破发生在最新一根砖（前一根未突破）
            if prev_close >= breakout_level:
                continue
            # 突破必须发生在颈线形成之后
            if last_idx <= neck_idx:
                continue

            # 两底高度差不过大（形态不过于倾斜）
            if abs(l2 - l1) > max_tilt_bricks * brick_size:
                continue

            strength = 1.0 - abs(l2 - l1) / (neckline - min(l1, l2) + 1e-9)
            candidate = {
                "type": "w_bottom_breakout",
                "direction": 1,
                "strength": max(0.1, min(1.0, strength)),
                "detail": f"W 底突破颈线 {neckline:.4f}，左底 {l1:.4f}，右底 {l2:.4f}",
                "_right_idx": l2_idx,
            }
            if best_w is None or l2_idx > best_w["_right_idx"]:
                best_w = candidate

    # ---- M 顶：在两个 peak 之间找一个 trough 作为颈线 ----
    for i in range(len(peaks)):
        for j in range(i + 1, len(peaks)):
            h1_idx = peaks[i]
            h2_idx = peaks[j]
            if h2_idx - h1_idx < min_swing_bricks:
                continue

            h1 = closes[h1_idx]
            h2 = closes[h2_idx]
            # 右顶降低
            if h2 >= h1 - 1e-9:
                continue

            # 两顶之间必须有一个 trough
            mid_troughs = [t for t in troughs if h1_idx < t < h2_idx]
            if not mid_troughs:
                continue
            # 颈线取中间最低 trough
            neck_idx = min(mid_troughs, key=lambda t: closes[t])
            neckline = closes[neck_idx]

            # 突破：最新收盘在颈线下方 ≥ breakout_bricks 块
            breakout_level = neckline - neckline_breakout_bricks * brick_size
            if last_close > breakout_level:
                continue
            # 突破发生在最新一根砖
            if prev_close <= breakout_level:
                continue

            if last_idx <= neck_idx:
                continue

            if abs(h1 - h2) > max_tilt_bricks * brick_size:
                continue

            strength = 1.0 - abs(h1 - h2) / (max(h1, h2) - neckline + 1e-9)
            candidate = {
                "type": "m_top_breakout",
                "direction": -1,
                "strength": max(0.1, min(1.0, strength)),
                "detail": f"M 顶突破颈线 {neckline:.4f}，左顶 {h1:.4f}，右顶 {h2:.4f}",
                "_right_idx": h2_idx,
            }
            if best_m is None or h2_idx > best_m["_right_idx"]:
                best_m = candidate

    signals = []
    for cand in (best_w, best_m):
        if cand is not None:
            cand.pop("_right_idx", None)
            signals.append(cand)
    return signals


def rsi_risk_note(rsi_value: Optional[float], overbought: float = 75, oversold: float = 25) -> str:
    """根据砖块 RSI 给出风险警示。"""
    if rsi_value is None or np.isnan(rsi_value):
        return ""
    if rsi_value >= overbought:
        return f"严重超买(RSI={rsi_value:.1f})"
    if rsi_value <= oversold:
        return f"严重超卖(RSI={rsi_value:.1f})"
    return ""


def filter_signal_by_rsi(signal: dict, rsi_value: Optional[float], overbought: float, oversold: float) -> bool:
    """极端 RSI 时抑制反向信号。

    - RSI 超买且信号方向为多头 → 抑制
    - RSI 超卖且信号方向为空头 → 抑制
    """
    if rsi_value is None or np.isnan(rsi_value):
        return True
    direction = signal.get("direction", 0)
    if direction == 1 and rsi_value >= overbought:
        return False
    if direction == -1 and rsi_value <= oversold:
        return False
    return True


def generate_signals(
    symbol: str,
    name: str,
    df: pd.DataFrame,
    renko: pd.DataFrame,
    brick_size: float,
    cfg: dict,
) -> list[Signal]:
    """综合生成某品种的全部 Renko 信号。"""
    if renko.empty or len(renko) < 20:
        return []

    strat = cfg["strategy"]
    rsi_period = strat["rsi"]["period"]
    overbought = strat["rsi"]["overbought"]
    oversold = strat["rsi"]["oversold"]

    rsi_series = rsi_on_bricks(renko, period=rsi_period)
    last_rsi = float(rsi_series.iloc[-1]) if len(rsi_series) else None
    if last_rsi is not None and np.isnan(last_rsi):
        last_rsi = None

    zones = detect_zones(
        renko,
        brick_size,
        lookback_bricks=strat["zones"]["lookback_bricks"],
        bin_mult=strat["zones"]["bin_mult"],
        min_cluster_bricks=strat["zones"]["min_cluster_bricks"],
    )
    support, resistance = _nearest_zones(zones, float(renko["close"].iloc[-1]))

    raw_signals = []
    raw_signals.extend(zone_signals(
        renko,
        zones,
        brick_size,
        touch_tolerance_bricks=strat["zones"]["touch_tolerance_bricks"],
        breakout_confirm_bricks=strat["zones"]["breakout_confirm_bricks"],
    ))
    raw_signals.extend(trend_pullback_signals(
        renko,
        confirm_bricks=strat["trend"]["confirm_bricks"],
        pullback_max_ratio=strat["trend"]["pullback_max_ratio"],
        entry_follow_bricks=strat["trend"]["entry_follow_bricks"],
        min_trend_bricks=strat["trend"]["min_trend_bricks"],
    ))
    raw_signals.extend(wm_pattern_signals(
        renko,
        brick_size,
        lookback_bricks=strat["wm"]["lookback_bricks"],
        min_swing_bricks=strat["wm"]["min_swing_bricks"],
        neckline_breakout_bricks=strat["wm"]["neckline_breakout_bricks"],
        max_tilt_bricks=strat["wm"]["max_tilt_bricks"],
    ))

    risk = rsi_risk_note(last_rsi, overbought, oversold)

    results = []
    last_dt = renko["datetime"].iloc[-1]
    last_close = float(renko["close"].iloc[-1])

    # 如果没有交易信号但有 RSI 风险，输出风险警示
    if not raw_signals and risk:
        results.append(Signal(
            symbol=symbol,
            name=name,
            datetime=last_dt,
            close=last_close,
            brick_size=brick_size,
            signal_type="rsi_risk",
            direction=0,
            strength_score=0.0,
            rsi_brick=last_rsi,
            nearest_support=support,
            nearest_resistance=resistance,
            risk_note=risk,
            detail=risk,
        ))

    for sig in raw_signals:
        if not filter_signal_by_rsi(sig, last_rsi, overbought, oversold):
            continue
        results.append(Signal(
            symbol=symbol,
            name=name,
            datetime=last_dt,
            close=last_close,
            brick_size=brick_size,
            signal_type=sig["type"],
            direction=sig["direction"],
            strength_score=round(float(sig["strength"]), 4),
            rsi_brick=last_rsi,
            nearest_support=support,
            nearest_resistance=resistance,
            risk_note=risk,
            detail=sig["detail"],
        ))

    return results
