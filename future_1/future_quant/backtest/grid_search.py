"""Grid search over QuantConfig parameters.

For each parameter combination, backtest every cached symbol and aggregate the
results. Rankings are emitted to CSV and the console.

Example::

    from future_quant.backtest import CacheManager, grid_search

    cm = CacheManager()
    cm.build_cache(length=2000)                 # one-time fetch
    data = cm.load_all()
    results = grid_search(data, rank_by="sharpe")
    results.save_csv("grid_results.csv")
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field, replace
from pathlib import Path

import pandas as pd

from future_quant.backtest.backtester import (
    DEFAULT_MULTIPLIERS,
    BacktestConfig,
    Backtester,
    BacktestResult,
)
from future_quant.config import QuantConfig

# The parameter axes most worth optimizing for the breakout-trigger + score model.
# 突破触发核心旋钮：评分阈值（决定出信号门槛）+ 突破量能门 + 突破 ATR 余量。
# 这三个直接决定"能抓到多少有效突破"。exhaustion 仍为评分维度，保留两个轴。
DEFAULT_GRID: dict[str, list] = {
    "signal_score_threshold": [45.0, 50.0, 55.0, 60.0],
    "breakout_volume_ratio": [0.6, 0.8, 1.0],
    "breakout_atr_margin": [0.15, 0.20, 0.30],
    "exhaustion_score_threshold": [0.55, 0.60],
    "min_exhaustion_checks": [3, 4],
}


@dataclass
class ParamCombo:
    params: dict
    aggregated: dict = field(default_factory=dict)
    per_symbol: list[dict] = field(default_factory=list)

    @property
    def key(self) -> str:
        return ",".join(f"{k}={v}" for k, v in sorted(self.params.items()))


@dataclass
class GridSearchResult:
    combos: list[ParamCombo] = field(default_factory=list)
    rank_by: str = "sharpe"

    def ranked(self) -> list[ParamCombo]:
        reverse = self.rank_by in ("net_pnl", "sharpe", "win_rate", "profit_factor", "total_trades")
        return sorted(self.combos, key=lambda c: c.aggregated.get(self.rank_by, 0.0), reverse=reverse)

    def to_frame(self) -> pd.DataFrame:
        rows = []
        for combo in self.combos:
            row = dict(combo.params)
            row.update(combo.aggregated)
            rows.append(row)
        return pd.DataFrame(rows)

    def save_csv(self, path: str | Path) -> None:
        self.to_frame().to_csv(path, index=False)


def grid_search(
    data: dict[str, pd.DataFrame],
    grid: dict[str, list] | None = None,
    base_config: QuantConfig | None = None,
    backtest_config: BacktestConfig | None = None,
    rank_by: str = "sharpe",
    verbose: bool = True,
) -> GridSearchResult:
    """Run a grid search over ``grid`` params against all symbols in ``data``.

    Parameters
    ----------
    data : dict[str, pd.DataFrame]
        Symbol -> K-line DataFrame (typically from ``CacheManager.load_all``).
    grid : dict[str, list]
        Parameter axes. Defaults to ``DEFAULT_GRID``.
    rank_by : str
        Metric used to rank combinations: net_pnl / sharpe / win_rate /
        profit_factor / total_trades / max_drawdown.
    """
    grid = grid or DEFAULT_GRID
    base_config = base_config or QuantConfig()

    keys = list(grid.keys())
    value_lists = [grid[k] for k in keys]
    combinations = list(itertools.product(*value_lists))
    if verbose:
        print(f"grid search: {len(combinations)} combos x {len(data)} symbols")

    result = GridSearchResult(rank_by=rank_by)

    for ci, values in enumerate(combinations, 1):
        overrides = dict(zip(keys, values))
        quant_config = replace(base_config, **overrides)
        bt_config = backtest_config or BacktestConfig()
        backtester = Backtester(quant_config, bt_config)

        per_symbol: list[dict] = []
        total_pnl = 0.0
        total_trades = 0
        wins = 0
        gross_win = 0.0
        gross_loss = 0.0
        all_returns: list[float] = []

        for symbol, df in data.items():
            multiplier = DEFAULT_MULTIPLIERS.get(symbol, bt_config.multiplier)
            sym_bt_config = replace(bt_config, multiplier=multiplier)
            backtester.backtest_config = sym_bt_config
            res: BacktestResult = backtester.run(df, symbol=symbol)
            total_pnl += res.net_pnl
            total_trades += res.n_trades
            wins += sum(1 for t in res.trades if t.is_win)
            gross_win += sum(t.pnl for t in res.trades if t.pnl > 0)
            gross_loss += abs(sum(t.pnl for t in res.trades if t.pnl <= 0))
            per_symbol.append(res.summary())

        win_rate = (wins / total_trades) if total_trades else 0.0
        profit_factor = (gross_win / gross_loss) if gross_loss > 0 else float("inf")
        # Aggregate Sharpe as a simple PnL-per-trade proxy when trades exist.
        if total_trades:
            per_trade = total_pnl / total_trades
            sharpe = per_trade  # simplified; full Sharpe needs per-trade series
        else:
            sharpe = 0.0

        combo = ParamCombo(
            params=overrides,
            aggregated={
                "net_pnl": total_pnl,
                "total_trades": total_trades,
                "win_rate": win_rate,
                "profit_factor": profit_factor,
                "sharpe": sharpe,
            },
            per_symbol=per_symbol,
        )
        result.combos.append(combo)
        if verbose:
            print(
                f"  [{ci}/{len(combinations)}] {combo.key} "
                f"-> pnl={total_pnl:>+10.2f} trades={total_trades:>4} "
                f"win={win_rate:.1%} pf={profit_factor:.2f}"
            )

    return result


def report(result: GridSearchResult, top_n: int = 10) -> str:
    """Render a human-readable ranking of the top parameter combinations."""
    lines = [f"Grid search ranking (by {result.rank_by}), top {top_n}:"]
    for i, combo in enumerate(result.ranked()[:top_n], 1):
        a = combo.aggregated
        lines.append(
            f"  #{i:>2} {combo.key}\n"
            f"       net_pnl={a.get('net_pnl', 0):>+10.2f}  "
            f"trades={a.get('total_trades', 0):>4}  "
            f"win={a.get('win_rate', 0):.1%}  "
            f"pf={a.get('profit_factor', 0):.2f}  "
            f"sharpe={a.get('sharpe', 0):.3f}"
        )
    return "\n".join(lines)
