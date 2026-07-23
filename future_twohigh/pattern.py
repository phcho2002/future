"""两高两低形态检测器 —— 系统的核心。

把 ZigZag 压缩出的转折点序列，识别成"两高两低"整理形态。

形态定义（多头为例，空头镜像）：
    取最近 4 个交替摆动点 H1→L1→H2→L2（高→低→高→低）。
    多头形态：H1, L1, H2, L2，向上突破 H2。
    空头形态：L1, H1, L2, H2（低→高→低→高），向下突破 L2。

三层硬门槛（必须全过才算有效形态）：
    ① 交替性：4 个点必须严格交替（ZigZag 状态机天然保证 high/low 交替，
       这里显式校验防退化）。
    ② 间隔：相邻点 index 差 ≥ min_bars_between_pivots（默认 3，防毛刺）。
    ③ 几何分类：
        矩形：|H2−H1|/H1 < tol 且 |L2−L1|/L1 < tol → 上下轨近似水平。
        收敛三角：H2<H1（高点下移）且 L2>L1（低点上移），
                  且 (H1−L1)>(H2−L2)（振幅收敛）。
        两者都不满足 → 丢弃（不是有效整理形态）。

为什么用 ZigZag 拐点而非全部 K 线：
    拐点是"已确认的转折"，天然过滤掉波段内的噪音。在拐点上判定矩形/三角，
    语义明确（"两个高点几乎等高 + 两个低点几乎等低 = 矩形"），
    抗噪且跨品种可比（用 ATR 归一化的 depth 切分）。

anti-repaint：基于 confirmed_pivots + confirmed_at，回测用 as_of 截断。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from future_zigzag.zigzag import Pivot, ZigZagResult

from .config import TwoHighConfig

Direction = Literal["long", "short"]
PatternKind = Literal["rectangle", "triangle"]


@dataclass(frozen=True)
class TwoHighPattern:
    """一个识别出的两高两低整理形态。

    Attributes
    ----------
    direction : 'long' | 'short'
        顺势突破方向。多头=向上突破 H2；空头=向下突破 L2。
    kind : 'rectangle' | 'triangle'
        整理形态类型。
    h1, l1, h2, l2 : Pivot
        四个交替摆动点（多头顺序：H1→L1→H2→L2；空头：L1→H1→L2→H2）。
        统一用 h1/l1/h2/l2 命名：h* 是高点 Pivot，l* 是低点 Pivot，
        下标 1=第一对，2=第二对（更近）。
    pivot_indices : list[int]
        四个拐点的 K 线索引（时间升序），供后续模块取 K 线数据。
    confirmed_at : int
        形态完成（最后一个拐点 L2/H2）的 confirmed_at（anti-repaint 时间戳）。
        回测只用 confirmed_at ≤ 当前K线 的形态。
    rect_h_diff : float
        |H2−H1|/H1（高点偏离比，矩形判定用）。
    rect_l_diff : float
        |L2−L1|/L1（低点偏离比）。
    amp_first, amp_second : float
        第一/第二对振幅（H1−L1 / H2−L2 的绝对值），三角收敛判定用。
    """

    direction: Direction
    kind: PatternKind
    h1: Pivot
    l1: Pivot
    h2: Pivot
    l2: Pivot
    pivot_indices: list[int]
    confirmed_at: int
    rect_h_diff: float
    rect_l_diff: float
    amp_first: float
    amp_second: float

    @property
    def breakout_level(self) -> float:
        """顺势突破参照位：多头=H2（突破阻力向上），空头=L2（跌破支撑向下）。"""
        return self.h2.price if self.direction == "long" else self.l2.price

    @property
    def opposite_level(self) -> float:
        """整理区间对侧（止损参照）：多头=L2（区间下沿），空头=H2（区间上沿）。"""
        return self.l2.price if self.direction == "long" else self.h2.price

    @property
    def range_high(self) -> float:
        """整理区间上沿 = max(H1, H2)。"""
        return max(self.h1.price, self.h2.price)

    @property
    def range_low(self) -> float:
        """整理区间下沿 = min(L1, L2)。"""
        return min(self.l1.price, self.l2.price)

    @property
    def swing1_amplitude(self) -> float:
        """条件1波段幅度 = H1−L1（斐波那契回撤的基准波段）。"""
        return self.amp_first

    def as_dict(self) -> dict:
        return {
            "dir": self.direction,
            "kind": self.kind,
            "h1": round(self.h1.price, 2),
            "l1": round(self.l1.price, 2),
            "h2": round(self.h2.price, 2),
            "l2": round(self.l2.price, 2),
            "rect_h": round(self.rect_h_diff, 4),
            "rect_l": round(self.rect_l_diff, 4),
            "amp1": round(self.amp_first, 2),
            "amp2": round(self.amp_second, 2),
        }


def _classify_geometry(h1: float, l1: float, h2: float, l2: float,
                       cfg: TwoHighConfig) -> PatternKind | None:
    """判定矩形 / 收敛三角 / 无效。

    多头/空头共用——几何判定只看四个价格的高低关系，与方向无关。
    """
    tol = cfg.rect_tolerance

    # ① 矩形：两高点近似等高 + 两低点近似等低（上下轨水平）
    h_diff = abs(h2 - h1) / h1 if h1 > 0 else float("inf")
    l_diff = abs(l2 - l1) / l1 if l1 > 0 else float("inf")
    if h_diff < tol and l_diff < tol:
        return "rectangle"

    # ② 收敛三角：高点下移(H2<H1) + 低点上移(L2>L1) + 振幅收敛
    amp_first = h1 - l1
    amp_second = h2 - l2
    if h2 < h1 and l2 > l1:
        if not cfg.require_contraction_amp or amp_first > amp_second:
            return "triangle"

    return None


def _check_spacing(pivots: list[Pivot], min_gap: int) -> bool:
    """相邻拐点 index 差均 ≥ min_gap。"""
    for a, b in zip(pivots, pivots[1:]):
        if b.index - a.index < min_gap:
            return False
    return True


def _try_form(p0: Pivot, p1: Pivot, p2: Pivot, p3: Pivot,
              cfg: TwoHighConfig) -> TwoHighPattern | None:
    """尝试把 4 个连续交替拐点组成长/短形态。

    多头形态：p0=H1(高) p1=L1(低) p2=H2(高) p3=L2(低) → 向上突破 H2
    空头形态：p0=L1(低) p1=H1(高) p2=L2(低) p3=H2(高) → 向下突破 L2

    返回标准化的 TwoHighPattern（h1/l1/h2/l2 字段统一为"高低点"语义）。
    """
    min_gap = cfg.zigzag.min_bars_between_pivots
    pivs = [p0, p1, p2, p3]
    if not _check_spacing(pivs, min_gap):
        return None

    if p0.kind == "high" and p1.kind == "low" and p2.kind == "high" and p3.kind == "low":
        # 多头：H1=p0, L1=p1, H2=p2, L2=p3
        direction: Direction = "long"
        h1, l1, h2, l2 = p0, p1, p2, p3
    elif p0.kind == "low" and p1.kind == "high" and p2.kind == "low" and p3.kind == "high":
        # 空头：L1=p0, H1=p1, L2=p2, H2=p3
        direction = "short"
        l1, h1, l2, h2 = p0, p1, p2, p3
    else:
        return None  # 非严格交替（理论上 ZigZag 不会产生，防退化）

    kind = _classify_geometry(h1.price, l1.price, h2.price, l2.price, cfg)
    if kind is None:
        return None

    rect_h = abs(h2.price - h1.price) / h1.price if h1.price > 0 else float("nan")
    rect_l = abs(l2.price - l1.price) / l1.price if l1.price > 0 else float("nan")
    amp_first = abs(h1.price - l1.price)
    amp_second = abs(h2.price - l2.price)

    return TwoHighPattern(
        direction=direction,
        kind=kind,
        h1=h1, l1=l1, h2=h2, l2=l2,
        pivot_indices=[p0.index, p1.index, p2.index, p3.index],
        confirmed_at=p3.confirmed_at,
        rect_h_diff=float(rect_h),
        rect_l_diff=float(rect_l),
        amp_first=float(amp_first),
        amp_second=float(amp_second),
    )


def detect_two_high(zz: ZigZagResult, cfg: TwoHighConfig | None = None,
                    as_of: int | None = None) -> list[TwoHighPattern]:
    """从 ZigZag 结果识别所有两高两低形态。

    Parameters
    ----------
    zz : ZigZagResult
    cfg : TwoHighConfig
    as_of : int, 可选
        回测时当前 K 线索引；只返回 ``confirmed_at <= as_of`` 的形态（anti-repaint）。

    Returns
    -------
    list[TwoHighPattern]
        按最后一个拐点(L2/H2)时间升序。
    """
    cfg = cfg or TwoHighConfig()
    pivots = zz.confirmed_pivots
    if as_of is not None:
        pivots = [p for p in pivots if p.confirmed_at <= as_of]
    if len(pivots) < 4:
        return []

    patterns: list[TwoHighPattern] = []
    # 滑动窗口：每 4 个连续拐点（ZigZag 保证严格交替）
    for i in range(len(pivots) - 3):
        window = pivots[i:i + 4]
        pat = _try_form(*window, cfg)
        if pat is not None:
            patterns.append(pat)
    return patterns


def latest_two_high(zz: ZigZagResult, cfg: TwoHighConfig | None = None,
                    as_of: int | None = None) -> TwoHighPattern | None:
    """取最近一个两高两低形态（按时间升序取最后一个）。"""
    pats = detect_two_high(zz, cfg, as_of=as_of)
    return pats[-1] if pats else None
