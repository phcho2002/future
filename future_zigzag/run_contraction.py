"""CLI: 收缩判定 + 前瞻回报验证。

对 9 品种 × 3000 根：ZigZag → Three Push → 收缩判定 → 前瞻回报。
核心输出：通过收缩 vs 未通过的模式，前瞻 MFE/胜率是否有显著差异
（证明收缩判定是否有预测力）。

用法:
    python -m future_zigzag.run_contraction
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
    SYMBOLS, ZigZagConfig, ThreePushConfig, ContractionConfig, LookaheadConfig,
)
from future_zigzag.zigzag import detect_zigzag  # noqa: E402
from future_zigzag.three_push import detect_three_push  # noqa: E402
from future_zigzag.contraction import evaluate_contraction  # noqa: E402
from future_zigzag.lookahead import evaluate_pattern, summarize_groups  # noqa: E402

OUTPUT_DIR = _SCRIPT_DIR / "output"
MIN_BARS = 3000


def run(depth: float = 1.5, min_score: float = 0.45, period: str = "15",
        length: int = MIN_BARS) -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    zcfg = ZigZagConfig(depth_atr_multiple=depth)
    tcfg = ThreePushConfig(zigzag=zcfg, valid_score_threshold=min_score)
    ccfg = ContractionConfig()
    lcfg = LookaheadConfig()

    print("=" * 84)
    print(f"  收缩判定 + 前瞻验证  depth={depth}×ATR  min_score={min_score}  period={period}")
    print(f"  前瞻 horizons={lcfg.horizons}  ATR回看={ccfg.atr_lookback}")
    print("=" * 84)

    # 聚合所有品种的模式（用于分组前瞻统计）
    passed_outcomes: list = []   # 通过收缩的模式
    failed_outcomes: list = []   # 未通过收缩的模式
    all_rows = []

    for i, (sym, name, exch) in enumerate(SYMBOLS):
        print(f"\n[{i+1}/{len(SYMBOLS)}] {sym} {name} ({exch})...", flush=True)
        try:
            df = get_klines(sym, exch, period=period, length=length, force=True)
            if df is None or df.empty:
                continue
        except Exception as e:
            print(f"  ❌ {e}"); continue
        if len(df) > MIN_BARS:
            df = df.tail(MIN_BARS).reset_index(drop=True)

        zz = detect_zigzag(df, zcfg)
        patterns = detect_three_push(zz, tcfg)
        valid = [p for p in patterns if p.score.hard_gate_passed and p.score.total >= min_score]

        # 收缩判定 + 前瞻
        n_pass = 0
        atr = zz.atr
        for p in valid:
            cr = evaluate_contraction(p, atr, len(df), ccfg)
            outs = evaluate_pattern(p, df, atr, lcfg)
            if cr.passed:
                passed_outcomes.append(outs); n_pass += 1
            else:
                failed_outcomes.append(outs)
            all_rows.append({
                "symbol": sym, "dir": p.direction, "rev": p.reversal_kind,
                "tp_score": round(p.score.total, 3),
                "contracted": int(cr.passed),
                "contraction_score": cr.score,
                "amp_c": cr.amp_contraction_score, "atr_c": cr.atr_contraction_score,
                "amp_r31": cr.amp_ratio_3to1, "atr_pct": cr.atr_percentile,
            })

        print(f"  有效={len(valid)} 通过收缩={n_pass} ({n_pass/max(len(valid),1)*100:.0f}%)")

    # ── 前瞻分组统计（核心验证）──
    print("\n" + "=" * 84)
    print("  前瞻回报分组统计（通过收缩 vs 未通过）")
    print("=" * 84)
    summary = summarize_groups({
        "通过收缩": passed_outcomes,
        "未通过": failed_outcomes,
    })
    if not summary.empty:
        print(summary.to_string(index=False))
        summary.to_csv(OUTPUT_DIR / "contraction_lookahead.csv", index=False, encoding="utf-8-sig")

        # 对比表：每个 horizon 的通过 vs 未通过
        print("\n  ── 预测力对比（通过收缩 减 未通过）──")
        for h in sorted(summary["horizon"].unique()):
            sub = summary[summary.horizon == h]
            p_row = sub[sub.group == "通过收缩"]
            f_row = sub[sub.group == "未通过"]
            if p_row.empty or f_row.empty:
                continue
            p, f = p_row.iloc[0], f_row.iloc[0]
            print(f"  h={h:>3}: MFE {p['mfe_mean']:.2f} vs {f['mfe_mean']:.2f} "
                  f"(Δ{p['mfe_mean']-f['mfe_mean']:+.2f})  "
                  f"胜率 {p['win_rate']:.1%} vs {f['win_rate']:.1%} "
                  f"(Δ{p['win_rate']-f['win_rate']:+.1%})  "
                  f"n={int(p['count'])}/{int(f['count'])}")

    if all_rows:
        pd.DataFrame(all_rows).to_csv(OUTPUT_DIR / "contraction_detail.csv",
                                      index=False, encoding="utf-8-sig")
        dfa = pd.DataFrame(all_rows)
        print(f"\n  共 {len(dfa)} 有效模式, {int(dfa['contracted'].sum())} 通过收缩")
        # 通过率
        print(f"  收缩通过率: {dfa['contracted'].mean():.1%}")

    return 0


if __name__ == "__main__":
    sys.exit(run())
