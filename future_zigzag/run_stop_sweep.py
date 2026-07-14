"""CLI: 止损参数扫描 —— 找最优止损方案。

回测暴露止损过松（54% 止损率、R 中位数 -1.006）。本脚本扫描：
  1. stop_base: 'p5'（极值外侧）vs 'p4'（回撤极值外侧，结构止损）
  2. stop_atr_buffer: 0.3 ~ 1.2 多档
每种组合重跑全回测，对比期望/胜率/盈亏比，找最优方案。

用法:
    python -m future_zigzag.run_stop_sweep
"""
from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import pandas as pd

_SCRIPT_DIR = Path(__file__).resolve().parent
_WORK_AI = _SCRIPT_DIR.parent
sys.path.insert(0, str(_WORK_AI))

from future_data import get_klines  # noqa: E402

from future_zigzag.config import (  # noqa: E402
    SYMBOLS, ZigZagConfig, ThreePushConfig, ContractionConfig, SignalConfig,
    BacktestConfig,
)
from future_zigzag.zigzag import detect_zigzag  # noqa: E402
from future_zigzag.three_push import detect_three_push  # noqa: E402
from future_zigzag.contraction import evaluate_contraction  # noqa: E402
from future_zigzag.signals import best_signal  # noqa: E402
from future_zigzag.backtest import run_backtest  # noqa: E402

OUTPUT_DIR = _SCRIPT_DIR / "output"
MIN_BARS = 3000


def collect_raw(depth=1.5, min_score=0.45, period="15", length=MIN_BARS):
    """收集全部 (symbol, pattern, df, atr) —— 信号在每个止损配置下重新生成。"""
    zcfg = ZigZagConfig(depth_atr_multiple=depth)
    tcfg = ThreePushConfig(zigzag=zcfg, valid_score_threshold=min_score)
    ccfg = ContractionConfig()
    raw = []
    for sym, _, exch in SYMBOLS:
        try:
            df = get_klines(sym, exch, period=period, length=length, force=True)
            if df is None or df.empty:
                continue
        except Exception:
            continue
        if len(df) > MIN_BARS:
            df = df.tail(MIN_BARS).reset_index(drop=True)
        zz = detect_zigzag(df, zcfg)
        pats = detect_three_push(zz, tcfg)
        valid = [p for p in pats if p.score.hard_gate_passed and p.score.total >= min_score]
        atr = zz.atr
        for p in valid:
            cr = evaluate_contraction(p, atr, len(df), ccfg)
            if cr.passed:
                raw.append((sym, p, df, atr))
    return raw


def run_sweep():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print("=" * 92)
    print("  止损参数扫描（stop_base × stop_atr_buffer）")
    print("=" * 92)

    print("  收集模式...")
    raw = collect_raw()
    print(f"  共 {len(raw)} 个收缩模式\n")

    rows = []
    # 只扫 p5（p4 经诊断是回测假象：P4 回撤极值比入场点远，做空时止损会落到入场价下方，
    # 导致 risk 虚小、PF 虚高。p4 作为止损基准在几何上不合理，已废弃）。
    buffers = [0.5, 0.7, 0.8, 0.9, 1.0, 1.1, 1.2, 1.5]
    base_cfg = BacktestConfig()

    for buf in buffers:
        scfg = SignalConfig(stop_base="p5", stop_atr_buffer=buf)
        # 用当前止损配置重新生成信号（止损影响信号本身的 stop 字段）
        sigs = []
        for sym, p, df, atr in raw:
            sig = best_signal(p, df, atr, scfg)
            if sig is not None:
                sigs.append((sym, sig, df, atr))
        if not sigs:
            continue
        res = run_backtest(sigs, base_cfg)
        s = res.summary()
        stop_rate = pd.Series([t.exit_reason for t in res.trades]).eq("stop").mean()
        rows.append({
            "stop_base": "p5", "buffer_atr": buf,
            "n_signals": len(sigs), "n_trades": s["n"],
            "win_rate": s["win_rate"], "avg_r": s["avg_r"],
            "profit_factor": s["profit_factor"], "stop_rate": round(float(stop_rate), 3),
            "avg_hold": s["avg_hold"],
        })
        print(f"  p5 buf={buf:.1f}  n={s['n']:<4} 胜率={s['win_rate']:.1%} "
              f"avgR={s['avg_r']:+.3f} PF={s['profit_factor']:.2f} "
              f"止损率={stop_rate:.0%}")

    df_res = pd.DataFrame(rows)
    df_res.to_csv(OUTPUT_DIR / "stop_sweep.csv", index=False, encoding="utf-8-sig")

    print("\n" + "=" * 92)
    print("  最优方案（按 avg_r 排序）")
    print("=" * 92)
    best = df_res.sort_values("avg_r", ascending=False).head(5)
    print(best.to_string(index=False))

    if len(best) > 0:
        r = best.iloc[0]
        print(f"\n  ★ 最优: stop_base={r['stop_base']} buffer={r['buffer_atr']} "
              f"avgR={r['avg_r']:+.3f} PF={r['profit_factor']:.2f} 胜率={r['win_rate']:.1%}")
    return 0


if __name__ == "__main__":
    sys.exit(run_sweep())
