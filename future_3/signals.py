"""
signals.py
==========
双均线缠绕放量突破信号识别。

多头(空头镜像):
  1. 缠绕带: 前 twist_window 根内 fast/slow 交叉次数 >= min_crossings,
            且均线密集度 <= twist_band_ratio (相对价差小)
  2. 金叉:   fast 由下穿上 slow
  3. 放量中阳: 金叉前后 ±bar_window 根内出现
            - 放量: volume > vol_factor * vol_ma
            - 中阳: body_ratio >= body_pct 且 close>open
  4. 加分:   缠绕带内底部抬高 (higher_low) -> 星级提升
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from indicators import attach_indicators


def _golden_cross(df: pd.DataFrame) -> pd.Series:
    """fast 由下穿上 slow (前一根 fast<=slow, 当前 fast>slow)"""
    f, s = df["ema_fast"], df["ema_slow"]
    return (f > s) & (f.shift(1) <= s.shift(1))


def _death_cross(df: pd.DataFrame) -> pd.Series:
    """fast 由上穿下 slow"""
    f, s = df["ema_fast"], df["ema_slow"]
    return (f < s) & (f.shift(1) >= s.shift(1))


def _vol_bar_long(df: pd.DataFrame, p: dict) -> pd.Series:
    """放量中阳: 放量 + 中阳实体 + 收阳"""
    vol_ok = df["volume"] > p["vol_factor"] * df["vol_ma"]
    body_ok = df["body_ratio"] >= p["body_pct"]
    up = df["close"] > df["open"]
    return vol_ok & body_ok & up


def _vol_bar_short(df: pd.DataFrame, p: dict) -> pd.Series:
    """放量中阴"""
    vol_ok = df["volume"] > p["vol_factor"] * df["vol_ma"]
    body_ok = df["body_ratio"] >= p["body_pct"]
    down = df["close"] < df["open"]
    return vol_ok & body_ok & down


def _twist_ok(df: pd.DataFrame, p: dict) -> pd.Series:
    """缠绕带: 交叉次数达标 + 密集度达标"""
    cross_ok = df["twist_cross"] >= p["min_crossings"]
    dense_ok = df["twist_density"] <= p["twist_band_ratio"]
    return cross_ok & dense_ok


def _window_or(arr: np.ndarray, i: int, w: int) -> bool:
    """arr[i-w .. i+w] 内是否存在 True"""
    n = len(arr)
    lo = max(0, i - w)
    hi = min(n, i + w + 1)
    return bool(arr[lo:hi].any())


def find_signals(df: pd.DataFrame, p: dict, precomputed: Optional[dict] = None) -> pd.DataFrame:
    """
    返回信号 DataFrame。每行一个有效信号(避免未来函数: 用放量K收盘确认,
    入场标记在确认根的"下一根开盘")。
    precomputed: 复用的 bonus 极值序列 (网格优化用)。
    """
    d = attach_indicators(df, p, precomputed=precomputed)
    n = len(d)

    twist = _twist_ok(d, p).to_numpy()
    gc = _golden_cross(d).to_numpy()
    dc = _death_cross(d).to_numpy()
    vbl = _vol_bar_long(d, p).to_numpy()
    vbs = _vol_bar_short(d, p).to_numpy()
    hl = d["higher_low"].to_numpy() if "higher_low" in d else np.zeros(n, dtype=bool)
    lh = d["lower_high"].to_numpy() if "lower_high" in d else np.zeros(n, dtype=bool)

    bw = p["bar_window"]
    dt = d["datetime"].to_numpy()
    rows = []

    for i in range(n):
        # ---- 多头 ----
        if gc[i] and twist[i] and _window_or(vbl, i, bw):
            j = min(n - 1, i + bw)  # 放量K最迟位置
            # 入场= 确认根(出现放量K的那根)的下一根开盘; 取最早满足的放量K
            conf = None
            for k in range(max(0, i - bw), min(n, i + bw + 1)):
                if vbl[k]:
                    conf = k
                    break
            entry_idx = min(n - 1, conf + 1)
            rows.append({
                "datetime": dt[i],
                "cross_idx": i,
                "confirm_idx": conf,
                "entry_idx": entry_idx,
                "entry_time": dt[entry_idx],
                "entry_price": float(d["open"].iloc[entry_idx]),
                "direction": 1,
                "bonus": bool(hl[i]),
                "twist_cross": int(d["twist_cross"].iloc[i]),
            })
        # ---- 空头 ----
        if dc[i] and twist[i] and _window_or(vbs, i, bw):
            conf = None
            for k in range(max(0, i - bw), min(n, i + bw + 1)):
                if vbs[k]:
                    conf = k
                    break
            entry_idx = min(n - 1, conf + 1)
            rows.append({
                "datetime": dt[i],
                "cross_idx": i,
                "confirm_idx": conf,
                "entry_idx": entry_idx,
                "entry_time": dt[entry_idx],
                "entry_price": float(d["open"].iloc[entry_idx]),
                "direction": -1,
                "bonus": bool(lh[i]),
                "twist_cross": int(d["twist_cross"].iloc[i]),
            })

    if not rows:
        return pd.DataFrame(columns=[
            "datetime", "cross_idx", "confirm_idx", "entry_idx", "entry_time",
            "entry_price", "direction", "bonus", "twist_cross",
        ])
    return pd.DataFrame(rows)


if __name__ == "__main__":
    from data_loader import load_klines, load_config

    cfg = load_config()
    p = cfg["strategy"]
    for sym in ["AU0", "M0", "TA0"]:
        df = load_klines(sym, cfg)
        sigs = find_signals(df, p)
        print(f"\n=== {sym}: {len(sigs)} signals ===")
        if len(sigs):
            print(sigs.tail(5).to_string(index=False))
