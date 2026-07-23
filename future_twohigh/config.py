"""future_twohigh 配置与品种定义。

两高两低突破系统 = 趋势 → 整理（两高两低）→ 顺势放量突破。

三层闸门（与 future_zigzag 的"硬门控 + 软评分"风格一致）：
    ① 形态（硬）：4 个交替摆动点 + 矩形/三角几何判定。
    ② 整理质量（软评分）：成交量萎缩 + 斐波那契回撤 + 整理时长。
    ③ 突破触发（硬）：顺势收盘突破 + 放量 + 大实体 + 幅度达标。

ZigZagConfig 直接复用 future_zigzag（同一套 anti-repaint 切分）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

# 复用 future_zigzag 的 ZigZagConfig（同一套 anti-repaint 切分，避免重写）。
from future_zigzag.config import ZigZagConfig, DEFAULT_MULTIPLIERS  # noqa: F401


@dataclass(frozen=True)
class TwoHighConfig:
    """两高两低形态判定配置。

    形态定义（多头为例，空头镜像）：
        取最近 4 个交替摆动点 H1→L1→H2→L2（高点→低点→高点→低点）。
        多头形态向上突破 H2；空头形态 L1→H1→L2→H2 向下突破 L2。

    间隔硬约束由 ZigZagConfig.min_bars_between_pivots 控制（默认 3 根），
    满足"每个点间隔 ≥ 3 根 K 线（防止毛刺）"。

    Parameters
    ----------
    zigzag : ZigZagConfig
        复用 future_zigzag 的切分配置。默认 min_bars_between_pivots=3。
    rect_tolerance : float
        矩形判定容差。|H2−H1|/H1 < 此值 且 |L2−L1|/L1 < 此值 → 上下轨近似水平。
        默认 0.02（2%）。跨品种用比例而非绝对值（玉米 vs 锡波动量级差几十倍）。
    max_lookback_pivots : int
        从最近一个拐点往回数 N 个已确认拐点寻找形态。默认 8 足以覆盖最近一组两高两低
        + 容错。
    require_contraction_amp : bool
        收敛三角是否额外要求 (H1−L1) > (H2−L2)（振幅严格收敛）。
        默认 True——经典收敛三角的几何特征。
    """

    zigzag: ZigZagConfig = field(default_factory=lambda: ZigZagConfig(min_bars_between_pivots=3))
    rect_tolerance: float = 0.02
    max_lookback_pivots: int = 8
    require_contraction_amp: bool = True


@dataclass(frozen=True)
class ConsolidationConfig:
    """整理质量软评分配置 —— 形态之后的独立闸门。

    三维各有明确物理含义、正交不重叠，用连续软评分（sigmoid）替代硬布尔，
    再按权重加权成总分。总分 ≥ consolidation_threshold 视为"合格整理"。

    为什么用软评分：成交量萎缩/斐波那契回撤/整理时长都是"程度"而非"是否"。
    硬阈值会把"略低于门槛"的好形态一刀切掉；连续分让边界附近形态保留信息。

    三维：
        ① 成交量萎缩（量能蓄势）：整理区间前半段均量 / 后半段均量，比值越小越萎缩。
           "整理期间成交量逐步萎缩（为突破蓄势）"的直接量化。
        ② 斐波那契回撤（中继 vs 反转）：回撤深度落在 50%~61.8% 区间给最高分。
           保证是"中继整理"而非"趋势反转"。
        ③ 整理时长：钟形打分，太短(<10根)是噪音，太长(>60根)趋势衰竭，中间最优。
    """

    # ① 成交量萎缩
    # vol_ratio = 后半段均量 / 前半段均量；ratio 越小越萎缩。
    # sigmoid 中点 0.8：ratio=0.8 → 0.5 分；ratio<0.8 → 越接近 1（萎缩显著）。
    volume_shrink_mid: float = 0.80
    volume_shrink_spread: float = 0.15

    # ② 斐波那契回撤
    # retracement = 回撤深度 / 条件1波段幅度（0=没回撤，1=全回撤）。
    # 用钟形：在 [fib_lo, fib_hi] = [0.50, 0.618] 区间给满分，偏离递减。
    fib_lo: float = 0.50
    fib_hi: float = 0.618
    fib_falloff: float = 0.15        # 偏离区间后的衰减宽度

    # ③ 整理时长（钟形）
    duration_lo: int = 10            # 太短(<此值)是噪音
    duration_hi: int = 60            # 太长(>此值)趋势衰竭
    duration_falloff: float = 8.0    # 偏离窗口后的衰减宽度（根）

    # 三维权重（sum=1.0）
    weight_volume: float = 0.45      # 量能蓄势是突破的直接前兆，权重最高
    weight_fibonacci: float = 0.30   # 斐波那契区分中继/反转
    weight_duration: float = 0.25    # 时长是辅助确认

    # 总分门槛
    consolidation_threshold: float = 0.40


@dataclass(frozen=True)
class BreakoutConfig:
    """突破信号触发配置（顺势放量突破）。

    参考未来 future_bb 已验证的三维突破阈值（volume>1.5×均量、实体>1.5×平均实体、
    突破幅度>0.5×ATR），但用 dataclass + 函数式风格，与 future_zigzag 一致。

    四个硬触发条件（AND）：
        ① 价格：收盘越过 H2（多头）/ 跌破 L2（空头）+ buffer×ATR。
        ② 量能：成交量 ≥ volume_multiple × 近 N 根均量。
        ③ 实体：K 线实体 ≥ body_multiple × 近 N 根平均实体。
        ④ 幅度：突破幅度（收盘 − 突破位）> magnitude_atr × ATR。

    所有触发只使用已收盘 K 线（anti-repaint 安全）。
    """

    # ① 价格突破
    breakout_buffer_atr: float = 0.05    # 收盘需越过突破位 + buffer×ATR（防贴边）
    breakout_max_lookback: int = 20      # L2/H2 确认后 N 根内出现突破

    # ② 量能
    volume_multiple: float = 1.5         # 量 ≥ 此值 × 近 N 根均量
    volume_lookback: int = 20

    # ③ 实体
    body_multiple: float = 1.5           # 实体 ≥ 此值 × 近 N 根平均实体
    body_lookback: int = 20

    # ④ 幅度
    magnitude_atr: float = 0.5           # 突破幅度 > 此值 × ATR

    # 止损：整理区间对侧（多头=L2，空头=H2）∓ buffer×ATR
    stop_atr_buffer: float = 0.3

    # 目标 R:R
    target_rr: float = 2.0


@dataclass(frozen=True)
class BacktestConfig:
    """回测引擎配置（信号独立结算模式，复用 future_zigzag.backtest）。

    与 future_zigzag.BacktestConfig 字段对齐，以便直接复用 run_backtest。
    """

    commission_rate: float = 0.00005    # 单边手续费，名义价值比例（万0.5）
    slippage_points: float = 1.0        # 单边滑点，价格点数
    multiplier: float = 10.0            # 合约乘数（元/点），按品种覆盖
    max_hold_bars: int = 80             # 超时平仓根数
    initial_capital: float = 100_000.0
    stop_mode: str = "structure"        # "structure" 用信号自带结构止损
    risk_pct: float = 0.009
    target_rr_by_type: dict = field(default_factory=lambda: {"breakout": 2.0})


# 当前回测/验证用品种（与 future_zigzag 一致，便于横向对比）。
# 元素为 (symbol, name, exchange)，与 future_data 的契约一致。
SYMBOLS: list[tuple[str, str, str]] = [
    ("PP0", "聚丙烯", "DCE"),
    ("IM0", "中证1000指数期货", "CFFEX"),
    ("LC0", "碳酸锂", "GFEX"),
    ("JM0", "焦煤", "DCE"),
    ("SC0", "上海原油", "INE"),
    ("IC0", "中证500指数期货", "CFFEX"),
    ("OI0", "菜油", "CZCE"),
    ("AG0", "白银", "SHFE"),
    ("SN0", "锡", "SHFE"),
]
