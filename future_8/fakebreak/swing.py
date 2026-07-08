"""波峰波谷（swing）结构检测 —— 趋势闸的基础。

借鉴 future_3/indicators.py 的对称窗口极值检测（_local_extrema），但判定逻辑
改为"最近 N 个 swing 点逐步递升/递降"，而非 future_3 的"窗内存在一对递升"。

核心概念：
    - swing_high（波峰）：左右各 lookback 根内最高的 high
    - swing_low（波谷）：左右各 lookback 根内最低的 low
    - 上升趋势 = 最近 N 个 swing 中，高点逐步升高 且 低点逐步升高（HH + HL）
    - 下降趋势 = 最近 N 个 swing 中，高点逐步降低 且 低点逐步降低（LH + LL）

趋势闸语义（对应假突破反转的前提）：
    - short_eligible（做空前提）= 处于下降趋势 + 当前在反弹中
      （反弹高点触及/接近前高阻力 → 假突破后回落）
    - long_eligible（做多前提）= 处于上升趋势 + 当前在回落中
      （回落低点触及/接近前低支撑 → 假跌破后反弹）
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class SwingPoint:
    """一个 swing 点（波峰或波谷）。"""

    index: int   # 在 DataFrame 中的位置
    price: float
    kind: str    # "high" 或 "low"


def find_swing_highs(high: np.ndarray, lookback: int = 3) -> list[int]:
    """找波峰：i 处 high 是 [i-lookback, i+lookback] 窗口内的严格最大值。

    返回波峰位置的索引列表（升序）。端点（i<lookback 或 i>=n-lookback）丢弃。
    """
    n = len(high)
    if n < 2 * lookback + 1:
        return []
    idxs: list[int] = []
    for i in range(lookback, n - lookback):
        window = high[i - lookback : i + lookback + 1]
        # 严格最大：等于窗口最大值，且不与邻居全等（防平台）
        if high[i] >= window.max() and not np.all(window == high[i]):
            # 还要求严格大于左右直接邻居
            if high[i] > high[i - 1] and high[i] > high[i + 1]:
                idxs.append(i)
    return idxs


def find_swing_lows(low: np.ndarray, lookback: int = 3) -> list[int]:
    """找波谷：i 处 low 是窗口内的严格最小值。镜像于 find_swing_highs。"""
    n = len(low)
    if n < 2 * lookback + 1:
        return []
    idxs: list[int] = []
    for i in range(lookback, n - lookback):
        window = low[i - lookback : i + lookback + 1]
        if low[i] <= window.min() and not np.all(window == low[i]):
            if low[i] < low[i - 1] and low[i] < low[i + 1]:
                idxs.append(i)
    return idxs


def detect_swing_structure(
    df: pd.DataFrame,
    lookback: int = 3,
    n_swings: int = 3,
    swing_window: int = 60,
) -> "SwingStructure":
    """分析最近 swing_window 根内的 swing 结构，判定趋势方向。

    Parameters
    ----------
    df : 含 high/low 列的 DataFrame
    lookback : swing 点检测的左右窗口
    n_swings : 判定趋势所需的 swing 点对数（最近 N 个高点 + N 个低点）
    swing_window : 只分析最近这么多根（限制回看范围，聚焦近期结构）

    Returns
    -------
    SwingStructure
    """
    recent = df.tail(swing_window).reset_index(drop=True)
    high = recent["high"].to_numpy(dtype=float)
    low = recent["low"].to_numpy(dtype=float)

    sh_idx = find_swing_highs(high, lookback)
    sl_idx = find_swing_lows(low, lookback)

    # 取最近 n_swings 个波峰/波谷的价格序列
    recent_highs = [high[i] for i in sh_idx[-n_swings:]] if len(sh_idx) >= 1 else []
    recent_lows = [low[i] for i in sl_idx[-n_swings:]] if len(sl_idx) >= 1 else []

    return SwingStructure(
        swing_high_idx=sh_idx,
        swing_low_idx=sl_idx,
        recent_highs=recent_highs,
        recent_lows=recent_lows,
    )


@dataclass
class SwingStructure:
    """swing 结构分析结果。"""

    swing_high_idx: list[int]   # 波峰位置（在 swing_window 截断后的索引）
    swing_low_idx: list[int]    # 波谷位置
    recent_highs: list[float]   # 最近 N 个波峰价格
    recent_lows: list[float]    # 最近 N 个波谷价格

    @property
    def is_uptrend(self) -> bool:
        """上升趋势：最近波峰逐步升高 且 波谷逐步升高（至少各有 2 个点且单调）。"""
        return self._monotone_up(self.recent_highs) and self._monotone_up(self.recent_lows)

    @property
    def is_downtrend(self) -> bool:
        """下降趋势：最近波峰逐步降低 且 波谷逐步降低。"""
        return self._monotone_down(self.recent_highs) and self._monotone_down(self.recent_lows)

    @staticmethod
    def _monotone_up(seq: list[float]) -> bool:
        """序列严格递增（至少 2 个元素）。"""
        if len(seq) < 2:
            return False
        return all(b > a for a, b in zip(seq, seq[1:]))

    @staticmethod
    def _monotone_down(seq: list[float]) -> bool:
        """序列严格递减（至少 2 个元素）。"""
        if len(seq) < 2:
            return False
        return all(b < a for a, b in zip(seq, seq[1:]))

    @property
    def has_enough_points(self) -> bool:
        """是否有足够的 swing 点做判定（至少 2 个高点 + 2 个低点）。"""
        return len(self.recent_highs) >= 2 and len(self.recent_lows) >= 2

    def describe(self) -> str:
        """人类可读的趋势描述。"""
        if not self.has_enough_points:
            return f"swing点不足(高{len(self.recent_highs)}/低{len(self.recent_lows)})"
        h = "↑".join(f"{x:.1f}" for x in self.recent_highs)
        l = "↑".join(f"{x:.1f}" for x in self.recent_lows)
        tag = ""
        if self.is_uptrend:
            tag = " [上升趋势 HH+HL]"
        elif self.is_downtrend:
            tag = " [下降趋势 LH+LL]"
        return f"高[{h}] 低[{l}]{tag}"
