"""15m K 线形态检测 —— 多周期入场的低周期确认。

提供两种低周期确认模式（由 mtf_signal 的 cfg.ltf_confirm_mode 选择）：

    1. 形态模式（"pattern"，原始）：吞没(2根) + 合并K实体(n根)，满足任一即可
    2. 假突破模式（"fakebreak"，默认，推荐）：把单周期的 Spring/Upthrust 逻辑
       搬到 15m，对 60m zone 边界做"刺穿→收回→失败打分"。确认力度远强于纯
       K 线形状，过滤掉绝大多数噪音交易。

假突破模式核心（detect_ltf_spring / detect_ltf_upthrust）：
    - Spring（做多）：15m low 刺破支撑区下沿 → post_break_bars 内收盘收回 → 三档打分
    - Upthrust（做空）：15m high 刺穿阻力区上沿 → 收回 → 三档打分
    三档打分与 signal.py 完全一致（快速反击/不创新极值/2B），可叠加满分 100。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from fakebreak.config import FakeBreakConfig
from fakebreak.types import PatternResult, SignalSide, Zone


def merge_bars(df: pd.DataFrame, n: int = 5) -> dict:
    """合并最近 n 根 K 线为 1 根的 OHLCV 概要。

    合并规则（标准 K 线合并）：
        open   = 第 1 根的 open
        high   = n 根最高
        low    = n 根最低
        close  = 末根的 close
        volume = n 根成交量之和

    返回 dict（不是 DataFrame），调用方按需取用。数据不足返回空 dict。
    """
    recent = df.tail(n)
    if len(recent) < 2:
        return {}
    return {
        "open": float(recent.iloc[0]["open"]),
        "high": float(recent["high"].max()),
        "low": float(recent["low"].min()),
        "close": float(recent.iloc[-1]["close"]),
        "volume": float(recent["volume"].sum()),
    }


def detect_engulfing(
    df: pd.DataFrame,
    min_body_atr: float = 0.3,
    atr: float | None = None,
) -> PatternResult | None:
    """2 根吞没形态：阳吞阴（做多）/ 阴吞阳（做空）。

    判定（取 df 最后 2 根：prev → cur）：
        - 阳吞阴（做多）：prev 是阴线(close<open)，cur 是阳线(close>open)，
          且 cur 的实体覆盖 prev 的实体（cur.open <= prev.close 且 cur.close >= prev.open）
        - 阴吞阳（做空）：镜像
        - 吞没方实体需 >= min_body_atr * atr（过滤十字星级别的小实体）

    Parameters
    ----------
    df : 低周期（15m）OHLCV DataFrame
    min_body_atr : 吞没方最小实体幅度（ATR 倍）
    atr : 当前 ATR 值（来自 60m，用于跨周期统一尺度）。None 时不过滤实体幅度
    """
    if len(df) < 2:
        return None
    prev = df.iloc[-2]
    cur = df.iloc[-1]

    p_open, p_close = float(prev["open"]), float(prev["close"])
    c_open, c_close = float(cur["open"]), float(cur["close"])

    prev_bear = p_close < p_open  # 前根阴线
    prev_bull = p_close > p_open  # 前根阳线
    cur_bull = c_close > c_open   # 当前阳线
    cur_bear = c_close < c_open   # 当前阴线

    cur_body = abs(c_close - c_open)
    if atr and atr > 0 and cur_body < min_body_atr * atr:
        return None  # 吞没方实体太小

    bar_idx = len(df) - 1
    entry = c_close

    # 阳吞阴：前阴后阳，当前阳线实体覆盖前根阴线实体
    if prev_bear and cur_bull and c_open <= p_close and c_close >= p_open:
        return PatternResult(
            side=SignalSide.LONG,
            name="engulfing",
            bar_idx=bar_idx,
            entry=entry,
            reason=f"阳吞阴 prev[{p_open:.1f}→{p_close:.1f}] cur[{c_open:.1f}→{c_close:.1f}] 实体{cur_body:.1f}",
        )
    # 阴吞阳：前阳后阴，当前阴线实体覆盖前根阳线实体
    if prev_bull and cur_bear and c_open >= p_close and c_close <= p_open:
        return PatternResult(
            side=SignalSide.SHORT,
            name="engulfing",
            bar_idx=bar_idx,
            entry=entry,
            reason=f"阴吞阳 prev[{p_open:.1f}→{p_close:.1f}] cur[{c_open:.1f}→{c_close:.1f}] 实体{cur_body:.1f}",
        )
    return None


def detect_merged_body(df: pd.DataFrame, n: int = 5) -> PatternResult | None:
    """n 根合并后的实体方向：阳实体做多 / 阴实体做空。

    合并最近 n 根 15m K 线为 1 根，看其 open→close 的实体方向：
        - close > open → 阳实体 → 做多
        - close < open → 阴实体 → 做空
        - close == open → 十字星，不算

    不设最小幅度门槛——合并 n 根本身已过滤掉单根噪音，合并后的实体
    代表这段时间的真实方向。若需要更严可由调用方叠加吞没确认。
    """
    merged = merge_bars(df, n)
    if not merged:
        return None

    o, c = merged["open"], merged["close"]
    bar_idx = len(df) - 1

    if c > o:
        return PatternResult(
            side=SignalSide.LONG,
            name="merged",
            bar_idx=bar_idx,
            entry=c,
            reason=f"合并{n}根阳实体 o{o:.1f}→c{c:.1f} (H{merged['high']:.1f}/L{merged['low']:.1f})",
        )
    if c < o:
        return PatternResult(
            side=SignalSide.SHORT,
            name="merged",
            bar_idx=bar_idx,
            entry=c,
            reason=f"合并{n}根阴实体 o{o:.1f}→c{c:.1f} (H{merged['high']:.1f}/L{merged['low']:.1f})",
        )
    return None  # 十字星


# ──────────────────────────────────────────────────────────────────────────
# 假突破模式（推荐）：15m 对 60m zone 边界做刺穿→收回→失败打分
# 逻辑移植自 signal.py 的 _detect_spring / _detect_upthrust，改为：
#   1. 关键位是 60m zone 的 center（与单周期 signal.py 一致），不是 zone 边界
#   2. 周期是 15m，ATR 仍用 60m 的（跨周期统一尺度，避免 15m ATR 过小导致刺穿门槛太松）
# ──────────────────────────────────────────────────────────────────────────

def detect_ltf_spring(
    df_15m: pd.DataFrame,
    support: Zone,
    atr: float,
    cfg: FakeBreakConfig,
) -> PatternResult | None:
    """15m Spring（做多）：刺破支撑区中心 → 收回 → 失败打分。

    遍历 signal_lookback 窗口内每根刺破 support.center - break_atr*ATR 的 break_bar，
    追踪其后 post_break_bars 根，找第一根收盘收回 support.center 上方的 recover_bar，
    按三档失败特征打分，返回 failure_score 最高的达标 setup。

    刺穿/收回基准统一用 zone.center（与单周期 signal.py 一致）：只要求价格越过
    关键位中心再收回，不要求跨越整个 zone 宽度（lower→upper），避免双重过滤过严。
    """
    recent = df_15m.tail(cfg.signal_lookback).reset_index(drop=True)
    n = len(recent)
    if n < 3 or not atr or atr <= 0:
        return None

    level = support.center
    break_threshold = level - cfg.break_atr * atr

    best: PatternResult | None = None
    for i in range(n - 1):
        break_low = float(recent.iloc[i]["low"])
        if break_low > break_threshold:
            continue  # 没刺破

        end = min(i + cfg.post_break_bars + 1, n)
        recover_idx = -1
        for j in range(i + 1, end):
            if float(recent.iloc[j]["close"]) > level:
                recover_idx = j
                break
        if recover_idx < 0:
            continue  # 没收回，不构成失败

        recover_bar = recent.iloc[recover_idx]
        entry = float(recover_bar["close"])

        score, tags = _score_failure_long(recent, i, recover_idx, end,
                                          level, break_low, atr, cfg)
        if score < cfg.min_failure_score:
            continue

        pr = PatternResult(
            side=SignalSide.LONG,
            name="ltf_spring",
            bar_idx=len(df_15m) - 1,
            entry=entry,
            stop_ref=break_low,  # 止损放刺破低点
            failure_score=score,
            reason=(f"15m Spring 刺破{level:.1f}后第{recover_idx - i}根收回"
                    f"{level:.1f}，刺破低{break_low:.1f}，失败{score:.0f}[{'+'.join(tags)}]"),
        )
        if best is None or score > best.failure_score:
            best = pr
    return best


def detect_ltf_upthrust(
    df_15m: pd.DataFrame,
    resistance: Zone,
    atr: float,
    cfg: FakeBreakConfig,
) -> PatternResult | None:
    """15m Upthrust（做空）：刺穿阻力区中心 → 收回 → 失败打分。镜像于 detect_ltf_spring。"""
    recent = df_15m.tail(cfg.signal_lookback).reset_index(drop=True)
    n = len(recent)
    if n < 3 or not atr or atr <= 0:
        return None

    level = resistance.center
    break_threshold = level + cfg.break_atr * atr

    best: PatternResult | None = None
    for i in range(n - 1):
        break_high = float(recent.iloc[i]["high"])
        if break_high < break_threshold:
            continue

        end = min(i + cfg.post_break_bars + 1, n)
        recover_idx = -1
        for j in range(i + 1, end):
            if float(recent.iloc[j]["close"]) < level:
                recover_idx = j
                break
        if recover_idx < 0:
            continue

        recover_bar = recent.iloc[recover_idx]
        entry = float(recover_bar["close"])

        score, tags = _score_failure_short(recent, i, recover_idx, end,
                                           level, break_high, atr, cfg)
        if score < cfg.min_failure_score:
            continue

        pr = PatternResult(
            side=SignalSide.SHORT,
            name="ltf_upthrust",
            bar_idx=len(df_15m) - 1,
            entry=entry,
            stop_ref=break_high,  # 止损放刺穿高点
            failure_score=score,
            reason=(f"15m Upthrust 刺穿{level:.1f}后第{recover_idx - i}根收回"
                    f"{level:.1f}，刺穿高{break_high:.1f}，失败{score:.0f}[{'+'.join(tags)}]"),
        )
        if best is None or score > best.failure_score:
            best = pr
    return best


# ── 三档失败打分（移植自 signal.py，逻辑完全一致）──

def _score_failure_short(
    recent: pd.DataFrame,
    break_idx: int,
    recover_idx: int,
    end: int,
    level: float,
    break_high: float,
    atr: float,
    cfg: FakeBreakConfig,
) -> tuple[float, list[str]]:
    """做空侧突破失败打分。返回 (总分, 命中档位标签列表)。"""
    score = 0.0
    tags: list[str] = []

    if recover_idx == break_idx + 1:
        rb = recent.iloc[recover_idx]
        body = float(rb["open"]) - float(rb["close"])  # 阴线实体
        if body >= cfg.recoil_atr * atr:
            score += cfg.score_fast_recoil
            tags.append("快速反击")

    post = recent.iloc[break_idx + 1:end]
    if len(post) > 0 and float(post["high"].max()) <= break_high:
        score += cfg.score_no_new_extreme
        tags.append("不创新高")

    beyond_threshold = level - cfg.beyond_atr * atr
    if float(recent.iloc[recover_idx]["close"]) <= beyond_threshold:
        score += cfg.score_2b_beyond
        tags.append("2B跌破")

    return score, tags


def _score_failure_long(
    recent: pd.DataFrame,
    break_idx: int,
    recover_idx: int,
    end: int,
    level: float,
    break_low: float,
    atr: float,
    cfg: FakeBreakConfig,
) -> tuple[float, list[str]]:
    """做多侧突破失败打分（镜像于 _score_failure_short）。"""
    score = 0.0
    tags: list[str] = []

    if recover_idx == break_idx + 1:
        rb = recent.iloc[recover_idx]
        body = float(rb["close"]) - float(rb["open"])  # 阳线实体
        if body >= cfg.recoil_atr * atr:
            score += cfg.score_fast_recoil
            tags.append("快速反击")

    post = recent.iloc[break_idx + 1:end]
    if len(post) > 0 and float(post["low"].min()) >= break_low:
        score += cfg.score_no_new_extreme
        tags.append("不创新低")

    beyond_threshold = level + cfg.beyond_atr * atr
    if float(recent.iloc[recover_idx]["close"]) >= beyond_threshold:
        score += cfg.score_2b_beyond
        tags.append("2B涨破")

    return score, tags
