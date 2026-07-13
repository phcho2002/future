"""
回测引擎
实现逐K线回放，模拟真实交易过程
"""

import pandas as pd
import numpy as np
from typing import Dict, List, Optional
from datetime import datetime
import os

from data_loader import DataLoader
from signal_generator import SignalGenerator
from position_manager import PositionManager
import config


class BacktestEngine:
    """回测引擎"""

    def __init__(
        self,
        initial_capital: float = None,
        commission_rate: float = None,
        slippage_ticks: int = None
    ):
        self.initial_capital = initial_capital or config.INITIAL_CAPITAL
        self.commission_rate = commission_rate or config.COMMISSION_RATE
        self.slippage_ticks = slippage_ticks or config.SLIPPAGE_TICKS

        self.data_loader = DataLoader()
        self.signal_generator = SignalGenerator()
        self.position_manager = PositionManager(self.initial_capital)

        # 回测记录
        self.equity_curve = []  # [(date, equity, positions)]
        self.trades = []  # 交易记录
        self.daily_pnl = []  # 每日盈亏

    def run(
        self,
        symbols: List[str] = None,
        start_date: str = None,
        end_date: str = None
    ) -> Dict:
        """
        运行回测

        Args:
            symbols: 品种列表（None表示使用配置中的品种池）
            start_date: 开始日期
            end_date: 结束日期

        Returns:
            回测结果字典
        """
        if symbols is None:
            symbols = config.FUTURES_UNIVERSE[:5]  # 测试时只用前5个品种

        if start_date is None:
            start_date = config.START_DATE

        print(f"=== 开始回测 ===")
        print(f"品种数: {len(symbols)}")
        print(f"时间范围: {start_date} - {end_date or '至今'}")
        print(f"初始资金: {self.initial_capital:,.0f}")

        # 加载所有品种数据
        all_data = self._load_all_data(symbols, start_date, end_date)

        if not all_data:
            print("数据加载失败，回测终止")
            return {}

        # 生成所有品种的信号
        all_signals = self._generate_all_signals(all_data)

        # 获取所有日期（取所有品种的并集）
        all_dates = self._get_all_dates(all_data)

        print(f"回测日期数: {len(all_dates)}")
        print(f"开始逐日回测...")

        # 逐日回放
        for i, date in enumerate(all_dates):
            if i % 100 == 0:
                print(f"进度: {i}/{len(all_dates)} ({i/len(all_dates)*100:.1f}%)")

            self._process_day(date, all_data, all_signals)

        # 计算结果
        results = self._calculate_results()

        print(f"\n=== 回测完成 ===")
        self._print_results(results)

        return results

    def _load_all_data(
        self,
        symbols: List[str],
        start_date: str,
        end_date: Optional[str]
    ) -> Dict[str, pd.DataFrame]:
        """加载所有品种数据"""
        all_data = {}

        for symbol in symbols:
            print(f"加载 {symbol} 数据...")
            df = self.data_loader.get_daily_data(symbol, start_date=start_date)

            if not df.empty:
                if end_date:
                    df = df[df['date'] <= end_date]
                all_data[symbol] = df
            else:
                print(f"  {symbol} 数据加载失败，跳过")

        return all_data

    def _generate_all_signals(self, all_data: Dict[str, pd.DataFrame]) -> Dict[str, pd.DataFrame]:
        """生成所有品种的信号"""
        all_signals = {}

        for symbol, df in all_data.items():
            print(f"生成 {symbol} 信号...")
            try:
                # reset_index 确保 signal_row.name（index标签）与 iloc 位置一致，
                # 避免 calculate_entry_price/calculate_stop_loss 按位置访问越界
                work = df.copy().reset_index(drop=True)
                df_with_signals = self.signal_generator.generate_signals(work)
                all_signals[symbol] = df_with_signals
                # 同步对齐 all_data，保证 _process_day 按 date 取行时 index 一致
                all_data[symbol] = work
            except Exception as e:
                print(f"  {symbol} 信号生成失败: {e}")
                all_signals[symbol] = df

        return all_signals

    def _get_all_dates(self, all_data: Dict[str, pd.DataFrame]) -> List[str]:
        """获取所有日期（按时间排序）"""
        all_dates_set = set()

        for df in all_data.values():
            all_dates_set.update(df['date'].values)

        return sorted(list(all_dates_set))

    def _process_day(
        self,
        date: str,
        all_data: Dict[str, pd.DataFrame],
        all_signals: Dict[str, pd.DataFrame]
    ):
        """
        处理单日交易

        流程：
        1. 更新持仓（检查止损、加仓、止盈）
        2. 检查新信号
        3. 开仓
        4. 记录权益
        """
        # 获取当日所有品种的价格
        current_prices = {}
        for symbol, df in all_data.items():
            day_data = df[df['date'] == date]
            if not day_data.empty:
                row = day_data.iloc[0]
                current_prices[symbol] = {
                    'open': row['open'],
                    'high': row['high'],
                    'low': row['low'],
                    'close': row['close'],
                    'atr': row.get('atr', 0)
                }

        # 1. 更新持仓
        self.position_manager.update_positions(date, current_prices)

        # 2. 检查新信号（多空双向）
        for symbol, signal_df in all_signals.items():
            day_signals = signal_df[signal_df['date'] == date]
            if day_signals.empty:
                continue
            row = day_signals.iloc[0]

            # 做多信号
            if row.get('final_signal_long', False):
                self._try_open_position(symbol, date, row, signal_df, direction=1)
            # 做空信号
            if row.get('final_signal_short', False):
                self._try_open_position(symbol, date, row, signal_df, direction=-1)

        # 3. 记录权益曲线
        equity = self._calculate_current_equity(current_prices)
        self.equity_curve.append((date, equity, len(self.position_manager.positions)))

    def _try_open_position(
        self,
        symbol: str,
        date: str,
        signal_row: pd.Series,
        df: pd.DataFrame,
        direction: int = 1
    ):
        """尝试开仓（支持多空双向）"""
        # 检查是否可以开仓（按方向）
        if not self.position_manager.can_open_position(symbol, direction):
            return

        # 获取入场参数
        signal_idx = signal_row.name
        dir_str = 'long' if direction == 1 else 'short'
        entry_price = self.signal_generator.calculate_entry_price(df, signal_idx, dir_str)
        stop_loss = self.signal_generator.calculate_stop_loss(df, signal_idx, entry_price, dir_str)
        atr = signal_row.get('atr', 0)

        # 计算滑点（做多加；做空减）
        if direction == 1:
            entry_price += self.slippage_ticks
        else:
            entry_price -= self.slippage_ticks

        # 开仓
        position = self.position_manager.open_position(
            symbol=symbol,
            date=date,
            entry_price=entry_price,
            stop_loss=stop_loss,
            atr=atr,
            direction=direction
        )

        if position:
            # 记录交易
            score_col = 'total_score_long' if direction == 1 else 'total_score_short'
            self.trades.append({
                'symbol': symbol,
                'direction': 'LONG' if direction == 1 else 'SHORT',
                'entry_date': date,
                'entry_price': entry_price,
                'stop_loss': stop_loss,
                'size': position.initial_size,
                'score': signal_row.get(score_col, 0)
            })

    def _calculate_current_equity(self, current_prices: Dict[str, Dict]) -> float:
        """计算当前权益"""
        equity = self.position_manager.current_capital

        # 加上持仓浮盈
        for symbol, position in self.position_manager.positions.items():
            if symbol in current_prices:
                current_price = current_prices[symbol]['close']
                unrealized_pnl = (current_price - position.get_average_price()) * position.get_total_size()
                equity += unrealized_pnl

        return equity

    def _calculate_results(self) -> Dict:
        """计算回测结果"""
        # 基础统计
        stats = self.position_manager.get_statistics()

        # 权益曲线分析
        if len(self.equity_curve) > 0:
            equity_df = pd.DataFrame(self.equity_curve, columns=['date', 'equity', 'positions'])

            # 最大回撤
            equity_series = equity_df['equity']
            rolling_max = equity_series.expanding().max()
            drawdown = (equity_series - rolling_max) / rolling_max
            max_drawdown = drawdown.min()

            # 收益率
            final_equity = equity_series.iloc[-1]
            total_return = (final_equity - self.initial_capital) / self.initial_capital

            # 年化收益率（假设回测时间为1年，实际应根据真实天数计算）
            trading_days = len(equity_df)
            years = trading_days / 252  # 假设一年252个交易日
            annual_return = (1 + total_return) ** (1 / years) - 1 if years > 0 else 0

            # 夏普比率（简化计算）
            daily_returns = equity_series.pct_change().dropna()
            if len(daily_returns) > 0 and daily_returns.std() > 0:
                sharpe_ratio = daily_returns.mean() / daily_returns.std() * np.sqrt(252)
            else:
                sharpe_ratio = 0

            stats.update({
                'max_drawdown': max_drawdown,
                'total_return': total_return,
                'annual_return': annual_return,
                'sharpe_ratio': sharpe_ratio,
                'trading_days': trading_days,
                'equity_curve': equity_df,
            })

        return stats

    def _print_results(self, results: Dict):
        """打印回测结果"""
        print(f"\n{'='*50}")
        print(f"{'回测结果':^50}")
        print(f"{'='*50}")

        print(f"\n【收益指标】")
        print(f"  总收益率: {results.get('total_return', 0)*100:.2f}%")
        print(f"  年化收益率: {results.get('annual_return', 0)*100:.2f}%")
        print(f"  最大回撤: {results.get('max_drawdown', 0)*100:.2f}%")
        print(f"  夏普比率: {results.get('sharpe_ratio', 0):.2f}")

        print(f"\n【交易指标】")
        print(f"  总交易次数: {results.get('total_trades', 0)}")
        print(f"  盈利次数: {results.get('winning_trades', 0)}")
        print(f"  亏损次数: {results.get('losing_trades', 0)}")
        print(f"  胜率: {results.get('win_rate', 0)*100:.2f}%")
        print(f"  平均盈亏: {results.get('avg_profit', 0):.2f}")

        print(f"\n【资金情况】")
        print(f"  初始资金: {self.initial_capital:,.0f}")
        print(f"  最终资金: {results.get('final_capital', 0):,.0f}")
        print(f"  总盈亏: {results.get('total_pnl', 0):,.0f}")

    def save_results(self, results: Dict, output_dir: str = "./output"):
        """保存回测结果"""
        os.makedirs(output_dir, exist_ok=True)

        # 保存权益曲线
        if 'equity_curve' in results:
            equity_df = results['equity_curve']
            equity_path = os.path.join(output_dir, 'equity_curve.csv')
            equity_df.to_csv(equity_path, index=False)
            print(f"权益曲线已保存至: {equity_path}")

        # 保存交易记录
        if self.trades:
            trades_df = pd.DataFrame(self.trades)
            trades_path = os.path.join(output_dir, 'trades.csv')
            trades_df.to_csv(trades_path, index=False)
            print(f"交易记录已保存至: {trades_path}")

        # 保存汇总统计
        summary = {k: v for k, v in results.items() if not isinstance(v, pd.DataFrame)}
        summary_df = pd.DataFrame([summary])
        summary_path = os.path.join(output_dir, 'summary.csv')
        summary_df.to_csv(summary_path, index=False)
        print(f"汇总统计已保存至: {summary_path}")


# 测试代码
if __name__ == "__main__":
    print("=== 回测引擎测试 ===")

    # 创建回测引擎
    engine = BacktestEngine(initial_capital=100000)

    # 运行回测（只测试2个品种，加快速度）
    results = engine.run(
        symbols=["RB0", "HC0"],
        start_date="20240101"
    )

    # 保存结果
    if results:
        engine.save_results(results)
