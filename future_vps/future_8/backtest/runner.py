"""多品种批量回测 + 简易参数网格。

复用 future_data.get_klines 拉深历史，逐品种跑 Backtester.run，聚合统计。
"""
from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path
from typing import Iterable

import pandas as pd

# 让 future_data / fakebreak / backtest 可被 import（脚本可能从 future_8/ 或仓库根运行）
_FUTURE_8 = Path(__file__).resolve().parents[1]  # future_8/
_WORK_AI = _FUTURE_8.parent
for _p in (str(_WORK_AI), str(_FUTURE_8)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from future_data import get_klines  # noqa: E402

from fakebreak.config import load_config  # noqa: E402
from fakebreak.data_loader import load_top40  # noqa: E402
from backtest.backtester import (  # noqa: E402
    Backtester,
    BacktestConfig,
    BacktestResult,
    DEFAULT_MULTIPLIERS,
)


def run_one(symbol: str, exchange: str, period: str, length: int, ttl_hours: float,
            strategy_config, backtest_config: BacktestConfig) -> BacktestResult:
    """单品种：拉数据 → 回测。"""
    df = get_klines(symbol, exchange, period=period, length=length, ttl_hours=ttl_hours)
    if df is None or df.empty:
        return BacktestResult(symbol=symbol)
    bt = Backtester(strategy_config, backtest_config)
    return bt.run(df, symbol=symbol)


def run_many(symbols, period: str = "15", length: int = 2000, ttl_hours: float = 999,
             strategy_config=None, backtest_config: BacktestConfig | None = None,
             progress: bool = True) -> list[BacktestResult]:
    """批量回测。symbols 为 [(symbol, name, exchange), ...]。"""
    sc = strategy_config or load_config()
    results = []
    for i, (sym, name, ex) in enumerate(symbols, 1):
        mult = DEFAULT_MULTIPLIERS.get(sym, 10.0)
        bc = backtest_config or replace(BacktestConfig(), multiplier=mult)
        bc = replace(bc, multiplier=mult)
        try:
            r = run_one(sym, ex, period, length, ttl_hours, sc, bc)
            results.append(r)
            if progress:
                s = r.summary()
                print(f"[{i}/{len(symbols)}] {sym} {name}: "
                      f"trades={s['trades']} pnl={s['net_pnl']} "
                      f"win={s['win_rate']:.1%} pf={s['profit_factor']} dd={s['max_drawdown']:.1%}")
        except Exception as e:  # noqa: BLE001
            if progress:
                print(f"[{i}/{len(symbols)}] {sym} {name}: SKIP ({e})")
    return results


def aggregate(results: Iterable[BacktestResult]) -> dict:
    """聚合多品种回测结果。"""
    results = list(results)
    all_trades = [t for r in results for t in r.trades]
    n = len(all_trades)
    wins = [t for t in all_trades if t.pnl > 0]
    losses = [abs(t.pnl) for t in all_trades if t.pnl <= 0]
    gross_pnl = sum(t.pnl for t in all_trades)
    return {
        "symbols": len(results),
        "trades": n,
        "net_pnl": round(gross_pnl, 2),
        "win_rate": round(len(wins) / n, 4) if n else 0.0,
        "avg_pnl": round(gross_pnl / n, 2) if n else 0.0,
        "profit_factor": round(sum(t.pnl for t in wins) / sum(losses), 4) if losses else float("inf"),
        "profitable_symbols": sum(1 for r in results if r.net_pnl > 0),
    }


def main(limit: int | None = None, period: str = "15", length: int = 2000):
    """命令行入口：回测全 TOP40。"""
    symbols = load_top40()
    if limit:
        symbols = symbols[:limit]
    tuples = [(s["symbol"], s["name"], s["exchange"]) for s in symbols]

    print(f"=== future_8 假突破反转系统 回测 ({period}m, {len(tuples)} 品种) ===")
    results = run_many(tuples, period=period, length=length)
    agg = aggregate(results)

    print(f"\n=== 汇总 ===")
    for k, v in agg.items():
        print(f"  {k}: {v}")

    # 明细 CSV
    rows = []
    for r in results:
        s = r.summary()
        s["name"] = ""
        rows.append(s)
    df = pd.DataFrame(rows)
    out = Path(__file__).resolve().parents[1] / "backtest_results.csv"
    df.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"\n明细已写: {out}")
    return results


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=None, help="只回测前 N 个品种")
    p.add_argument("--period", default="15")
    p.add_argument("--length", type=int, default=2000)
    a = p.parse_args()
    main(limit=a.limit, period=a.period, length=a.length)
