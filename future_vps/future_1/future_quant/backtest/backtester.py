"""Vectorized rolling backtest for the wedge-reversal engine.

Model (deliberately simple, declared as a simplification in the design):
- At each bar ``i`` (from ``warmup`` onward) the engine runs on ``df.iloc[:i+1]``.
- If a valid signal is produced and no position is open, enter at the *next*
  bar's open.
- An open position exits at the first subsequent bar whose intrabar high/low
  touches the stop (loss) or target_1 (win). If both are touched in the same
  bar we conservatively assume the stop hit first.
- Costs: commission (per side, as a fraction of notional) + slippage (in price
  points per side).
- One position at a time per symbol (no pyramiding, no shorts-into-longs stack).

This is a research-grade model, not a production execution model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np
import pandas as pd

from future_quant.config import QuantConfig
from future_quant.core.types import SignalSide
from future_quant.engine import QuantEngine

# Default per-contract multipliers (yuan per point) for cost & PnL math.
# Source: rough exchange defaults; override via BacktestConfig.multipliers.
DEFAULT_MULTIPLIERS: dict[str, float] = {
    "IF0": 300.0, "IC0": 200.0, "IH0": 300.0, "IM0": 200.0,
    "RB0": 10.0, "HC0": 10.0, "AU0": 1000.0, "AG0": 15.0,
    "CU0": 5.0, "AL0": 5.0, "ZN0": 5.0, "NI0": 1.0,
    "M0": 10.0, "Y0": 10.0, "P0": 10.0, "A0": 10.0, "C0": 10.0,
    "SR0": 10.0, "CF0": 5.0, "TA0": 5.0, "MA0": 10.0, "PP0": 5.0,
    "I0": 100.0, "J0": 100.0, "JM0": 60.0,
    "FU0": 10.0, "BU0": 10.0, "RU0": 10.0,
    "FG0": 20.0, "SA0": 20.0, "SF0": 5.0, "SM0": 5.0,
    "AP0": 10.0, "CJ0": 5.0, "UR0": 20.0,
    "LC0": 1.0, "SI0": 5.0,
    "TF0": 10000.0, "T0": 10000.0, "TS0": 20000.0,
}


@dataclass(frozen=True)
class BacktestConfig:
    commission_rate: float = 0.00005   # per side, fraction of notional
    slippage_points: float = 1.0       # per side, in price points
    multiplier: float = 10.0           # contract multiplier (yuan/point)
    warmup: int = 120                  # bars skipped before first signal eval
    step: int = 1                      # evaluate every Nth bar (1 = every bar)
    initial_capital: float = 100_000.0


@dataclass
class Trade:
    symbol: str
    side: str
    entry_idx: int
    exit_idx: int
    entry_price: float
    exit_price: float
    stop: float
    target: float
    pnl: float
    is_win: bool
    bars_held: int


@dataclass
class BacktestResult:
    symbol: str
    trades: list[Trade] = field(default_factory=list)
    net_pnl: float = 0.0
    win_rate: float = 0.0
    max_drawdown: float = 0.0
    sharpe: float = 0.0
    profit_factor: float = 0.0
    equity_curve: list[float] = field(default_factory=list)

    @property
    def n_trades(self) -> int:
        return len(self.trades)

    def summary(self) -> dict:
        return {
            "symbol": self.symbol,
            "trades": self.n_trades,
            "net_pnl": round(self.net_pnl, 2),
            "win_rate": round(self.win_rate, 4),
            "max_drawdown": round(self.max_drawdown, 4),
            "sharpe": round(self.sharpe, 4),
            "profit_factor": round(self.profit_factor, 4),
        }


class Backtester:
    def __init__(
        self,
        quant_config: QuantConfig | None = None,
        backtest_config: BacktestConfig | None = None,
    ) -> None:
        self.quant_config = quant_config or QuantConfig()
        self.backtest_config = backtest_config or BacktestConfig()

    def run(self, df: pd.DataFrame, symbol: str = "SYMBOL") -> BacktestResult:
        """Backtest one symbol's K-line series."""
        cfg = self.backtest_config
        engine = QuantEngine(self.quant_config)
        result = BacktestResult(symbol=symbol)

        df = df.reset_index(drop=True)
        n = len(df)
        if n < cfg.warmup + 5:
            return result

        highs = df["high"].to_numpy(dtype=float)
        lows = df["low"].to_numpy(dtype=float)
        opens = df["open"].to_numpy(dtype=float)

        in_pos = False
        pos_side: str = ""
        entry_price = stop = target = 0.0
        entry_idx = 0

        equity = cfg.initial_capital
        result.equity_curve = [equity]
        peak = equity

        for i in range(cfg.warmup, n - 1, cfg.step):
            # ---- manage open position: check stop / target on bar i ----
            if in_pos:
                exit_price = self._check_exit(
                    pos_side, highs[i], lows[i], stop, target
                )
                if exit_price is not None:
                    gross = self._pnl(pos_side, entry_price, exit_price)
                    cost = self._round_turn_cost(entry_price, exit_price)
                    pnl = (gross - cost) * 1.0  # 1 lot
                    equity += pnl
                    is_win = pnl > 0
                    result.trades.append(
                        Trade(
                            symbol=symbol,
                            side=pos_side,
                            entry_idx=entry_idx,
                            exit_idx=i,
                            entry_price=entry_price,
                            exit_price=exit_price,
                            stop=stop,
                            target=target,
                            pnl=pnl,
                            is_win=is_win,
                            bars_held=i - entry_idx,
                        )
                    )
                    in_pos = False
                    result.equity_curve.append(equity)
                    peak = max(peak, equity)

            if in_pos:
                continue

            # ---- look for a new entry: analyze df up to bar i ----
            window = df.iloc[: i + 1]
            analysis = engine.analyze_df(window)
            sig = analysis.signal
            if not sig.is_valid or sig.side == SignalSide.NONE:
                continue
            if sig.levels.entry is None or sig.levels.stop is None or sig.levels.target_1 is None:
                continue

            # Enter at next bar's open (i+1), adjusting for slippage.
            side = sig.side.value
            entry_price = self._slipped_entry(side, opens[i + 1])
            # Inherit the strategy's intended stop / target_1 (set at signal time).
            stop = float(sig.levels.stop)
            target = float(sig.levels.target_1)
            # Guard against instantly-invalidated setups.
            if side == "long" and (entry_price <= stop or entry_price >= target):
                continue
            if side == "short" and (entry_price >= stop or entry_price <= target):
                continue
            in_pos = True
            pos_side = side
            entry_idx = i + 1

        # ---- close any dangling position at last close ----
        if in_pos:
            last_close = float(df.iloc[-1]["close"])
            gross = self._pnl(pos_side, entry_price, last_close)
            cost = self._round_turn_cost(entry_price, last_close)
            pnl = gross - cost
            equity += pnl
            result.trades.append(
                Trade(
                    symbol=symbol,
                    side=pos_side,
                    entry_idx=entry_idx,
                    exit_idx=n - 1,
                    entry_price=entry_price,
                    exit_price=last_close,
                    stop=stop,
                    target=target,
                    pnl=pnl,
                    is_win=pnl > 0,
                    bars_held=n - 1 - entry_idx,
                )
            )
            result.equity_curve.append(equity)

        self._finalize(result, cfg.initial_capital)
        return result

    # ------------------------------------------------------------------ math
    def _check_exit(
        self,
        side: str,
        bar_high: float,
        bar_low: float,
        stop: float,
        target: float,
    ) -> float | None:
        """Return the exit price if bar i touched stop or target, else None.

        Conservative: if both touched same bar, assume the stop hit first.
        """
        if side == "long":
            if bar_low <= stop:
                return stop
            if bar_high >= target:
                return target
        else:  # short
            if bar_high >= stop:
                return stop
            if bar_low <= target:
                return target
        return None

    def _pnl(self, side: str, entry: float, exit_price: float) -> float:
        direction = 1.0 if side == "long" else -1.0
        return direction * (exit_price - entry) * self.backtest_config.multiplier

    def _slipped_entry(self, side: str, open_price: float) -> float:
        slip = self.backtest_config.slippage_points
        # Buyers pay up, sellers sell down.
        return open_price + slip if side == "long" else open_price - slip

    def _round_turn_cost(self, entry: float, exit_price: float) -> float:
        cfg = self.backtest_config
        notional = (abs(entry) + abs(exit_price)) * cfg.multiplier
        commission = notional * cfg.commission_rate
        slippage = 2.0 * cfg.slippage_points * cfg.multiplier  # entry + exit
        return commission + slippage

    def _finalize(self, result: BacktestResult, initial_capital: float) -> None:
        trades = result.trades
        result.net_pnl = sum(t.pnl for t in trades)
        if trades:
            wins = [t.pnl for t in trades if t.pnl > 0]
            losses = [abs(t.pnl) for t in trades if t.pnl <= 0]
            result.win_rate = len(wins) / len(trades)
            result.profit_factor = (sum(wins) / sum(losses)) if losses else float("inf")

        # Max drawdown & Sharpe from the equity curve (per-trade returns).
        curve = np.asarray(result.equity_curve, dtype=float)
        if len(curve) > 1:
            peak = np.maximum.accumulate(curve)
            dd = (peak - curve) / np.where(peak > 0, peak, 1.0)
            result.max_drawdown = float(dd.max()) if dd.size else 0.0
            rets = np.diff(curve) / np.where(curve[:-1] > 0, curve[:-1], 1.0)
            if rets.std() > 0:
                # Annualization factor omitted; this is a per-trade Sharpe proxy.
                result.sharpe = float(rets.mean() / rets.std() * np.sqrt(len(rets)))
