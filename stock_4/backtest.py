"""A 股日线回测引擎。

适配 A 股规则:
  - 仅做多，T+1（买入次日才能卖出）
  - 手 = 100 股，按整手向下取整
  - 佣金万分之 2.5（最低 5 元，双边）+ 卖出印花税 0.05%
  - 信号日收盘确认 → 次日开盘买入
  - 涨停过滤：次日开盘涨停则放弃入场
  - 初始止损 = 突破位 - 1.5×ATR；移动止损 = 跟踪最低价 - 2.0×ATR
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from engine import SecondBreakoutEngine, StrategyParams
import indicators as ind
from tdx_reader import TDXLocalProvider


@dataclass
class BacktestParams:
    """回测参数（从 config.yaml 的 backtest 段加载）。"""

    initial_capital: float = 100000
    risk_per_trade: float = 0.02
    commission_rate: float = 0.00025
    min_commission: float = 5.0
    stamp_duty: float = 0.0005
    slippage: float = 0.0
    initial_stop_atr: float = 1.5
    trail_atr_mult: float = 2.0
    max_hold_days: int = 60
    start_date: str = "20210101"
    limit_pct: float = 0.10

    @classmethod
    def from_dict(cls, d: dict) -> "BacktestParams":
        known = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class Trade:
    """单笔交易记录。"""

    symbol: str
    entry_date: object
    entry_price: float
    exit_date: object
    exit_price: float
    shares: int
    pnl: float          # 净盈亏（扣费后）
    pnl_pct: float      # 净盈亏百分比（相对本金）
    hold_days: int
    exit_reason: str    # initial_stop / trail_stop / max_hold / last_bar


def _commission(amount: float, params: BacktestParams) -> float:
    """单边佣金 = max(amount × rate, min_commission)。"""
    return max(amount * params.commission_rate, params.min_commission)


class Backtester:
    """单股回测器。"""

    def __init__(self, strategy: StrategyParams, bt: BacktestParams):
        self.s = strategy
        self.b = bt

    def backtest(self, df: pd.DataFrame, symbol: str = "") -> list[Trade]:
        """对单只股票回测，返回交易列表。

        Args:
            df: 日线 DataFrame[date,open,high,low,close,volume,amount]，升序
            symbol: 股票代码（记录用）
        """
        engine = SecondBreakoutEngine(df, self.s)
        signals = engine.run()
        if not signals:
            return []

        df = engine.df.reset_index(drop=True)
        n = len(df)
        trades: list[Trade] = []

        # 按信号序号索引，便于查找
        for sig in signals:
            entry_idx = sig.idx + 1   # 次日开盘买入
            if entry_idx >= n:
                continue              # 信号在最后一根，无法入场

            entry_row = df.iloc[entry_idx]
            entry_price = float(entry_row["open"])

            # 涨停过滤：开盘涨停放弃入场（按板块区分 10% / 20%）
            prev_close = float(df.iloc[entry_idx - 1]["close"])
            from tdx_reader import limit_pct_of
            lim_pct = limit_pct_of(symbol, self.b.limit_pct)
            if ind.is_limit_up(entry_price, prev_close, lim_pct):
                continue

            # 初始止损
            stop_price = sig.resistance - self.b.initial_stop_atr * sig.atr
            if stop_price >= entry_price:
                continue   # 止损在入场价上方，不合理，跳过

            # 仓位：按固定风险比例计算手数
            risk_per_share = entry_price - stop_price
            if risk_per_share <= 0:
                continue
            capital = self.b.initial_capital
            risk_budget = capital * self.b.risk_per_trade
            shares = int(risk_budget / risk_per_share / 100) * 100   # 整手
            if shares <= 0:
                continue

            # 逐 K 线跟踪持仓，检查出场
            trail_stop = stop_price
            exit_idx = None
            exit_price = None
            exit_reason = ""

            for j in range(entry_idx, n):
                row = df.iloc[j]
                day_low = float(row["low"])
                day_high = float(row["high"])
                day_close = float(row["close"])
                day_atr = float(row["atr"]) if np.isfinite(row["atr"]) else sig.atr
                hold = j - entry_idx

                # T+1：买入当天不能卖
                if j == entry_idx:
                    # 更新移动止损（用当日 low）
                    new_trail = day_low - self.b.trail_atr_mult * day_atr
                    trail_stop = max(trail_stop, new_trail)
                    continue

                # 1. 初始止损 / 移动止损（取较高者 = 更紧的保护）
                effective_stop = max(stop_price, trail_stop)
                if day_low <= effective_stop:
                    exit_idx = j
                    exit_price = effective_stop
                    exit_reason = "trail_stop" if trail_stop >= stop_price else "initial_stop"
                    break

                # 2. 最大持仓天数
                if self.b.max_hold_days > 0 and hold >= self.b.max_hold_days:
                    exit_idx = j
                    exit_price = day_close
                    exit_reason = "max_hold"
                    break

                # 更新移动止损（跟踪最低价 - N×ATR，只上移）
                new_trail = day_low - self.b.trail_atr_mult * day_atr
                trail_stop = max(trail_stop, new_trail)

            # 未触发止损 → 用最后一根收盘平仓
            if exit_idx is None:
                exit_idx = n - 1
                exit_price = float(df.iloc[exit_idx]["close"])
                exit_reason = "last_bar"

            # 计算盈亏（扣除佣金 + 印花税）
            buy_amount = entry_price * shares
            sell_amount = exit_price * shares
            cost = _commission(buy_amount, self.b) + _commission(sell_amount, self.b)
            cost += sell_amount * self.b.stamp_duty          # 卖出印花税
            cost += self.b.slippage * shares * 2             # 滑点
            pnl = sell_amount - buy_amount - cost
            invested = buy_amount + _commission(buy_amount, self.b)
            pnl_pct = pnl / invested * 100 if invested > 0 else 0.0
            hold_days = exit_idx - entry_idx

            trades.append(Trade(
                symbol=symbol,
                entry_date=df.iloc[entry_idx]["date"],
                entry_price=round(entry_price, 2),
                exit_date=df.iloc[exit_idx]["date"],
                exit_price=round(exit_price, 2),
                shares=shares,
                pnl=round(pnl, 2),
                pnl_pct=round(pnl_pct, 2),
                hold_days=hold_days,
                exit_reason=exit_reason,
            ))

        return trades


def summarize(trades: list[Trade]) -> dict:
    """汇总交易统计。"""
    if not trades:
        return {
            "count": 0, "win_rate": 0, "avg_pnl": 0, "total_pnl": 0,
            "avg_hold": 0, "profit_factor": 0, "max_win": 0, "max_loss": 0,
        }
    pnls = np.array([t.pnl for t in trades])
    wins = pnls[pnls > 0]
    losses = pnls[pnls < 0]
    gross_profit = wins.sum() if len(wins) else 0.0
    gross_loss = abs(losses.sum()) if len(losses) else 0.0
    return {
        "count": len(trades),
        "win_rate": round(len(wins) / len(trades) * 100, 1),
        "avg_pnl": round(pnls.mean(), 2),
        "total_pnl": round(pnls.sum(), 2),
        "avg_hold": round(np.mean([t.hold_days for t in trades]), 1),
        "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss > 0 else float("inf"),
        "max_win": round(pnls.max(), 2),
        "max_loss": round(pnls.min(), 2),
        "avg_pnl_pct": round(np.mean([t.pnl_pct for t in trades]), 2),
    }


class BacktestRunner:
    """回测运行器：加载配置，支持单股/全市场回测。"""

    def __init__(self, config_path: str | Path = "config.yaml"):
        with open(config_path, encoding="utf-8") as f:
            self.cfg = yaml.safe_load(f)
        self.provider = TDXLocalProvider(self.cfg["data"]["tdx_vipdoc"])
        self.strategy = StrategyParams.from_dict(self.cfg["strategy"])
        self.bt = BacktestParams.from_dict(self.cfg["backtest"])
        self.backtester = Backtester(self.strategy, self.bt)
        self.output_dir = Path("output")
        self.output_dir.mkdir(exist_ok=True)

    def backtest_symbol(self, symbol: str, verbose: bool = False) -> tuple[list[Trade], dict]:
        """单股回测。"""
        df = self.provider.history(
            symbol, lookback=0, start_date=self.bt.start_date
        )
        if len(df) < self.strategy.pattern_lookback + self.strategy.ema_period:
            return [], {}
        trades = self.backtester.backtest(df, symbol)
        stats = summarize(trades)
        if verbose and trades:
            print(f"\n{'='*60}")
            print(f"{symbol}  |  {stats['count']} 笔 | 胜率 {stats['win_rate']}% "
                  f"| 总盈亏 {stats['total_pnl']} | 盈亏比 {stats['profit_factor']}")
            print(f"{'='*60}")
            for t in trades:
                print(f"  {str(t.entry_date)[:10]}→{str(t.exit_date)[:10]} "
                      f"买{t.entry_price} 卖{t.exit_price} {t.shares}股 "
                      f"盈亏{t.pnl:+.0f}({t.pnl_pct:+.1f}%) "
                      f"持{t.hold_days}天 [{t.exit_reason}]")
        return trades, stats

    def backtest_all(self, verbose: bool = True) -> pd.DataFrame:
        """全市场回测，返回汇总 DataFrame。"""
        symbols = self.provider.all_main_board_symbols()
        if verbose:
            print(f"回测 {len(symbols)} 只股票...")
        rows = []
        all_trades: list[Trade] = []
        for k, sym in enumerate(symbols):
            if verbose and (k + 1) % 200 == 0:
                print(f"  进度 {k + 1}/{len(symbols)}...")
            trades, stats = self.backtest_symbol(sym)
            if stats:
                all_trades.extend(trades)
                rows.append({"symbol": sym, **stats})

        summary_df = pd.DataFrame(rows)
        if not summary_df.empty:
            summary_df = summary_df.sort_values("total_pnl", ascending=False).reset_index(drop=True)

        # 保存
        today = pd.Timestamp.now().strftime("%Y%m%d")
        summary_df.to_csv(self.output_dir / f"backtest_summary_{today}.csv",
                          index=False, encoding="utf-8-sig")
        if all_trades:
            trades_df = pd.DataFrame([
                {
                    "symbol": t.symbol, "entry_date": str(t.entry_date)[:10],
                    "entry_price": t.entry_price, "exit_date": str(t.exit_date)[:10],
                    "exit_price": t.exit_price, "shares": t.shares,
                    "pnl": t.pnl, "pnl_pct": t.pnl_pct,
                    "hold_days": t.hold_days, "exit_reason": t.exit_reason,
                }
                for t in all_trades
            ])
            trades_df.to_csv(self.output_dir / f"backtest_trades_{today}.csv",
                             index=False, encoding="utf-8-sig")

        # 全市场汇总
        if all_trades:
            overall = summarize(all_trades)
            if verbose:
                print(f"\n{'='*60}")
                print(f"全市场汇总: {overall['count']} 笔交易")
                print(f"  胜率 {overall['win_rate']}% | 总盈亏 {overall['total_pnl']} "
                      f"| 盈亏比 {overall['profit_factor']}")
                print(f"  平均持仓 {overall['avg_hold']} 天 | 单笔均盈亏 {overall['avg_pnl']}")
                print(f"{'='*60}")
        return summary_df
