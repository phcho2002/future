"""趋势闸：基于波峰波谷（swing）结构判定。

第一版用"N根K线累计跌幅"，但它只看首尾两点，分不清真趋势和波段回归。
现改为 swing 结构判定：真正的下降趋势是一系列更低的波峰 + 更低的波谷（LH+LL）。

趋势闸语义（假突破反转的前提）：
    - short_eligible = 处于下降趋势(LH+LL) + 当前在反弹中（从最近波谷回升了足够幅度）
      → 反弹触及前高阻力 → 假突破失败 → 做空
    - long_eligible = 处于上升趋势(HH+HL) + 当前在回落中（从最近波峰回落了足够幅度）
      → 回落触及前低支撑 → 假跌破失败 → 做多
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from fakebreak.swing import detect_swing_structure, SwingStructure


@dataclass
class TrendState:
    """趋势闸判定结果。"""

    short_eligible: bool  # 允许找做空 setup
    long_eligible: bool   # 允许找做多 setup
    reason: str = ""

    # 诊断信息
    is_downtrend: bool = False
    is_uptrend: bool = False
    is_rebounding: bool = False   # 下降趋势中正在反弹
    is_pulling_back: bool = False  # 上升趋势中正在回落
    cur: float = 0.0
    last_swing_low: float = 0.0
    last_swing_high: float = 0.0


def detect_trend(
    df: pd.DataFrame,
    atr: float,
    swing_lookback: int = 3,
    n_swings: int = 3,
    swing_window: int = 60,
    pullback_atr: float = 0.8,
) -> TrendState:
    """判定当前是否满足"下降趋势+反弹中"（空头）或镜像（多头）。

    Parameters
    ----------
    df : 含 high/low/close/atr 列的 DataFrame
    atr : 当前 ATR 值（用于判定反弹/回落幅度门槛）
    swing_lookback : swing 点检测窗口
    n_swings : 判定趋势取最近几个 swing 点
    swing_window : swing 结构回看根数
    pullback_atr : 反弹/回落需达到的幅度（ATR倍），过滤趋势中继
    """
    if "atr" not in df.columns or len(df) < swing_window:
        return TrendState(False, False, reason="数据不足")
    if not atr or atr <= 0 or atr != atr:
        return TrendState(False, False, reason="ATR 无效")

    struct = detect_swing_structure(df, swing_lookback, n_swings, swing_window)

    if not struct.has_enough_points:
        return TrendState(
            False, False,
            reason=f"swing点不足: {struct.describe()}",
        )

    cur = float(df["close"].iloc[-1])
    last_swing_low = struct.recent_lows[-1] if struct.recent_lows else cur
    last_swing_high = struct.recent_highs[-1] if struct.recent_highs else cur

    is_down = struct.is_downtrend
    is_up = struct.is_uptrend

    # 反弹中：当前价高于最近波谷一定幅度（下降趋势里正在向上反弹）
    rebound = cur - last_swing_low
    is_rebounding = rebound >= pullback_atr * atr

    # 回落中：当前价低于最近波峰一定幅度（上升趋势里正在向下回落）
    pullback = last_swing_high - cur
    is_pulling_back = pullback >= pullback_atr * atr

    short_eligible = is_down and is_rebounding
    long_eligible = is_up and is_pulling_back

    # 组装 reason
    parts = [struct.describe()]
    if is_down:
        parts.append(f"反弹{rebound / atr:.1f}ATR{'✓' if is_rebounding else '✗(未达)'}")
    if is_up:
        parts.append(f"回落{pullback / atr:.1f}ATR{'✓' if is_pulling_back else '✗(未达)'}")
    if not is_down and not is_up:
        parts.append("非明确趋势")
    reason = " | ".join(parts)

    return TrendState(
        short_eligible=short_eligible,
        long_eligible=long_eligible,
        reason=reason,
        is_downtrend=is_down,
        is_uptrend=is_up,
        is_rebounding=is_rebounding,
        is_pulling_back=is_pulling_back,
        cur=cur,
        last_swing_low=last_swing_low,
        last_swing_high=last_swing_high,
    )
