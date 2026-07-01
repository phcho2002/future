import numpy as np
import pandas as pd

from future_quant.config import QuantConfig
from future_quant.core.types import ChannelAnalysis, ChannelType, PushSet
from future_quant.indicators import (
    linear_regression_slope,
    normalized_slope,
    slope_to_degrees,
)


def analyze_channel(df: pd.DataFrame, push_set: PushSet, config: QuantConfig) -> ChannelAnalysis:
    if len(df) < 10:
        return ChannelAnalysis(ChannelType.UNKNOWN, reason="not enough bars")

    scan = df.tail(config.push_scan_bars)
    # Normalize slopes by ATR so the angle thresholds are comparable across
    # instruments at very different price levels (gold ~600 vs rebar ~3000).
    atr_ref = float(scan["atr"].mean()) or 1e-12
    upper_slope = normalized_slope(scan["high"], atr_ref)
    lower_slope = normalized_slope(scan["low"], atr_ref)
    upper_deg = slope_to_degrees(upper_slope)
    lower_deg = slope_to_degrees(lower_slope)
    diff = abs(abs(upper_deg) - abs(lower_deg))
    same_sign = upper_slope * lower_slope > 0

    # 突破边界价格：用独立窗口拟合 high/low 的回归线，取末点（最后一根 K 线）
    # 的拟合值作为当前轨道价位。所有通道类型都计算，供 signals.py 判定突破。
    upper_line_price, lower_line_price = _boundary_prices(df, config)

    if len(push_set.pushes) >= 3 and _third_push_is_parabolic(push_set, config):
        return ChannelAnalysis(
            ChannelType.PARABOLIC_WEDGE,
            upper_slope=upper_slope,
            lower_slope=lower_slope,
            slope_diff_deg=diff,
            upper_line_price=upper_line_price,
            lower_line_price=lower_line_price,
            reason="third push slope/strength exceeds prior pushes",
        )

    if diff < config.parallel_slope_diff_deg:
        channel_type = ChannelType.THREE_PUSH_NON_WEDGE if len(push_set.pushes) >= 3 else ChannelType.PARALLEL
        reason = "parallel trend lines; treat three pushes as structure, not wedge"
    elif diff > config.wedge_slope_diff_deg and same_sign:
        channel_type = ChannelType.CONVERGING_WEDGE
        reason = "same-sign slopes with meaningful convergence"
    elif diff > config.wedge_slope_diff_deg and not same_sign:
        channel_type = ChannelType.EXPANDING_TRIANGLE
        reason = "opposite-sign slopes with meaningful expansion"
    else:
        channel_type = ChannelType.UNKNOWN
        reason = "slope relationship not decisive"

    return ChannelAnalysis(
        channel_type=channel_type,
        upper_slope=upper_slope,
        lower_slope=lower_slope,
        slope_diff_deg=diff,
        upper_line_price=upper_line_price,
        lower_line_price=lower_line_price,
        reason=reason,
    )


def _boundary_prices(
    df: pd.DataFrame, config: QuantConfig
) -> tuple[float | None, float | None]:
    """拟合上/下轨道并取末点拟合值，作为当前阻力/支撑价。

    用 ``channel_boundary_window`` 窗口内的 high 拟合上轨、low 拟合下轨，
    回归直线外推到窗口末端（= 最后一根 K 线）即为「此刻」的轨道价位。
    落空数据时返回 (None, None)，signals.py 会按无突破处理。
    """
    window = config.channel_boundary_window
    scan = df.tail(window)
    if len(scan) < 5:
        return None, None
    y_high = scan["high"].to_numpy(dtype=float)
    y_low = scan["low"].to_numpy(dtype=float)
    x = np.arange(len(scan), dtype=float)
    try:
        # polyfit(x, y, 1) 返回 [斜率, 截距]，末点 x = len-1
        upper_line = float(np.polyval(np.polyfit(x, y_high, 1), len(scan) - 1))
        lower_line = float(np.polyval(np.polyfit(x, y_low, 1), len(scan) - 1))
    except (np.linalg.LinAlgError, ValueError):
        return None, None
    # 归一化：保证 upper >= lower（噪声下拟合线可能交叉）
    if upper_line < lower_line:
        upper_line, lower_line = lower_line, upper_line
    return upper_line, lower_line


def _third_push_is_parabolic(push_set: PushSet, config: QuantConfig) -> bool:
    p1, p2, p3 = push_set.pushes[-3:]
    prior_avg = (abs(p1.slope) + abs(p2.slope)) / 2
    if prior_avg <= 0:
        return False
    return abs(p3.slope) / prior_avg >= config.parabolic_third_push_ratio
