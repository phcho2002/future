"""
信号生成器
整合突破检测、过滤条件，生成最终交易信号
"""

import pandas as pd
import numpy as np
from typing import Dict, List, Tuple
from breakout_detector import BreakoutDetector
from filter_conditions import SignalFilter
import config


class SignalGenerator:
    """交易信号生成器"""

    def __init__(self):
        self.detector = BreakoutDetector()
        self.filter = SignalFilter()
        self.config = config

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        生成完整的【多空双向】交易信号

        Args:
            df: OHLCV数据

        Returns:
            包含多空入场、出场信号的DataFrame
        """
        # 1. 检测突破（做多 + 做空）
        df = self.detector.detect(df)            # 做多三维突破
        df = self.detector.detect_short(df)      # 做空三维突破

        # 2. 检测特殊突破类型（多空）
        df['is_consolidation_breakout'] = self.detector.detect_consolidation_breakout(df)
        df['is_second_breakout'] = self.detector.detect_second_breakout(df)
        df['is_second_breakdown'] = self.detector.detect_second_breakdown(df)

        # 3. 过滤和评分（多空双向，产出 final_signal_long / final_signal_short）
        df = self.filter.filter_and_score(df)

        # 4. 生成出场信号（多空）
        df = self._generate_exit_signals(df)

        return df

    def _generate_exit_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        生成【多空双向】出场信号

        做多出场：
        1. ADX转弱(<20)
        2. +DI 下穿 -DI（多头动能衰退）
        3. 收盘破前5根最低

        做空出场：
        1. ADX转弱(<20)
        2. -DI 下穿 +DI（空头动能衰退，即 +DI 上穿 -DI）
        3. 收盘破前5根最高
        """
        close = df['close']
        low = df['low']
        high = df['high']

        # 初始化出场信号列
        df['long_exit_signal'] = False
        df['short_exit_signal'] = False
        # 向后兼容
        df['stop_loss_signal'] = False
        df['trend_reversal_signal'] = False
        df['exit_signal'] = False

        if 'adx' in df.columns:
            # ADX转弱（多空通用）
            adx_weak = df['adx'] < 20

            # ---- 做多趋势反转 ----
            # +DI 下穿 -DI
            di_cross_down = (df['plus_di'] < df['minus_di']) & (df['plus_di'].shift(1) >= df['minus_di'].shift(1))
            # 破前5根最低
            prev_low = low.rolling(window=5).min().shift(1)
            break_prev_low = close < prev_low

            df['long_exit_signal'] = adx_weak | di_cross_down | break_prev_low

            # ---- 做空趋势反转 ----
            # -DI 下穿 +DI（即 +DI 上穿 -DI，空头动能衰退）
            di_cross_up = (df['plus_di'] > df['minus_di']) & (df['plus_di'].shift(1) <= df['minus_di'].shift(1))
            # 破前5根最高
            prev_high = high.rolling(window=5).max().shift(1)
            break_prev_high = close > prev_high

            df['short_exit_signal'] = adx_weak | di_cross_up | break_prev_high

            # 向后兼容：exit_signal 保留为做多出场（原语义）
            df['trend_reversal_signal'] = df['long_exit_signal']
            df['exit_signal'] = df['long_exit_signal']

        return df

    def calculate_entry_price(self, df: pd.DataFrame, signal_index: int, direction: str = 'long') -> float:
        """
        计算入场价格

        Args:
            df: 数据DataFrame
            signal_index: 信号索引
            direction: 'long' 或 'short'

        Returns:
            入场价格（下一根K线开盘价）
        """
        if signal_index + 1 < len(df):
            return df.iloc[signal_index + 1]['open']
        else:
            return df.iloc[signal_index]['close']

    def calculate_stop_loss(self, df: pd.DataFrame, entry_index: int, entry_price: float, direction: str = 'long') -> float:
        """
        计算止损价格

        做多：止损 = 突破点（阻力位）下方 1×ATR
        做空：止损 = 跌破点（支撑位）上方 1×ATR

        Args:
            df: 数据DataFrame
            entry_index: 入场索引
            entry_price: 入场价格
            direction: 'long' 或 'short'

        Returns:
            止损价格
        """
        atr = df.iloc[entry_index]['atr']

        if direction == 'long':
            resistance = df.iloc[entry_index]['resistance_level']
            # 止损位 = 突破点下方1ATR
            stop_loss = resistance - atr * self.config.INITIAL_STOP_ATR
        else:
            support = df.iloc[entry_index]['support_level']
            # 止损位 = 跌破点上方1ATR
            stop_loss = support + atr * self.config.INITIAL_STOP_ATR

        return stop_loss

    def calculate_position_size(
        self,
        capital: float,
        entry_price: float,
        stop_loss: float,
        risk_per_trade: float = None
    ) -> int:
        """
        计算仓位大小

        Args:
            capital: 可用资金
            entry_price: 入场价格
            stop_loss: 止损价格
            risk_per_trade: 单笔风险比例（None则使用配置）

        Returns:
            持仓手数
        """
        if risk_per_trade is None:
            risk_per_trade = self.config.MAX_SINGLE_LOSS

        # 单笔最大风险金额
        max_risk_amount = capital * risk_per_trade

        # 每手风险
        risk_per_contract = abs(entry_price - stop_loss)

        # 持仓手数（初始仓位30%）
        position_size = int(max_risk_amount / risk_per_contract * self.config.INITIAL_POSITION)

        return max(1, position_size)  # 至少1手

    def get_signal_summary(self, df: pd.DataFrame) -> Dict:
        """
        获取信号统计摘要（多空双向）

        Args:
            df: 包含信号的DataFrame

        Returns:
            统计字典
        """
        def _safe_sum(col):
            return int(df[col].sum()) if col in df.columns else 0

        def _safe_avg(sig_col, score_col):
            if sig_col in df.columns and df[sig_col].any() and score_col in df.columns:
                return float(df[df[sig_col]][score_col].mean())
            return 0.0

        summary = {
            # 做多
            'long_breakouts': _safe_sum('breakout_signal'),
            'long_mandatory_pass': _safe_sum('mandatory_pass_long'),
            'long_final_signals': _safe_sum('final_signal_long'),
            'long_second_breakouts': _safe_sum('is_second_breakout'),
            'long_avg_score': _safe_avg('final_signal_long', 'total_score_long'),
            # 做空
            'short_breakouts': _safe_sum('short_breakout_signal'),
            'short_mandatory_pass': _safe_sum('mandatory_pass_short'),
            'short_final_signals': _safe_sum('final_signal_short'),
            'short_second_breakdowns': _safe_sum('is_second_breakdown'),
            'short_avg_score': _safe_avg('final_signal_short', 'total_score_short'),
        }
        # 向后兼容字段
        summary['total_breakouts'] = summary['long_breakouts']
        summary['final_signals'] = summary['long_final_signals'] + summary['short_final_signals']
        return summary


