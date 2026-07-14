"""ZigZag 模块配置与品种定义。

ZigZagConfig 控制波段切分行为；SYMBOLS 是当前回测/验证用的 9 个品种。
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ZigZagConfig:
    """ZigZag 段切分配置。

    Parameters
    ----------
    depth_atr_multiple : float
        反转门槛 = 该值 × ATR。用 ATR 归一化后跨品种可比
        （玉米和锡波动量级差几十倍，固定百分比不可比）。
        默认 1.5：经 9 品种 × 3000 根验证，mean 段 ≈ 3.1 ATR、跨品种极稳
        （3.05~3.26，<7% 波动）。1.0 太细（2.4 ATR，毛刺多），2.0 太粗
        （4.2 ATR，会漏 Three Push 的子推动）。
    atr_period : int
        ATR 计算周期（Wilder RMA）。14 是 TradingView/MT4 的通用默认。
    reversal_mode : str
        "extreme" —— 用反向影线（low/high）触发反转，经典 ZigZag；
        "close"  —— 用收盘价触发，更干净、毛刺少，适合偏收盘价体系的策略。
    min_bars_between_pivots : int
        相邻拐点间最少间隔 K 线数，防止在 depth 附近的抖动产生密集拐点。
    """

    depth_atr_multiple: float = 1.5
    atr_period: int = 14
    reversal_mode: str = "extreme"
    min_bars_between_pivots: int = 2


@dataclass(frozen=True)
class ThreePushConfig:
    """Three Push 识别与四维量化评分配置。

    四维各有明确物理含义、正交不重叠，用连续软评分（soft sigmoid）替代
    future_1 的"7 个布尔等权平均"。

    硬门控（必须满足才视为有效 Three Push）：
        amplitude OR volume 至少一项"显著衰竭"（decay >= respective threshold）。
        这是 Al Brooks / Lance Beggs 体系里最硬的两条——幅度和量能是衰竭的
        直接证据；推动力和时间作为辅助确认，不单独构成衰竭。
        （future_1 把 strength_ratio 算了不用，且无任何硬门控。）
    """

    # ── ZigZag 配置（Three Push 基于 Pivot，复用同一套切分）──
    zigzag: ZigZagConfig = field(default_factory=ZigZagConfig)

    # ── 三推结构门控 ──
    # 同向三段需要"同向"：第三推的 net 幅度方向与前两推一致。
    # 抓取范围：从最近一个拐点（第三推极值）往回数 N 个拐点寻找模式。
    max_lookback_pivots: int = 9

    # ── 四维：每维用 soft sigmoid 映射到 [0,1]，再按权重加权 ──
    # soft_score(x, mid, spread) = 1 / (1 + exp(-(x-mid)/spread))
    #   x 越小（越衰竭）→ 越接近 1（高分）。

    # ① 幅度 extent：第三推 net_move / max(p1,p2) net_move
    #    经典衰减判据。<0.6 算显著衰竭。
    amplitude_mid: float = 0.60       # sigmoid 中点
    amplitude_spread: float = 0.12    # 越小越陡（越接近硬阈值）

    # ② 时间 duration：第三推 K 线数 / max(p1,p2) K 线数
    #    第三推常靠"耗时"而非"推进"——耗时长但幅度小是典型衰竭。
    #    注意：时间长本身是中性偏弱的衰竭信号，所以这里方向反过来——
    #    时间越长越倾向"拖延型衰竭"，但权重较低。
    duration_mid: float = 1.20
    duration_spread: float = 0.30

    # ③ 成交量 volume：第三推均量 / 前两推均量
    #    <0.5~0.7 算量能枯竭。<0.6 算显著。
    volume_mid: float = 0.60
    volume_spread: float = 0.12

    # ④ 推动力 momentum：第三推 atr_multiple / max(p1,p2) atr_multiple
    #    （包含幅度+斜率+实体强度的综合推动力，沿用 future_1 pushes.py 的复合思路）
    #    <0.5 算显著衰竭。
    momentum_mid: float = 0.50
    momentum_spread: float = 0.12

    # ── 硬门控阈值（必须达到才算"该维显著衰竭"）──
    amplitude_hard: float = 0.60      # decay <= 此值 = 幅度显著衰竭
    volume_hard: float = 0.60         # ratio <= 此值 = 量能显著衰竭

    # ── 四维权重（sum=1.0），决定各维对总分的影响力 ──
    # 幅度+量能是主证，推动力次之，时间最弱（仅拖延型衰竭才体现）。
    weight_amplitude: float = 0.35
    weight_duration: float = 0.15
    weight_volume: float = 0.30
    weight_momentum: float = 0.20

    # ── 总分门槛 ──
    # 0~1 连续分。>= 0.45 视为"有效 Three Push 衰竭"（供后续反转信号消费）。
    # 这个值不是魔法数——它由"四维软评分 + 权重"自然产生，
    # 且配合硬门控（幅度或量能至少一项显著）双重约束。
    valid_score_threshold: float = 0.45


@dataclass(frozen=True)
class ContractionConfig:
    """收缩判定配置 —— Three Push 之后、反转信号之前的独立闸门。

    "收缩"在数学上 = 波幅递减 + 波动率收缩。这是"楔形/三角形即将突破"的本质，
    也是期货行情最稳定的模式之一（波动率收缩 → 即将扩张）。
    future_1 完全没有这一层。

    两个独立判据，至少满足其一即视为"已收缩"：
        ① 幅度递减（amplitude contraction）：三个推动腿幅度单调递减。
           这是楔形/三角形的直接几何特征。注意 Three Push 结构已保证
           "高点递升 P1<P3<P5"，收缩看的是"每次推进的幅度越来越小"。
        ② ATR 收缩（volatility contraction）：模式末端 ATR 处于历史低位
           （相对回看窗的分位）。"波动率收缩 → 即将扩张"。
    """

    # ① 幅度递减判据
    # amp_ratio_3to1 = L3.net_move / L1.net_move；amp_ratio_2to1 = L2/L1
    # 收缩 = L3 < L2 < L1（严格递减），用软评分度量"递减程度"。
    amp_contraction_mid: float = 0.55     # L3/L1 的 sigmoid 中点
    amp_contraction_spread: float = 0.15

    # ② ATR 收缩判据
    atr_lookback: int = 100               # 历史回看窗（根）
    atr_percentile_threshold: float = 0.35  # 末端 ATR 分位 <= 此值 = 收缩
    atr_soft_mid: float = 0.35            # 软评分中点
    atr_soft_spread: float = 0.12

    # 末端 ATR 取值位置：模式 P5 之后 N 根（给一点确认时间，避免用 P5 那根的瞬时值）
    atr_end_offset: int = 3

    # 收缩评分门槛 + 权重（幅度递减 + ATR收缩 各占一半）
    contraction_threshold: float = 0.40
    weight_amp_contraction: float = 0.55
    weight_atr_contraction: float = 0.45


@dataclass(frozen=True)
class LookaheadConfig:
    """前瞻回报验证配置。

    用"结果"客观验证信号预测力：对每个在 K 线 i 确认的模式，
    统计未来 ``horizons`` 根 K 线内，价格沿"反转方向"的最大有利/不利幅度。
    这是 future_1 完全缺失的——它只调出魔法数 55，从不验证信号是否赚钱。

    注意：前瞻验证仅用于"评估指标质量"，不参与实盘信号（实盘无未来信息）。
    """
    horizons: tuple = (10, 20, 40, 80)    # 未来 N 根 K 线
    # 反转方向：顶部三推(P5=high) → 看空，统计 close - P5close 是否为负（下跌）
    # 底部三推(P5=low) → 看多，统计 close - P5close 是否为正（上涨）
    # MFE = 最大有利偏移（顶部=最大下跌幅度，底部=最大上涨幅度），ATR 归一化


@dataclass(frozen=True)
class SignalConfig:
    """三类反转信号触发器配置。

    future_1 这一块基本是空的——config 里 reversal_wick_body_multiple /
    confirm_breakout_atr 等参数定义了但代码一行没用，三个检测器塌缩成一个
    body>=0.3×ATR 的弱布尔。本模块把三类写成独立检测器，各有硬触发条件。

    三类信号：
        ① 楔形趋势线突破：拐点趋势线收盘突破。
        ② 强反转 K：大实体 + 收盘极端 + 放量 + 位置在 P5 极值附近。
        ③ 二次入场：首次突破 → 回踩不破前极值 → 二次突破（状态机，跨 K 线时序）。
    所有触发器只使用 confirmed Pivot 和已收盘 K 线（anti-repaint 安全）。
    """

    # ── 止损配置（三类共用）──
    # stop_base: 'p5' = P5 极值外侧（传统）。'p4' 已废弃（P4 回撤极值比入场点远，
    #   做空时止损会落到入场价下方，是回测假象）。
    stop_base: str = "p5"
    # stop_atr_buffer: 经扫描验证，反转信号需要呼吸空间——紧止损(0.5)被噪音频繁打掉
    # (止损率56%)，松止损(1.5)扛住噪音让盈利单跑出来(止损率48%、PF 1.60)。
    # 这推翻了"止损过松"的初始假设：趋势/反转类策略靠少数大赢盈利，紧止损砍掉萌芽。
    stop_atr_buffer: float = 1.5

    # ── ① 楔形趋势线突破 ──
    # 用三个推极值(P1,P3,P5)拟合趋势线，收盘突破即触发。
    trendline_break_buffer_atr: float = 0.05   # 突破需超过趋势线 + buffer×ATR（防贴边）
    trendline_max_lookback: int = 12           # 突破检测窗口：P5 确认后 N 根内

    # ── ② 强反转 K 线 ──
    # reversal_body_atr 从 0.7 放松到 0.5：0.7 时样本仅 27（最少），
    # 但胜率最高(63%)、止损率最低(44%)。放松到 0.5 增加样本量，验证是否能保持质量。
    reversal_body_atr: float = 0.5        # 实体 >= 此值×ATR
    reversal_close_extreme_ratio: float = 0.30  # 收盘在 K 线极端 30% 区间内
    reversal_volume_ratio: float = 1.2    # 量 >= 近 10 根均值×此值
    reversal_volume_lookback: int = 10
    reversal_position_atr: float = 1.5    # 反转 K 必须在 P5 极值 ±此值×ATR 内（位置约束）
    reversal_max_lookback: int = 8        # P5 确认后 N 根内出现强反转 K

    # ── ③ 二次入场（状态机）──
    # 首次突破 → 回踩 → 二次突破
    second_entry_pullback_atr: float = 1.0   # 回踩幅度门槛（ATR）
    second_entry_max_lookback: int = 20      # 首次突破后 N 根内完成二次入场
    second_entry_break_atr: float = 0.15     # 二次突破 buffer

    # 信号确认后有效窗口（供前瞻/回测取入场点）
    signal_valid_bars: int = 3


# 合约乘数（元/点），来源同 future_1/backtester 的交易所默认值；补全 SN0/OI0/SC0。
DEFAULT_MULTIPLIERS: dict[str, float] = {
    "IF0": 300.0, "IC0": 200.0, "IH0": 300.0, "IM0": 200.0,
    "RB0": 10.0, "HC0": 10.0, "AU0": 1000.0, "AG0": 15.0,
    "CU0": 5.0, "AL0": 5.0, "ZN0": 5.0, "NI0": 1.0, "SN0": 1.0,
    "M0": 10.0, "Y0": 10.0, "P0": 10.0, "C0": 10.0,
    "SR0": 10.0, "CF0": 5.0, "TA0": 5.0, "MA0": 10.0, "PP0": 5.0,
    "OI0": 10.0, "I0": 100.0, "J0": 100.0, "JM0": 60.0,
    "FU0": 10.0, "BU0": 10.0, "RU0": 10.0, "SC0": 1000.0,
    "FG0": 20.0, "SA0": 20.0, "SM0": 5.0, "LC0": 1.0,
    "TF0": 10000.0, "T0": 10000.0, "TS0": 20000.0,
}


@dataclass(frozen=True)
class BacktestConfig:
    """回测引擎配置（信号独立结算模式）。

    仓位模式：信号独立结算——每个信号独立入场+止损+目标，互不影响（允许同品种
    多仓重叠）。最大化信号利用率，统计的是"每个信号单独执行的期望"。
    假设标注：不考虑资金挤占、交易间有相关性，期望值会比单仓模式偏高。

    成本：手续费（单边，名义价值比例）+ 滑点（单边，价格点数）×合约乘数。
    沿用 future_1 backtester 的研究级惯例。

    出场：止损/目标 同根触及保守按止损先成交；超时 max_hold_bars 平仓。

    目标 R:R：按信号类型差异化——reversal_bar 胜率高用较低 R:R，
    wedge_breakout 胜率低用较高 R:R 补偿。具体最优值由 R:R 敏感度分析决定。
    """

    commission_rate: float = 0.00005   # 单边手续费，名义价值比例（万0.5）
    slippage_points: float = 1.0       # 单边滑点，价格点数
    multiplier: float = 10.0           # 合约乘数（元/点），按品种覆盖
    max_hold_bars: int = 80            # 超时平仓根数
    initial_capital: float = 100_000.0  # 初始资金（仅用于回撤/夏普归一化展示）

    # 止损模式：
    #   "structure" = 用信号自带的结构止损（P5+ATR），risk 由结构距离决定。
    #   "fixed_risk" = 固定金额资金止损：每笔最大亏 initial_capital × risk_pct。
    #     止损价 = 入场 ± (risk_amount / multiplier)，risk_amount = capital × risk_pct。
    #     每笔 R 恒为 1（亏固定金额），R:R 的 R 用固定金额距离。
    stop_mode: str = "structure"
    risk_pct: float = 0.009            # 每笔最大亏损 = 总资金 × 0.9%（fixed_risk 模式用）

    # 按信号类型差异化目标 R:R（reward:risk）。默认值后续由敏感度分析校准。
    target_rr_by_type: dict = field(default_factory=lambda: {
        "wedge_breakout": 2.0,
        "reversal_bar": 1.5,
        "second_entry": 2.0,
    })


# 当前回测/验证用品种（用户指定）。
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
