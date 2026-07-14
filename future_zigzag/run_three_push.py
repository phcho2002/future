"""CLI: Three Push 识别 + 四维评分。

对 9 品种 × 3000 根 15min 跑 ZigZag → Three Push 检测 → 四维评分 → 画图。
重点输出：四维诊断力统计（每维对"有效模式"的区分度）。

用法:
    python -m future_zigzag.run_three_push
    python -m future_zigzag.run_three_push --depth 1.5 --min-score 0.45
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

_SCRIPT_DIR = Path(__file__).resolve().parent
_WORK_AI = _SCRIPT_DIR.parent
sys.path.insert(0, str(_WORK_AI))

from future_data import get_klines  # noqa: E402

from future_zigzag.config import SYMBOLS, ZigZagConfig, ThreePushConfig  # noqa: E402
from future_zigzag.zigzag import detect_zigzag  # noqa: E402
from future_zigzag.three_push import detect_three_push  # noqa: E402
from future_zigzag import visualize  # noqa: E402

OUTPUT_DIR = _SCRIPT_DIR / "output"
MIN_BARS = 3000


def run(depth: float = 1.5, min_score: float = 0.45, period: str = "15",
        length: int = MIN_BARS) -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    zcfg = ZigZagConfig(depth_atr_multiple=depth)
    tcfg = ThreePushConfig(zigzag=zcfg, valid_score_threshold=min_score)

    print("=" * 80)
    print(f"  Three Push 识别 + 四维评分  depth={depth}×ATR  period={period}  length={length}")
    print(f"  有效门槛: 硬门控(幅度或量能显著) AND 总分>={min_score}")
    print("=" * 80)

    all_rows = []
    diag_rows = []

    for i, (sym, name, exch) in enumerate(SYMBOLS):
        print(f"\n[{i+1}/{len(SYMBOLS)}] {sym} {name} ({exch})...", flush=True)
        try:
            df = get_klines(sym, exch, period=period, length=length, force=True)
            if df is None or df.empty:
                print(f"  ❌ 无数据"); continue
        except Exception as e:
            print(f"  ❌ {e}"); continue

        if len(df) > MIN_BARS:
            df = df.tail(MIN_BARS).reset_index(drop=True)

        zz = detect_zigzag(df, zcfg)
        patterns = detect_three_push(zz, tcfg)
        valid = [p for p in patterns if p.score.hard_gate_passed
                 and p.score.total >= min_score]

        print(f"  ZigZag pivots={zz.pivot_count()}  Three Push模式={len(patterns)}  "
              f"有效={len(valid)}")

        # 每品种诊断行
        if patterns:
            arr = np.array([p.score.total for p in patterns])
            varr = np.array([p.score.total for p in valid]) if valid else np.array([])
            print(f"  总分分布: 全部 mean={np.mean(arr):.2f}/max={np.max(arr):.2f}  "
                  f"有效 mean={np.mean(varr):.2f}" if len(varr) else
                  f"  总分分布: 全部 mean={np.mean(arr):.2f}/max={np.max(arr):.2f}  有效=0")
            for p in valid[-3:]:  # 最近3个有效
                s = p.score
                print(f"    [{p.reversal_kind}] dir={p.direction} 总={s.total:.2f} "
                      f"幅={s.amplitude:.2f}({s.amplitude_ratio:.2f}) "
                      f"量={s.volume:.2f}({s.volume_ratio:.2f}) "
                      f"力={s.momentum:.2f}({s.momentum_ratio:.2f}) "
                      f"时={s.duration:.2f}({s.duration_ratio:.2f}) "
                      f"门{'✓' if s.hard_gate_passed else '✗'}")

        # 汇总行
        all_rows.append({
            "symbol": sym, "pivots": zz.pivot_count(),
            "patterns": len(patterns),
            "valid": len(valid),
            "valid_ratio": round(len(valid) / len(patterns), 3) if patterns else 0,
            "mean_total": round(float(np.mean([p.score.total for p in patterns])), 3) if patterns else 0,
            "max_total": round(float(np.max([p.score.total for p in patterns])), 3) if patterns else 0,
        })

        # 四维诊断行（用于分析各维区分力）
        for p in patterns:
            s = p.score
            diag_rows.append({
                "symbol": sym, "direction": p.direction,
                "amp": round(s.amplitude, 3), "dur": round(s.duration, 3),
                "vol": round(s.volume, 3), "mom": round(s.momentum, 3),
                "total": round(s.total, 3),
                "valid": int(s.hard_gate_passed and s.total >= min_score),
                "amp_r": round(s.amplitude_ratio, 3) if np.isfinite(s.amplitude_ratio) else None,
                "vol_r": round(s.volume_ratio, 3) if np.isfinite(s.volume_ratio) else None,
            })

        # 画图
        try:
            visualize.plot_three_push(zz, patterns, sym, name, OUTPUT_DIR,
                                      tail=600, top_n=4)
        except Exception as e:
            print(f"  ⚠️ 画图失败: {e}")

    # 汇总
    print("\n" + "=" * 80)
    print("  汇总")
    print("=" * 80)
    if all_rows:
        dfa = pd.DataFrame(all_rows)
        print(dfa.to_string(index=False))
        dfa.to_csv(OUTPUT_DIR / "threepush_report.csv", index=False, encoding="utf-8-sig")

    # 四维诊断力分析
    if diag_rows:
        dfd = pd.DataFrame(diag_rows)
        dfd.to_csv(OUTPUT_DIR / "threepush_dim_diagnostic.csv", index=False,
                   encoding="utf-8-sig")
        print("\n  ── 四维诊断力（有效 vs 无效模式的均值差异）──")
        grp = dfd.groupby("valid")[["amp", "dur", "vol", "mom", "total"]].mean()
        print(grp.to_string())
        if 0 in grp.index and 1 in grp.index:
            diff = (grp.loc[1] - grp.loc[0])
            print(f"\n  各维区分度(有效-无效, 越大越好):")
            for dim in ["amp", "vol", "mom", "dur", "total"]:
                print(f"    {dim:<5}: {diff[dim]:+.3f}")
        print(f"\n  共 {len(dfd)} 个模式, {int(dfd['valid'].sum())} 个有效")

    return 0


def main(argv=None):
    import argparse
    p = argparse.ArgumentParser(description="Three Push 识别 + 四维评分")
    p.add_argument("--depth", type=float, default=1.5)
    p.add_argument("--min-score", type=float, default=0.45)
    p.add_argument("--period", default="15")
    p.add_argument("--length", type=int, default=MIN_BARS)
    a = p.parse_args(argv)
    return run(depth=a.depth, min_score=a.min_score, period=a.period, length=a.length)


if __name__ == "__main__":
    sys.exit(main())
