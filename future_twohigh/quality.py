"""整理质量软评分 —— 形态之后的独立闸门。

三维各有明确物理含义、正交不重叠，用连续软评分（sigmoid）替代硬布尔，
加权成总分。总分 ≥ consolidation_threshold 视为"合格整理"。

为什么独立成闸门而非混进形态判定：
    形态判定（矩形/三角）只看四个拐点的几何关系——"是不是整理形态"。
    整理质量看的是"这个整理够不够好、是不是中继而非反转"——"值不值得赌突破"。
    两者正交：一个矩形可以几何完美但量能没萎缩（无蓄势、突破乏力），
    也可以量能萎缩漂亮但斐波那契回撤过深（接近反转）。作为独立闸门更精确。

三维：
    ① 成交量萎缩（量能蓄势）：整理区间前半段均量 / 后半段均量，比值越小越萎缩。
       "整理期间成交量逐步萎缩（为突破蓄势）"的直接量化——这是用户强调
       "强烈建议加上"的一条。
    ② 斐波那契回撤（中继 vs 反转）：回撤深度落在 50%~61.8% 区间给最高分。
       保证是"中继整理"而非"趋势反转"——回撤太浅（<38.2%）突破力度存疑，
       回撤太深（>78.6%）已接近反转。
    ③ 整理时长：钟形打分。太短(<10根)是噪音，太长(>60根)趋势衰竭，中间最优。

注意：这里的"条件1波段"指形态之前的趋势波段。斐波那契回撤衡量的是
整理区间相对前一段趋势的回撤深度。
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .config import ConsolidationConfig
from .pattern import TwoHighPattern


@dataclass(frozen=True)
class ConsolidationResult:
    """单个两高两低形态的整理质量评分。"""

    passed: bool                      # 总分 ≥ threshold
    score: float                      # 加权总分 [0,1]
    volume_score: float               # 成交量萎缩子分 [0,1]
    fib_score: float                  # 斐波那契回撤子分 [0,1]
    duration_score: float             # 整理时长子分 [0,1]
    volume_ratio: float               # 后半段均量/前半段均量（越小越萎缩）
    fib_retracement: float            # 回撤深度比（0=没回撤，1=全回撤）
    duration_bars: int                # 整理时长（根）

    def as_dict(self) -> dict:
        return {
            "passed": int(self.passed),
            "score": round(self.score, 3),
            "vol": round(self.volume_score, 3),
            "fib": round(self.fib_score, 3),
            "dur": round(self.duration_score, 3),
            "vol_r": round(self.volume_ratio, 3) if np.isfinite(self.volume_ratio) else None,
            "fib_r": round(self.fib_retracement, 3) if np.isfinite(self.fib_retracement) else None,
            "dur_bars": self.duration_bars,
        }


# ─────────────────────────────────────────────────────────────
# 软评分工具（与 future_zigzag.three_push._soft_decay 同构）
# ─────────────────────────────────────────────────────────────

def _soft_decay(ratio: float, mid: float, spread: float) -> float:
    """ratio 越小 → 越接近 1。

    sigmoid(ratio; mid, spread) = 1 / (1 + exp((ratio - mid)/spread))
    ratio=mid → 0.5；ratio<<mid → ~1；ratio>>mid → ~0。
    """
    if not np.isfinite(ratio):
        return 0.0
    z = (ratio - mid) / spread
    if z > 35:
        return 0.0
    if z < -35:
        return 1.0
    return float(1.0 / (1.0 + math.exp(z)))


def _bell(value: float, lo: float, hi: float, falloff: float) -> float:
    """钟形评分：value 落在 [lo, hi] 内给满分，线性偏离递减到 0。

    value ∈ [lo, hi] → 1.0
    value < lo → max(0, 1 - (lo - value)/falloff)
    value > hi → max(0, 1 - (value - hi)/falloff)
    """
    if not np.isfinite(value):
        return 0.0
    if value < lo:
        d = lo - value
    elif value > hi:
        d = value - hi
    else:
        return 1.0
    if falloff <= 0:
        return 0.0
    return max(0.0, 1.0 - d / falloff)


# ─────────────────────────────────────────────────────────────
# 三维评分
# ─────────────────────────────────────────────────────────────

def _avg_volume(df: pd.DataFrame, lo: int, hi: int) -> float:
    """[lo, hi) 区间平均成交量。"""
    if hi <= lo or "volume" not in df.columns:
        return float("nan")
    seg = df["volume"].iloc[lo:hi]
    if seg.empty:
        return float("nan")
    return float(seg.mean())


def _score_volume(pattern: TwoHighPattern, df: pd.DataFrame,
                  cfg: ConsolidationConfig) -> tuple[float, float]:
    """成交量萎缩分。返回 (score, ratio)。

    整理区间 = [第一个拐点 index, 最后一个拐点 index]。
    ratio = 后半段均量 / 前半段均量；ratio 越小越萎缩（蓄势）。
    """
    idx = pattern.pivot_indices
    start, end = idx[0], idx[-1]
    mid = (start + end) // 2
    vol_pre = _avg_volume(df, start, mid + 1)
    vol_post = _avg_volume(df, mid + 1, end + 1)
    if not np.isfinite(vol_pre) or vol_pre <= 0 or not np.isfinite(vol_post):
        return 0.0, float("nan")
    ratio = vol_post / vol_pre
    score = _soft_decay(ratio, cfg.volume_shrink_mid, cfg.volume_shrink_spread)
    return score, float(ratio)


def _score_fibonacci(pattern: TwoHighPattern, df: pd.DataFrame,
                     cfg: ConsolidationConfig) -> tuple[float, float]:
    """斐波那契回撤分。返回 (score, retracement)。

    条件1波段 = 形态之前的趋势波段。用"区间外沿到整理区间最深处"衡量回撤：
        多头：前趋势高点(H1 或更高的前高) → 整理最低点 min(L1,L2)
        回撤深度 = (H1 − min(L1,L2)) / (H1 − 前波段起点)

    但形态内只有 4 个点，前波段起点未知。改用更稳健的近似：
        以 amp_first(H1−L1) 作为波段幅度基准，
        回撤深度 = (H1 − min(L1,L2)) / H1   （价格比例回撤）

    落在 [fib_lo, fib_hi]=[0.50, 0.618] 区间给满分（经典中继回撤区）。
    """
    h1 = pattern.h1.price
    lowest = min(pattern.l1.price, pattern.l2.price)
    if h1 <= 0:
        return 0.0, float("nan")
    retracement = (h1 - lowest) / h1
    score = _bell(retracement, cfg.fib_lo, cfg.fib_hi, cfg.fib_falloff)
    return score, float(retracement)


def _score_duration(pattern: TwoHighPattern,
                    cfg: ConsolidationConfig) -> tuple[float, int]:
    """整理时长分。返回 (score, bars)。

    时长 = 最后一个拐点 index − 第一个拐点 index（整理区间的 K 线跨度）。
    钟形：[duration_lo, duration_hi] 内满分，偏离递减。
    """
    idx = pattern.pivot_indices
    bars = idx[-1] - idx[0]
    score = _bell(float(bars), cfg.duration_lo, cfg.duration_hi, cfg.duration_falloff)
    return score, int(bars)


# ─────────────────────────────────────────────────────────────
# 汇总
# ─────────────────────────────────────────────────────────────

def evaluate_quality(pattern: TwoHighPattern, df: pd.DataFrame,
                     cfg: ConsolidationConfig | None = None) -> ConsolidationResult:
    """评估单个两高两低形态的整理质量。

    Parameters
    ----------
    pattern : TwoHighPattern
    df : pd.DataFrame
        OHLCV K 线（ZigZagResult.df）。
    cfg : ConsolidationConfig
    """
    cfg = cfg or ConsolidationConfig()

    vol_score, vol_ratio = _score_volume(pattern, df, cfg)
    fib_score, fib_ret = _score_fibonacci(pattern, df, cfg)
    dur_score, dur_bars = _score_duration(pattern, cfg)

    total = (
        cfg.weight_volume * vol_score
        + cfg.weight_fibonacci * fib_score
        + cfg.weight_duration * dur_score
    )
    passed = total >= cfg.consolidation_threshold

    return ConsolidationResult(
        passed=bool(passed),
        score=float(total),
        volume_score=float(vol_score),
        fib_score=float(fib_score),
        duration_score=float(dur_score),
        volume_ratio=float(vol_ratio) if np.isfinite(vol_ratio) else float("nan"),
        fib_retracement=float(fib_ret) if np.isfinite(fib_ret) else float("nan"),
        duration_bars=dur_bars,
    )
