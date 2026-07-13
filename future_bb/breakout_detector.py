"""
突破检测引擎
实现三维突破识别：价格+量能+时间
"""

import pandas as pd
import numpy as np
from typing import Dict, List, Tuple
from indicators import Indicators, BreakoutIndicators
import config


class BreakoutDetector:
    """突破检测器"""

    def __init__(self):
        self.config = config

    def detect(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        检测【做多】突破信号（三维：价格 + 量能 + 时间）

        Args:
            df: OHLCV数据，必须包含列：open, high, low, close, volume, open_interest

        Returns:
            添加了突破信号列的DataFrame（列名无前缀，向后兼容）
        """
        # 1. 价格维度突破
        price_breakout = self._detect_price_breakout(df)

        # 2. 量能维度确认
        volume_confirm = self._detect_volume_confirm(df)

        # 3. 时间维度验证
        time_confirm = self._detect_time_confirm(df)

        # 综合三维判断
        df['breakout_signal'] = price_breakout & volume_confirm & time_confirm
        df['price_breakout'] = price_breakout
        df['volume_confirm'] = volume_confirm
        df['time_confirm'] = time_confirm

        return df

    def detect_short(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        检测【做空】突破信号（三维向下突破，与 detect() 对称镜像）

        - 价格：收盘跌破前N日最低价×(1-阈值)，且跌破幅度 > 0.5×ATR
        - 量能：放量 AND 持仓增长（与做多共用 _detect_volume_confirm）
        - 时间：收盘跌破支撑位 AND 强势实体 AND 下影线短

        Args:
            df: OHLCV数据（detect() 已写入 atr 列则复用，否则重算）

        Returns:
            添加 short_* 系列列的DataFrame
        """
        # 1. 价格维度向下突破
        price_breakdown = self._detect_price_breakout_down(df)

        # 2. 量能维度确认（与做多共用：放量 + 持仓增长）
        volume_confirm = self._detect_volume_confirm(df)

        # 3. 时间维度验证（空头版）
        time_confirm = self._detect_time_confirm_down(df)

        # 综合三维判断
        df['short_breakout_signal'] = price_breakdown & volume_confirm & time_confirm
        df['short_price_breakout'] = price_breakdown
        df['short_volume_confirm'] = volume_confirm
        df['short_time_confirm'] = time_confirm

        return df

    def _detect_price_breakout(self, df: pd.DataFrame) -> pd.Series:
        """
        价格维度：结构突破

        条件：
        1. 收盘价突破前N日最高价 * (1 + threshold)
        2. 突破幅度 > 0.5 ATR
        """
        close = df['close']
        high = df['high']
        low = df['low']

        # 计算ATR
        atr = Indicators.ATR(high, low, close, self.config.ATR_PERIOD)
        df['atr'] = atr

        # 前N日最高价（不包括当日）
        prev_high = high.shift(1).rolling(window=self.config.LOOKBACK_PERIOD).max()
        df['resistance_level'] = prev_high

        # 突破条件1：价格突破
        condition1 = close > prev_high * (1 + self.config.BREAKOUT_THRESHOLD)

        # 突破条件2：突破幅度 > 0.5 ATR
        breakout_magnitude = close - prev_high
        condition2 = breakout_magnitude > atr * self.config.BREAKOUT_ATR_MULTIPLIER

        price_breakout = condition1 & condition2

        return price_breakout

    def _detect_volume_confirm(self, df: pd.DataFrame) -> pd.Series:
        """
        量能维度：动力确认

        条件：
        1. 成交量 > 20日均量 * 1.5
        2. 持仓量增长（期货特有）
        """
        volume = df['volume']

        # 成交量放大
        volume_ma = Indicators.volume_ma(volume, period=20)
        df['volume_ma'] = volume_ma
        condition1 = volume > volume_ma * self.config.VOLUME_MULTIPLIER

        # 持仓量增长（如果有该列）
        if 'open_interest' in df.columns:
            condition2 = BreakoutIndicators.is_oi_increasing(df['open_interest'])
        else:
            condition2 = pd.Series(True, index=df.index)  # 如果没有持仓量数据，默认通过

        volume_confirm = condition1 & condition2

        return volume_confirm

    def _detect_time_confirm(self, df: pd.DataFrame) -> pd.Series:
        """
        时间维度：有效性验证

        条件：
        1. 收盘价站稳突破位
        2. 突破K线实体 >= 近20根平均实体 * 1.5
        3. 上影线短（上涨突破时上影线 < 实体30%）
        """
        open_price = df['open']
        high = df['high']
        low = df['low']
        close = df['close']

        # 条件1：收盘价站稳突破位（已在价格维度检测）
        condition1 = close > df.get('resistance_level', close)

        # 条件2：强势实体
        condition2 = BreakoutIndicators.is_strong_body(
            open_price, close,
            period=20,
            multiplier=self.config.BODY_SIZE_MULTIPLIER
        )

        # 条件3：上影线短（上涨突破）
        body = abs(close - open_price)
        upper_shadow = Indicators.upper_shadow(high, open_price, close)
        is_bullish = close > open_price

        condition3 = ~is_bullish | (upper_shadow < body * 0.3)  # 阴线或上影线短

        time_confirm = condition1 & condition2 & condition3

        return time_confirm

    # ============================================================
    # 做空（向下突破）三维检测 —— detect() 的对称镜像
    # ============================================================

    def _detect_price_breakout_down(self, df: pd.DataFrame) -> pd.Series:
        """
        价格维度：向下结构突破

        条件：
        1. 收盘价跌破前N日最低价 × (1 - threshold)
        2. 跌破幅度 > SHORT_BREAKOUT_ATR_MULTIPLIER × ATR
        """
        close = df['close']
        low = df['low']
        high = df['high']

        # ATR（若 detect() 已计算则复用）
        if 'atr' in df.columns and df['atr'].notna().any():
            atr = df['atr']
        else:
            atr = Indicators.ATR(high, low, close, self.config.ATR_PERIOD)
            df['atr'] = atr

        # 前N日最低价（不包括当日）
        prev_low = low.shift(1).rolling(window=self.config.LOOKBACK_PERIOD).min()
        df['support_level'] = prev_low

        # 跌破条件1：价格跌破
        condition1 = close < prev_low * (1 - self.config.SHORT_BREAKOUT_THRESHOLD)

        # 跌破条件2：跌破幅度 > 0.5 ATR
        breakdown_magnitude = prev_low - close
        condition2 = breakdown_magnitude > atr * self.config.SHORT_BREAKOUT_ATR_MULTIPLIER

        price_breakdown = condition1 & condition2

        return price_breakdown

    def _detect_time_confirm_down(self, df: pd.DataFrame) -> pd.Series:
        """
        时间维度：向下突破有效性验证

        条件：
        1. 收盘价跌破支撑位（已在价格维度检测）
        2. 突破K线实体 >= 近20根平均实体 × SHORT_BODY_SIZE_MULTIPLIER
        3. 下影线短（下跌突破时下影线 < 实体30%）
        """
        open_price = df['open']
        high = df['high']
        low = df['low']
        close = df['close']

        # 条件1：收盘价跌破支撑位
        condition1 = close < df.get('support_level', close)

        # 条件2：强势实体（阴线实体大）
        condition2 = BreakoutIndicators.is_strong_body(
            open_price, close,
            period=20,
            multiplier=self.config.SHORT_BODY_SIZE_MULTIPLIER
        )

        # 条件3：下影线短（下跌突破）
        body = abs(close - open_price)
        lower_shadow = Indicators.lower_shadow(low, open_price, close)
        is_bearish = close < open_price

        condition3 = ~is_bearish | (lower_shadow < body * 0.3)  # 阳线或下影线短

        time_confirm = condition1 & condition2 & condition3

        return time_confirm

    def count_resistance_touches(self, df: pd.DataFrame, resistance_level: float, lookback: int = 30) -> int:
        """
        计算阻力位触碰次数（结构质量评估）

        Args:
            df: OHLCV数据
            resistance_level: 阻力位价格
            lookback: 回看周期

        Returns:
            触碰次数
        """
        high = df['high'].tail(lookback)
        touches = Indicators.count_resistance_touches(high, resistance_level, tolerance=0.01)

        return touches

    def detect_consolidation_breakout(self, df: pd.DataFrame, consolidation_period: int = 20) -> pd.Series:
        """
        检测箱体整理后的突破（高质量突破）

        Args:
            df: OHLCV数据
            consolidation_period: 整理周期

        Returns:
            布尔序列（True表示箱体突破）
        """
        high = df['high']
        low = df['low']
        close = df['close']

        # 检测横盘整理
        is_consolidating = Indicators.detect_consolidation(high, low, period=consolidation_period, threshold=0.05)

        # 前一日处于整理，当日突破
        was_consolidating = is_consolidating.shift(1)
        breakout = df.get('breakout_signal', pd.Series(False, index=df.index))

        consolidation_breakout = was_consolidating & breakout

        return consolidation_breakout

    def detect_second_breakout(self, df: pd.DataFrame, lookback: int = 10) -> pd.Series:
        """
        检测二次突破（第一次突破失败，缩量回踩，再次放量突破）

        Args:
            df: OHLCV数据
            lookback: 回看周期

        Returns:
            布尔序列（True表示二次突破）
        """
        volume = df['volume']
        breakout = df.get('breakout_signal', pd.Series(False, index=df.index))

        # 查找前次突破
        prev_breakout_mask = pd.Series(False, index=df.index)

        for i in range(1, lookback + 1):
            prev_breakout_mask |= breakout.shift(i)

        # 前次突破后缩量
        volume_ma = Indicators.volume_ma(volume, period=20)
        volume_contracted = volume < volume_ma * 0.6

        # 当前再次放量突破
        volume_expanded = volume > volume_ma * self.config.VOLUME_MULTIPLIER * 1.2

        second_breakout = prev_breakout_mask & volume_contracted.shift(1) & breakout & volume_expanded

        return second_breakout

    def detect_second_breakdown(self, df: pd.DataFrame, lookback: int = 10) -> pd.Series:
        """
        检测二次【向下】突破（第一次跌破失败，缩量反弹，再次放量跌破）

        与 detect_second_breakout 对称：前次有向下突破信号 + 前一根缩量 + 当前放量跌破。

        Args:
            df: OHLCV数据（需已含 short_breakout_signal 列）
            lookback: 回看周期

        Returns:
            布尔序列（True表示二次向下突破）
        """
        volume = df['volume']
        breakdown = df.get('short_breakout_signal', pd.Series(False, index=df.index))

        # 查找前次向下突破
        prev_breakdown_mask = pd.Series(False, index=df.index)
        for i in range(1, lookback + 1):
            prev_breakdown_mask |= breakdown.shift(i)

        # 前次跌破后缩量
        volume_ma = Indicators.volume_ma(volume, period=20)
        volume_contracted = volume < volume_ma * 0.6

        # 当前再次放量跌破
        volume_expanded = volume > volume_ma * self.config.SHORT_VOLUME_MULTIPLIER * 1.2

        second_breakdown = prev_breakdown_mask & volume_contracted.shift(1) & breakdown & volume_expanded

        return second_breakdown


# 测试代码
if __name__ == "__main__":
    from data_loader import DataLoader

    print("=== 测试突破检测引擎 ===")

    # 加载数据
    loader = DataLoader()
    df = loader.get_daily_data("RB0", start_date="20240101")

    if not df.empty:
        # 创建检测器
        detector = BreakoutDetector()

        # 检测突破
        df = detector.detect(df)

        # 统计结果
        total_breakouts = df['breakout_signal'].sum()
        print(f"\n总突破信号数: {total_breakouts}")

        if total_breakouts > 0:
            # 显示最近的突破信号
            breakout_dates = df[df['breakout_signal']].tail(5)
            print(f"\n最近5个突破信号:")
            print(breakout_dates[['date', 'close', 'volume', 'atr', 'resistance_level']])

            # 检测箱体突破
            consolidation_breakouts = detector.detect_consolidation_breakout(df)
            print(f"\n箱体突破信号数: {consolidation_breakouts.sum()}")

            # 检测二次突破
            second_breakouts = detector.detect_second_breakout(df)
            print(f"二次突破信号数: {second_breakouts.sum()}")
    else:
        print("数据加载失败")
