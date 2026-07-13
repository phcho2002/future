from dataclasses import dataclass


@dataclass(frozen=True)
class WyckoffConfig:
    """威科夫量价分析参数配置"""

    # ── 基础指标 ──
    atr_period: int = 14
    rsi_period: int = 14

    # ── 区间分析 ──
    range_lookback: int = 30           # 区间分析回看周期
    min_test_count: int = 3            # 最少测试次数
    max_contraction_ratio: float = 0.4 # 波动幅度收敛最大比率（越小越收敛）
    test_tolerance_atr: float = 0.5    # 测试核心价位的ATR容差

    # ── 成交量分析 ──
    volume_lookback: int = 10          # 成交量回看周期
    climax_volume_multiplier: float = 1.5  # 高潮量相对均量倍数
    volume_decline_threshold: float = 0.6  # 梯量递减阈值（近期均量/前期均量）
    effort_result_lookback: int = 5    # Effort vs Result 背离检测回看周期

    # ── 停止行为 ──
    wick_ratio_threshold: float = 0.5  # 影线占K线范围比例（长腿K线阈值）
    stop_volume_multiplier: float = 1.5  # 停止行为放量倍数（相对前5日均量）
    stop_lookback: int = 10            # 停止行为检测回看周期

    # ── Spring / Upthrust 确认 ──
    spring_recover_bars: int = 3       # 假跌破后收回最大K线数
    upthrust_recover_bars: int = 3     # 假突破后回落最大K线数
    spring_break_atr: float = 0.3      # 假跌破需击穿支撑的ATR倍数
    upthrust_break_atr: float = 0.3    # 假突破需突破压力的ATR倍数
    recover_volume_ratio: float = 1.2  # 收回时成交量相对均量倍数

    # ── RSI 过滤 ──
    long_rsi_max: float = 40.0         # 做多时RSI上限（超卖区）
    short_rsi_min: float = 60.0        # 做空时RSI下限（超买区）

    # ── 目标位计算 ──
    target_atr_multiple: float = 2.0     # 目标位ATR倍数

    # ── 阶段识别 ──
    climax_range_ratio: float = 0.05   # 高潮K线波动范围占价格比率
    ar_min_bars: int = 3               # 自动反弹最少K线数
