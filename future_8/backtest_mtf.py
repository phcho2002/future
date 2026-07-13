#!/usr/bin/env python3
"""future_8 MTF 回测入口 — 60m关键位 + 15m形态入场。

用 future_data (xtquant) 拉 15m 深历史，内部聚合 60m，跑 MTFBacktester。
资金管理沿用单周期那套：500万初始，每次8%开仓，加仓+跟踪止损+硬止损。

用法：
    python backtest_mtf.py
"""
from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import pandas as pd

_HERE = Path(__file__).resolve().parent
_WORK_AI = _HERE.parent
for _p in (str(_WORK_AI), str(_HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from future_data import get_klines  # noqa: E402

from fakebreak.config import load_config  # noqa: E402
from backtest.backtester import BacktestConfig, BacktestResult, DEFAULT_MULTIPLIERS  # noqa: E402
from backtest.mtf_backtester import MTFBacktester  # noqa: E402

# ── 目标品种 ──
TARGETS = [
    ("PP0", "聚丙烯", "dce"),
    ("IM0", "中证1000", "cffex"),
    ("LC0", "碳酸锂", "gfex"),
    ("JM0", "焦煤", "dce"),
    ("SC0", "原油", "ine"),
    ("IC0", "中证500", "cffex"),
    ("OI0", "菜油", "czce"),
    ("AG0", "白银", "shfe"),
]

LENGTH = 2000          # 15m 拉取根数
TTL = 9999             # 纯读缓存（盘后）
WARMUP = 200           # 预热根数（15m）


def main():
    sc = load_config()
    print("=" * 80)
    print(f"  future_8 MTF 回测 — 60m关键位 + 15m形态入场")
    print(f"  品种: {len(TARGETS)} 个 | 15m长度: {LENGTH} | 初始资金: 500万 | 每次开仓: 8%")
    print(f"  参数: proximity={sc.proximity_atr}ATR engulf>={sc.engulf_min_body_atr}ATR merge_n={sc.merge_bars_n} target={sc.target_atr}ATR")
    print("=" * 80)

    results: list[BacktestResult] = []
    for i, (sym, name, ex) in enumerate(TARGETS, 1):
        mult = DEFAULT_MULTIPLIERS.get(sym, 10.0)
        bc = replace(BacktestConfig(multiplier=mult), multiplier=mult, warmup=WARMUP)

        print(f"[{i}/{len(TARGETS)}] {sym} {name} (x{mult}) ...", end=" ", flush=True)
        try:
            df_15m = get_klines(sym, ex, period="15", length=LENGTH, ttl_hours=TTL)
            if df_15m is None or df_15m.empty or len(df_15m) < WARMUP + 10:
                print(f"数据不足 ({len(df_15m) if df_15m is not None else 0})")
                results.append(BacktestResult(symbol=sym))
                continue

            bt = MTFBacktester(sc, bc)
            r = bt.run(df_15m, symbol=sym)
            results.append(r)
            s = r.summary()
            print(f"trades={s['trades']:4d}  pnl={s['net_pnl']:>10,.0f}  "
                  f"win={s['win_rate']:.1%}  pf={s['profit_factor']:.2f}  dd={s['max_drawdown']:.1%}")
        except Exception as e:  # noqa: BLE001
            print(f"ERR {str(e)[:80]}")
            results.append(BacktestResult(symbol=sym))

    # ── 汇总 ──
    print("\n" + "=" * 80)
    print("  【汇总】")
    print("=" * 80)
    all_trades = [t for r in results for t in r.trades]
    n = len(all_trades)
    wins = [t for t in all_trades if t.pnl > 0]
    losses = [abs(t.pnl) for t in all_trades if t.pnl <= 0]
    gross = sum(t.pnl for t in all_trades)
    pf = (sum(t.pnl for t in wins) / sum(losses)) if losses else float("inf")
    print(f"  品种数:   {len(results)}")
    print(f"  总交易数: {n}")
    print(f"  净盈亏:   {gross:,.0f}")
    print(f"  胜率:     {len(wins)/n:.1%}" if n else "  胜率:     -")
    print(f"  盈亏比PF: {pf:.2f}")
    print(f"  盈利品种: {sum(1 for r in results if r.net_pnl > 0)}/{len(results)}")

    # 明细表
    rows = []
    for r in results:
        s = r.summary()
        wins_t = [t for t in r.trades if t.pnl > 0]
        losses_t = [t for t in r.trades if t.pnl <= 0]
        avg_win = sum(t.pnl for t in wins_t) / len(wins_t) if wins_t else 0
        avg_loss = abs(sum(t.pnl for t in losses_t) / len(losses_t)) if losses_t else 0
        rr = avg_win / avg_loss if avg_loss > 0 else 0
        rows.append({
            "symbol": s["symbol"], "trades": s["trades"],
            "net_pnl": s["net_pnl"], "win_rate": s["win_rate"],
            "profit_factor": s["profit_factor"], "max_drawdown": s["max_drawdown"],
            "sharpe": s["sharpe"],
            "avg_win": round(avg_win, 0), "avg_loss": round(avg_loss, 0),
            "rr": round(rr, 2),
        })
    df = pd.DataFrame(rows)
    print(f"\n{df.to_string(index=False)}")

    out = _HERE / "backtest_mtf_results.csv"
    df.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"\n明细已写: {out}")


if __name__ == "__main__":
    main()
