from dataclasses import dataclass


@dataclass(frozen=True)
class QuantConfig:
    """Centralized thresholds for market-structure and wedge logic."""

    atr_period: int = 14

    # Module 1.1
    trend_window: int = 5
    trend_min_count: int = 4
    range_window: int = 10
    range_min_inside_count: int = 7
    overlap_filter_window: int = 20
    overlap_filter_ratio: float = 0.60
    body_overlap_threshold: float = 0.30
    bar_overlap_threshold: float = 0.20
    narrow_channel_overlap_ratio: float = 0.35
    wide_channel_overlap_ratio: float = 0.60

    # Module 1.2
    parallel_slope_diff_deg: float = 3.0
    wedge_slope_diff_deg: float = 15.0
    parabolic_third_push_ratio: float = 1.50

    # Module 2
    push_scan_bars: int = 60
    push_min_atr: float = 3.0
    push_min_bars: int = 4
    pullback_min_atr: float = 1.0
    exhaustion_distance_ratio: float = 0.70
    exhaustion_avg_bar_ratio: float = 0.60
    exhaustion_strong_body_ratio: float = 0.50
    exhaustion_volume_ratio: float = 0.50
    exhaustion_slope_ratio: float = 0.50
    long_wick_ratio_threshold: float = 0.60
    # Exhaustion gating (tightened): require both a high average score AND a
    # minimum number of the 7 checks to pass, so a single dominating check
    # cannot manufacture a signal.
    exhaustion_score_threshold: float = 0.60
    min_exhaustion_checks: int = 4

    # Module 3
    third_push_strength_max_ratio: float = 0.60
    channel_overshoot_atr: float = 1.0
    fail_check_bars_min: int = 3
    fail_check_bars_max: int = 5
    # Third-push extreme confirmation: a genuine wedge reversal requires the
    # third push to print a new pattern high (bearish reversal) / low (bullish
    # reversal). ``wick_tolerance`` allows it to fall slightly short.
    third_push_extreme_tolerance: float = 0.002
    # Reversal-candle quality (signals.py::_find_reversal_bar). The weak
    # "bearish close OR long wick" rule is replaced by a composite: the candle
    # must show a real body of at least ``reversal_body_atr_ratio`` * ATR OR a
    # wick at least ``reversal_wick_body_multiple`` * its body, AND close back
    # through the signal bar's midpoint.
    reversal_body_atr_ratio: float = 0.3
    reversal_wick_body_multiple: float = 2.0
    # Confirmation candle: must break the signal bar extreme by at least
    # ``confirm_breakout_atr`` * ATR, on volume >= ``confirm_volume_ratio`` *
    # the recent average.
    confirm_breakout_atr: float = 0.2
    confirm_volume_ratio: float = 1.0
    confirm_volume_lookback: int = 10

    # Module 5
    stop_atr_buffer: float = 1.0
    wide_channel_stop_atr_buffer: float = 1.5
    risk_per_trade: float = 0.01
    daily_loss_stop: float = 0.05
    # Minimum reward:risk; signals whose target_1 does not clear this are dropped.
    min_reward_risk: float = 1.5

    # ---- 突破交易专用风险几何（build_breakout_levels）----
    # 突破场景不沿用"止损=区间另一侧"（那会让止损距离 ≈ 2×区间高度，远大于
    # 目标=形态高度，导致 R:R<0.5 倒挂）。改为 ATR 止损 + 固定 R 倍数目标：
    # 止损贴在突破K线反侧 + stop_atr_buffer*ATR；目标 = 风险距离 × target_R。
    breakout_stop_atr_buffer: float = 1.0  # 突破K线反侧外移的 ATR 缓冲
    breakout_target_r: float = 2.0          # target_1 = 风险距离 × R（R:R=2:1）
    breakout_target2_r: float = 3.0         # target_2 = 风险距离 × R（R:R=3:1）

    # ------------------------------------------------------------------
    # Module 6 — 突破触发 + 加权评分模型
    #
    # 触发条件（必要门槛）：最近 K 线收盘突破区间边界 / 楔形轨道
    #   （收盘越过边界 ≥ breakout_atr_margin*ATR 且量能达标）。
    # 其余维度（通道形态/推数/突破力度/exhaustion/结构/风报比）全部转为评分，
    # 总分 ≥ signal_score_threshold 才出有效信号。
    # ------------------------------------------------------------------
    signal_score_threshold: float = 55.0  # 回测验证的最优阈值(4品种×6月)：55为唯一盈利点

    # ---- 突破触发判定（替代旧反转硬门槛）----
    breakout_atr_margin: float = 0.2       # 收盘越过边界的最小 ATR 余量
    breakout_volume_ratio: float = 0.8     # 突破量 ≥ 近期均值的倍数
    breakout_volume_lookback: int = 10     # 量能均值回看窗口
    # 计算通道边界价格的回看窗口（拟合上/下轨用），与 push_scan_bars 解耦
    channel_boundary_window: int = 20

    # ---- 维度满分（权重，合计 = 100）----
    # 通道形态分：楔形突破最高，平行区间突破次之，扩散再次，未知最低
    channel_wedge_score: float = 22.0          # 收敛楔形 / 抛物楔形
    channel_parallel_score: float = 18.0       # 平行区间（突破最干净）
    channel_expanding_score: float = 14.0      # 扩散三角
    channel_three_push_score: float = 12.0     # 三推非楔形
    channel_unknown_score: float = 6.0         # 未定型结构

    push_count_three_score: float = 14.0       # 3 推
    push_count_two_score: float = 7.0          # 2 推（需补偿）
    breakout_score_max: float = 22.0           # 突破力度（按 ATR 倍数线性计分）
    exhaustion_score_max: float = 14.0         # exhaustion 综合分
    structure_score_max: float = 10.0          # 结构加分（三推极值 + 反转K线）
    reward_risk_score_max: float = 10.0        # 风报比评分（完全评分项，无硬底线）

    # 突破力度计分锚点：breakout_margin 达到这些 ATR 倍数时的得分比例
    breakout_min_atr: float = 0.2    # 刚过硬门槛（0.2 ATR）= 满分的 20%
    breakout_full_atr: float = 1.0   # ≥1.0 ATR = 突破满分
    breakout_volume_bonus: float = 0.15  # volume 达标时的额外加成（占比满分）

    # 风报比计分锚点：R:R 在 [reward_risk_min_score, reward_risk_full] 区间线性映射
    reward_risk_min_score: float = 0.5   # R:R≤0.5 给 0 分
    reward_risk_full: float = 2.0        # R:R≥2.0 给满分

    # exhaustion 计分：passed_checks 需达到此数才给 exhaustion 分
    exhaustion_min_checks_for_score: int = 3
