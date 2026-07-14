"""Three Push 识别 + 四维量化评分。

基于 ZigZag 的已确认 Pivot，识别"同向三段推进"模式，并用四个正交维度
对"第三推衰竭程度"做连续评分。

为什么这样设计（对比 future_1 的缺陷）：
    future_1/pushes.py:151 用"7 个布尔检查等权平均"算衰竭分——勉强过 4 个
    弱检查 = 强力满足 4 个检查，分一样；且 strength_ratio 算了不用、
    完全不看时间维度、无任何硬门控。

    本模块：
    1. 四维正交（幅度/时间/成交量/推动力），各用 soft sigmoid 连续映射。
    2. 硬门控：幅度或成交量至少一项"显著衰竭"才算有效 Three Push。
    3. 时间维度是 future_1 完全缺失的——"耗时型衰竭"（第三推耗时长但幅度小）。
    4. 基于 confirmed_pivots + confirmed_at，天然继承 anti-repaint。

模式定义（顶部反转为例，底部镜像）：
    三个同向（向上）的推动腿 Leg1/Leg2/Leg3，中间夹两个反向回撤 Pullback1/2。
    Leg3 是最近一段（极值 = 第三推高点）。
    经典三推衰竭：Leg3 幅度/量能/推动力衰减，且高点逐步抬高但力度递减。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal

import numpy as np
import pandas as pd

from .config import ThreePushConfig
from .zigzag import Pivot, ZigZagResult

Direction = Literal["up", "down"]


@dataclass(frozen=True)
class Leg:
    """一段推动腿（从一个 Pivot 到下一个同向更远的 Pivot）。

    Attributes
    ----------
    start_idx, end_idx : int
        起止拐点的 K 线索引。
    start_price, end_price : float
    direction : 'up' | 'down'
    net_move : float
        绝对幅度 = |end_price - start_price|。
    bars : int
        腿的 K 线跨度（end_idx - start_idx），>=1。
    avg_volume : float
        腿内平均成交量。
    atr_multiple : float
        net_move / 该段中点 ATR —— 推动力（跨品种可比）。
    """

    start_idx: int
    end_idx: int
    start_price: float
    end_price: float
    direction: Direction
    net_move: float
    bars: int
    avg_volume: float
    atr_multiple: float


@dataclass(frozen=True)
class FourDimScore:
    """四维评分明细。每维 [0,1]，越大越衰竭（越支持反转）。"""

    amplitude: float       # 幅度衰减
    duration: float        # 时间（拖延型衰竭）
    volume: float          # 量能枯竭
    momentum: float        # 推动力衰减
    total: float           # 加权总分 [0,1]

    # 原始比率（诊断用）
    amplitude_ratio: float
    duration_ratio: float
    volume_ratio: float
    momentum_ratio: float

    # 硬门控结果
    amplitude_significant: bool
    volume_significant: bool
    hard_gate_passed: bool

    def as_dict(self) -> dict:
        return {
            "amp": round(self.amplitude, 3),
            "dur": round(self.duration, 3),
            "vol": round(self.volume, 3),
            "mom": round(self.momentum, 3),
            "total": round(self.total, 3),
            "amp_r": round(self.amplitude_ratio, 3),
            "dur_r": round(self.duration_ratio, 3),
            "vol_r": round(self.volume_ratio, 3),
            "mom_r": round(self.momentum_ratio, 3),
            "gate": int(self.hard_gate_passed),
        }


@dataclass
class ThreePushPattern:
    """一个识别出的三推模式。"""

    direction: Direction             # 三推的方向（反转的相反方向）
    legs: list[Leg]                  # [Leg1, Leg2, Leg3]，均同向
    reversal_kind: Literal["high", "low"]   # 第三推极值类型（顶部=high/底部=low）
    pivot_indices: list[int]         # 模式涉及的 Pivot 索引（4 个拐点：P0,P1,P2,P3）
    score: FourDimScore
    confirmed_at: int                # 第三推极值拐点的 confirmed_at（anti-repaint 时间戳）


# ─────────────────────────────────────────────────────────────
# 评分工具
# ─────────────────────────────────────────────────────────────

def _soft_decay(ratio: float, mid: float, spread: float) -> float:
    """连续软评分：ratio 越小（越衰竭）→ 越接近 1。

    sigmoid(ratio; mid, spread) = 1 / (1 + exp((ratio - mid)/spread))
    ratio=mid → 0.5；ratio<<mid → ~1；ratio>>mid → ~0。
    spread 越小越陡（越接近硬阈值）。

    用于幅度/量能/推动力（ratio 越小=越衰竭=越高分）。
    """
    if not np.isfinite(ratio):
        return 0.0
    z = (ratio - mid) / spread
    # 防 overflow
    if z > 35:
        return 0.0
    if z < -35:
        return 1.0
    return float(1.0 / (1.0 + math.exp(z)))


def _soft_growth(ratio: float, mid: float, spread: float) -> float:
    """时间维度：ratio 越大（越拖延）→ 越接近 1（拖延型衰竭）。

    = 1 - _soft_decay（方向相反）。
    """
    return 1.0 - _soft_decay(ratio, mid, spread)


def _avg_volume(df: pd.DataFrame, lo: int, hi: int) -> float:
    """[lo, hi) 区间平均成交量。"""
    if hi <= lo:
        return float("nan")
    if "volume" not in df.columns:
        return float("nan")
    seg = df["volume"].iloc[lo:hi]
    if seg.empty:
        return float("nan")
    return float(seg.mean())


def _atr_at(atr: pd.Series, idx: int) -> float:
    arr = atr.to_numpy(dtype=float)
    if 0 <= idx < len(arr):
        v = arr[idx]
        return float(v) if np.isfinite(v) and v > 0 else float("nan")
    return float("nan")


# ─────────────────────────────────────────────────────────────
# 模式提取
# ─────────────────────────────────────────────────────────────

def _build_leg(p_start: Pivot, p_end: Pivot, df: pd.DataFrame,
               atr: pd.Series) -> Leg | None:
    """从两个相邻 Pivot 构造一段腿。

    方向由起止价格关系决定。注意：腿必须是单调推进的——
    p_start 到 p_end 若方向不一致（净位移为 0 或反号）则不是有效腿。
    """
    move = p_end.price - p_start.price
    if move == 0:
        return None
    direction: Direction = "up" if move > 0 else "down"
    net = abs(move)
    bars = p_end.index - p_start.index
    if bars < 1:
        return None
    avg_vol = _avg_volume(df, p_start.index, p_end.index + 1)
    mid = (p_start.index + p_end.index) // 2
    a = _atr_at(atr, mid)
    atr_mult = net / a if np.isfinite(a) and a > 0 else float("nan")
    return Leg(
        start_idx=p_start.index, end_idx=p_end.index,
        start_price=p_start.price, end_price=p_end.price,
        direction=direction, net_move=net, bars=bars,
        avg_volume=avg_vol, atr_multiple=atr_mult,
    )


def _score_legs(legs: list[Leg], cfg: ThreePushConfig) -> FourDimScore | None:
    """对三段腿计算四维评分。legs = [L1, L2, L3] 均同向。"""
    if len(legs) != 3:
        return None
    l1, l2, l3 = legs

    # 基准 = max(L1, L2) —— 用较强的前两推做参照（保守，避免被一个弱 L2 拉低门槛）
    def _safe_max(a, b):
        vals = [v for v in (a, b) if np.isfinite(v)]
        return max(vals) if vals else float("nan")

    base_move = _safe_max(l1.net_move, l2.net_move)
    base_bars = max(l1.bars, l2.bars)
    base_vol = _safe_max(l1.avg_volume, l2.avg_volume)
    base_mom = _safe_max(l1.atr_multiple, l2.atr_multiple)

    def _ratio(num, den):
        if not np.isfinite(den) or den <= 0 or not np.isfinite(num):
            return float("nan")
        return num / den

    amp_ratio = _ratio(l3.net_move, base_move)
    dur_ratio = _ratio(l3.bars, base_bars)
    vol_ratio = _ratio(l3.avg_volume, base_vol)
    mom_ratio = _ratio(l3.atr_multiple, base_mom)

    # 四维软评分
    s_amp = _soft_decay(amp_ratio, cfg.amplitude_mid, cfg.amplitude_spread)
    s_dur = _soft_growth(dur_ratio, cfg.duration_mid, cfg.duration_spread)
    s_vol = _soft_decay(vol_ratio, cfg.volume_mid, cfg.volume_spread)
    s_mom = _soft_decay(mom_ratio, cfg.momentum_mid, cfg.momentum_spread)

    total = (
        cfg.weight_amplitude * s_amp
        + cfg.weight_duration * s_dur
        + cfg.weight_volume * s_vol
        + cfg.weight_momentum * s_mom
    )

    # 硬门控：幅度或量能至少一项显著衰竭
    amp_sig = np.isfinite(amp_ratio) and amp_ratio <= cfg.amplitude_hard
    vol_sig = np.isfinite(vol_ratio) and vol_ratio <= cfg.volume_hard
    gate = amp_sig or vol_sig

    return FourDimScore(
        amplitude=s_amp, duration=s_dur, volume=s_vol, momentum=s_mom,
        total=float(total),
        amplitude_ratio=float(amp_ratio) if np.isfinite(amp_ratio) else float("nan"),
        duration_ratio=float(dur_ratio) if np.isfinite(dur_ratio) else float("nan"),
        volume_ratio=float(vol_ratio) if np.isfinite(vol_ratio) else float("nan"),
        momentum_ratio=float(mom_ratio) if np.isfinite(mom_ratio) else float("nan"),
        amplitude_significant=bool(amp_sig),
        volume_significant=bool(vol_sig),
        hard_gate_passed=bool(gate),
    )


def detect_three_push(zz: ZigZagResult, cfg: ThreePushConfig | None = None,
                      as_of: int | None = None) -> list[ThreePushPattern]:
    """从 ZigZag 结果识别所有 Three Push 模式。

    模式定义（6 个交替拐点 P0..P5）：
        三个同向推动腿 + 两个反向回撤腿。
        顶部三推（向上推动）：P0=低 P1=高 P2=低 P3=高 P4=低 P5=高
            推动腿 = P0→P1, P2→P3, P4→P5（均向上）
            回撤腿 = P1→P2, P3→P4（均向下）
            结构约束：三个顶递升 P1<P3<P5，两个低递升 P0<P2<P4
            （每次推动都创新高，每次回撤不破前低 = 趋势性三推）
        底部三推（向下推动）：镜像。

    Parameters
    ----------
    zz : ZigZagResult
    cfg : ThreePushConfig
    as_of : int, 可选
        回测时当前 K 线索引；只返回 ``confirmed_at <= as_of`` 的模式（anti-repaint）。

    Returns
    -------
    list[ThreePushPattern]
        按第三推极值(P5)时间升序。
    """
    cfg = cfg or ThreePushConfig()
    pivots = zz.confirmed_pivots
    if as_of is not None:
        pivots = [p for p in pivots if p.confirmed_at <= as_of]
    if len(pivots) < 6:
        return []

    df = zz.df
    atr = zz.atr
    patterns: list[ThreePushPattern] = []

    # 滑动窗口：每 6 个连续拐点 P0..P5
    # ZigZag 拐点严格交替（high/low），所以 P0..P5 形如 low,high,low,high,low,high
    # 或 high,low,high,low,high,low。
    for i in range(len(pivots) - 5):
        p0, p1, p2, p3, p4, p5 = pivots[i:i + 6]

        # 由 P0 的类型决定方向
        if p0.kind == "low":
            # 顶部三推：P0..P5 = low,high,low,high,low,high
            direction: Direction = "up"
            reversal_kind = "high"
            pushes = [p1, p3, p5]   # 三个推顶（high）
            dips = [p0, p2, p4]     # 三个回撤低点（low）
            # 结构约束：顶递升 + 低递升
            if not (p1.price < p3.price < p5.price and p0.price < p2.price < p4.price):
                continue
        else:
            # 底部三推：P0..P5 = high,low,high,low,high,low
            direction = "down"
            reversal_kind = "low"
            pushes = [p1, p3, p5]   # 三个推底（low）
            dips = [p0, p2, p4]     # 三个反弹高点（high）
            # 结构约束：底递降 + 高递降
            if not (p1.price > p3.price > p5.price and p0.price > p2.price > p4.price):
                continue

        # 三个推动腿：dips[k] → pushes[k]
        legs_raw = [
            _build_leg(dips[0], pushes[0], df, atr),   # P0→P1
            _build_leg(dips[1], pushes[1], df, atr),   # P2→P3
            _build_leg(dips[2], pushes[2], df, atr),   # P4→P5
        ]
        if any(l is None for l in legs_raw):
            continue
        legs = legs_raw  # type: ignore[assignment]
        # 推动腿必须同向
        if any(l.direction != direction for l in legs):
            continue

        score = _score_legs(legs, cfg)
        if score is None:
            continue

        patterns.append(ThreePushPattern(
            direction=direction,
            legs=legs,
            reversal_kind=reversal_kind,
            pivot_indices=[p0.index, p1.index, p2.index, p3.index, p4.index, p5.index],
            score=score,
            confirmed_at=p5.confirmed_at,
        ))

    return patterns


def latest_three_push(zz: ZigZagResult, cfg: ThreePushConfig | None = None,
                      as_of: int | None = None,
                      require_valid: bool = True) -> ThreePushPattern | None:
    """取最近一个 Three Push 模式。

    require_valid=True 时只返回通过硬门控 + 总分门槛的模式。
    """
    cfg = cfg or ThreePushConfig()
    pats = detect_three_push(zz, cfg, as_of=as_of)
    # 已按时间升序，取最后一个满足条件的
    for p in reversed(pats):
        if require_valid:
            if p.score.hard_gate_passed and p.score.total >= cfg.valid_score_threshold:
                return p
        else:
            return p
    return None
