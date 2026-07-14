"""前瞻回报验证 —— 用"结果"客观评估信号的预测力。

这是 future_1 完全缺失的一环：它只调出魔法数 55，从不验证信号是否真赚钱。
本模块对每个在 K 线 i 确认的模式，统计未来 N 根 K 线内价格沿"反转方向"的
最大有利/不利偏移（MFE/MAE，ATR 归一化），从而判断：
    - 通过某闸门（如收缩）的模式，未来是否真的更可能反转？
    - 不同 Four-Dim 分数段的模式，前瞻回报是否有单调差异？

注意：前瞻验证只用于"评估指标质量/调参"，不参与实盘（实盘无未来信息）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import numpy as np
import pandas as pd

from .config import LookaheadConfig
from .three_push import ThreePushPattern


@dataclass(frozen=True)
class LookaheadOutcome:
    """单个模式在某个 horizon 的前瞻结果（ATR 归一化）。"""

    horizon: int
    mfe: float      # 最大有利偏移（顶部=下跌幅度；底部=上涨幅度）/ ATR，>0=有利
    mae: float      # 最大不利偏移 / ATR，>0=不利
    ret: float      # horizon 根后的收盘价位移 / ATR，正=沿反转方向


def _atr_val(atr: pd.Series, idx: int) -> float:
    arr = atr.to_numpy(dtype=float)
    if 0 <= idx < len(arr):
        v = arr[idx]
        return float(v) if np.isfinite(v) and v > 0 else float("nan")
    return float("nan")


def evaluate_pattern(pattern: ThreePushPattern, df: pd.DataFrame,
                     atr: pd.Series, cfg: LookaheadConfig | None = None
                     ) -> list[LookaheadOutcome]:
    """计算一个模式在所有 horizon 的前瞻 MFE/MAE/ret。

    顶部三推（reversal_kind='high'）：反转方向=看空，有利=价格下跌。
    底部三推（reversal_kind='low'）：反转方向=看多，有利=价格上涨。

    所有偏移用模式确认点(P5)的 ATR 归一化，跨品种可比。
    """
    cfg = cfg or LookaheadConfig()
    n = len(df)
    close = df["close"].to_numpy(dtype=float)
    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)

    entry_idx = pattern.confirmed_at   # P5 的 confirmed_at（确认那根，anti-repaint 安全起点）
    if entry_idx >= n - 1:
        return []
    entry_price = close[entry_idx]
    a = _atr_val(atr, entry_idx)
    if not np.isfinite(a) or a <= 0:
        return []

    is_top = pattern.reversal_kind == "high"   # 顶部→看空，有利=下跌
    sign = -1.0 if is_top else 1.0             # 沿反转方向的有利位移符号

    outcomes: list[LookaheadOutcome] = []
    for h in cfg.horizons:
        end = min(entry_idx + h, n - 1)
        if end <= entry_idx:
            continue
        seg_close = close[entry_idx + 1:end + 1]
        seg_hi = high[entry_idx + 1:end + 1]
        seg_lo = low[entry_idx + 1:end + 1]

        # 收盘位移：顶部看 close-entry 的下跌，所以 mfe 用 sign*(close-entry)
        moves = (seg_close - entry_price) * sign
        ret = moves[-1] / a if len(moves) else float("nan")

        # MFE：沿反转方向的最大有利幅度
        # 顶部(看空)：有利=最低价 below entry → (entry - low) ；sign=-1 → -1*(low-entry)=entry-low
        # 底部(看多)：有利=最高价 above entry → (high - entry)
        if is_top:
            fav = entry_price - seg_lo   # 下跌幅度
            adv = seg_hi - entry_price   # 反弹幅度（不利）
        else:
            fav = seg_hi - entry_price
            adv = entry_price - seg_lo
        mfe = (fav.max() / a) if len(fav) else float("nan")
        mae = (adv.max() / a) if len(adv) else float("nan")

        outcomes.append(LookaheadOutcome(horizon=int(h), mfe=float(mfe), mae=float(mae),
                                         ret=float(ret)))
    return outcomes


def summarize_groups(outcomes_by_group: dict[str, list[list[LookaheadOutcome]]],
                     ) -> pd.DataFrame:
    """按分组（如 通过/未通过收缩）汇总前瞻统计。

    返回 DataFrame: group × horizon → mean MFE/MAE/ret, win_rate, count
    """
    rows = []
    for group, outcomes_list in outcomes_by_group.items():
        # 聚合该组所有模式在所有 horizon 的结果
        by_h: dict[int, list[LookaheadOutcome]] = {}
        for outs in outcomes_list:
            for o in outs:
                by_h.setdefault(o.horizon, []).append(o)
        for h, os_ in sorted(by_h.items()):
            mfes = np.array([o.mfe for o in os_ if np.isfinite(o.mfe)])
            maes = np.array([o.mae for o in os_ if np.isfinite(o.mae)])
            rets = np.array([o.ret for o in os_ if np.isfinite(o.ret)])
            if len(rets) == 0:
                continue
            rows.append({
                "group": group,
                "horizon": h,
                "count": len(rets),
                "mfe_mean": round(float(mfes.mean()), 3) if len(mfes) else float("nan"),
                "mae_mean": round(float(maes.mean()), 3) if len(maes) else float("nan"),
                "ret_mean": round(float(rets.mean()), 3),
                "win_rate": round(float((rets > 0).mean()), 3),   # ret>0 = 沿反转方向
                "mfe_mae_ratio": round(float(mfes.mean() / maes.mean()), 3)
                                 if len(maes) and maes.mean() > 0 else float("nan"),
            })
    return pd.DataFrame(rows)
