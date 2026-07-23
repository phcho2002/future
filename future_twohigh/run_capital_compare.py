"""CLI: 资金回测对比 —— futures_trend_rank 品种 × 周期(15/30/60min) × 资金(15K/30K/60K)。

对每个周期拉数据、跑完整信号链路、用带入初始资金的方式逐根盯市回测，
对比 15K / 30K / 60K 哪个效果好。回答"给定一笔钱，这套策略能赚多少/亏多少"。

与 run_backtest.py（R 结算）的区别：本脚本模拟真实账户——按风险预算定手数、
持仓占用资金、逐根按 OHLC 触发止损/目标、记录权益曲线与最大回撤。

用法:
    python -m future_twohigh.run_capital_compare
    python -m future_twohigh.run_capital_compare --periods 15 30 60 --capitals 15000 30000 60000
    python -m future_twohigh.run_capital_compare --period 30 --capitals 30000 60000
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
    TwoHighConfig, ConsolidationConfig, BreakoutConfig, DEFAULT_MULTIPLIERS,
)
from future_twohigh.pattern import detect_two_high  # noqa: E402
from future_twohigh.quality import evaluate_quality  # noqa: E402
from future_twohigh.signals import detect_breakout  # noqa: E402
from future_twohigh.capital_backtest import simulate_capital, CapitalResult  # noqa: E402
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
    """跑信号链路，返回 {symbol: list[BreakoutSignal]}。"""
    zcfg = ZigZagConfig(depth_atr_multiple=depth, min_bars_between_pivots=3)
    tcfg = TwoHighConfig(zigzag=zcfg)
    ccfg = ConsolidationConfig(consolidation_threshold=min_quality)
    scfg = BreakoutConfig()
    signals: dict[str, list] = {}
    n_total = 0
    for sym, df in df_dict.items():
        if df is None or df.empty or len(df) < MIN_BARS:
            continue
        df = df.tail(min(len(df), FETCH_LENGTH)).reset_index(drop=True)
        df_dict[sym] = df   # 回写截断后的 df（保证 capital 回测与信号同源）
        zz = detect_zigzag(df, zcfg)
        atr = zz.atr
        sigs = []
        for p in detect_two_high(zz, tcfg):
            cr = evaluate_quality(p, df, ccfg)
            if not cr.passed:
                continue
            sig = detect_breakout(p, df, atr, scfg)
            if sig is not None:
                sigs.append(sig)
        if sigs:
            signals[sym] = sigs
            n_total += len(sigs)
    print(f"    信号总数: {n_total}  有信号品种: {len(signals)}")
    return signals


def main(argv=None):
    import argparse
    p = argparse.ArgumentParser(description="两高两低突破 资金回测对比 15K/30K/60K")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--periods", nargs="+", default=["15", "30", "60"])
    g.add_argument("--period", default=None, help="单周期（覆盖 --periods）")
    p.add_argument("--source", default="top20", choices=["top20", "trend_rank", "top40"],
                   help="品种源（默认 top20 = future_trend_rank.json 的 top 20个高动量品种）")
    p.add_argument("--symbols", nargs="*", default=None)
    p.add_argument("--capitals", nargs="+", type=float, default=[15000, 30000, 60000],
                   help="初始资金对比档（元）")
    p.add_argument("--depth", type=float, default=1.5)
    p.add_argument("--quality-min", type=float, default=0.40)
    p.add_argument("--risk-pct", type=float, default=0.02, help="每笔最大亏损=资金×此值")
    p.add_argument("--max-pct", type=float, default=0.50, help="单笔名义敞口上限=资金×此值")
    p.add_argument("--max-hold", type=int, default=80)
    p.add_argument("--cooldown", type=int, default=10)
    p.add_argument("--min-qty", type=int, default=1,
                   help="每笔最小手数（默认1，期货最小1手；设0则纯风险预算，小资金会大量跳过）")
    a = p.parse_args(argv)

    periods = [a.period] if a.period else a.periods
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    symbols = load_symbols(a.symbols, source=a.source)

    print("=" * 96)
    print(f"  资金回测对比  品种源={a.source}({len(symbols)}品种)  周期={periods}min  "
          f"资金档={a.capitals}  risk={a.risk_pct}  max_pct={a.max_pct}")
    print("=" * 96)

    all_rows = []          # 汇总表行
    equity_dfs = {}        # (period, capital) -> equity curve DataFrame

    for period in periods:
        plabel = f"{period}min"
        print(f"\n{'─'*96}\n  周期 {plabel}\n{'─'*96}")
        df_dict = fetch_batch(symbols, period)
        signals = collect_signals(df_dict, a.depth, a.quality_min)

        mult = {sym: DEFAULT_MULTIPLIERS.get(sym, 10.0) for sym in df_dict}

        for cap in a.capitals:
            res = simulate_capital(
                df_dict, signals,
                initial_capital=cap,
                risk_pct=a.risk_pct, max_pct=a.max_pct,
                commission_rate=0.00005, slippage_points=1.0,
                max_hold=a.max_hold, cooldown_bars=a.cooldown,
                target_rr=2.0, multipliers=mult, min_qty=a.min_qty,
            )
            s = res.summary()
            print(f"  资金={int(cap):>6}  终值={s['final_equity']:>8}  "
                  f"收益={s['return_pct']:+.2%}  交易={s['trades']:>4}  "
                  f"胜率={s['win_rate']:.1%}  PF={s['profit_factor']:.2f}  "
                  f"最大回撤={s['max_drawdown']:.1%}  跳过(资金不足)={s['skipped_low_qty']}")
            all_rows.append({
                "period": plabel, "initial_capital": int(cap),
                "final_equity": s["final_equity"], "net_profit": s["net_profit"],
                "return_pct": s["return_pct"], "trades": s["trades"],
                "win_rate": s["win_rate"], "profit_factor": s["profit_factor"],
                "max_drawdown": s["max_drawdown"], "avg_hold": s["avg_hold"],
                "skipped_low_qty": s["skipped_low_qty"],
                "skipped_cooldown": s["skipped_cooldown"],
            })
            equity_dfs[(plabel, int(cap))] = pd.DataFrame(res.equity_curve)

    # ── 汇总对比表 ──
    print(f"\n{'='*96}\n  资金对比汇总（按周期×资金档）\n{'='*96}")
    df_sum = pd.DataFrame(all_rows)
    print(df_sum.to_string(index=False))
    df_sum.to_csv(OUTPUT_DIR / "capital_compare_summary.csv", index=False, encoding="utf-8-sig")

    # ── 哪个最好？按收益率 ranking ──
    best = df_sum.loc[df_sum["return_pct"].idxmax()]
    print(f"\n  ★ 收益率最高: 周期={best['period']} 资金={int(best['initial_capital'])} "
          f"收益={best['return_pct']:+.2%} PF={best['profit_factor']:.2f} 回撤={best['max_drawdown']:.1%}")
    best_pf = df_sum.loc[df_sum["profit_factor"].idxmax()]
    print(f"  ★ 盈亏比最优: 周期={best_pf['period']} 资金={int(best_pf['initial_capital'])} "
          f"PF={best_pf['profit_factor']:.2f} 收益={best_pf['return_pct']:+.2%}")

    # ── 各周期内资金档对比（收益率随资金变化）──
    print(f"\n  按周期看资金档（收益率/回撤随资金变化）:")
    for period in periods:
        plabel = f"{period}min"
        sub = df_sum[df_sum["period"] == plabel].sort_values("initial_capital")
        if sub.empty:
            continue
        line_ret = "  ".join(
            f"{int(r['initial_capital'])//1000}K:{r['return_pct']:+.1%}"
            for _, r in sub.iterrows())
        line_mdd = "  ".join(
            f"{int(r['initial_capital'])//1000}K:{r['max_drawdown']:.1%}"
            for _, r in sub.iterrows())
        line_pf = "  ".join(
            f"{int(r['initial_capital'])//1000}K:{r['profit_factor']:.2f}"
            for _, r in sub.iterrows())
        print(f"    {plabel:7} 收益[{line_ret}]")
        print(f"    {'':7} 回撤[{line_mdd}]")
        print(f"    {'':7} 盈亏比[{line_pf}]")

    # ── 存权益曲线 ──
    for (plabel, cap), edf in equity_dfs.items():
        edf.to_csv(OUTPUT_DIR / f"equity_{plabel}_{cap//1000}k.csv",
                   index=False, encoding="utf-8-sig")

    print(f"\n  输出已存 output/capital_compare_summary.csv + equity_*.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
