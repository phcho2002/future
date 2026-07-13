"""
策略主逻辑
整合所有模块，提供统一接口
"""

import pandas as pd
from typing import Dict, List, Optional
from datetime import datetime

from data_loader import DataLoader
from signal_generator import SignalGenerator
from position_manager import PositionManager
from backtest_engine import BacktestEngine
import config


class BreakoutStrategy:
    """突破交易策略"""

    def __init__(self):
        self.config = config
        self.data_loader = DataLoader()
        self.signal_generator = SignalGenerator()

    def analyze_symbol(self, symbol: str, start_date: str = None) -> pd.DataFrame:
        """
        分析单个品种

        Args:
            symbol: 品种代码
            start_date: 开始日期

        Returns:
            包含信号的DataFrame
        """
        print(f"\n{'='*50}")
        print(f"分析品种: {symbol}")
        print(f"{'='*50}")

        # 加载数据
        df = self.data_loader.get_daily_data(symbol, start_date=start_date)

        if df.empty:
            print(f"数据加载失败")
            return pd.DataFrame()

        # 生成信号
        df = self.signal_generator.generate_signals(df)

        # 获取统计
        summary = self.signal_generator.get_signal_summary(df)

        print(f"\n信号统计:")
        print(f"  总突破数: {summary['total_breakouts']}")
        print(f"  通过必要条件: {summary['passed_mandatory']}")
        print(f"  最终信号数: {summary['final_signals']}")
        print(f"  箱体突破: {summary['consolidation_breakouts']}")
        print(f"  二次突破: {summary['second_breakouts']}")
        print(f"  平均评分: {summary['avg_score']:.1f}")

        return df

    def scan_universe(self, symbols: List[str] = None, start_date: str = None) -> pd.DataFrame:
        """
        扫描品种池，找出当前有信号的品种

        Args:
            symbols: 品种列表（None表示使用配置中的品种池）
            start_date: 开始日期

        Returns:
            包含所有信号的汇总DataFrame
        """
        if symbols is None:
            symbols = self.config.FUTURES_UNIVERSE

        print(f"\n{'='*50}")
        print(f"扫描品种池: {len(symbols)} 个品种")
        print(f"{'='*50}")

        all_signals = []

        for i, symbol in enumerate(symbols):
            print(f"\n[{i+1}/{len(symbols)}] {symbol}", end=" ... ")

            try:
                df = self.data_loader.get_daily_data(symbol, start_date=start_date)

                if df.empty:
                    print("无数据")
                    continue

                # 生成信号
                df = self.signal_generator.generate_signals(df)

                # 获取最近的信号
                recent_signals = df[df['final_signal']].tail(5)

                if not recent_signals.empty:
                    recent_signals = recent_signals.copy()
                    recent_signals['symbol'] = symbol
                    all_signals.append(recent_signals)
                    print(f"发现 {len(recent_signals)} 个信号")
                else:
                    print("无信号")

            except Exception as e:
                print(f"错误: {e}")

        # 合并所有信号
        if all_signals:
            result_df = pd.concat(all_signals, ignore_index=True)
            result_df = result_df.sort_values(['date', 'total_score'], ascending=[False, False])

            print(f"\n{'='*50}")
            print(f"扫描完成，共发现 {len(result_df)} 个信号")
            print(f"{'='*50}")

            return result_df
        else:
            print("\n未发现任何信号")
            return pd.DataFrame()

    def backtest(
        self,
        symbols: List[str] = None,
        start_date: str = None,
        end_date: str = None,
        initial_capital: float = None
    ) -> Dict:
        """
        回测策略

        Args:
            symbols: 品种列表
            start_date: 开始日期
            end_date: 结束日期
            initial_capital: 初始资金

        Returns:
            回测结果字典
        """
        engine = BacktestEngine(initial_capital=initial_capital)
        results = engine.run(symbols=symbols, start_date=start_date, end_date=end_date)

        # 保存结果
        if results:
            engine.save_results(results)

        return results

    def get_latest_signals(self, symbols: List[str] = None, top_n: int = 10) -> pd.DataFrame:
        """
        获取最新的交易信号（用于实盘）

        Args:
            symbols: 品种列表
            top_n: 返回前N个最高分信号

        Returns:
            最新信号DataFrame
        """
        # 扫描最近30天的数据
        from datetime import datetime, timedelta
        start_date = (datetime.now() - timedelta(days=30)).strftime("%Y%m%d")

        signals_df = self.scan_universe(symbols=symbols, start_date=start_date)

        if signals_df.empty:
            return pd.DataFrame()

        # 只保留最新日期的信号
        latest_date = signals_df['date'].max()
        latest_signals = signals_df[signals_df['date'] == latest_date]

        # 按评分排序，取前N个
        latest_signals = latest_signals.sort_values('total_score', ascending=False).head(top_n)

        return latest_signals


# 测试代码
if __name__ == "__main__":
    print("=== 突破交易策略测试 ===")

    strategy = BreakoutStrategy()

    # 测试1：分析单个品种
    print("\n【测试1】分析单个品种")
    df = strategy.analyze_symbol("RB0", start_date="20240101")
    if not df.empty and df['final_signal'].any():
        signals = df[df['final_signal']]
        print(f"\n最近5个信号:")
        print(signals[['date', 'close', 'total_score']].tail())

    # 测试2：扫描品种池（只扫描前5个，加快速度）
    print("\n【测试2】扫描品种池")
    signals = strategy.scan_universe(symbols=["RB0", "HC0", "I0"], start_date="20240101")
    if not signals.empty:
        print(f"\n发现的信号:")
        print(signals[['symbol', 'date', 'close', 'total_score']].head(10))

    # 测试3：回测（可选，耗时较长）
    print("\n【测试3】回测策略（跳过，耗时较长）")
    # results = strategy.backtest(symbols=["RB0"], start_date="20240101")
