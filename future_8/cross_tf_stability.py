"""跨周期稳定性对比 —— 在 30m/60m/日线 上跑同一套规则，找多周期都盈利的品种。

用法：
    python cross_tf_stability.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from dataclasses import replace

_HERE = Path(__file__).resolve().parent
_WORK_AI = _HERE.parent
for _p in (str(_WORK_AI), str(_HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import numpy as np
import pandas as pd

from backtest.backtester import Backtester, BacktestConfig, DEFAULT_MULTIPLIERS
from fakebreak.config import load_config

CACHE = _WORK_AI / "quote_cache"

# 60m 波动率前12（含 IC0）
POOL = ["LC0", "MA0", "TA0", "EB0", "AG0", "JM0", "IC0", "PP0", "SC0", "SN0", "IM0", "L0"]
NAMES = {
    "LC0": "碳酸锂", "MA0": "甲醇", "TA0": "PTA", "EB0": "苯乙烯", "AG0": "白银",
    "JM0": "焦煤", "IC0": "中证500", "PP0": "聚丙烯", "SC0": "原油", "SN0": "锡",
    "IM0": "中证1000", "L0": "塑料",
}
# 缓存文件后缀约定
TF_SUFFIX = {"30m": "30m", "60m": "60m", "日线": "1440m"}


def run_one_tf(symbols: list[str], tf: str, sc) -> dict[str, dict]:
    """单周期：返回 {symbol: {trades, pnl, pf, wr, dd, bars}}。"""
    suffix = TF_SUFFIX[tf]
    out = {}
    for sym in symbols:
        p = CACHE / f"{sym}_{suffix}.parquet"
        if not p.exists():
            continue
        df = pd.read_parquet(p)
        if len(df) < 150:
            continue
        bt_cfg = replace(BacktestConfig(), multiplier=DEFAULT_MULTIPLIERS.get(sym, 10.0))
        bt = Backtester(sc, bt_cfg)
        r = bt.run(df, sym)
        out[sym] = {
            "trades": r.n_trades,
            "pnl": r.net_pnl,
            "pf": r.profit_factor if r.n_trades else 0.0,
            "wr": r.win_rate if r.n_trades else 0.0,
            "dd": r.max_drawdown,
            "bars": len(df),
        }
    return out


def main():
    sc = load_config()
    timeframes = ["30m", "60m", "日线"]

    print(f"{'='*90}")
    print(f"  跨周期稳定性对比（{len(POOL)}品种 × {len(timeframes)}周期）")
    print(f"  规则：500万/8%仓位/加仓1/2/跟踪止损1.2ATR/硬止损0.9%/无滑点")
    print(f"{'='*90}\n")

    results: dict[str, dict[str, dict]] = {}
    for tf in timeframes:
        t0 = time.time()
        results[tf] = run_one_tf(POOL, tf, sc)
        print(f"  {tf} 完成 ({time.time()-t0:.0f}s)\n")

    # ── 跨周期对比表 ──
    print(f"{'品种':<6}{'名称':<10}", end="")
    for tf in timeframes:
        print(f"| {tf:>10} {'PF':>5} {'胜率':>4} ", end="")
    print("| 盈利周期数")
    print("-" * 100)

    candidate_rows = []
    for sym in POOL:
        name = NAMES.get(sym, "")
        print(f"{sym:<6}{name:<10}", end="")
        win_count = 0
        row = {"symbol": sym, "name": name}
        for tf in timeframes:
            d = results[tf].get(sym)
            if d is None:
                print(f"| {'无数据':>18} ", end="")
                row[f"{tf}_pnl"] = None
                row[f"{tf}_pf"] = None
                continue
            pnl = d["pnl"]
            pf = d["pf"]
            wr = d["wr"]
            tag = "★" if pnl > 0 else " "
            print(f"| {pnl:>9,.0f}{tag} {pf:>5.2f} {wr:>3.0%} ", end="")
            row[f"{tf}_pnl"] = pnl
            row[f"{tf}_pf"] = pf
            row[f"{tf}_wr"] = wr
            row[f"{tf}_dd"] = d["dd"]
            row[f"{tf}_trades"] = d["trades"]
            if pnl > 0:
                win_count += 1
        row["win_count"] = win_count
        candidate_rows.append(row)
        print(f"|  {win_count}/{len(timeframes)}")

    # ── 候选池：≥2个周期盈利 ──
    print(f"\n{'='*90}")
    print(f"  候选池（≥2个周期盈利）")
    print(f"{'='*90}")
    candidates = [r for r in candidate_rows if r["win_count"] >= 2]
    candidates.sort(key=lambda x: (-x["win_count"], -sum(x[f"{tf}_pnl"] for tf in timeframes if x.get(f"{tf}_pnl"))))
    if candidates:
        print(f"{'品种':<6}{'名称':<10}{'盈利周期':>8}{'30m':>12}{'60m':>12}{'日线':>12}{'合计盈亏':>12}")
        for r in candidates:
            total = sum(r[f"{tf}_pnl"] for tf in timeframes if r.get(f"{tf}_pnl"))
            p30 = f"{r['30m_pnl']:+,.0f}" if r.get("30m_pnl") is not None else "-"
            p60 = f"{r['60m_pnl']:+,.0f}" if r.get("60m_pnl") is not None else "-"
            pd1 = f"{r['日线_pnl']:+,.0f}" if r.get("日线_pnl") is not None else "-"
            print(f"{r['symbol']:<6}{r['name']:<10}{r['win_count']}/{len(timeframes):>5}{p30:>12}{p60:>12}{pd1:>12}{total:>12,.0f}")

    # 全周期盈利
    all3 = [r for r in candidate_rows if r["win_count"] == 3]
    print(f"\n  全周期盈利({len(all3)}): {[r['symbol'] for r in all3]}")
    print(f"  ≥2周期盈利({len(candidates)}): {[r['symbol'] for r in candidates]}")

    # 写 CSV
    out_path = _HERE / "cross_tf_stability.csv"
    pd.DataFrame(candidate_rows).to_csv(out_path, index=False, encoding="utf-8-sig")
    print(f"\n  明细已写: {out_path}")


if __name__ == "__main__":
    main()
