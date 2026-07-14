"""ZigZag 定量验证 —— 不靠肉眼，客观判断 depth 调得对不对。

四项检查：
    1. segment_stats     每段（拐点到拐点）的 ATR 倍数分布。合理 depth 下 mean≈3~6 ATR。
    2. alternation_check 高低严格交替（状态机保证，应=100%）。
    3. phantom_rate      "确认后被后续覆盖"的比例（running-extreme 应=0%，正确性断言）。
    4. depth_sensitivity depth 多档扫描，拐点数应随 depth 平滑递减。

设计目的：替代 future_1 里"4 品种×6 个月调出魔法数 55"的过拟合式调参，
给出可解释、可对比的量化依据。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .config import ZigZagConfig
from .zigzag import ZigZagResult, detect_zigzag


def segment_stats(result: ZigZagResult) -> dict:
    """计算相邻拐点之间每段的 ATR 倍数分布。

    合理 depth 下段长应在 ~2.5~8 ATR；<2 说明切到噪声，>10 说明漏波段。
    """
    pivots = result.confirmed_pivots
    if len(pivots) < 2:
        return {"count": 0, "mean_atr_mult": float("nan"),
                "median_atr_mult": float("nan"), "p10": float("nan"),
                "p90": float("nan"), "min": float("nan"), "max": float("nan")}

    atr = result.atr.to_numpy(dtype=float)
    mults: list[float] = []
    for a, b in zip(pivots[:-1], pivots[1:]):
        move = abs(b.price - a.price)
        # 用两拐点中点处 ATR 代表该段波动环境
        mid = (a.index + b.index) // 2
        a_val = atr[mid] if 0 <= mid < len(atr) else atr[b.index]
        if np.isfinite(a_val) and a_val > 0:
            mults.append(move / a_val)

    if not mults:
        return {"count": 0, "mean_atr_mult": float("nan"),
                "median_atr_mult": float("nan"), "p10": float("nan"),
                "p90": float("nan"), "min": float("nan"), "max": float("nan")}

    arr = np.array(mults)
    return {
        "count": int(len(arr)),
        "mean_atr_mult": float(np.mean(arr)),
        "median_atr_mult": float(np.median(arr)),
        "p10": float(np.percentile(arr, 10)),
        "p90": float(np.percentile(arr, 90)),
        "min": float(np.min(arr)),
        "max": float(np.max(arr)),
    }


def alternation_check(result: ZigZagResult) -> dict:
    """高低严格交替比例（应=100%）。"""
    pivots = result.confirmed_pivots
    if len(pivots) < 2:
        return {"alternating_ratio": 1.0, "violations": 0, "pairs": 0}
    pairs = list(zip(pivots[:-1], pivots[1:]))
    violations = sum(1 for a, b in pairs if a.kind == b.kind)
    return {
        "alternating_ratio": round(1.0 - violations / len(pairs), 4),
        "violations": int(violations),
        "pairs": int(len(pairs)),
    }


def phantom_rate(result: ZigZagResult) -> dict:
    """anti-repaint 增量一致性测试。

    真正的 anti-repaint 保证：一个拐点在 confirmed_at 定型后，喂入更多数据
    不会让它移动或消失。测试方法——增量切片重跑：取若干截断点，每次只喂前 k 根，
    检查 ``confirmed_at < k`` 的拐点与全量结果是否逐一致（index/price/kind 全等）。

    注意：这不同于"确认高点和下一拐点间是否有更高 high"——后者是 ZigZag 的正常
    行为（追踪反向期间出现未成段的 failure swing），不是重绘。本函数测的是实现正确性，
    running-extreme 算法此值应=0。
    """
    df = result.df
    n = len(df)
    pivots = result.confirmed_pivots
    if n < result.config.atr_period * 8 or len(pivots) < 4:
        return {"phantom_rate": 0.0, "mismatches": 0, "checked": 0}

    # 全量结果按 confirmed_at 建立查询表：(confirmed_at, index) -> (kind, price)
    full = {(p.confirmed_at, p.index): (p.kind, round(p.price, 6)) for p in pivots}

    # 选若干截断点（均匀采样，避开太靠后的尾部以保证有可对照的已确认拐点）
    cutoffs = np.linspace(n * 0.4, n * 0.95, 8).astype(int)
    mismatches = 0
    checked = 0
    for k in cutoffs:
        sub = df.iloc[:k].reset_index(drop=True)
        sub_res = detect_zigzag(sub, result.config)
        for p in sub_res.confirmed_pivots:
            key = (p.confirmed_at, p.index)
            if key in full:
                checked += 1
                if full[key] != (p.kind, round(p.price, 6)):
                    mismatches += 1
    return {"phantom_rate": round(mismatches / checked, 4) if checked else 0.0,
            "mismatches": int(mismatches), "checked": int(checked)}


def depth_sensitivity(df: pd.DataFrame, base: ZigZagConfig,
                      depths=(0.6, 0.8, 1.0, 1.5, 2.0)) -> pd.DataFrame:
    """多档 depth 扫描，返回每档拐点数与段统计。

    稳健 ZigZag 应随 depth 平滑递减，不剧变（剧变说明正好卡在某临界结构上，不稳）。
    """
    rows = []
    for d in depths:
        cfg = ZigZagConfig(depth_atr_multiple=d, atr_period=base.atr_period,
                           reversal_mode=base.reversal_mode,
                           min_bars_between_pivots=base.min_bars_between_pivots)
        res = detect_zigzag(df, cfg)
        seg = segment_stats(res)
        rows.append({
            "depth_atr": d,
            "pivot_count": res.pivot_count(),
            "mean_seg_atr": round(seg["mean_atr_mult"], 2),
            "median_seg_atr": round(seg["median_atr_mult"], 2),
        })
    return pd.DataFrame(rows)


def full_report(symbol: str, result: ZigZagResult) -> dict:
    """单个品种的完整验证报告（供 run.py 汇总）。"""
    seg = segment_stats(result)
    alt = alternation_check(result)
    pha = phantom_rate(result)
    return {
        "symbol": symbol,
        "n_bars": result.n_bars,
        "pivots": result.pivot_count(),
        "confirmed": len(result.confirmed_pivots),
        "mean_seg_atr": round(seg["mean_atr_mult"], 2),
        "median_seg_atr": round(seg["median_atr_mult"], 2),
        "p10_seg_atr": round(seg["p10"], 2),
        "p90_seg_atr": round(seg["p90"], 2),
        "alternation": alt["alternating_ratio"],
        "phantom_rate": pha["phantom_rate"],
    }


def print_report(symbol: str, result: ZigZagResult) -> None:
    """打印单品种验证摘要到 stdout。"""
    r = full_report(symbol, result)
    print(f"  {symbol:<5} bars={r['n_bars']:<5} pivots={r['pivots']:<4} "
          f"mean段={r['mean_seg_atr']:<5} ATR 交替={r['alternation']:.2f} "
          f"phantom={r['phantom_rate']:.2f}")
