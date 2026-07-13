#!/usr/bin/env python3
"""future_8 fakebreak — 指定品种小时线(60m)扫描 (akshare数据源)

品种: PP0, IM0, LC0, JM0, SC0, IC0
周期: 60m (小时线)
参数: 系统内置 (15m→60m 窗口等比缩放)
"""
from __future__ import annotations
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_WORK_AI = _HERE.parent
sys.path.insert(0, str(_WORK_AI))
sys.path.insert(0, str(_HERE))

import numpy as np
import pandas as pd
import akshare as ak

from fakebreak.config import FakeBreakConfig, TIER1, TIER2, EXCLUDED
from fakebreak.signal import generate_signal
from fakebreak.types import SignalSide

# ── 指定品种 ──
TARGETS = [
    ("PP0", "聚丙烯", "dce"),
    ("IM0", "中证1000指数", "cffex"),
    ("LC0", "碳酸锂", "gfex"),
    ("JM0", "焦煤", "dce"),
    ("SC0", "上海原油", "ine"),
    ("IC0", "中证500指数", "cffex"),
    ("OI0", "菜油", "zce"),
    ("AG0", "白银", "shfe"),
]

# ── 梯队映射 ──
def get_tier(symbol):
    if symbol in TIER1: return "T1"
    if symbol in TIER2: return "T2"
    if symbol in EXCLUDED: return "EX"
    return "T3"

# ── 数据获取 ──
def get_60m_data(symbol, use_cache=True):
    """akshare 60分钟K线 + 缓存"""
    cache_path = _HERE / "cache" / f"{symbol}_60m_ak.pkl"
    
    if use_cache and cache_path.exists():
        try:
            mtime = cache_path.stat().st_mtime
            if time.time() - mtime < 7200:  # 2小时缓存
                with open(cache_path, "rb") as f:
                    df = pd.read_pickle(f)
                return df
        except:
            pass
    
    try:
        df = ak.futures_zh_minute_sina(symbol=symbol, period="60")
        if df is None or df.empty:
            return pd.DataFrame()
        
        df.columns = [c.lower() for c in df.columns]
        if "datetime" not in df.columns and "date" in df.columns:
            df = df.rename(columns={"date": "datetime"})
        
        for col in ["open", "high", "low", "close", "volume"]:
            if col not in df.columns:
                return pd.DataFrame()
        
        df = df.sort_values("datetime").reset_index(drop=True)
        
        # 缓存
        cache_path.parent.mkdir(exist_ok=True)
        df.to_pickle(cache_path)
        return df
    except Exception as e:
        return pd.DataFrame()

# ── 主扫描 ──
def main():
    print(f"{'='*78}")
    print(f"  future_8 假突破反转 — 指定品种小时线(60m)扫描")
    print(f"  数据源: akshare | 品种: {len(TARGETS)} 个")
    print(f"  {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*78}")

    # 配置参数 (15m→60m 缩放)
    cfg = FakeBreakConfig()
    cfg.swing_window = 60
    cfg.zone_window = 60
    cfg.signal_lookback = 10
    cfg.post_break_bars = 5

    rows_long, rows_short, errors = [], [], []
    start = time.time()

    for i, (code, name, ex) in enumerate(TARGETS):
        tier = get_tier(code)
        print(f"  [{i+1}/{len(TARGETS)}] {code} {name} [{tier}] ...", end=" ", flush=True)
        
        df = get_60m_data(code, use_cache=True)
        if df.empty or len(df) < 60:
            ndf = len(df) if df is not None else 0
            print(f"数据不足 ({ndf} 行)")
            continue
        
        try:
            sig = generate_signal(df, cfg)
            if sig.is_valid and sig.levels:
                lv = sig.levels
                tag = "V" if sig.volume_confirm else "x"
                print(f"✓ {sig.pattern} 入={lv.entry} 止={lv.stop} 目={lv.target} RR={lv.reward_risk:.2f} {tag}")
                row = {
                    "code": code, "name": name, "tier": tier,
                    "dir": "做多" if sig.side == SignalSide.LONG else "做空",
                    "pattern": sig.pattern,
                    "entry": lv.entry, "stop": lv.stop, "target": lv.target,
                    "rr": round(lv.reward_risk, 2),
                    "vol_confirm": sig.volume_confirm,
                    "zone": f"{sig.zone.center:.1f}" if sig.zone else "",
                    "reason": sig.reason[:60],
                    "bars": len(df),
                    "latest": str(df.iloc[-1].get("datetime", "")),
                }
                (rows_long if sig.side == SignalSide.LONG else rows_short).append(row)
            else:
                print(f"-- ({sig.reason[:40]})")
        except Exception as e:
            print(f"ERR {str(e)[:80]}")
            errors.append((code, str(e)[:80]))

    elapsed = time.time() - start
    print(f"\n  扫描完成: 耗时 {elapsed:.0f}s")
    print(f"  做多信号: {len(rows_long)}  做空信号: {len(rows_short)}  错误: {len(errors)}")

    # ── 排序 ──
    df_long = pd.DataFrame(rows_long)
    df_short = pd.DataFrame(rows_short)
    for d in (df_long, df_short):
        if not d.empty:
            d.sort_values(["vol_confirm", "rr"], ascending=[False, False], inplace=True)

    print(f"\n{'='*78}")
    print(f"  【做多候选】↑ Spring (假跌破支撑后收回)")
    print(f"{'='*78}")
    if not df_long.empty:
        for _, r in df_long.iterrows():
            print(f"  {r['code']:4s} {r['name'][:10]:12s} [{r['tier']}] 入={r['entry']} 止={r['stop']} 目={r['target']} RR={r['rr']:.2f} {'V' if r['vol_confirm'] else 'x'} [{r['reason']}]")
    else:
        print("  (无)")

    print(f"\n{'='*78}")
    print(f"  【做空候选】↓ Upthrust (假突破阻力后收回)")
    print(f"{'='*78}")
    if not df_short.empty:
        for _, r in df_short.iterrows():
            print(f"  {r['code']:4s} {r['name'][:10]:12s} [{r['tier']}] 入={r['entry']} 止={r['stop']} 目={r['target']} RR={r['rr']:.2f} {'V' if r['vol_confirm'] else 'x'} [{r['reason']}]")
    else:
        print("  (无)")

    all_rows = rows_long + rows_short
    if all_rows:
        out = _HERE / "fakebreak_60m_signals.csv"
        pd.DataFrame(all_rows).to_csv(out, index=False, encoding="utf-8-sig")
        print(f"\n  结果已写: {out}")

    if errors:
        print(f"\n  错误 ({len(errors)} 个):")
        for c, e in errors:
            print(f"    {c}: {e}")

    print(f"\n{'='*78}")

if __name__ == "__main__":
    main()
