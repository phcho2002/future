"""
技术指标计算模块
包含均线、ATR、ADX等常用指标
"""

import pandas as pd
import numpy as np
from typing import Tuple


class Indicators:
    """技术指标计算器"""

    @staticmethod
    def SMA(data: pd.Series, period: int) -> pd.Series:
        """简单移动平均线"""
        return data.rolling(window=period).mean()

    @staticmethod
    def EMA(data: pd.Series, period: int) -> pd.Series:
        """指数移动平均线"""
        return data.ewm(span=period, adjust=False).mean()

    @staticmethod
    def ATR(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
        """
        真实波动幅度（Average True Range）

        Args:
            high: 最高价序列
            low: 最低价序列
            close: 收盘价序列
            period: 计算周期

        Returns:
            ATR序列
        """
        # 计算真实波动幅度TR
        tr1 = high - low  # 当日最高最低价差
        tr2 = abs(high - close.shift(1))  # 当日最高与昨收差
        tr3 = abs(low - close.shift(1))  # 当日最低与昨收差

        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

        # 计算ATR（TR的移动平均）
        atr = tr.rolling(window=period).mean()

        return atr

    @staticmethod
    def ADX(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> Tuple[pd.Series, pd.Series, pd.Series]:
        """
        平均趋向指数（Average Directional Index）

        Args:
            high: 最高价序列
            low: 最低价序列
            close: 收盘价序列
            period: 计算周期

        Returns:
            (ADX, +DI, -DI)三个序列
        """
        # 计算+DM和-DM
        high_diff = high.diff()
        low_diff = -low.diff()

        plus_dm = pd.Series(0.0, index=high.index)
        minus_dm = pd.Series(0.0, index=high.index)

        plus_dm[(high_diff > low_diff) & (high_diff > 0)] = high_diff
        minus_dm[(low_diff > high_diff) & (low_diff > 0)] = low_diff

        # 计算TR
        tr1 = high - low
        tr2 = abs(high - close.shift(1))
        tr3 = abs(low - close.shift(1))
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

        # 平滑处理
        atr = tr.rolling(window=period).mean()
        plus_di = 100 * (plus_dm.rolling(window=period).mean() / atr)
        minus_di = 100 * (minus_dm.rolling(window=period).mean() / atr)

        # 计算DX和ADX
        dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di)
        adx = dx.rolling(window=period).mean()

        return adx, plus_di, minus_di

    @staticmethod
    def highest(data: pd.Series, period: int) -> pd.Series:
        """滚动最高值"""
        return data.rolling(window=period).max()

    @staticmethod
    def lowest(data: pd.Series, period: int) -> pd.Series:
        """滚动最低值"""
        return data.rolling(window=period).min()

    @staticmethod
    def body_size(open_price: pd.Series, close: pd.Series) -> pd.Series:
        """K线实体大小"""
        return abs(close - open_price)

    @staticmethod
    def upper_shadow(high: pd.Series, open_price: pd.Series, close: pd.Series) -> pd.Series:
        """上影线长度"""
        return high - pd.concat([open_price, close], axis=1).max(axis=1)

    @staticmethod
    def lower_shadow(low: pd.Series, open_price: pd.Series, close: pd.Series) -> pd.Series:
        """下影线长度"""
        return pd.concat([open_price, close], axis=1).min(axis=1) - low

    @staticmethod
    def count_resistance_touches(high: pd.Series, resistance_level: float, tolerance: float = 0.01) -> int:
        """
        计算阻力位触碰次数

        Args:
            high: 最高价序列
            resistance_level: 阻力位价格
            tolerance: 容差（百分比）

        Returns:
            触碰次数
        """
        upper_bound = resistance_level * (1 + tolerance)
        lower_bound = resistance_level * (1 - tolerance)

        touches = ((high >= lower_bound) & (high <= upper_bound)).sum()

        return touches

    @staticmethod
    def relative_strength(close: pd.Series, benchmark_close: pd.Series, period: int = 20) -> pd.Series:
        """
        相对强度（相对于基准的表现）

        Args:
            close: 品种收盘价
            benchmark_close: 基准收盘价（如板块指数）
            period: 计算周期

        Returns:
            相对强度序列（>1表示强于基准）
        """
        # 计算收益率
        returns = close.pct_change(period)
        benchmark_returns = benchmark_close.pct_change(period)

        # 相对强度
        rs = (1 + returns) / (1 + benchmark_returns)

        return rs

    @staticmethod
    def volume_ma(volume: pd.Series, period: int = 20) -> pd.Series:
        """成交量移动平均"""
        return volume.rolling(window=period).mean()

    @staticmethod
    def is_bullish_alignment(mas: dict) -> pd.Series:
        """
        判断是否多头排列

        Args:
            mas: 均线字典 {5: ma5, 10: ma10, 20: ma20, 60: ma60}

        Returns:
            布尔序列（True表示多头排列）
        """
        periods = sorted(mas.keys())

        # 从短到长检查是否依次递增
        alignment = pd.Series(True, index=mas[periods[0]].index)

        for i in range(len(periods) - 1):
            alignment &= (mas[periods[i]] > mas[periods[i + 1]])

        return alignment

    @staticmethod
    def detect_consolidation(high: pd.Series, low: pd.Series, period: int = 20, threshold: float = 0.05) -> pd.Series:
        """
        检测横盘整理区间

        Args:
            high: 最高价序列
            low: 最低价序列
            period: 检测周期
            threshold: 波动阈值（5%以内视为横盘）

        Returns:
            布尔序列（True表示横盘）
        """
        highest_high = high.rolling(window=period).max()
        lowest_low = low.rolling(window=period).min()

        range_pct = (highest_high - lowest_low) / lowest_low

        return range_pct <= threshold

    @staticmethod
    def detect_box(high: pd.Series, low: pd.Series, period: int = 30,
                   range_max: float = 0.02, touch_min: int = 2,
                   touch_tol: float = 0.005) -> pd.Series:
        """
        增强版箱体检测：波幅小 + 上下边界各自被触碰≥touch_min次。

        相比 detect_consolidation（只看波幅），本方法额外要求边界被反复测试，
        过滤掉"波幅小但无明显边界"的随机震荡。

        Args:
            high, low: 最高/最低价序列
            period: 回看窗口
            range_max: 最大波幅（(最高-最低)/最低）
            touch_min: 上下边界各自最小触碰次数
            touch_tol: 边界触碰容差（距边界 < touch_tol 视为触碰）

        Returns:
            布尔序列（True 表示该根K线处于合格箱体中）
        """
        hh = high.rolling(period).max()
        ll = low.rolling(period).min()
        rng_pct = (hh - ll) / ll

        # 上边界触碰：high 接近区间最高（在 touch_tol 容差内）
        up_touch = ((hh - high).abs() / hh < touch_tol).rolling(period).sum()
        # 下边界触碰：low 接近区间最低
        dn_touch = ((low - ll).abs() / ll < touch_tol).rolling(period).sum()

        is_box = (rng_pct <= range_max) & (up_touch >= touch_min) & (dn_touch >= touch_min)
        return is_box

    @staticmethod
    def detect_wedge(high: pd.Series, low: pd.Series, close: pd.Series,
                     period: int = 30, atr_shrink: float = 0.8,
                     range_shrink: float = 0.8) -> pd.Series:
        """
        收敛楔形检测：波动率收敛 + 波幅收窄。

        判定（比"高低点回归斜率"更稳健，避免噪声下斜率同时满足的罕见性）：
        1. 近 period 根的 ATR 均值 < 前 period 根 ATR 均值 × atr_shrink（波动收敛≥20%）
        2. 近 period 根后半段波幅 < 前半段波幅 × range_shrink（波幅收窄）

        Args:
            high, low, close: OHLC
            period: 回看窗口
            atr_shrink: 近期ATR/前期ATR 上限
            range_shrink: 后半段波幅/前半段波幅 上限

        Returns:
            布尔序列
        """
        atr = Indicators.ATR(high, low, close, 14)
        atr_now = atr.rolling(period).mean()
        atr_pre = atr.shift(period).rolling(period).mean()

        # 后半段 vs 前半段波幅
        half = period // 2
        hh2 = high.rolling(half).max().shift(half)   # 前半段最高
        ll2 = low.rolling(half).min().shift(half)    # 前半段最低
        rng_pre = hh2 - ll2
        hh1 = high.rolling(half).max()               # 后半段最高
        ll1 = low.rolling(half).min()                # 后半段最低
        rng_now = hh1 - ll1

        shrink_ok = (atr_now < atr_pre * atr_shrink) & (rng_now < rng_pre * range_shrink)
        return shrink_ok.fillna(False)


class BreakoutIndicators:
    """突破相关指标"""

    @staticmethod
    def is_price_breakout(close: pd.Series, high: pd.Series, lookback: int = 20, threshold: float = 0.005) -> pd.Series:
        """
        价格突破检测

        Args:
            close: 收盘价序列
            high: 最高价序列
            lookback: 回看周期
            threshold: 突破阈值（百分比）

        Returns:
            布尔序列（True表示突破）
        """
        # 计算前N日最高价（不包括当日）
        prev_high = high.shift(1).rolling(window=lookback).max()

        # 收盘价突破前高 * (1 + threshold)
        breakout = close > prev_high * (1 + threshold)

        return breakout

    @staticmethod
    def is_volume_surge(volume: pd.Series, period: int = 20, multiplier: float = 1.5) -> pd.Series:
        """
        量能放大检测

        Args:
            volume: 成交量序列
            period: 均量周期
            multiplier: 放大倍数

        Returns:
            布尔序列（True表示量能放大）
        """
        volume_ma = volume.rolling(window=period).mean()
        surge = volume > volume_ma * multiplier

        return surge

    @staticmethod
    def is_oi_increasing(open_interest: pd.Series) -> pd.Series:
        """
        持仓量增长检测

        Args:
            open_interest: 持仓量序列

        Returns:
            布尔序列（True表示持仓量增长）
        """
        return open_interest > open_interest.shift(1)

    @staticmethod
    def is_strong_body(open_price: pd.Series, close: pd.Series, period: int = 20, multiplier: float = 1.5) -> pd.Series:
        """
        强势K线实体检测

        Args:
            open_price: 开盘价序列
            close: 收盘价序列
            period: 平均周期
            multiplier: 实体倍数

        Returns:
            布尔序列（True表示强势实体）
        """
        body = abs(close - open_price)
        body_ma = body.rolling(window=period).mean()

        return body > body_ma * multiplier

    @staticmethod
    def no_pullback(low: pd.Series, breakout_level: pd.Series, bars: int = 3) -> pd.Series:
        """
        突破后不回头检测

        Args:
            low: 最低价序列
            breakout_level: 突破位序列
            bars: 检测K线数量

        Returns:
            布尔序列（True表示未回头）
        """
        no_pullback = pd.Series(True, index=low.index)

        for i in range(1, bars + 1):
            no_pullback &= (low.shift(-i) > breakout_level)

        return no_pullback

    # ============================================================
    # 做空（向下突破）对称指标
    # ============================================================

    @staticmethod
    def is_price_breakout_down(
        close: pd.Series, low: pd.Series, lookback: int = 20, threshold: float = 0.005
    ) -> pd.Series:
        """
        价格向下突破检测（做空入场）

        条件：收盘价跌破前N日最低价 × (1 - threshold)

        Args:
            close: 收盘价序列
            low: 最低价序列
            lookback: 回看周期
            threshold: 跌破阈值（百分比）

        Returns:
            布尔序列（True表示向下突破）
        """
        # 前N日最低价（不包括当日）
        prev_low = low.shift(1).rolling(window=lookback).min()

        # 收盘价跌破前低 × (1 - threshold)
        breakdown = close < prev_low * (1 - threshold)

        return breakdown

    @staticmethod
    def is_support_touched(
        low: pd.Series, support_level: pd.Series, lookback: int = 20, tolerance: float = 0.01
    ) -> pd.Series:
        """
        支撑位触碰检测（滚动窗口内是否触碰，用于结构质量评估）

        与 is_resistance_touched 对称：统计近 lookback 根K线内，
        最低价落在支撑位 ±tolerance 区间的次数是否达标。

        Args:
            low: 最低价序列
            support_level: 支撑位序列（通常为前N日最低价）
            lookback: 滚动窗口
            tolerance: 容差（百分比）

        Returns:
            滚动触碰次数序列
        """
        lower_bound = support_level * (1 - tolerance)
        upper_bound = support_level * (1 + tolerance)
        touched = (low >= lower_bound) & (low <= upper_bound)
        return touched.rolling(window=lookback).sum()

    @staticmethod
    def no_rebound(high: pd.Series, breakdown_level: pd.Series, bars: int = 3) -> pd.Series:
        """
        跌破后不反弹检测（no_pullback 的空头镜像）

        Args:
            high: 最高价序列
            breakdown_level: 跌破位序列
            bars: 检测K线数量

        Returns:
            布尔序列（True表示未反弹回跌破位之上）
        """
        no_reb = pd.Series(True, index=high.index)

        for i in range(1, bars + 1):
            no_reb &= (high.shift(-i) < breakdown_level)

        return no_reb


# 测试代码
if __name__ == "__main__":
    # 生成测试数据
    np.random.seed(42)
    n = 100

    dates = pd.date_range('2024-01-01', periods=n)
    close = pd.Series(100 + np.cumsum(np.random.randn(n) * 0.5), index=dates)
    high = close + np.random.rand(n) * 2
    low = close - np.random.rand(n) * 2
    open_price = close + np.random.randn(n) * 0.5
    volume = pd.Series(np.random.randint(1000, 5000, n), index=dates)

    print("=== 测试技术指标 ===")

    # 测试SMA
    ma20 = Indicators.SMA(close, 20)
    print(f"\nMA20最后5个值:\n{ma20.tail()}")

    # 测试ATR
    atr = Indicators.ATR(high, low, close, 14)
    print(f"\nATR最后5个值:\n{atr.tail()}")

    # 测试ADX
    adx, plus_di, minus_di = Indicators.ADX(high, low, close, 14)
    print(f"\nADX最后5个值:\n{adx.tail()}")
    print(f"+DI最后5个值:\n{plus_di.tail()}")
    print(f"-DI最后5个值:\n{minus_di.tail()}")

    # 测试突破检测
    breakout = BreakoutIndicators.is_price_breakout(close, high, 20, 0.005)
    print(f"\n突破信号数量: {breakout.sum()}")

    # 测试量能放大
    volume_surge = BreakoutIndicators.is_volume_surge(volume, 20, 1.5)
    print(f"量能放大信号数量: {volume_surge.sum()}")
