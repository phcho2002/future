#!/usr/bin/env python3
"""单周期60m vs MTF(60m+15m) 公平对比回测。

同品种、同资金管理(500万/8%开仓)、同数据源，对比两套信号路径。
单周期60m：Backtester + generate_signal（假突破 Spring/Upthrust）
MTF：MTFBacktester + generate_mtf_signal（60m关键位 + 15m形态）
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

from fakebreak.config import FakeBreakConfig  # noqa: E402
from backtest.backtester import Backtester, BacktestConfig, DEFAULT_MULTIPLIERS  # noqa: E402
from backtest.mtf_backtester import MTFBacktester  # noqa: E402

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
LENGTH = 2000
TTL = 9999
WARMUP = 200


def run_one(sym, ex, mult, sc):
    """拉一次 15m 数据，单周期60m 和 MTF 各跑一遍。"""
    df_15m = get_klines(sym, ex, period="15", length=LENGTH, ttl_hours=TTL)
    if df_15m is None or df_15m.empty or len(df_15m) < WARMUP + 10:
        return None, None
    df_15m = df_15m.reset_index(drop=True).copy()
    df_15m["datetime"] = pd.to_datetime(df_15m["datetime"])

    bc = replace(BacktestConfig(), multiplier=mult, warmup=WARMUP)

    # 单周期60m：从 15m 聚合出 60m 喂给 Backtester
    df_60m = df_15m.copy()
    df_60m["_h"] = df_60m["datetime"].dt.floor("h")
    df_60m = df_60m.groupby("_h").agg(
        open=("open", "first"), high=("high", "max"), low=("low", "min"),
        close=("close", "last"), volume=("volume", "sum"),
    ).reset_index().rename(columns={"_h": "datetime"})

    bt_single = Backtester(sc, bc)
    r_single = bt_single.run(df_60m, symbol=sym)

    # MTF：15m 主时间轴，内部聚合 60m
    bt_mtf = MTFBacktester(sc, bc)
    r_mtf = bt_mtf.run(df_15m, symbol=sym)

    return r_single, r_mtf


def main():
    sc = FakeBreakConfig()
    print("=" * 90)
    print("  单周期60m (假突破) vs MTF (60m关键位+15m形态) — 公平对比")
    print(f"  品种: {len(TARGETS)} | 15m长度: {LENGTH} | 初始资金: 500万 | 每次开仓: 8%")
    print("=" * 90)

    rows = []
    agg_s = {"trades": 0, "pnl": 0.0, "wins": 0, "gross_win": 0.0, "gross_loss": 0.0, "profit": 0}
    agg_m = {"trades": 0, "pnl": 0.0, "wins": 0, "gross_win": 0.0, "gross_loss": 0.0, "profit": 0}

    for i, (sym, name, ex) in enumerate(TARGETS, 1):
        mult = DEFAULT_MULTIPLIERS.get(sym, 10.0)
        print(f"[{i}/{len(TARGETS)}] {sym} {name} ...", end=" ", flush=True)
        try:
            r_s, r_m = run_one(sym, ex, mult, sc)
            if r_s is None:
                print("数据不足")
                continue

            # 单周期统计
            for t in r_s.trades:
                agg_s["trades"] += 1
                agg_s["pnl"] += t.pnl
                if t.pnl > 0:
                    agg_s["wins"] += 1
                    agg_s["gross_win"] += t.pnl
                else:
                    agg_s["gross_loss"] += abs(t.pnl)
            if r_s.net_pnl > 0:
                agg_s["profit"] += 1

            # MTF 统计
            for t in r_m.trades:
                agg_m["trades"] += 1
                agg_m["pnl"] += t.pnl
                if t.pnl > 0:
                    agg_m["wins"] += 1
                    agg_m["gross_win"] += t.pnl
                else:
                    agg_m["gross_loss"] += abs(t.pnl)
            if r_m.net_pnl > 0:
                agg_m["profit"] += 1

            rows.append({
                "symbol": sym,
                "s_trades": r_s.n_trades, "s_pnl": round(r_s.net_pnl, 0),
                "s_win": f"{r_s.win_rate:.1%}", "s_pf": round(r_s.profit_factor, 2),
                "s_dd": f"{r_s.max_drawdown:.1%}",
                "m_trades": r_m.n_trades, "m_pnl": round(r_m.net_pnl, 0),
                "m_win": f"{r_m.win_rate:.1%}", "m_pf": round(r_m.profit_factor, 2),
                "m_dd": f"{r_m.max_drawdown:.1%}",
                "diff_pnl": round(r_m.net_pnl - r_s.net_pnl, 0),
            })
            print(f"单60m: {r_s.n_trades}笔 {r_s.net_pnl:>+10,.0f} pf={r_s.profit_factor:.2f}  |  "
                  f"MTF: {r_m.n_trades}笔 {r_m.net_pnl:>+10,.0f} pf={r_m.profit_factor:.2f}")
        except Exception as e:  # noqa: BLE001
            print(f"ERR {str(e)[:80]}")

    print("\n" + "=" * 90)
    print("  【逐品种对比】")
    print("=" * 90)
    df = pd.DataFrame(rows)
    print(df.to_string(index=False))

    print(f"\n{'='*90}")
    print(f"  【汇总对比】")
    print(f"{'='*90}")
    s_pf = agg_s["gross_win"] / agg_s["gross_loss"] if agg_s["gross_loss"] else float("inf")
    m_pf = agg_m["gross_win"] / agg_m["gross_loss"] if agg_m["gross_loss"] else float("inf")
    s_wr = agg_s["wins"] / agg_s["trades"] if agg_s["trades"] else 0
    m_wr = agg_m["wins"] / agg_m["trades"] if agg_m["trades"] else 0
    s_avg = agg_s["pnl"] / agg_s["trades"] if agg_s["trades"] else 0
    m_avg = agg_m["pnl"] / agg_m["trades"] if agg_m["trades"] else 0

    print(f"{'指标':<16} {'单周期60m':>16} {'MTF(60+15)':>16} {'差异':>16}")
    print(f"{'-'*64}")
    print(f"{'总交易数':<16} {agg_s['trades']:>16} {agg_m['trades']:>16} {agg_m['trades']-agg_s['trades']:>+16}")
    print(f"{'净盈亏':<16} {agg_s['pnl']:>16,.0f} {agg_m['pnl']:>16,.0f} {agg_m['pnl']-agg_s['pnl']:>+16,.0f}")
    print(f"{'胜率':<16} {s_wr:>16.1%} {m_wr:>16.1%} {m_wr-s_wr:>+16.1%}")
    print(f"{'盈亏比PF':<16} {s_pf:>16.2f} {m_pf:>16.2f} {m_pf-s_pf:>+16.2f}")
    print(f"{'单笔均盈亏':<16} {s_avg:>16,.0f} {m_avg:>16,.0f} {m_avg-s_avg:>+16,.0f}")
    print(f"{'盈利品种':<16} {agg_s['profit']:>16} {agg_m['profit']:>16} {agg_m['profit']-agg_s['profit']:>+16}")

    out = _HERE / "backtest_compare_mtf.csv"
    df.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"\n明细已写: {out}")


if __name__ == "__main__":
    main()
