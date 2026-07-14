"""CLI: 完整回测 —— 信号独立结算 + R:R 敏感度 + 分类型统计。

对 9 品种：ZigZag → Three Push → 收缩 → 去重信号 → 回测。
核心输出：整体期望/胜率/盈亏比、R:R 敏感度、按信号类型分开的统计。

用法:
    python -m future_zigzag.run_backtest
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

_SCRIPT_DIR = Path(__file__).resolve().parent
_WORK_AI = _SCRIPT_DIR.parent
sys.path.insert(0, str(_WORK_AI))

from future_data import get_klines  # noqa: E402

from future_zigzag.config import (  # noqa: E402
    SYMBOLS, ZigZagConfig, ThreePushConfig, ContractionConfig, SignalConfig,
    BacktestConfig, DEFAULT_MULTIPLIERS,
)
from future_zigzag.zigzag import detect_zigzag  # noqa: E402
from future_zigzag.three_push import detect_three_push  # noqa: E402
from future_zigzag.contraction import evaluate_contraction  # noqa: E402
from future_zigzag.signals import best_signal  # noqa: E402
from future_zigzag.backtest import run_backtest, rr_sweep, BacktestResult  # noqa: E402

OUTPUT_DIR = _SCRIPT_DIR / "output"
MIN_BARS = 3000


def collect_signals(depth=1.5, min_score=0.45, period="15", length=MIN_BARS):
    """收集全部去重信号（带 df/atr 供回测）。"""
    zcfg = ZigZagConfig(depth_atr_multiple=depth)
    tcfg = ThreePushConfig(zigzag=zcfg, valid_score_threshold=min_score)
    ccfg = ContractionConfig()
    scfg = SignalConfig()
    signals = []
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
            if not cr.passed:
                continue
            sig = best_signal(p, df, atr, scfg)
            if sig is not None:
                signals.append((sym, sig, df, atr))
    return signals


def _run_one(signals, cfg, label):
    """跑一种配置并打印/返回结果。"""
    res = run_backtest(signals, cfg)
    s = res.summary()
    exits = pd.Series([t.exit_reason for t in res.trades]).value_counts(normalize=True).to_dict()
    print(f"\n  [{label}]")
    print(f"  总交易={s['n']}  胜率={s['win_rate']:.1%}  avgR={s['avg_r']:.3f}  "
          f"PF={s['profit_factor']:.2f}  持仓={s['avg_hold']:.0f}根  "
          f"止损率={exits.get('stop',0):.0%}")
    return res, exits


def run(depth=1.5, min_score=0.45, period="15", length=MIN_BARS):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print("=" * 88)
    print(f"  完整回测（信号独立结算） depth={depth}×ATR  period={period}")
    print("=" * 88)

    print("  收集信号...")
    signals = collect_signals(depth, min_score, period, length)
    print(f"  共 {len(signals)} 个去重信号\n")

    if not signals:
        print("  无信号，退出"); return 1

    # ── 止损模式对比：固定资金止损 vs 结构止损 ──
    print("=" * 88)
    print("  止损模式对比")
    print("=" * 88)

    # 固定金额资金止损（0.9% × 资金）
    cfg_fixed = BacktestConfig(stop_mode="fixed_risk", risk_pct=0.009)
    res_fixed, exits_fixed = _run_one(signals, cfg_fixed,
                                      f"固定资金止损 risk={cfg_fixed.risk_pct:.1%}×资金")

    # 结构止损（P5+1.5ATR，经扫描验证最优 buffer）
    cfg_struct = BacktestConfig(stop_mode="structure")
    res_struct, exits_struct = _run_one(signals, cfg_struct,
                                        "结构止损 P5+1.5×ATR")

    # ── 固定资金止损：R:R 敏感度（找最优目标倍数）──
    print("\n" + "=" * 88)
    print("  固定资金止损 R:R 敏感度（统一目标倍数）")
    print("=" * 88)
    # rr_sweep 用统一 R:R，需要 fixed_risk 模式
    sweep_rows = []
    for rr in (1.0, 1.5, 2.0, 2.5, 3.0):
        cfg_rr = BacktestConfig(stop_mode="fixed_risk", risk_pct=0.009,
                                target_rr_by_type={"wedge_breakout": rr, "reversal_bar": rr, "second_entry": rr})
        res = run_backtest(signals, cfg_rr)
        s = res.summary()
        sweep_rows.append({"target_rr": rr, "n": s["n"], "win_rate": s["win_rate"],
                           "avg_r": s["avg_r"], "profit_factor": s["profit_factor"]})
        print(f"  R:R={rr}  n={s['n']:<4} 胜率={s['win_rate']:.1%} "
              f"avgR={s['avg_r']:+.3f} PF={s['profit_factor']:.2f}")
    pd.DataFrame(sweep_rows).to_csv(OUTPUT_DIR / "backtest_rr_sweep_fixedrisk.csv",
                                    index=False, encoding="utf-8-sig")

    # ── 固定资金止损：按信号类型 + 按品种明细 ──
    print("\n" + "=" * 88)
    print("  固定资金止损：按信号类型")
    print("=" * 88)
    res = res_fixed
    type_rows = []
    for stype, trades in res.by_type().items():
        sub = BacktestResult(trades=trades)
        ss = sub.summary()
        exits = pd.Series([t.exit_reason for t in trades]).value_counts(normalize=True).to_dict()
        type_rows.append({
            "signal_type": stype, "n": ss["n"], "win_rate": ss["win_rate"],
            "avg_r": ss["avg_r"], "profit_factor": ss["profit_factor"],
            "avg_hold": ss["avg_hold"],
            "target%": round(exits.get("target", 0), 3),
            "stop%": round(exits.get("stop", 0), 3),
            "timeout%": round(exits.get("timeout", 0), 3),
        })
        print(f"  {stype:<16} n={ss['n']:<4} 胜率={ss['win_rate']:.1%} "
              f"avgR={ss['avg_r']:.3f} PF={ss['profit_factor']:.2f} "
              f"目标{exits.get('target',0):.0%}/止损{exits.get('stop',0):.0%}/超时{exits.get('timeout',0):.0%}")

    print("\n  ── 按品种 ──")
    by_sym = {}
    for t in res.trades:
        by_sym.setdefault(t.symbol, []).append(t)
    sym_rows = []
    for sym in sorted(by_sym):
        sub = BacktestResult(trades=by_sym[sym])
        ss = sub.summary()
        sym_rows.append({"symbol": sym, "n": ss["n"], "win_rate": ss["win_rate"],
                         "avg_r": ss["avg_r"], "profit_factor": ss["profit_factor"]})
        print(f"  {sym:<5} n={ss['n']:<4} 胜率={ss['win_rate']:.1%} "
              f"avgR={ss['avg_r']:.3f} PF={ss['profit_factor']:.2f}")

    # 存明细
    trades_df = pd.DataFrame([{
        "symbol": t.symbol, "type": t.signal_type, "side": t.side,
        "entry_idx": t.entry_idx, "entry": t.entry_price, "exit": t.exit_price,
        "reason": t.exit_reason, "stop": t.stop_price, "target": t.target_price,
        "hold": t.hold_bars, "net_pnl": t.net_pnl, "r_multiple": t.r_multiple,
        "cost": t.cost_points,
    } for t in res.trades])
    trades_df.to_csv(OUTPUT_DIR / "backtest_trades.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(type_rows).to_csv(OUTPUT_DIR / "backtest_by_type.csv",
                                   index=False, encoding="utf-8-sig")
    pd.DataFrame(sym_rows).to_csv(OUTPUT_DIR / "backtest_by_symbol.csv",
                                  index=False, encoding="utf-8-sig")
    print(f"\n  明细已存 backtest_trades.csv / backtest_by_type.csv / backtest_by_symbol.csv")
    return 0


if __name__ == "__main__":
    sys.exit(run())
