"""
过滤条件模块
实现【严格AND】必要条件和加分条件的评估（多空对称）
"""

import warnings
import pandas as pd
import numpy as np
from typing import Dict
from indicators import Indicators, BreakoutIndicators
import config

# 模块级标志：持仓量缺失时只告警一次，避免刷屏
_warned_no_open_interest = False


class FilterConditions:
    """过滤条件评估器（严格AND，多空对称）"""

    def __init__(self):
        self.config = config

    def evaluate_mandatory_conditions(self, df: pd.DataFrame, direction: str = 'long') -> pd.Series:
        """
        评估必要条件（严格AND逻辑，多空对称）

        必要条件（全部满足才通过）：
        1. 趋势背景：ADX > 25 AND DI同向（多：+DI>-DI；空：-DI>+DI）
        2. 波动率确认：近期曾压缩 OR 当日TR扩张（满足其一；分钟周期突破前压缩罕见，
           故降为确认性条件，不强制压缩）
        3. 结构质量：阻力/支撑位触碰 >= RESISTANCE_TOUCHES 次

        说明：
        - 已去掉均线多头/空头排列要求
        - 量能确认（放量+持仓增长）已在三维突破检测中完成
        - 持仓量：有 open_interest 列则严检（必须增长），无则降级跳过

        Args:
            df: 包含OHLCV和技术指标的DataFrame
            direction: 'long'（做多）或 'short'（做空）

        Returns:
            布尔序列（True表示满足所有必要条件）
        """
        if direction not in ('long', 'short'):
            raise ValueError(f"direction 必须是 'long' 或 'short'，收到: {direction}")

        # 条件1：趋势背景（严格：ADX>25 AND DI同向，无MA排列）
        trend_ok = self._check_trend_background(df, direction)

        # 条件2：波动率（严格AND：先压缩 AND 后扩张）
        volatility_ok = self._check_volatility_pattern(df)

        # 条件3：结构质量（严格：触碰 >= 3 次）
        structure_ok = self._check_structure_quality(df, direction)

        # 综合判断（严格AND）
        mandatory_pass = trend_ok & volatility_ok & structure_ok

        return mandatory_pass

    def calculate_bonus_score(self, df: pd.DataFrame, direction: str = 'long') -> pd.Series:
        """
        计算加分条件得分（多空对称）

        加分项：
        1. 相对强度 +20分（需基准数据，暂跳过）
        2. 多周期共振 +30分（需多周期数据，暂跳过）
        3. 二次突破 +25分（多：is_second_breakout；空：is_second_breakdown）

        Args:
            df: 包含OHLCV和技术指标的DataFrame
            direction: 'long' 或 'short'

        Returns:
            加分得分序列
        """
        bonus_score = pd.Series(0, index=df.index)

        # 加分1：相对强度（需要基准数据，这里暂时跳过）
        # bonus_score += self._score_relative_strength(df) * config.SCORE_RELATIVE_STRENGTH

        # 加分2：多周期共振（需要多周期数据，这里暂时跳过）
        # bonus_score += self._score_multi_timeframe(df) * config.SCORE_MULTI_TIMEFRAME

        # 加分3：二次突破（按方向取对应列）
        col = 'is_second_breakout' if direction == 'long' else 'is_second_breakdown'
        if col in df.columns:
            bonus_score += df[col].astype(int) * self.config.SCORE_SECOND_BREAKOUT

        return bonus_score

    def _check_trend_background(self, df: pd.DataFrame, direction: str = 'long') -> pd.Series:
        """
        检查趋势背景（严格：ADX>25 AND DI同向，无MA排列要求）

        做多：ADX > 25 AND +DI > -DI
        做空：ADX > 25 AND -DI > +DI
        """
        close = df['close']
        high = df['high']
        low = df['low']

        # 计算ADX（若已存在则复用）
        if 'adx' in df.columns and df['adx'].notna().any():
            adx = df['adx']
            plus_di = df['plus_di']
            minus_di = df['minus_di']
        else:
            adx, plus_di, minus_di = Indicators.ADX(high, low, close, self.config.ADX_PERIOD)
            df['adx'] = adx
            df['plus_di'] = plus_di
            df['minus_di'] = minus_di

        # 条件1：ADX有强度（严格 > 25）
        adx_strong = adx > self.config.ADX_THRESHOLD

        # 条件2：DI同向
        if direction == 'long':
            di_aligned = plus_di > minus_di      # 多头占优
        else:
            di_aligned = minus_di > plus_di      # 空头占优

        trend_ok = adx_strong & di_aligned

        return trend_ok

    def _check_volatility_pattern(self, df: pd.DataFrame) -> pd.Series:
        """
        检查波动率模式（满足其一即可）

        实测发现：在分钟周期（如30分钟）上，价格突破前N根高点时，前N根本身是
        一波上涨，ATR 普遍高于均线（处于扩张或正常状态），"突破前压缩"几乎不成立。
        因此波动率不作为必要AND条件，改为"满足其一即可"的确认性条件：
        1. 近期曾压缩：突破前 ATR_COMPRESSION_LOOKBACK 根内曾出现 ATR < 均线×0.7
        2. 当日扩张：突破当日真实波幅 TR > ATR × ATR_EXPANSION（1.2）

        二者其一成立即视为波动率确认（三维突破已隐含强波动，这里只做补充验证）。
        多空对称。
        """
        if 'atr' not in df.columns or df['atr'].isna().all():
            atr = Indicators.ATR(df['high'], df['low'], df['close'], self.config.ATR_PERIOD)
            df['atr'] = atr

        atr = df['atr']

        # ATR均值
        atr_ma = Indicators.SMA(atr, 20)
        df['atr_ma'] = atr_ma

        # 当日真实波幅 TR
        high = df['high']; low = df['low']; close = df['close']
        tr1 = high - low
        tr2 = (high - close.shift(1)).abs()
        tr3 = (low - close.shift(1)).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        df['tr'] = tr

        # 条件1：近期曾压缩
        compressed_bar = (atr < atr_ma * self.config.ATR_COMPRESSION).fillna(False)
        lookback = self.config.ATR_COMPRESSION_LOOKBACK
        compressed_recent = (
            compressed_bar.shift(1).rolling(window=lookback, min_periods=1).max().fillna(0) > 0
        )

        # 条件2：突破当日 TR 扩张
        atr_expanded = (tr > atr * self.config.ATR_EXPANSION).fillna(False)

        # 满足其一即可（确认性条件，非必要AND）
        volatility_ok = compressed_recent | atr_expanded

        return volatility_ok

    def _check_structure_quality(self, df: pd.DataFrame, direction: str = 'long') -> pd.Series:
        """
        检查结构质量（严格：阻力/支撑位触碰 >= RESISTANCE_TOUCHES 次）

        做多：检查阻力位（resistance_level）触碰次数
        做空：检查支撑位（support_level）触碰次数
        """
        level_col = 'resistance_level' if direction == 'long' else 'support_level'
        price_col = 'high' if direction == 'long' else 'low'  # 做多看最高价触阻力，做空看最低价触支撑

        if level_col not in df.columns:
            # 无对应位数据，无法评估结构质量 → 严格返回 False（不出信号）
            return pd.Series(False, index=df.index)

        level = df[level_col]
        lookback = 20

        # 价格接近关键位的判定（容差 3%）
        # 注意：level 是滚动序列（前N高/前N低），用向量化的滚动触碰计数
        close_to_level = (df[price_col] - level).abs() / level < 0.03

        # 计算滚动触碰次数
        touches = close_to_level.rolling(window=lookback).sum()
        touches_col = f'{level_col}_touches'
        df[touches_col] = touches

        structure_ok = touches >= self.config.RESISTANCE_TOUCHES

        return structure_ok

    def _check_open_interest(self, df: pd.DataFrame) -> pd.Series:
        """
        检查持仓量增长（量能确认的子条件，多空通用）

        - 有 open_interest 列 → 必须增长（当前 > 前一根）
        - 无该列 → 降级：跳过此条件（返回全True）+ 一次性告警

        Returns:
            布尔序列（True表示满足；无数据时全True降级）
        """
        global _warned_no_open_interest

        if 'open_interest' not in df.columns or df['open_interest'].isna().all():
            if self.config.OI_INCREASING_REQUIRED and not _warned_no_open_interest:
                warnings.warn(
                    "数据无 open_interest 列（持仓量），量能确认自动降级为仅检查放量。"
                    "此告警仅出现一次。",
                    stacklevel=2,
                )
                _warned_no_open_interest = True
            return pd.Series(True, index=df.index)

        return BreakoutIndicators.is_oi_increasing(df['open_interest'])

    def _score_relative_strength(self, df: pd.DataFrame, benchmark_df: pd.DataFrame = None) -> pd.Series:
        """
        评估相对强度

        需要基准指数数据（如行业指数）
        """
        if benchmark_df is None:
            return pd.Series(0, index=df.index)

        # 计算相对强度
        rs = Indicators.relative_strength(df['close'], benchmark_df['close'], period=20)

        # 相对强度创新高 -> 得1分
        rs_high = rs > rs.rolling(window=60).max().shift(1)

        return rs_high.astype(int)

    def _score_multi_timeframe(self, df_daily: pd.DataFrame, df_hourly: pd.DataFrame = None) -> pd.Series:
        """
        评估多周期共振

        需要多个时间周期的数据
        """
        if df_hourly is None:
            return pd.Series(0, index=df_daily.index)

        daily_breakout = df_daily.get('breakout_signal', pd.Series(False, index=df_daily.index))
        return pd.Series(0, index=df_daily.index)