# 测试代码
if __name__ == "__main__":
    from data_loader import DataLoader

    print("=== 测试信号生成器 ===")

    # 加载数据
    loader = DataLoader()
    df = loader.get_daily_data("RB0", start_date="20240101")

    if not df.empty:
        # reset_index 确保 iloc 按位置访问与 index 标签一致
        df = df.reset_index(drop=True)

        # 生成信号
        generator = SignalGenerator()
        df = generator.generate_signals(df)

        # 获取摘要
        summary = generator.get_signal_summary(df)
        print(f"\n信号统计摘要:")
        for key, value in summary.items():
            print(f"  {key}: {value}")

        # 显示做多信号
        if summary['long_final_signals'] > 0 and 'final_signal_long' in df.columns:
            long_sigs = df[df['final_signal_long']].copy()
            print(f"\n[做多] 最近交易信号:")
            print(long_sigs[['date', 'close', 'total_score_long', 'atr']].tail())

            last_idx = long_sigs.index[-1]
            entry_price = generator.calculate_entry_price(df, last_idx, 'long')
            stop_loss = generator.calculate_stop_loss(df, last_idx, entry_price, 'long')
            print(f"[做多] 最后信号参数: 入场={entry_price:.2f} 止损={stop_loss:.2f} "
                  f"风险={abs(entry_price - stop_loss):.2f}点")

        # 显示做空信号
        if summary['short_final_signals'] > 0 and 'final_signal_short' in df.columns:
            short_sigs = df[df['final_signal_short']].copy()
            print(f"\n[做空] 最近交易信号:")
            print(short_sigs[['date', 'close', 'total_score_short', 'atr']].tail())

            last_idx = short_sigs.index[-1]
            entry_price = generator.calculate_entry_price(df, last_idx, 'short')
            stop_loss = generator.calculate_stop_loss(df, last_idx, entry_price, 'short')
            print(f"[做空] 最后信号参数: 入场={entry_price:.2f} 止损={stop_loss:.2f} "
                  f"(止损在上方) 风险={abs(stop_loss - entry_price):.2f}点")
    else:
        print("数据加载失败")
