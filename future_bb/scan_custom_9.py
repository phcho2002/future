#!/usr/bin/env python3
"""临时扫描脚本 — 只扫描指定9个品种"""
import os
import sys
import json
import time
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

_WORK_AI = Path(r"D:\work_ai")
_HERE = _WORK_AI / "future_bb"
sys.path.insert(0, str(_WORK_AI))
sys.path.insert(0, str(_HERE))

import numpy as np
import pandas as pd
import akshare as ak

import config
from breakout_detector import BreakoutDetector
from filter_conditions import SignalFilter
from indicators import Indicators

# ── 自定义品种池 ──
CUSTOM_SYMBOLS = ["PP0", "IM0", "LC0", "JM0", "SC0", "IC0", "OI0", "AG0", "SN0"]

# 读取 futures_top40.json 获取名称和交易所
with open(_WORK_AI / "futures_top40.json", encoding="utf-8") as f:
    raw = json.load(f)

sym_info = {}
for r in raw.get("symbols", []):
    if isinstance(r, (list, tuple)) and len(r) >= 3:
        sym_info[r[0]] = {"name": r[1], "exchange": r[2]}

# 构建品种池
symbols = []
for code in CUSTOM_SYMBOLS:
    info = sym_info.get(code, {"name": code, "exchange": ""})
    symbols.append({"symbol": code, "name": info["name"], "exchange": info["exchange"]})

print(f"{'='*70}")
print(f"  future_bb 突破系统 — 自定义品种扫描 (9个)")
print(f"  数据源: akshare (60m) | 品种: {len(symbols)} 个")
print(f"  品种: {', '.join(CUSTOM_SYMBOLS)}")
print(f"  {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S')}")
print(f"{'='*70}")

# ── 数据获取 ──
def get_60m_data(symbol, use_cache=True):
    """获取60分钟K线数据（直接实时获取，不用缓存）"""
    try:
        df = ak.futures_zh_minute_sina(symbol=symbol, period="60")
        if df is None or df.empty:
            return pd.DataFrame()
        df.columns = [c.lower() for c in df.columns]
        if "datetime" not in df.columns and "date" in df.columns:
            df = df.rename(columns={"date": "datetime"})
        if "open_interest" not in df.columns and "hold" in df.columns:
            df = df.rename(columns={"hold": "open_interest"})
        for col in ["open", "high", "low", "close", "volume"]:
            if col not in df.columns:
                return pd.DataFrame()
        if "open_interest" not in df.columns:
            df["open_interest"] = 0
        df = df.sort_values("datetime").reset_index(drop=True)
        return df
    except Exception as e:
        return pd.DataFrame()

# ── 信号生成 ──
detector = BreakoutDetector()
sf = SignalFilter()

all_signals = []
errors = []

for i, sym in enumerate(symbols):
    code = sym["symbol"]
    name = sym["name"]
    print(f"  [{i+1}/{len(symbols)}] {code} {name} ...", end=" ", flush=True)
    
    df = get_60m_data(code, use_cache=False)
    if df.empty or len(df) < 60:
        ndf = len(df) if df is not None else 0
        print(f"数据不足 ({ndf} 行)")
        continue
    
    try:
        df = detector.detect(df)
        df = detector.detect_short(df)
        df = sf.filter_and_score(df)
        df = df.reset_index(drop=True)
        latest = df.iloc[-1]
        
        long_sig = bool(latest.get("final_signal_long", False))
        short_sig = bool(latest.get("final_signal_short", False))
        score = float(latest.get("total_score_long", 0) if long_sig else latest.get("total_score_short", 0))
        
        if long_sig or short_sig:
            direction = "做多" if long_sig else "做空"
            price = float(latest["close"])
            atr = float(latest.get("atr", 0))
            adx = float(latest.get("adx", 0))
            
            if long_sig:
                stop = price - atr * config.INITIAL_STOP_ATR
                target = price + atr * config.INITIAL_STOP_ATR * config.PROFIT_TAKE_1_RATIO
            else:
                stop = price + atr * config.INITIAL_STOP_ATR
                target = price - atr * config.INITIAL_STOP_ATR * config.PROFIT_TAKE_1_RATIO
            
            rr = abs(target - price) / abs(price - stop) if abs(price - stop) > 0 else 0
            
            print(f"★ {direction} 价={price:.1f} 分={score:.0f} ADX={adx:.0f} RR={rr:.2f}")
            
            all_signals.append({
                "code": code,
                "name": name,
                "direction": direction,
                "price": price,
                "atr": atr,
                "adx": adx,
                "score": score,
                "rr": rr,
                "stop": stop,
                "target": target,
                "datetime": str(latest.get("datetime", "")),
            })
        else:
            price = float(latest["close"])
            atr = float(latest.get("atr", 0))
            adx = float(latest.get("adx", 0))
            resistance = float(latest.get("resistance_level", 0))
            support = float(latest.get("support_level", 0))
            
            reason_parts = []
            if not bool(latest.get("breakout_signal", False)) and not bool(latest.get("short_breakout_signal", False)):
                if resistance > 0:
                    gap = (resistance * (1 + config.BREAKOUT_THRESHOLD) - price) / price * 100
                    reason_parts.append(f"距前高+{gap:.2f}%")
                if support > 0:
                    gap = (price - support * (1 - config.SHORT_BREAKOUT_THRESHOLD)) / price * 100
                    reason_parts.append(f"距前低-{gap:.2f}%")
            else:
                reason_parts.append("ADX/波动/结构未达标")
            
            print(f"-- ({', '.join(reason_parts)})")
    
    except Exception as e:
        print(f"ERR {str(e)[:80]}")
        errors.append((code, str(e)[:80]))

# ── 输出 ──
print(f"\n{'='*70}")
print(f"  扫描完成: {len(all_signals)} 个信号 / {len(errors)} 个错误")
print(f"{'='*70}")

if all_signals:
    longs = sorted([s for s in all_signals if s["direction"] == "做多"], key=lambda x: -x["score"])
    shorts = sorted([s for s in all_signals if s["direction"] == "做空"], key=lambda x: -x["score"])
    
    print(f"\n  【做多信号】({len(longs)} 个)")
    print(f"  {'品种':6s} {'名称':12s} {'价格':>10s} {'止损':>10s} {'止盈':>10s} {'ATR':>8s} {'ADX':>5s} {'分':>4s} {'RR':>4s}")
    for s in longs:
        print(f"  {s['code']:6s} {s['name'][:12]:12s} {s['price']:10.1f} {s['stop']:10.1f} {s['target']:10.1f} {s['atr']:8.1f} {s['adx']:5.0f} {s['score']:4.0f} {s['rr']:4.2f}")
    
    print(f"\n  【做空信号】({len(shorts)} 个)")
    print(f"  {'品种':6s} {'名称':12s} {'价格':>10s} {'止损':>10s} {'止盈':>10s} {'ATR':>8s} {'ADX':>5s} {'分':>4s} {'RR':>4s}")
    for s in shorts:
        print(f"  {s['code']:6s} {s['name'][:12]:12s} {s['price']:10.1f} {s['stop']:10.1f} {s['target']:10.1f} {s['atr']:8.1f} {s['adx']:5.0f} {s['score']:4.0f} {s['rr']:4.2f}")
    
    # 保存CSV
    out = _HERE / "signals_custom_9.csv"
    pd.DataFrame(all_signals).to_csv(out, index=False, encoding="utf-8-sig")
    print(f"\n  结果已写: {out}")
else:
    print("\n  (无信号)")

print(f"\n{'='*70}")
