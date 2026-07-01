"""
signals.py
==========
双均线缠绕放量突破信号识别。

多头(空头镜像):
  1. 缠绕带: 前 twist_window 根内 fast/slow 交叉次数 >= min_crossings,
            且均线密集度 <= twist_band_ratio (相对价差小)
  2. 金叉:   fast 由下穿上 slow
  3. 放量中阳: 金叉前后 ±bar_window 根内出现
            - 放量: volume > vol_factor * vol_ma   OR   volume > prev_vol_factor * 前一根 volume
            - 中阳: body_ratio >= body_pct 且 close>open
            - 站上双均线: close > ema_fast 且 close > ema_slow
  4. 加分:   缠绕带内底部抬高 (higher_low) -> 星级提升

空头:
  1. 缠绕带
  2. 死叉: fast 由上穿下 slow
  3. 放量中阴: 放量 + 中阴实体 + close < ema_fast 且 close < ema_slow
  4. 加分: 高点降低 (lower_high)
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


def _volume_burst(df: pd.DataFrame, p: dict) -> pd.Series:
    """
    放量判断（双条件或关系）：
    - 成交量 > vol_factor * 最近 vol_ma 均值
    - 成交量 > prev_vol_factor * 前一根成交量
    """
    vs_ma = df["volume"] > p["vol_factor"] * df["vol_ma"]
    vs_prev = df["volume"] > p["prev_vol_factor"] * df["prev_volume"]
    return vs_ma | vs_prev


def _vol_bar_long(df: pd.DataFrame, p: dict) -> pd.Series:
    """放量中阳：放量 + 中阳实体 + 收阳 + 站上双均线"""
    vol_ok = _volume_burst(df, p)
    body_ok = df["body_ratio"] >= p["body_pct"]
    up = df["close"] > df["open"]
    above_ma = (df["close"] > df["ema_fast"]) & (df["close"] > df["ema_slow"])
    return vol_ok & body_ok & up & above_ma


def _vol_bar_short(df: pd.DataFrame, p: dict) -> pd.Series:
    """放量中阴：放量 + 中阴实体 + 收阴 + 跌破双均线"""
    vol_ok = _volume_burst(df, p)
    body_ok = df["body_ratio"] >= p["body_pct"]
    down = df["close"] < df["open"]
    below_ma = (df["close"] < df["ema_fast"]) & (df["close"] < df["ema_slow"])
    return vol_ok & body_ok & down & below_ma


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
    返回信号 DataFrame。每行一个有效信号(避免未来函数: 用交叉前/交叉当根的放量K收盘确认,
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
            # 只允许交叉前或交叉当根的放量K作为确认，避免未来函数
            conf = None
            for k in range(max(0, i - bw), i + 1):
                if vbl[k]:
                    conf = k
                    break
            if conf is None:
                continue
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
            for k in range(max(0, i - bw), i + 1):
                if vbs[k]:
                    conf = k
                    break
            if conf is None:
                continue
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


def find_alerts(df: pd.DataFrame, p: dict, precomputed: Optional[dict] = None) -> pd.DataFrame:
    """
    预警信号：在正式突破(cross)发生前，提前标记可能即将突破的品种。
    条件：
      1. 缠绕带已形成(twist_ok)
      2. 当前尚未出现金叉/死叉
      3. 收盘价同时贴近 fast/slow 均线（|close-ema|/close <= alert_band_ratio）
      4. 按 alert_cooldown_bars 去重，避免连续刷屏
    返回列与 find_signals 基本一致，direction 表示预警方向(1=可能向上突破, -1=可能向下突破)。
    """
    if not p.get("alert_enable", True):
        return pd.DataFrame(columns=[
            "datetime", "cross_idx", "entry_idx", "entry_time",
            "entry_price", "direction", "twist_cross", "dist_fast", "dist_slow",
        ])

    d = attach_indicators(df, p, precomputed=precomputed)
    n = len(d)
    if n == 0:
        return pd.DataFrame(columns=[
            "datetime", "cross_idx", "entry_idx", "entry_time",
            "entry_price", "direction", "twist_cross", "dist_fast", "dist_slow",
        ])

    twist = _twist_ok(d, p).to_numpy()
    gc = _golden_cross(d).to_numpy()
    dc = _death_cross(d).to_numpy()

    band = p.get("alert_band_ratio", 0.003)
    dist_fast = ((d["close"] - d["ema_fast"]).abs() / d["close"]).to_numpy()
    dist_slow = ((d["close"] - d["ema_slow"]).abs() / d["close"]).to_numpy()
    close_to_ma = (dist_fast <= band) & (dist_slow <= band)

    # 多头预警：缠绕带 + 未金叉 + 价格贴近均线 + 收盘在 fast 附近或下方（有向上空间）
    long_alert = twist & ~gc & close_to_ma & (d["close"] <= d["ema_fast"])
    # 空头预警：缠绕带 + 未死叉 + 价格贴近均线 + 收盘在 fast 附近或上方（有向下空间）
    short_alert = twist & ~dc & close_to_ma & (d["close"] >= d["ema_fast"])

    cooldown = p.get("alert_cooldown_bars", 5)
    dt = d["datetime"].to_numpy()
    rows = []
    last_alert = -cooldown
    for i in range(n):
        if long_alert.iloc[i] and (i - last_alert) >= cooldown:
            rows.append({
                "datetime": dt[i],
                "cross_idx": i,
                "entry_idx": i,
                "entry_time": dt[i],
                "entry_price": float(d["close"].iloc[i]),
                "direction": 1,
                "twist_cross": int(d["twist_cross"].iloc[i]),
                "dist_fast": float(dist_fast[i]),
                "dist_slow": float(dist_slow[i]),
            })
            last_alert = i
        elif short_alert.iloc[i] and (i - last_alert) >= cooldown:
            rows.append({
                "datetime": dt[i],
                "cross_idx": i,
                "entry_idx": i,
                "entry_time": dt[i],
                "entry_price": float(d["close"].iloc[i]),
                "direction": -1,
                "twist_cross": int(d["twist_cross"].iloc[i]),
                "dist_fast": float(dist_fast[i]),
                "dist_slow": float(dist_slow[i]),
            })
            last_alert = i

    if not rows:
        return pd.DataFrame(columns=[
            "datetime", "cross_idx", "entry_idx", "entry_time",
            "entry_price", "direction", "twist_cross", "dist_fast", "dist_slow",
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
