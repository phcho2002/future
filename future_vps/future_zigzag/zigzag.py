"""ZigZag 波段切分 —— running-extreme 状态机（天然 anti-repaint）。

核心思想（经典 ZigZag，depth 用 ATR 归一化）：

    状态: direction(找高/找低), running_extreme=(idx, price)
    逐根 i:
        depth_i = depth_atr_multiple × atr[i]              # 自适应门槛
        若 找高:
            若 high[i] > running_extreme: 极值刷新 = (i, high[i])   # 持续追踪真极值
            若 running_extreme - low[i] >= depth_i:                # 反向达 depth → 确认高点
                记录 Pivot(idx, price, 'high', confirmed_at=i)
                翻转 -> 找低, running_extreme = (i, low[i])
        找低: 镜像

为什么这样设计（对比 future_1 的缺陷）：
    1. anti-repaint 内建于算法：拐点只有价格反向波动 >= depth 后才被"确认"，且
       每个 Pivot 在 confirmed_at 那根就定型、永不再变。回测只用
       ``confirmed_at <= 当前K线`` 的 Pivot，无任何未来信息。
       （future_1 无此机制，回测可用未来信息。）
    2. 极值持续刷新直到反向 depth 出现 → 天然抗假突破/overshoot：被略微超越的拐点
       会平滑迁移到真正极值，不留下虚假锯齿。
       （future_1 用 close-to-close 切推动，影线极值完全丢失。）
    3. depth 用 ATR 归一化 → 跨品种可比（玉米 vs 锡波动差几十倍）。
    4. 高/低严格交替（状态机翻转决定）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import numpy as np
import pandas as pd

from .config import ZigZagConfig
from .indicators import add_atr


Kind = Literal["high", "low"]


@dataclass(frozen=True)
class Pivot:
    """一个已确认（或暂定）的 ZigZag 拐点。

    Attributes
    ----------
    index : int
        拐点所在的 K 线位置（极值出现的索引，在传入 df 中的行号）。
    price : float
        拐点价格（high 或 low）。
    kind : 'high' | 'low'
    confirmed_at : int
        该拐点被确认时的 K 线索引。回测只能使用 ``confirmed_at <= 当前K线`` 的拐点。
        这是 anti-repaint 的关键。
    confirmed : bool
        是否已确认。仅最后一段的 running extreme 为 confirmed=False（暂定）。
    """

    index: int
    price: float
    kind: Kind
    confirmed_at: int
    confirmed: bool = True


@dataclass
class ZigZagResult:
    """detect_zigzag 的返回。

    pivots 按时间升序；其中只有最后一个可能是 confirmed=False（暂定极值）。
    """

    pivots: list[Pivot]
    provisional: Pivot | None                 # 未确认的当前 running extreme（可能为 None）
    config: ZigZagConfig
    n_bars: int
    atr: pd.Series                            # 供 validate / visualize 复用
    df: pd.DataFrame                          # 供 visualize 取 high/low/datetime

    @property
    def confirmed_pivots(self) -> list[Pivot]:
        """仅已确认拐点（回测安全集合）。"""
        return [p for p in self.pivots if p.confirmed]

    def pivot_count(self) -> int:
        return len(self.pivots)


def detect_zigzag(df: pd.DataFrame, config: ZigZagConfig | None = None) -> ZigZagResult:
    """对 OHLCV DataFrame 跑 ZigZag 切分。

    Parameters
    ----------
    df : DataFrame
        需含 datetime/open/high/low/close/volume（future_data 标准 schema，升序）。
    config : ZigZagConfig, 可选

    Returns
    -------
    ZigZagResult
    """
    config = config or ZigZagConfig()
    if config.reversal_mode not in ("extreme", "close"):
        raise ValueError(f"reversal_mode 必须是 'extreme' 或 'close'，得到 {config.reversal_mode!r}")

    need = {"high", "low", "close"}
    missing = need - set(df.columns)
    if missing:
        raise ValueError(f"df 缺少必要列: {missing}")

    data = add_atr(df.reset_index(drop=True), period=config.atr_period)
    n = len(data)
    high = data["high"].to_numpy(dtype=float)
    low = data["low"].to_numpy(dtype=float)
    close = data["close"].to_numpy(dtype=float)
    atr = data["atr"].to_numpy(dtype=float)

    if n < max(config.atr_period + 1, 2 * config.min_bars_between_pivots + 1):
        return ZigZagResult(pivots=[], provisional=None, config=config, n_bars=n,
                            atr=data["atr"], df=data)

    use_close = config.reversal_mode == "close"
    min_gap = config.min_bars_between_pivots

    # ---- 种子：用前几根的初始极值确定初始方向 ----
    # 取前 atr_period+1 根（ATR 在此之后较稳）确定第一个 running extreme 与方向。
    seed_end = min(config.atr_period + 1, n)
    seed_high_idx = int(np.argmax(high[:seed_end]))
    seed_low_idx = int(np.argmin(low[:seed_end]))

    pivots: list[Pivot] = []

    if seed_high_idx >= seed_low_idx:
        # 先出现低点再走高 → 先确认低点，转向找高
        direction: Literal["up", "down"] = "up"  # "up" = 正在追踪一个上升段(找高点)
        ext_idx, ext_price = seed_low_idx, float(low[seed_low_idx])
        pivots.append(Pivot(index=seed_low_idx, price=ext_price, kind="low",
                            confirmed_at=seed_low_idx, confirmed=True))
    else:
        direction = "down"  # 正在追踪一个下降段(找低点)
        ext_idx, ext_price = seed_high_idx, float(high[seed_high_idx])
        pivots.append(Pivot(index=seed_high_idx, price=ext_price, kind="high",
                            confirmed_at=seed_high_idx, confirmed=True))

    # ---- 主循环：running-extreme 状态机 ----
    start = max(seed_high_idx, seed_low_idx) + 1
    for i in range(start, n):
        a = atr[i]
        if not np.isfinite(a) or a <= 0:
            continue
        depth = config.depth_atr_multiple * a

        if direction == "up":
            # 追踪高点：刷新极值（持续追踪真极值，天然抗假突破）
            if high[i] > ext_price:
                ext_price = float(high[i])
                ext_idx = i
            # 反向波动达 depth → 确认高点
            trigger = close[i] if use_close else low[i]
            if ext_price - trigger >= depth and (i - ext_idx) >= 0:
                if i - pivots[-1].confirmed_at >= min_gap:
                    pivots.append(Pivot(index=ext_idx, price=ext_price, kind="high",
                                        confirmed_at=i, confirmed=True))
                    direction = "down"
                    ext_idx, ext_price = i, float(low[i])
        else:  # direction == "down"
            if low[i] < ext_price:
                ext_price = float(low[i])
                ext_idx = i
            trigger = close[i] if use_close else high[i]
            if trigger - ext_price >= depth and (i - ext_idx) >= 0:
                if i - pivots[-1].confirmed_at >= min_gap:
                    pivots.append(Pivot(index=ext_idx, price=ext_price, kind="low",
                                        confirmed_at=i, confirmed=True))
                    direction = "up"
                    ext_idx, ext_price = i, float(high[i])

    # ---- 暂定极值（最后一段 running extreme，未确认） ----
    provisional: Pivot | None = None
    if pivots:
        last = pivots[-1]
        prov_kind: Kind = "high" if direction == "up" else "low"
        # 仅当暂定极值与最后一个已确认拐点不同时才记录
        if ext_idx != last.index:
            provisional = Pivot(index=ext_idx, price=ext_price, kind=prov_kind,
                                confirmed_at=n - 1, confirmed=False)

    return ZigZagResult(pivots=pivots, provisional=provisional, config=config,
                        n_bars=n, atr=data["atr"], df=data)
