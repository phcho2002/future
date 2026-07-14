"""完整回测引擎 —— 信号独立结算模式。

把 best_signal → 入场 → 止损/目标平仓 → 统计期望串起来。
这是把"信号质量验证"升级为"策略盈亏验证"的最后一步。

仓位模式：信号独立结算——每个信号独立入场+止损+目标，互不影响。
假设：允许同品种多仓重叠，不考虑资金挤占，交易间有相关性。
适合评估"每个信号单独执行的期望"，不适合评估资金曲线真实性。

anti-repaint：信号 trigger_idx 已收盘，入场用次根开盘，全程无未来信息。

成本：手续费（万0.5 单边）+ 滑点（1 点 单边）×合约乘数，沿用 future_1 惯例。

出场：逐根检查止损/目标，同根触及保守按止损先成交；超时收盘平仓。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import numpy as np
import pandas as pd

from .config import BacktestConfig, DEFAULT_MULTIPLIERS
from .signals import ReversalSignal

ExitReason = Literal["stop", "target", "timeout"]


@dataclass(frozen=True)
class Trade:
    """单笔交易结果。"""

    symbol: str
    signal_type: str
    side: Literal["long", "short"]
    entry_idx: int          # 入场 K 线（次根开盘）
    entry_price: float
    exit_idx: int
    exit_price: float
    exit_reason: ExitReason
    stop_price: float
    target_price: float
    hold_bars: int
    gross_pnl: float        = 0.0   # 毛盈亏（价格点数，正=盈利）
    net_pnl: float          = 0.0   # 净盈亏（扣成本，价格点数）
    r_multiple: float       = 0.0   # 净盈亏 / 风险(R)
    cost_points: float      = 0.0   # 总成本（价格点数）

    @property
    def is_win(self) -> bool:
        return self.net_pnl > 0


@dataclass
class BacktestResult:
    """回测汇总结果。"""

    trades: list[Trade] = field(default_factory=list)
    config: BacktestConfig | None = None

    @property
    def n(self) -> int:
        return len(self.trades)

    @property
    def win_rate(self) -> float:
        return float(np.mean([t.is_win for t in self.trades])) if self.trades else 0.0

    @property
    def net_pnl_total(self) -> float:
        """净盈亏总和（价格点数）。"""
        return float(sum(t.net_pnl for t in self.trades))

    @property
    def avg_r(self) -> float:
        """平均 R 倍数（净盈亏/单笔风险）。"""
        return float(np.mean([t.r_multiple for t in self.trades])) if self.trades else 0.0

    @property
    def expectancy(self) -> float:
        """期望值 = 平均每笔净 R。"""
        return self.avg_r

    @property
    def profit_factor(self) -> float:
        gross_win = sum(t.net_pnl for t in self.trades if t.net_pnl > 0)
        gross_loss = abs(sum(t.net_pnl for t in self.trades if t.net_pnl <= 0))
        return gross_win / gross_loss if gross_loss > 1e-9 else float("inf")

    @property
    def avg_hold(self) -> float:
        return float(np.mean([t.hold_bars for t in self.trades])) if self.trades else 0.0

    def by_type(self) -> dict[str, list[Trade]]:
        d: dict[str, list[Trade]] = {}
        for t in self.trades:
            d.setdefault(t.signal_type, []).append(t)
        return d

    def summary(self) -> dict:
        return {
            "n": self.n,
            "win_rate": round(self.win_rate, 3),
            "avg_r": round(self.avg_r, 3),
            "expectancy_R": round(self.expectancy, 3),
            "profit_factor": round(self.profit_factor, 3),
            "avg_hold": round(self.avg_hold, 1),
            "net_pnl_points": round(self.net_pnl_total, 1),
        }


def _round_turn_cost(entry: float, exit_price: float, cfg: BacktestConfig) -> float:
    """往返总成本（价格点数）。

    手续费 = 名义价值 × commission_rate × 2（开平）；转成价格点数除以乘数。
    滑点 = slippage_points × 2（开平）。
    """
    notional_avg = ((entry + exit_price) / 2) * cfg.multiplier
    commission_pts = (notional_avg * cfg.commission_rate * 2) / cfg.multiplier
    slippage_pts = 2.0 * cfg.slippage_points
    return commission_pts + slippage_pts


def run_backtest(signals: list[tuple[str, ReversalSignal, pd.DataFrame, pd.Series]],
                 cfg: BacktestConfig | None = None) -> BacktestResult:
    """对一批信号跑独立结算回测。

    Parameters
    ----------
    signals : list of (symbol, ReversalSignal, df, atr)
        每个元素 = (品种代码, 去重后信号, 该品种 K 线 DataFrame, ATR Series)。
    cfg : BacktestConfig

    Returns
    -------
    BacktestResult
    """
    cfg = cfg or BacktestConfig()
    trades: list[Trade] = []

    for symbol, sig, df, atr in signals:
        mult = DEFAULT_MULTIPLIERS.get(symbol, cfg.multiplier)
        sym_cfg = BacktestConfig(
            commission_rate=cfg.commission_rate, slippage_points=cfg.slippage_points,
            multiplier=mult, max_hold_bars=cfg.max_hold_bars,
            initial_capital=cfg.initial_capital,
            target_rr_by_type=cfg.target_rr_by_type,
            stop_mode=cfg.stop_mode, risk_pct=cfg.risk_pct,
        )
        t = _simulate_one(symbol, sig, df, atr, sym_cfg)
        if t is not None:
            trades.append(t)

    return BacktestResult(trades=trades, config=cfg)


def _simulate_one(symbol: str, sig: ReversalSignal, df: pd.DataFrame,
                  atr: pd.Series, cfg: BacktestConfig) -> Trade | None:
    """模拟单笔交易。

    入场：信号 trigger_idx 次根开盘（+滑点）。
    出场：逐根检查止损/目标；同根触及保守按止损先成交；超时收盘平仓。
    """
    n = len(df)
    open_ = df["open"].to_numpy(float)
    high = df["high"].to_numpy(float)
    low = df["low"].to_numpy(float)
    close = df["close"].to_numpy(float)

    entry_idx = sig.trigger_idx + 1   # 次根开盘入场
    if entry_idx >= n:
        return None

    is_long = sig.side == "long"
    raw_entry = float(open_[entry_idx])
    # 滑点：做多成交价更高，做空更低
    entry_price = raw_entry + cfg.slippage_points if is_long else raw_entry - cfg.slippage_points

    # 止损价：structure=信号自带结构止损；fixed_risk=固定金额资金止损
    if cfg.stop_mode == "fixed_risk":
        risk_amount = cfg.initial_capital * cfg.risk_pct   # 元
        risk_points = risk_amount / cfg.multiplier          # 价格点数
        if risk_points < 1e-9:
            return None
        stop = entry_price - risk_points if is_long else entry_price + risk_points
        risk = risk_points
    else:  # "structure"
        stop = sig.stop
        risk = abs(entry_price - stop)
        if risk < 1e-9:
            return None
    # 目标价（按信号类型差异化 R:R）
    target_rr = cfg.target_rr_by_type.get(sig.signal_type, 2.0)
    if is_long:
        target = entry_price + target_rr * risk
    else:
        target = entry_price - target_rr * risk

    last = min(entry_idx + cfg.max_hold_bars, n - 1)
    exit_idx = last
    exit_price = float(close[last])
    exit_reason: ExitReason = "timeout"

    for i in range(entry_idx + 1, last + 1):
        h, l = float(high[i]), float(low[i])
        if is_long:
            hit_stop = l <= stop
            hit_target = h >= target
        else:
            hit_stop = h >= stop
            hit_target = l <= target
        # 同根触及保守按止损先成交
        if hit_stop:
            exit_idx, exit_price, exit_reason = i, stop, "stop"
            break
        if hit_target:
            exit_idx, exit_price, exit_reason = i, target, "target"
            break

    # 滑点出场
    if is_long:
        exit_price_exec = exit_price - cfg.slippage_points
    else:
        exit_price_exec = exit_price + cfg.slippage_points

    gross = (exit_price_exec - entry_price) if is_long else (entry_price - exit_price_exec)
    cost = _round_turn_cost(entry_price, exit_price_exec, cfg)
    net = gross - cost
    r_mult = net / risk

    return Trade(
        symbol=symbol, signal_type=sig.signal_type, side=sig.side,
        entry_idx=entry_idx, entry_price=round(entry_price, 4),
        exit_idx=exit_idx, exit_price=round(exit_price_exec, 4),
        exit_reason=exit_reason, stop_price=round(stop, 4),
        target_price=round(target, 4), hold_bars=exit_idx - entry_idx,
        gross_pnl=round(gross, 4), net_pnl=round(net, 4),
        r_multiple=round(r_mult, 4), cost_points=round(cost, 4),
    )


def rr_sweep(signals: list[tuple[str, ReversalSignal, pd.DataFrame, pd.Series]],
             rr_grid=(1.0, 1.5, 2.0, 2.5, 3.0),
             base_cfg: BacktestConfig | None = None) -> pd.DataFrame:
    """R:R 敏感度分析：对统一 R:R 扫描，找整体最优。

    注意：这里用统一 R:R（不分类型）扫描，用于观察 R:R 对期望的影响趋势，
    辅助设定 target_rr_by_type 的初始值。
    """
    base_cfg = base_cfg or BacktestConfig()
    rows = []
    for rr in rr_grid:
        cfg = BacktestConfig(
            commission_rate=base_cfg.commission_rate,
            slippage_points=base_cfg.slippage_points,
            multiplier=base_cfg.multiplier,
            max_hold_bars=base_cfg.max_hold_bars,
            initial_capital=base_cfg.initial_capital,
            target_rr_by_type={"wedge_breakout": rr, "reversal_bar": rr, "second_entry": rr},
            stop_mode=base_cfg.stop_mode, risk_pct=base_cfg.risk_pct,
        )
        res = run_backtest(signals, cfg)
        rows.append({
            "target_rr": rr,
            "n": res.n,
            "win_rate": round(res.win_rate, 3),
            "avg_r": round(res.avg_r, 3),
            "profit_factor": round(res.profit_factor, 3),
            "net_pnl": round(res.net_pnl_total, 1),
        })
    return pd.DataFrame(rows)
