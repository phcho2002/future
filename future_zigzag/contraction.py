"""收缩判定 —— Three Push 之后、反转信号之前的独立闸门。

两个独立判据，至少满足其一：
    ① 幅度递减（amplitude contraction）：三推幅度 L1 > L2 > L3 单调递减。
       楔形/三角形的直接几何特征——每次推进力度越来越弱。
    ② ATR 收缩（volatility contraction）：模式末端 ATR 处于历史低位分位。
       "波动率收缩 → 即将扩张"是期货行情最稳定的模式之一。

为什么独立成闸门而非混进四维评分：
    Three Push 四维评的是"第三推相对前两推的衰竭"；收缩评的是"整个模式的
    几何收敛性 + 波动率环境"。两者正交——一个 Three Push 可以衰竭但未收缩
    （V 型反转），也可以收缩但未衰竭（慢速楔形）。作为独立闸门能更精确刻画状态。
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .config import ContractionConfig
from .three_push import ThreePushPattern


@dataclass(frozen=True)
class ContractionResult:
    """单个 Three Push 模式的收缩判定结果。"""

    passed: bool                       # 是否通过收缩门槛
    score: float                       # 总分 [0,1]，越大越收缩
    amp_contraction_score: float       # 幅度递减子分
    atr_contraction_score: float       # ATR 收缩子分
    amp_ratio_3to1: float              # L3/L1 幅度比
    amp_ratio_2to1: float              # L2/L1 幅度比
    amp_monotone: bool                 # L1>L2>L3 严格递减
    atr_percentile: float              # 末端 ATR 历史分位 [0,1]
    atr_end_idx: int                   # 取 ATR 的 K 线索引

    def as_dict(self) -> dict:
        return {
            "passed": int(self.passed),
            "score": round(self.score, 3),
            "amp_c": round(self.amp_contraction_score, 3),
            "atr_c": round(self.atr_contraction_score, 3),
            "amp_r31": round(self.amp_ratio_3to1, 3) if np.isfinite(self.amp_ratio_3to1) else None,
            "amp_r21": round(self.amp_ratio_2to1, 3) if np.isfinite(self.amp_ratio_2to1) else None,
            "amp_mono": int(self.amp_monotone),
            "atr_pct": round(self.atr_percentile, 3) if np.isfinite(self.atr_percentile) else None,
        }


def _soft_le(ratio: float, mid: float, spread: float) -> float:
    """ratio 越小 → 越接近 1（越收缩）。同 three_push._soft_decay。"""
    if not np.isfinite(ratio):
        return 0.0
    z = (ratio - mid) / spread
    if z > 35:
        return 0.0
    if z < -35:
        return 1.0
    return float(1.0 / (1.0 + math.exp(z)))


def _atr_percentile(atr: pd.Series, end_idx: int, lookback: int) -> float:
    """end_idx 处 ATR 在过去 lookback 根的分位 [0,1]。"""
    arr = atr.to_numpy(dtype=float)
    lo = max(0, end_idx - lookback + 1)
    if end_idx >= len(arr) or end_idx < 0:
        return float("nan")
    cur = arr[end_idx]
    if not np.isfinite(cur):
        return float("nan")
    window = arr[lo:end_idx + 1]
    valid = window[np.isfinite(window)]
    if len(valid) < 5:
        return float("nan")
    return float((valid <= cur).mean())


def evaluate_contraction(pattern: ThreePushPattern, atr: pd.Series,
                         n_bars: int, cfg: ContractionConfig | None = None) -> ContractionResult:
    """评估单个 Three Push 模式的收缩程度。

    Parameters
    ----------
    pattern : ThreePushPattern
    atr : pd.Series
        ATR 序列（ZigZagResult.atr）。
    n_bars : int
        总 K 线数（用于边界检查）。
    cfg : ContractionConfig
    """
    cfg = cfg or ContractionConfig()
    legs = pattern.legs  # [L1, L2, L3]
    if len(legs) != 3:
        return _empty_result()
    l1, l2, l3 = legs

    # ① 幅度递减
    def _safe_ratio(num, den):
        if not np.isfinite(den) or den <= 0 or not np.isfinite(num):
            return float("nan")
        return num / den

    r31 = _safe_ratio(l3.net_move, l1.net_move)
    r21 = _safe_ratio(l2.net_move, l1.net_move)
    monotone = (np.isfinite(r31) and np.isfinite(r21) and r21 < 1.0 and r31 < r21)
    # 幅度递减子分：L3/L1 越小越收缩
    amp_score = _soft_le(r31, cfg.amp_contraction_mid, cfg.amp_contraction_spread)

    # ② ATR 收缩
    end_idx = min(pattern.pivot_indices[-1] + cfg.atr_end_offset, n_bars - 1)
    pct = _atr_percentile(atr, end_idx, cfg.atr_lookback)
    # ATR 分位越低越收缩；分位 0.35 → 中点
    atr_score = _soft_le(pct, cfg.atr_soft_mid, cfg.atr_soft_spread)

    total = (cfg.weight_amp_contraction * amp_score
             + cfg.weight_atr_contraction * atr_score)
    passed = total >= cfg.contraction_threshold

    return ContractionResult(
        passed=bool(passed),
        score=float(total),
        amp_contraction_score=float(amp_score),
        atr_contraction_score=float(atr_score),
        amp_ratio_3to1=float(r31) if np.isfinite(r31) else float("nan"),
        amp_ratio_2to1=float(r21) if np.isfinite(r21) else float("nan"),
        amp_monotone=bool(monotone),
        atr_percentile=float(pct) if np.isfinite(pct) else float("nan"),
        atr_end_idx=int(end_idx),
    )


def _empty_result() -> ContractionResult:
    return ContractionResult(
        passed=False, score=0.0, amp_contraction_score=0.0, atr_contraction_score=0.0,
        amp_ratio_3to1=float("nan"), amp_ratio_2to1=float("nan"), amp_monotone=False,
        atr_percentile=float("nan"), atr_end_idx=-1,
    )