class SignalFilter:
    """信号过滤器（整合检测和评分，多空双向）"""

    def __init__(self):
        self.config = config
        self.filter_conditions = FilterConditions()

    def filter_and_score(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        过滤并评分【多空双向】突破信号

        同时处理做多（breakout_signal）和做空（short_breakout_signal），
        分别产出 final_signal_long / final_signal_short 两列。

        Args:
            df: 包含突破信号的DataFrame（需已运行 detect() 和 detect_short()）

        Returns:
            添加了评分和多空最终信号的DataFrame
        """
        # ---- 做多 ----
        mandatory_long = self.filter_conditions.evaluate_mandatory_conditions(df, direction='long')
        df['mandatory_pass_long'] = mandatory_long
        bonus_long = self.filter_conditions.calculate_bonus_score(df, direction='long')
        df['bonus_score_long'] = bonus_long
        df['total_score_long'] = self.config.BASE_SCORE + bonus_long

        df['final_signal_long'] = (
            df.get('breakout_signal', False).fillna(False)
            & mandatory_long.fillna(False)
            & (df['total_score_long'] >= self.config.MIN_ENTRY_SCORE)
        )

        # ---- 做空 ----
        mandatory_short = self.filter_conditions.evaluate_mandatory_conditions(df, direction='short')
        df['mandatory_pass_short'] = mandatory_short
        bonus_short = self.filter_conditions.calculate_bonus_score(df, direction='short')
        df['bonus_score_short'] = bonus_short
        df['total_score_short'] = self.config.BASE_SCORE + bonus_short

        df['final_signal_short'] = (
            df.get('short_breakout_signal', False).fillna(False)
            & mandatory_short.fillna(False)
            & (df['total_score_short'] >= self.config.MIN_ENTRY_SCORE)
        )

        # 向后兼容：final_signal = final_signal_long（原依赖此列名的脚本/回测不受影响）
        df['final_signal'] = df['final_signal_long']

        return df

    def get_entry_signals(self, df: pd.DataFrame, direction: str = 'long') -> pd.DataFrame:
        """
        获取入场信号列表

        Args:
            df: 过滤后的DataFrame
            direction: 'long' 或 'short'

        Returns:
            只包含入场信号的DataFrame
        """
        col = 'final_signal_long' if direction == 'long' else 'final_signal_short'
        score_col = 'total_score_long' if direction == 'long' else 'total_score_short'

        if col not in df.columns:
            return df.iloc[0:0]

        signals = df[df[col]].copy()
        signals['direction'] = direction.upper()
        signals['total_score'] = signals[score_col]

        # 添加信号质量标签
        signals['quality'] = 'HIGH'
        signals.loc[signals['total_score'] >= 90, 'quality'] = 'VERY_HIGH'
        signals.loc[signals['total_score'] < 85, 'quality'] = 'MEDIUM'

        return signals


# 测试代码
if __name__ == "__main__":
    from data_loader import DataLoader
    from breakout_detector import BreakoutDetector

    print("=== 测试过滤条件模块 ===")

    # 加载数据
    loader = DataLoader()
    df = loader.get_daily_data("RB0", start_date="20240101")

    if not df.empty:
        # 检测突破（多空双向）
        detector = BreakoutDetector()
        df = detector.detect(df)                  # 做多三维突破
        df = detector.detect_short(df)            # 做空三维突破

        # 标记二次突破（多空）
        df['is_second_breakout'] = detector.detect_second_breakout(df)
        df['is_second_breakdown'] = detector.detect_second_breakdown(df)

        # 过滤和评分（多空双向）
        signal_filter = SignalFilter()
        df = signal_filter.filter_and_score(df)

        # 统计结果
        long_signals = int(df['final_signal_long'].sum())
        short_signals = int(df['final_signal_short'].sum())
        print(f"\n做多入场信号数: {long_signals}")
        print(f"做空入场信号数: {short_signals}")

        for direction, col in [('做多', 'final_signal_long'), ('做空', 'final_signal_short')]:
            n = int(df[col].sum())
            if n > 0:
                signals = signal_filter.get_entry_signals(df, direction='long' if direction == '做多' else 'short')
                print(f"\n[{direction}] 信号质量分布:")
                print(signals['quality'].value_counts())
                print(f"[{direction}] 最近的高质量信号:")
                print(signals[['date', 'close', 'total_score', 'quality']].tail())
    else:
        print("数据加载失败")
