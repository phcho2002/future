"""xtquant vs akshare 对比：PP0 + IC0 的日线/小时线强弱分析。

对比维度：
  1. 数据拉取速度（秒）
  2. 拉到的 bar 数
  3. 最新收盘价（验证数据一致性）
  4. 五维强弱评分（突破/回调/异常K/共振/动量 → 总分）

运行：D:\work_ai\.venv\Scripts\python.exe _compare_xt_ak.py
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, r"d:\work_ai")

import pandas as pd

# ============================================================
# 评分函数（直接复用 futures_strength_analysis 的五维评分）
# ============================================================
from futures_strength_analysis import (
    score_breakthrough,
    score_pullback,
    score_abnormal_kline,
    score_resonance,
    score_intraday_momentum,
)

SYMBOLS = [
    ("PP0", "聚丙烯", "DCE"),
    ("IC0", "中证500指数", "CFFEX"),
]
PERIODS = [
    ("1440", "日线", 200),
    ("60", "小时线", 1500),
]


def _score_all(df, short_w=20, long_w=60):
    """跑五维评分，返回 (总分, 各维度明细)."""
    s1, r1 = score_breakthrough(df, short_w)
    s2, r2 = score_pullback(df, short_w)
    s3, r3 = score_abnormal_kline(df)
    s4, r4 = score_resonance(df, long_w)
    s5, r5 = score_intraday_momentum(df)
    total = s1 + s2 + s3 + s4 + s5
    return total, {
        "突破": (s1, r1),
        "回调": (s2, r2),
        "异常K": (s3, r3),
        "共振": (s4, r4),
        "动量": (s5, r5),
    }


def fetch_xtquant(symbol, exchange, period, length):
    """用 xtquant 后端拉取（强制不走缓存）。"""
    from future_data.provider import fetch_kline
    os.environ.pop("FUTURE_DATA_BACKEND", None)  # 确保默认 xtquant
    df = fetch_kline(symbol, exchange, period=str(period), length=int(length), backend="xtquant")
    return df


def fetch_akshare_direct(symbol, exchange, period, length):
    """用 akshare 直接拉取（不经过 future_data）。"""
    import akshare as ak

    if str(period) == "1440":
        from datetime import datetime, timedelta
        end = datetime.now().strftime("%Y%m%d")
        start = (datetime.now() - timedelta(days=400)).strftime("%Y%m%d")
        raw = ak.futures_main_sina(symbol=symbol, start_date=start, end_date=end)
    else:
        raw = ak.futures_zh_minute_sina(symbol=symbol, period=str(period))
        time.sleep(0.3)

    # 规范化
    if raw is None or raw.empty:
        return None
    df = raw.copy()
    rename = {c: str(c).strip().lower() for c in df.columns}
    cn_map = {"日期": "date", "开盘": "open", "开盘价": "open", "最高": "high",
              "最高价": "high", "最低": "low", "最低价": "low", "收盘": "close",
              "收盘价": "close", "成交量": "volume", "持仓量": "hold"}
    df = df.rename(columns={**rename, **cn_map})
    if "datetime" not in df.columns and "date" in df.columns:
        df["datetime"] = pd.to_datetime(df["date"])
    for col in ("open", "high", "low", "close", "volume"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["open", "high", "low", "close"]).sort_values("datetime").reset_index(drop=True)
    if length and len(df) > length:
        df = df.tail(length).reset_index(drop=True)
    return df


def star(score):
    if score >= 70:
        return "★★★★ 强势"
    if score >= 55:
        return "★★★ 中性偏强"
    if score >= 45:
        return "★★ 中性偏弱"
    return "★ 弱势"


def main():
    print("=" * 80)
    print("  xtquant vs akshare 数据源对比")
    print("  品种: PP0(聚丙烯/DCE) + IC0(中证500/CFFEX)")
    print("  周期: 日线(1440) + 小时线(60)")
    print("=" * 80)

    for sym, name, exch in SYMBOLS:
        print(f"\n{'─' * 80}")
        print(f"  {sym} {name} ({exch})")
        print(f"{'─' * 80}")

        for period, plabel, length in PERIODS:
            print(f"\n  ── {plabel} (period={period}, length={length}) ──")

            # ---- xtquant ----
            t0 = time.time()
            try:
                df_xt = fetch_xtquant(sym, exch, period, length)
                xt_time = time.time() - t0
                xt_bars = len(df_xt) if df_xt is not None else 0
                xt_close = df_xt.iloc[-1]["close"] if xt_bars > 0 else None
                xt_last_dt = df_xt.iloc[-1]["datetime"] if xt_bars > 0 else None
                xt_total, xt_detail = _score_all(df_xt) if xt_bars > 0 else (0, {})
            except Exception as e:
                xt_time = time.time() - t0
                xt_bars = 0
                xt_close = None
                xt_last_dt = None
                xt_total = 0
                xt_detail = {}
                print(f"  [xtquant] 失败: {e}")

            # ---- akshare ----
            t0 = time.time()
            try:
                df_ak = fetch_akshare_direct(sym, exch, period, length)
                ak_time = time.time() - t0
                ak_bars = len(df_ak) if df_ak is not None else 0
                ak_close = df_ak.iloc[-1]["close"] if ak_bars > 0 else None
                ak_last_dt = df_ak.iloc[-1]["datetime"] if ak_bars > 0 else None
                ak_total, ak_detail = _score_all(df_ak) if ak_bars > 0 else (0, {})
            except Exception as e:
                ak_time = time.time() - t0
                ak_bars = 0
                ak_close = None
                ak_last_dt = None
                ak_total = 0
                ak_detail = {}
                print(f"  [akshare] 失败: {e}")

            # ---- 对比输出 ----
            print(f"\n  {'指标':<14} {'xtquant':<28} {'akshare':<28}")
            print(f"  {'─'*14} {'─'*28} {'─'*28}")
            print(f"  {'耗时(秒)':<14} {xt_time:<28.2f} {ak_time:<28.2f}")
            print(f"  {'bar 数':<14} {xt_bars:<28} {ak_bars:<28}")
            print(f"  {'最新收盘':<14} {xt_close if xt_close else '-':<28} {ak_close if ak_close else '-':<28}")
            print(f"  {'最新时间':<14} {str(xt_last_dt)[:19] if xt_last_dt else '-':<28} {str(ak_last_dt)[:19] if ak_last_dt else '-':<28}")
            print(f"  {'总分':<14} {xt_total:<28} {ak_total:<28}")
            print(f"  {'评级':<14} {star(xt_total):<28} {star(ak_total):<28}")

            # 五维明细
            if xt_detail and ak_detail:
                print(f"\n  {'五维明细':<14} {'xtquant':<28} {'akshare':<28}")
                print(f"  {'─'*14} {'─'*28} {'─'*28}")
                for dim in ("突破", "回调", "异常K", "共振", "动量"):
                    xt_s = xt_detail[dim][0]
                    ak_s = ak_detail[dim][0]
                    diff = xt_s - ak_s
                    flag = "" if diff == 0 else f" (差{diff:+d})"
                    print(f"  {dim:<14} {xt_s:<28} {ak_s:<28}{flag}")

            # 收盘价差异
            if xt_close and ak_close and xt_close > 0 and ak_close > 0:
                diff_pct = abs(xt_close - ak_close) / ak_close * 100
                print(f"\n  收盘价差异: {diff_pct:.3f}%")

    print(f"\n{'=' * 80}")
    print("  对比完成")
    print("=" * 80)


if __name__ == "__main__":
    main()
