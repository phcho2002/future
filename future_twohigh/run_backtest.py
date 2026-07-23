"""CLI: TOP40 全品种 × 周期 回测。

流程：
  1. 逐品种拉数据（get_klines 优先 xtquant，回退 akshare）
  2. 每个品种跑完整信号链路：zigzag → two_high → quality → breakout
  3. 收集所有信号，复用 future_zigzag.backtest 引擎跑独立结算回测
  4. 输出按品种/方向/形态汇总（胜率/PF/avgR）

用法:
    python -m future_twohigh.run_backtest
    python -m future_twohigh.run_backtest --periods 15 30 --depth 1.5
    python -m future_twohigh.run_backtest --period 60 --source trend_rank
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

_SCRIPT_DIR = Path(__file__).resolve().parent
_WORK_AI = _SCRIPT_DIR.parent
sys.path.insert(0, str(_WORK_AI))

from future_data import get_klines  # noqa: E402

from future_zigzag.config import ZigZagConfig  # noqa: E402
from future_zigzag.zigzag import detect_zigzag  # noqa: E402

from future_twohigh.config import (  # noqa: E402
    TwoHighConfig, ConsolidationConfig, BreakoutConfig, BacktestConfig,
)
from future_twohigh.pattern import detect_two_high  # noqa: E402
from future_twohigh.quality import evaluate_quality  # noqa: E402
from future_twohigh.signals import detect_breakout  # noqa: E402
from future_twohigh.backtest import run_backtest, BacktestResult  # noqa: E402

from future_twohigh.scan import load_symbols  # noqa: E402

OUTPUT_DIR = _SCRIPT_DIR / "output"
MIN_BARS = 800
FETCH_LENGTH = 3000


def fetch_batch(symbols, period, length=FETCH_LENGTH):
    """逐品种拉数据（get_klines 优先 xtquant，回退 akshare）。"""
    print(f"  逐品种拉取 {len(symbols)}品种 period={period} length={length}...", flush=True)
    t0 = time.time()
    out = {}
    for i, (sym, name, exch) in enumerate(symbols):
        try:
            df = get_klines(sym, exch, period=period, length=length, force=True)
            if df is not None and not df.empty:
                out[sym] = df
        except Exception as e:
            print(f"    {sym} 失败: {e}")
        if (i + 1) % 10 == 0:
            print(f"    进度 {i+1}/{len(symbols)} ({time.time()-t0:.0f}s)", flush=True)
    print(f"  拿到 {len(out)}/{len(symbols)} 个品种, 耗时 {time.time()-t0:.0f}s")
    return out


def collect_signals(df_dict, depth=1.5, min_quality=0.40):
    """从 {symbol: DataFrame} 收集信号。纯计算，无网络。

    返回 (signals, stats)：signals = [(sym, sig, df, atr)]，stats 按品种统计。
    """
    zcfg = ZigZagConfig(depth_atr_multiple=depth, min_bars_between_pivots=3)
    tcfg = TwoHighConfig(zigzag=zcfg)
    ccfg = ConsolidationConfig(consolidation_threshold=min_quality)
    scfg = BreakoutConfig()
    signals = []
    stats = []
    for sym, df in df_dict.items():
        if df is None or df.empty or len(df) < MIN_BARS:
            stats.append({"symbol": sym, "bars": len(df) if df is not None else 0, "signals": 0,
                          "long": 0, "short": 0})
            continue
        df = df.tail(min(len(df), FETCH_LENGTH)).reset_index(drop=True)
        zz = detect_zigzag(df, zcfg)
        pats = detect_two_high(zz, tcfg)
        atr = zz.atr
        n_sig = n_long = n_short = 0
        for p in pats:
            cr = evaluate_quality(p, df, ccfg)
            if not cr.passed:
                continue
            sig = detect_breakout(p, df, atr, scfg)
            if sig is not None:
                signals.append((sym, sig, df, atr))
                n_sig += 1
                if sig.side == "long":
                    n_long += 1
                else:
                    n_short += 1
        stats.append({"symbol": sym, "bars": len(df), "signals": n_sig,
                      "long": n_long, "short": n_short})
    return signals, stats


def run_period(df_dict, period_label, depth=1.5, min_quality=0.40):
    """跑单周期回测，返回 (BacktestResult, stats)。"""
    signals, stats = collect_signals(df_dict, depth, min_quality)
    n_bars_ok = sum(1 for s in stats if s["bars"] >= MIN_BARS)
    n_sigs = sum(s["signals"] for s in stats)
    print(f"  [{period_label}] 有效品种={n_bars_ok}/{len(stats)} 信号={n_sigs}")

    cfg = BacktestConfig(stop_mode="structure")
    res = run_backtest(signals, cfg)
    return res, stats


def main(argv=None):
    import argparse
    p = argparse.ArgumentParser(description="两高两低突破 TOP40 全品种回测")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--periods", nargs="+", default=["15", "30"])
    g.add_argument("--period", default=None, help="单周期（覆盖 --periods）")
    p.add_argument("--source", default="top40", choices=["top40", "trend_rank"])
    p.add_argument("--symbols", nargs="*", default=None)
    p.add_argument("--depth", type=float, default=1.5)
    p.add_argument("--quality-min", type=float, default=0.40)
    a = p.parse_args(argv)

    periods = [a.period] if a.period else a.periods
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    symbols = load_symbols(a.symbols, source=a.source)
    print("=" * 92)
    print(f"  两高两低突破回测  品种={len(symbols)}  周期={periods}  "
          f"depth={a.depth}×ATR  质量门槛={a.quality_min}")
    print("=" * 92)

    results = {}
    all_stats = {}
    for period in periods:
        plabel = f"{period}min"
        print(f"\n{'─'*92}\n  周期 {plabel}\n{'─'*92}")
        df_dict = fetch_batch(symbols, period)
        res, stats = run_period(df_dict, plabel, a.depth, a.quality_min)
        results[plabel] = res
        all_stats[plabel] = stats
        s = res.summary()
        print(f"  总交易={s['n']}  胜率={s['win_rate']:.1%}  avgR={s['avg_r']:+.3f}  "
              f"PF={s['profit_factor']:.2f}  持仓={s['avg_hold']:.0f}根")

    # ── 周期对比 ──
    print(f"\n{'='*92}\n  周期对比总览\n{'='*92}")
    cmp_rows = []
    for plabel, res in results.items():
        s = res.summary()
        exits = pd.Series([t.exit_reason for t in res.trades]).value_counts(normalize=True).to_dict()
        cmp_rows.append({
            "period": plabel, "n_trades": s["n"], "win_rate": s["win_rate"],
            "avg_r": s["avg_r"], "PF": s["profit_factor"], "avg_hold": s["avg_hold"],
            "stop%": round(exits.get("stop", 0), 3), "target%": round(exits.get("target", 0), 3),
        })
    df_cmp = pd.DataFrame(cmp_rows)
    print(df_cmp.to_string(index=False))
    df_cmp.to_csv(OUTPUT_DIR / "twohigh_period_compare.csv", index=False, encoding="utf-8-sig")

    _breakdown_by_dim(results, "symbol", "品种")
    _breakdown_by_dim(results, "side", "方向")

    # 逐笔明细
    for plabel, res in results.items():
        trades_df = pd.DataFrame([{
            "period": plabel, "symbol": t.symbol, "side": t.side,
            "entry": t.entry_price, "exit": t.exit_price, "reason": t.exit_reason,
            "hold": t.hold_bars, "net_pnl": t.net_pnl, "r_multiple": t.r_multiple,
        } for t in res.trades])
        trades_df.to_csv(OUTPUT_DIR / f"twohigh_{plabel}_trades.csv",
                         index=False, encoding="utf-8-sig")

    print(f"\n  输出已存 output/twohigh_*.csv")
    return 0


def _breakdown_by_dim(results, dim_key, dim_label):
    """按品种/方向拆分统计。dim_key: 'symbol' | 'side'。"""
    attr = "symbol" if dim_key == "symbol" else "side"
    for plabel, res in results.items():
        print(f"\n  ── {plabel} 按{dim_label} ──")
        groups = {}
        for t in res.trades:
            groups.setdefault(getattr(t, attr), []).append(t)
        rows = []
        for key, trades in sorted(groups.items()):
            sub = BacktestResult(trades=trades)
            ss = sub.summary()
            rows.append({dim_label: key, "n": ss["n"], "win_rate": ss["win_rate"],
                         "avg_r": ss["avg_r"], "PF": ss["profit_factor"]})
        rows.sort(key=lambda r: r["avg_r"], reverse=True)
        for r in rows:
            print(f"    {r[dim_label]:<6} n={r['n']:<4} 胜率={r['win_rate']:.1%} "
                  f"avgR={r['avg_r']:+.3f} PF={r['PF']:.2f}")
        pd.DataFrame(rows).to_csv(OUTPUT_DIR / f"twohigh_{plabel}_by_{dim_key}.csv",
                                  index=False, encoding="utf-8-sig")


if __name__ == "__main__":
    sys.exit(main())
