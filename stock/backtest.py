"""
N型主升浪交易系统 — 回测引擎
=================================
基于N型结构信号的事件驱动回测，模拟真实交易流程。
"""

import numpy as np
import pandas as pd
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field

from config import Config
from data_utils import add_all_indicators
from n_pattern import NPatternDetector, check_buy_signal


@dataclass
class Trade:
    """单笔交易记录"""
    entry_date: object = None
    exit_date: object = None
    entry_price: float = 0.0
    exit_price: float = 0.0
    shares: int = 0
    pnl: float = 0.0
    pnl_pct: float = 0.0
    exit_reason: str = ''          # sl / tp1 / tp2 / trail / ma / time / close
    holding_bars: int = 0
    max_profit_pct: float = 0.0    # 持仓期间最大浮盈%
    max_loss_pct: float = 0.0      # 持仓期间最大浮亏%
    score: int = 0
    grade: str = ''


@dataclass
class BacktestResult:
    """回测结果"""
    trades: List[Trade] = field(default_factory=list)
    equity_curve: pd.Series = field(default_factory=pd.Series)
    # 汇总指标
    total_return: float = 0.0
    annual_return: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    avg_holding_bars: float = 0.0
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0


class BacktestEngine:
    """
    N型主升浪策略回测引擎

    逐K线遍历，检测入场/出场信号，记录每笔交易，
    计算权益曲线和绩效指标。
    """

    def __init__(self, cfg: Config):
        self.cfg = cfg

    def run(self, df: pd.DataFrame, symbol: str = '') -> BacktestResult:
        """
        执行回测

        参数
        ----
        df : 含所有技术指标的DataFrame（需先调用 add_all_indicators）
        symbol : 股票代码（用于日志）

        返回
        ----
        BacktestResult
        """
        df = df.copy()
        n = len(df)
        if n < 100:
            return BacktestResult()

        # ================================================================
        # 初始化
        # ================================================================
        detector = NPatternDetector(self.cfg)
        initial_capital = self.cfg.backtest.initial_capital
        capital = initial_capital
        equity = np.full(n, initial_capital, dtype=float)

        trades: List[Trade] = []
        position = 0          # 当前持仓股数
        entry_price = 0.0     # 入场均价
        entry_idx = -1        # 入场位置
        stop_loss = 0.0       # 当前止损价
        tp1_price = 0.0       # 第一止盈价
        tp2_price = 0.0       # 第二止盈价
        tp1_hit = False       # TP1 是否已触发
        highest_since_entry = 0.0  # 入场后最高价（移动止损用）
        entry_score = 0
        entry_grade = ''

        commission_rate = self.cfg.backtest.commission
        slippage_rate = self.cfg.backtest.slippage

        # ================================================================
        # 逐K线回测
        # ================================================================
        for idx, (ts, row) in enumerate(df.iterrows()):
            close_price = row['close']
            high_price = row['high']
            low_price = row['low']
            atr_val = row.get('atr', np.nan)

            # --- 更新N型结构 ---
            n_result = detector.process_bar(idx, row)

            # ================================================================
            # 持仓管理
            # ================================================================
            if position > 0:
                exit_now = False
                exit_price_val = close_price
                exit_reason = ''

                # 更新持仓期间最高价
                if high_price > highest_since_entry:
                    highest_since_entry = high_price

                # 1. 止损检查
                if low_price <= stop_loss:
                    exit_price_val = stop_loss * (1 - slippage_rate)
                    exit_now = True
                    exit_reason = 'SL'

                # 2. 第一止盈（平50%）
                elif high_price >= tp1_price and not tp1_hit:
                    tp1_hit = True
                    # 平掉一半仓位
                    exit_shares = position // 2
                    tp1_exit = tp1_price * (1 - slippage_rate)
                    pnl_tp1 = (tp1_exit - entry_price) * exit_shares
                    pnl_tp1 -= commission_rate * (tp1_exit * exit_shares + entry_price * exit_shares)
                    capital += pnl_tp1
                    position -= exit_shares

                    trades.append(Trade(
                        entry_date=df.index[entry_idx],
                        exit_date=ts,
                        entry_price=entry_price,
                        exit_price=tp1_exit,
                        shares=exit_shares,
                        pnl=pnl_tp1,
                        pnl_pct=(tp1_exit - entry_price) / entry_price * 100,
                        exit_reason='TP1',
                        holding_bars=idx - entry_idx,
                        max_profit_pct=(highest_since_entry - entry_price) / entry_price * 100,
                        score=entry_score,
                        grade=entry_grade,
                    ))

                # 3. 第二止盈
                elif high_price >= tp2_price:
                    exit_price_val = tp2_price * (1 - slippage_rate)
                    exit_now = True
                    exit_reason = 'TP2'

                # 4. 移动止损（TP1触发后启用）
                elif tp1_hit and self.cfg.risk.use_trailing and not np.isnan(atr_val):
                    trail_stop = highest_since_entry - atr_val * self.cfg.risk.trail_atr_mult
                    trail_stop = max(trail_stop, stop_loss)  # 不低于原始止损
                    if low_price <= trail_stop:
                        exit_price_val = trail_stop * (1 - slippage_rate)
                        exit_now = True
                        exit_reason = 'Trail'

                # 5. MA破位
                elif self.cfg.ma.enabled:
                    ema_s = row.get('ema_s', np.nan)
                    ema_m = row.get('ema_m', np.nan)
                    if not np.isnan(ema_s) and not np.isnan(ema_m):
                        if ema_s < ema_m and close_price < ema_m:
                            exit_now = True
                            exit_reason = 'MA'

                # 6. 时间止损
                if idx - entry_idx >= self.cfg.risk.max_holding_bars:
                    exit_now = True
                    exit_reason = 'Time'

                # --- 执行平仓 ---
                if exit_now and position > 0:
                    pnl = (exit_price_val - entry_price) * position
                    pnl -= commission_rate * (exit_price_val * position + entry_price * position)
                    capital += pnl

                    trades.append(Trade(
                        entry_date=df.index[entry_idx],
                        exit_date=ts,
                        entry_price=entry_price,
                        exit_price=exit_price_val,
                        shares=position,
                        pnl=pnl,
                        pnl_pct=(exit_price_val - entry_price) / entry_price * 100,
                        exit_reason=exit_reason,
                        holding_bars=idx - entry_idx,
                        max_profit_pct=(highest_since_entry - entry_price) / entry_price * 100,
                        score=entry_score,
                        grade=entry_grade,
                    ))

                    position = 0
                    entry_price = 0.0
                    stop_loss = 0.0
                    tp1_price = 0.0
                    tp2_price = 0.0
                    tp1_hit = False
                    highest_since_entry = 0.0

            # ================================================================
            # 入场检测
            # ================================================================
            if position == 0:
                buy_signal, signal = check_buy_signal(row, n_result, self.cfg)

                if buy_signal and signal.score >= 30:
                    entry_price = close_price * (1 + slippage_rate)

                    # 止损价 = max(ATR止损, L2)
                    atr_stop = entry_price - atr_val * self.cfg.risk.stop_atr_mult if not np.isnan(atr_val) else 0
                    sl = max(atr_stop, n_result.l2_price) if not np.isnan(n_result.l2_price) else atr_stop

                    if sl <= 0 or entry_price <= sl:
                        continue  # 无效止损

                    risk_per_share = entry_price - sl
                    risk_capital = capital * self.cfg.risk.risk_percent / 100
                    shares = int(risk_capital / risk_per_share)

                    if shares < 100:  # A股最小100股
                        continue

                    # 确保不超资金
                    max_shares = int(capital / entry_price)
                    shares = min(shares, max_shares)
                    if shares < 100:
                        continue

                    # 扣除买入佣金
                    capital -= commission_rate * entry_price * shares

                    position = shares
                    entry_idx = idx
                    stop_loss = sl
                    tp1_price = n_result.tp1
                    tp2_price = n_result.tp2
                    tp1_hit = False
                    highest_since_entry = entry_price
                    entry_score = signal.score
                    entry_grade = signal.grade

            # ================================================================
            # 更新权益曲线
            # ================================================================
            if position > 0:
                equity[idx] = capital + position * close_price
            else:
                equity[idx] = capital

        # ================================================================
        # 强制平仓（最后K线仍有持仓）
        # ================================================================
        if position > 0:
            last_close = df['close'].iloc[-1]
            pnl = (last_close - entry_price) * position
            pnl -= commission_rate * (last_close * position + entry_price * position)
            capital += pnl

            trades.append(Trade(
                entry_date=df.index[entry_idx],
                exit_date=df.index[-1],
                entry_price=entry_price,
                exit_price=last_close,
                shares=position,
                pnl=pnl,
                pnl_pct=(last_close - entry_price) / entry_price * 100,
                exit_reason='Close',
                holding_bars=len(df) - 1 - entry_idx,
                max_profit_pct=(highest_since_entry - entry_price) / entry_price * 100,
                score=entry_score,
                grade=entry_grade,
            ))

        # ================================================================
        # 计算绩效指标
        # ================================================================
        equity_series = pd.Series(equity, index=df.index, name='equity')
        result = self._compute_metrics(trades, equity_series, initial_capital)
        result.trades = trades
        result.equity_curve = equity_series

        return result

    def _compute_metrics(self, trades: List[Trade], equity: pd.Series,
                         initial_capital: float) -> BacktestResult:
        """计算回测绩效指标"""
        result = BacktestResult()

        if not trades:
            return result

        winning = [t for t in trades if t.pnl > 0]
        losing = [t for t in trades if t.pnl <= 0]

        result.total_trades = len(trades)
        result.winning_trades = len(winning)
        result.losing_trades = len(losing)
        result.win_rate = len(winning) / len(trades) * 100 if trades else 0

        result.avg_win = np.mean([t.pnl for t in winning]) if winning else 0
        result.avg_loss = np.mean([t.pnl for t in losing]) if losing else 0

        total_wins = sum(t.pnl for t in winning)
        total_losses = abs(sum(t.pnl for t in losing))
        result.profit_factor = total_wins / total_losses if total_losses > 0 else float('inf')

        result.total_return = (equity.iloc[-1] / initial_capital - 1) * 100

        # 年化收益率
        days = (equity.index[-1] - equity.index[0]).days
        if days > 0:
            result.annual_return = ((equity.iloc[-1] / initial_capital) ** (365 / days) - 1) * 100

        # 最大回撤
        peak = equity.expanding().max()
        drawdown = (equity - peak) / peak * 100
        result.max_drawdown = drawdown.min()

        # 夏普比率
        returns = equity.pct_change().dropna()
        if len(returns) > 1 and returns.std() > 0:
            # 周线数据：年化因子 = sqrt(52)
            result.sharpe_ratio = (returns.mean() / returns.std()) * np.sqrt(52)

        # 平均持仓周期
        result.avg_holding_bars = np.mean([t.holding_bars for t in trades])

        return result

    def summary(self, result: BacktestResult) -> str:
        """生成回测报告文本"""
        lines = [
            '=' * 60,
            '  N型主升浪策略 — 回测报告',
            '=' * 60,
            f'  初始资金:     ¥{self.cfg.backtest.initial_capital:,.0f}',
            f'  最终权益:     ¥{result.equity_curve.iloc[-1]:,.0f}',
            f'  总收益率:     {result.total_return:+.2f}%',
            f'  年化收益:     {result.annual_return:+.2f}%',
            f'  最大回撤:     {result.max_drawdown:.2f}%',
            f'  夏普比率:     {result.sharpe_ratio:.2f}',
            f'',
            f'  交易次数:     {result.total_trades}',
            f'  胜率:         {result.win_rate:.1f}%',
            f'  盈利因子:     {result.profit_factor:.2f}',
            f'  平均盈利:     ¥{result.avg_win:,.0f}',
            f'  平均亏损:     ¥{result.avg_loss:,.0f}',
            f'  平均持仓:     {result.avg_holding_bars:.0f} 根K线',
            '=' * 60,
        ]

        if result.trades:
            lines.append('')
            lines.append('  最近5笔交易:')
            lines.append('  ' + '-' * 56)
            lines.append(f'  {"入场日期":<12} {"出场日期":<12} {"盈亏%":>8} {"原因":<8} {"评分":>4}')
            for t in result.trades[-5:]:
                entry_str = str(t.entry_date)[:10] if t.entry_date else '?'
                exit_str = str(t.exit_date)[:10] if t.exit_date else '?'
                lines.append(
                    f'  {entry_str:<12} {exit_str:<12} {t.pnl_pct:>+7.1f}% '
                    f'{t.exit_reason:<8} {t.grade:>4}'
                )

        return '\n'.join(lines)
