"""CLI: TOP40 全品种 × 双周期（15min / 30min）回测对比。

流程：
  1. fetch_many 单连接批量拉 40 品种 × 2 周期数据（比逐个快很多）
  2. 缓存到 parquet，回测阶段纯读盘无网络
  3. 每个周期跑全回测，输出按品种/信号类型/周期对比

用法:
    python -m future_zigzag.run_top40
    python -m future_zigzag.run_top40 --periods 15 30 --depth 1.5
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

from future_zigzag.config import (  # noqa: E402
    ZigZagConfig, ThreePushConfig, ContractionConfig, SignalConfig, BacktestConfig,
)
from future_zigzag.zigzag import detect_zigzag  # noqa: E402
from future_zigzag.three_push import detect_three_push  # noqa: E402
from future_zigzag.contraction import evaluate_contraction  # noqa: E402
from future_zigzag.signals import best_signal  # noqa: E402
from future_zigzag.backtest import run_backtest, BacktestResult  # noqa: E402

OUTPUT_DIR = _SCRIPT_DIR / "output"
TOP40_JSON = _WORK_AI / "futures_top40.json"
MIN_BARS = 800           # 统计所需最低根数（xtquant 端口冲突时回退 akshare 约 1023 根）
FETCH_LENGTH = 3000      # 尝试拉取根数


def load_top40() -> list[tuple[str, str, str]]:
    with open(TOP40_JSON, encoding="utf-8") as f:
        d = json.load(f)
    return [(s[0], s[1], s[2]) for s in d["symbols"]]


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


def collect_signals_from_df(df_dict, depth=1.5, min_score=0.45):
    """从 {symbol: DataFrame} 收集去重信号。纯计算，无网络。"""
    zcfg = ZigZagConfig(depth_atr_multiple=depth)
    tcfg = ThreePushConfig(zigzag=zcfg, valid_score_threshold=min_score)
    ccfg = ContractionConfig()
    scfg = SignalConfig()
    signals = []
    stats = []
    for sym, df in df_dict.items():
        if df is None or df.empty or len(df) < MIN_BARS:
            stats.append({"symbol": sym, "bars": len(df) if df is not None else 0, "signals": 0})
            continue
        df = df.tail(min(len(df), FETCH_LENGTH)).reset_index(drop=True)
        zz = detect_zigzag(df, zcfg)
        pats = detect_three_push(zz, tcfg)
        valid = [p for p in pats if p.score.hard_gate_passed and p.score.total >= min_score]
        atr = zz.atr
        n_sig = 0
        for p in valid:
            cr = evaluate_contraction(p, atr, len(df), ccfg)
            if not cr.passed:
                continue
            sig = best_signal(p, df, atr, scfg)
            if sig is not None:
                signals.append((sym, sig, df, atr))
                n_sig += 1
        stats.append({"symbol": sym, "bars": len(df), "signals": n_sig})
    return signals, stats


def run_period(df_dict, period_label, depth=1.5, min_score=0.45):
    """跑单周期回测，返回 (BacktestResult, stats)。"""
    signals, stats = collect_signals_from_df(df_dict, depth, min_score)
    n_bars_ok = sum(1 for s in stats if s["bars"] >= MIN_BARS)
    n_sigs = sum(s["signals"] for s in stats)
    print(f"  [{period_label}] 有效品种={n_bars_ok}/{len(stats)} 信号={n_sigs}")

    cfg = BacktestConfig(stop_mode="structure")   # 结构止损 P5+1.5ATR（经验证最优）
    res = run_backtest(signals, cfg)
    return res, stats


def main(argv=None):
    import argparse
    p = argparse.ArgumentParser(description="TOP40 全品种双周期回测")
    p.add_argument("--periods", nargs="+", default=["15", "30"])
    p.add_argument("--depth", type=float, default=1.5)
    p.add_argument("--min-score", type=float, default=0.45)
    a = p.parse_args(argv)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    symbols = load_top40()
    print("=" * 90)
    print(f"  TOP40 全品种双周期回测  品种={len(symbols)}  周期={a.periods}  depth={a.depth}×ATR")
    print("=" * 90)

    results = {}   # period_label -> BacktestResult
    all_stats = {}
    for period in a.periods:
        plabel = f"{period}min"
        print(f"\n{'─'*90}")
        print(f"  周期 {plabel}")
        print(f"{'─'*90}")
        df_dict = fetch_batch(symbols, period)
        res, stats = run_period(df_dict, plabel, a.depth, a.min_score)
        results[plabel] = res
        all_stats[plabel] = stats
        s = res.summary()
        print(f"  总交易={s['n']}  胜率={s['win_rate']:.1%}  avgR={s['avg_r']:+.3f}  "
              f"PF={s['profit_factor']:.2f}  持仓={s['avg_hold']:.0f}根")

    # ── 周期对比 ──
    print(f"\n{'='*90}")
    print("  周期对比总览")
    print(f"{'='*90}")
    cmp_rows = []
    for plabel, res in results.items():
        s = res.summary()
        exits = pd.Series([t.exit_reason for t in res.trades]).value_counts(normalize=True).to_dict()
        cmp_rows.append({
            "period": plabel, "n_trades": s["n"], "win_rate": s["win_rate"],
            "avg_r": s["avg_r"], "PF": s["profit_factor"], "avg_hold": s["avg_hold"],
            "stop%": round(exits.get("stop", 0), 3),
            "target%": round(exits.get("target", 0), 3),
        })
    df_cmp = pd.DataFrame(cmp_rows)
    print(df_cmp.to_string(index=False))
    df_cmp.to_csv(OUTPUT_DIR / "top40_period_compare.csv", index=False, encoding="utf-8-sig")

    # ── 按周期 × 品种明细 ──
    for plabel, res in results.items():
        print(f"\n  ── {plabel} 按品种 ──")
        by_sym = {}
        for t in res.trades:
            by_sym.setdefault(t.symbol, []).append(t)
        sym_rows = []
        for sym in sorted(by_sym):
            sub = BacktestResult(trades=by_sym[sym])
            ss = sub.summary()
            sym_rows.append({"period": plabel, "symbol": sym, "n": ss["n"],
                             "win_rate": ss["win_rate"], "avg_r": ss["avg_r"],
                             "PF": ss["profit_factor"]})
        sym_rows.sort(key=lambda r: r["avg_r"], reverse=True)
        for r in sym_rows:
            print(f"    {r['symbol']:<5} n={r['n']:<4} 胜率={r['win_rate']:.1%} "
                  f"avgR={r['avg_r']:+.3f} PF={r['PF']:.2f}")
        pd.DataFrame(sym_rows).to_csv(OUTPUT_DIR / f"top40_{plabel}_by_symbol.csv",
                                      index=False, encoding="utf-8-sig")

    # ── 按周期 × 信号类型 ──
    for plabel, res in results.items():
        print(f"\n  ── {plabel} 按信号类型 ──")
        type_rows = []
        for stype, trades in res.by_type().items():
            sub = BacktestResult(trades=trades)
            ss = sub.summary()
            exits = pd.Series([t.exit_reason for t in trades]).value_counts(normalize=True).to_dict()
            type_rows.append({"period": plabel, "type": stype, "n": ss["n"],
                              "win_rate": ss["win_rate"], "avg_r": ss["avg_r"],
                              "PF": ss["profit_factor"],
                              "stop%": round(exits.get("stop", 0), 3)})
            print(f"    {stype:<16} n={ss['n']:<4} 胜率={ss['win_rate']:.1%} "
                  f"avgR={ss['avg_r']:+.3f} PF={ss['profit_factor']:.2f}")
        pd.DataFrame(type_rows).to_csv(OUTPUT_DIR / f"top40_{plabel}_by_type.csv",
                                       index=False, encoding="utf-8-sig")

    # ── 逐笔明细 ──
    for plabel, res in results.items():
        trades_df = pd.DataFrame([{
            "period": plabel, "symbol": t.symbol, "type": t.signal_type, "side": t.side,
            "entry": t.entry_price, "exit": t.exit_price, "reason": t.exit_reason,
            "hold": t.hold_bars, "net_pnl": t.net_pnl, "r_multiple": t.r_multiple,
        } for t in res.trades])
        trades_df.to_csv(OUTPUT_DIR / f"top40_{plabel}_trades.csv",
                         index=False, encoding="utf-8-sig")

    print(f"\n  输出已存 output/top40_*.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
