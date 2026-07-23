"""资金回测 —— 带入初始资金、按手数交易、逐根盯市的真实资金曲线。

与 backtest.py（信号独立结算，每笔 R 恒定、不考虑资金挤占）不同，本模块模拟
真实账户：按风险预算定手数、持仓占用资金、逐根按 OHLC 触发止损/目标、
记录权益曲线与最大回撤。这样能回答"给定一笔钱，这套策略能赚多少/亏多少"。

设计借鉴 future_turning.capital_backtest.simulate_capital（同一套成熟范式），
但针对两高两低突破信号做了简化：
    - 单一入场（无加仓 tranche），止损/目标由信号自带结构决定。
    - 风险预算定手数：qty = floor(capital×risk_pct / (|entry−stop|×multiplier))，
      上限 max_pct 名义敞口。小资金时手数可能为 0 → 信号被跳过（资金不足）。
    - 同品种同时只持一仓；冷却期内不再开同品种新仓。
    - 逐根按 OHLC 判断止损/目标，同根触及保守按止损先成交（stop-first）。
    - 超时 max_hold 根按收盘平仓。

为什么独立于 backtest.py：
    backtest.py 的 R 结算回答"每个信号的期望 R"；本模块回答"账户曲线长什么样"。
    两者都需要——前者评估信号质量，后者评估可投资性。小资金（15K）和大资金
    （60K）的差异主要在手数取整粒度（手数下取整对小资金伤害大）和仓位挤占。

输入信号兼容 BreakoutSignal（duck-typing：.side/.signal_type/.trigger_idx/.entry/.stop）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .config import DEFAULT_MULTIPLIERS
from .signals import BreakoutSignal


# ─────────────────────────────────────────────────────────────
# 仓位定手数
# ─────────────────────────────────────────────────────────────

def _risk_qty(capital: float, risk_pct: float, entry: float, stop: float,
              multiplier: float, min_qty: int = 1) -> int:
    """风险预算定手数：floor(capital×risk_pct / (|entry−stop|×multiplier))，但不少于 min_qty。

    每笔最大亏损 = capital×risk_pct；除以单手风险距离得到手数，下取整。
    期货最小交易 1 手——风险预算下取整为 0 时，对极小资金账户仍允许开 min_qty 手
    （否则小账户永远无法交易）。min_qty=0 时退化为纯风险预算（保守）。
    """
    risk_per_lot = abs(entry - stop) * multiplier
    if risk_per_lot <= 1e-12:
        return min_qty
    qty = int((capital * risk_pct) // risk_per_lot)
    return max(min_qty, qty)


def _nominal_qty(capital: float, max_pct: float, price: float, multiplier: float) -> int:
    """名义敞口上限对应手数：floor(capital×max_pct / (price×multiplier))。"""
    if price <= 0 or multiplier <= 0:
        return 0
    return max(0, int((capital * max_pct) // (price * multiplier)))


# ─────────────────────────────────────────────────────────────
# 逐根出场决策
# ─────────────────────────────────────────────────────────────

def _decide_exit(pos: dict, bar, idx: int, max_hold: int
                 ) -> tuple[str, float | None, str]:
    """返回 (action, price, reason)。action in {hold, close}。

    side-aware：long 用 low<=stop / high>=target；short 镜像。
    同根触及保守按止损先成交（stop-first）。
    """
    side = pos["side"]
    o = float(bar["open"]); h = float(bar["high"]); l = float(bar["low"])
    stop = pos["stop"]; target = pos["target"]; entry_idx = pos["entry_idx"]

    # 开盘跳空穿越止损/目标 → 立即按开盘平，止损优先
    if side == "long":
        if o <= stop:
            return "close", o, "gap_stop"
        if o >= target:
            return "close", o, "gap_target"
    else:
        if o >= stop:
            return "close", o, "gap_stop"
        if o <= target:
            return "close", o, "gap_target"

    # 盘中触及止损/目标，止损优先
    if side == "long":
        if l <= stop:
            return "close", stop, "stop"
        if h >= target:
            return "close", target, "target"
    else:
        if h >= stop:
            return "close", stop, "stop"
        if l <= target:
            return "close", target, "target"

    if idx - entry_idx >= max_hold:
        return "close", float(bar["close"]), "timeout"
    return "hold", None, ""


# ─────────────────────────────────────────────────────────────
# 资金模拟
# ─────────────────────────────────────────────────────────────

@dataclass
class CapitalResult:
    """资金回测结果。"""

    initial_capital: float
    final_equity: float
    trades: list[dict] = field(default_factory=list)
    equity_curve: list[dict] = field(default_factory=list)
    skipped_low_qty: int = 0          # 因手数为 0（资金不足）被跳过的信号数
    skipped_cooldown: int = 0         # 因冷却期被跳过的信号数
    params: dict = field(default_factory=dict)

    @property
    def net_profit(self) -> float:
        return self.final_equity - self.initial_capital

    @property
    def return_pct(self) -> float:
        return self.final_equity / self.initial_capital - 1.0 if self.initial_capital else 0.0

    @property
    def trade_count(self) -> int:
        return len(self.trades)

    @property
    def win_rate(self) -> float:
        if not self.trades:
            return 0.0
        return sum(1 for t in self.trades if t["pnl"] > 0) / len(self.trades)

    @property
    def profit_factor(self) -> float:
        wins = sum(t["pnl"] for t in self.trades if t["pnl"] > 0)
        losses = abs(sum(t["pnl"] for t in self.trades if t["pnl"] < 0))
        return wins / losses if losses > 1e-9 else float("inf")

    @property
    def max_drawdown(self) -> float:
        """权益曲线最大回撤（负数比例，如 −0.15 = 回撤 15%）。"""
        if not self.equity_curve:
            return 0.0
        peak = self.equity_curve[0]["equity"]
        mdd = 0.0
        for pt in self.equity_curve:
            peak = max(peak, pt["equity"])
            dd = pt["equity"] / peak - 1.0 if peak > 0 else 0.0
            mdd = min(mdd, dd)
        return mdd

    @property
    def avg_hold(self) -> float:
        if not self.trades:
            return 0.0
        return float(np.mean([t["hold_bars"] for t in self.trades]))

    def summary(self) -> dict:
        return {
            "initial_capital": round(self.initial_capital, 0),
            "final_equity": round(self.final_equity, 0),
            "net_profit": round(self.net_profit, 0),
            "return_pct": round(self.return_pct, 4),
            "trades": self.trade_count,
            "win_rate": round(self.win_rate, 4),
            "profit_factor": round(self.profit_factor, 3),
            "max_drawdown": round(self.max_drawdown, 4),
            "avg_hold": round(self.avg_hold, 1),
            "skipped_low_qty": self.skipped_low_qty,
            "skipped_cooldown": self.skipped_cooldown,
        }


def simulate_capital(
    data: dict[str, pd.DataFrame],
    signals: dict[str, list[BreakoutSignal]],
    *,
    initial_capital: float = 30_000.0,
    risk_pct: float = 0.02,
    max_pct: float = 0.50,
    commission_rate: float = 0.00005,
    slippage_points: float = 1.0,
    max_hold: int = 80,
    cooldown_bars: int = 10,
    target_rr: float = 2.0,
    multipliers: dict[str, float] | None = None,
    min_qty: int = 1,
) -> CapitalResult:
    """多品种、次日开盘入场的资金回测。

    Parameters
    ----------
    data : dict[symbol, DataFrame]
        各品种 OHLCV（datetime 升序）。
    signals : dict[symbol, list[BreakoutSignal]]
        各品种的突破信号（按 trigger_idx 升序更佳，内部会按时间调度）。
    initial_capital : float
        初始资金（元）。15K / 30K / 60K 对比用。
    risk_pct : float
        每笔最大亏损 = 资金 × 此值（风险预算定手数）。
    max_pct : float
        单笔名义敞口上限 = 资金 × 此值（防止重仓）。
    commission_rate : float
        单边手续费率（名义价值比例，万0.5）。
    slippage_points : float
        单边滑点（价格点数）。
    max_hold : int
        超时平仓根数。
    cooldown_bars : int
        同品种平仓后冷却 N 根不再开仓。
    target_rr : float
        目标 = 入场 + target_rr × 风险。
    multipliers : dict[symbol, float]
        合约乘数；缺失时用 DEFAULT_MULTIPLIERS，再缺失用 10.0。
    min_qty : int
        每笔最小手数（默认 1）。期货最小交易 1 手——风险预算下取整为 0 时，
        对极小资金账户仍允许开 min_qty 手（否则小账户永远无法交易）。
        设 0 则退化为纯风险预算（保守，小资金会大量跳过）。
    """
    multipliers = multipliers or {}
    capital = float(initial_capital)
    positions: dict[str, dict] = {}
    last_trade_end: dict[str, int] = {}

    # 信号按 (symbol, entry_idx=trigger_idx+1) 调度：次根开盘入场
    pending: dict[tuple[str, int], list[BreakoutSignal]] = {}
    for symbol, sig_list in signals.items():
        for sig in sig_list:
            pending.setdefault((symbol, sig.trigger_idx + 1), []).append(sig)

    trades: list[dict] = []
    equity_curve: list[dict] = []
    skipped_low_qty = 0
    skipped_cooldown = 0
    peak = capital

    dates = sorted({pd.Timestamp(x) for frame in data.values() for x in frame["datetime"]})
    date_index = {symbol: {pd.Timestamp(d): i for i, d in enumerate(frame["datetime"])}
                  for symbol, frame in data.items()}

    def _gross(side: str, exit_price: float, entry_price: float, qty: int, mult: float) -> float:
        if side == "long":
            return (exit_price - entry_price) * qty * mult
        return (entry_price - exit_price) * qty * mult

    def _round_turn_cost(entry: float, exit_price: float, qty: int, mult: float) -> float:
        notional_avg = ((entry + exit_price) / 2) * mult * qty
        return notional_avg * commission_rate * 2 + slippage_points * 2 * mult * qty

    def close_position(symbol: str, idx: int, price: float, reason: str, date) -> None:
        nonlocal capital
        pos = positions.pop(symbol)
        exit_price = float(price)
        cost = _round_turn_cost(pos["avg_price"], exit_price, pos["qty"], pos["multiplier"])
        net = _gross(pos["side"], exit_price, pos["avg_price"], pos["qty"], pos["multiplier"]) - cost
        capital += net
        last_trade_end[symbol] = idx
        trades.append({
            "symbol": symbol, "side": pos["side"], "entry_date": str(pos["entry_date"]),
            "exit_date": str(date), "qty": pos["qty"], "entry_price": round(pos["avg_price"], 2),
            "exit_price": round(exit_price, 2), "pnl": round(net, 0), "reason": reason,
            "hold_bars": idx - pos["entry_idx"],
        })

    for date in dates:
        # Stage 1：逐根盯市出场（side-aware）
        for symbol, pos in list(positions.items()):
            if date not in date_index.get(symbol, {}):
                continue
            i = date_index[symbol][date]
            if i < pos["entry_idx"]:
                continue
            bar = data[symbol].iloc[i]
            action, price, reason = _decide_exit(pos, bar, i, max_hold)
            if action == "close":
                close_position(symbol, i, price, reason, date)

        # Stage 2：执行待入场信号（当日开盘）
        for (symbol, entry_idx), sig_list in list(pending.items()):
            if symbol not in data or entry_idx >= len(data[symbol]):
                pending.pop((symbol, entry_idx), None)
                continue
            frame = data[symbol]
            if pd.Timestamp(frame.iloc[entry_idx]["datetime"]) != date:
                continue
            pending.pop((symbol, entry_idx), None)
            bar = frame.iloc[entry_idx]
            open_price = float(bar["open"])
            mult = float(multipliers.get(symbol, DEFAULT_MULTIPLIERS.get(symbol, 10.0)))

            for sig in sorted(sig_list, key=lambda x: x.trigger_idx):
                side = sig.side
                # 同品种已持仓 → 跳过
                if symbol in positions:
                    continue
                # 冷却期
                if symbol in last_trade_end and entry_idx - last_trade_end[symbol] < cooldown_bars:
                    skipped_cooldown += 1
                    continue
                # 入场价（含滑点：做多成交更高，做空更低）
                entry_price = open_price + slippage_points if side == "long" else open_price - slippage_points
                stop = float(sig.stop)
                risk = abs(entry_price - stop)
                if risk <= 0:
                    continue
                # 风险预算定手数（min_qty 手保底，期货最小 1 手）
                qty = _risk_qty(capital, risk_pct, entry_price, stop, mult, min_qty=min_qty)
                # 名义敞口上限（防止重仓；小资金若名义敞口超限仍放行 min_qty 手）
                qty_cap = _nominal_qty(capital, max_pct, entry_price, mult)
                if qty_cap <= 0:
                    # 名义敞口连 1 手都开不起（保证金不足）→ 跳过
                    skipped_low_qty += 1
                    continue
                qty = max(min_qty, min(qty, qty_cap))
                target = entry_price + target_rr * risk if side == "long" else entry_price - target_rr * risk
                positions[symbol] = {
                    "side": side, "qty": qty, "multiplier": mult,
                    "avg_price": entry_price, "entry_idx": entry_idx, "entry_date": date,
                    "stop": stop, "target": target,
                }

        # 权益 = 现金 + 持仓浮盈
        equity = capital
        for symbol, pos in positions.items():
            if date in date_index.get(symbol, {}):
                close = float(data[symbol].iloc[date_index[symbol][date]]["close"])
                equity += _gross(pos["side"], close, pos["avg_price"], pos["qty"], pos["multiplier"])
        peak = max(peak, equity)
        equity_curve.append({
            "date": str(date), "equity": round(equity, 0),
            "drawdown": round(equity / peak - 1.0, 4) if peak > 0 else 0.0,
            "open_positions": len(positions),
        })

    # 收尾：强制平掉剩余仓位
    if dates:
        last_date = dates[-1]
        for symbol in list(positions):
            if last_date in date_index.get(symbol, {}):
                i = date_index[symbol][last_date]
                close_position(symbol, i, float(data[symbol].iloc[i]["close"]), "end_of_test", last_date)
        if equity_curve:
            equity_curve[-1]["equity"] = round(capital, 0)
            equity_curve[-1]["open_positions"] = 0

    return CapitalResult(
        initial_capital=initial_capital,
        final_equity=capital,
        trades=trades,
        equity_curve=equity_curve,
        skipped_low_qty=skipped_low_qty,
        skipped_cooldown=skipped_cooldown,
        params={
            "risk_pct": risk_pct, "max_pct": max_pct, "max_hold": max_hold,
            "commission_rate": commission_rate, "slippage_points": slippage_points,
            "cooldown_bars": cooldown_bars, "target_rr": target_rr,
        },
    )


__all__ = ["simulate_capital", "CapitalResult"]
