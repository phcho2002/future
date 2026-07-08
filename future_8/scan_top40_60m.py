#!/usr/bin/env python3
"""TOP40 fakebreak scan — 60min K线 (小时线假突破反转).

使用系统内置参数，仅将周期从15m改为60m，窗口参数按周期比例缩放。
数据源: quote_cache parquet (绕过tqsdk)
品种池: futures_top40.json
"""
from __future__ import annotations
import sys
import time
import json
from pathlib import Path

# 确保 future_8 可被 import
_HERE = Path(__file__).resolve().parent
_WORK_AI = _HERE.parent
sys.path.insert(0, str(_WORK_AI))
sys.path.insert(0, str(_HERE))

import numpy as np
import pandas as pd

from fakebreak.config import FakeBreakConfig, load_config, TIER1, TIER2, EXCLUDED
from fakebreak.signal import generate_signal
from fakebreak.types import SignalSide


def main():
    # ── 加载品种表 ──
    with open(_WORK_AI / "futures_top40.json", encoding="utf-8") as f:
        raw = json.load(f)

    symbols = []
    for i, r in enumerate(raw.get("symbols", []), 1):
        if isinstance(r, (list, tuple)) and len(r) >= 3:
            symbols.append({"排名": i, "symbol": r[0], "name": r[1], "exchange": r[2]})
        elif isinstance(r, dict):
            symbols.append(r)

    tier_map = {}
    for s in symbols:
        sym = s["symbol"]
        if sym in TIER1:
            tier_map[sym] = "T1"
        elif sym in TIER2:
            tier_map[sym] = "T2"
        elif sym in EXCLUDED:
            tier_map[sym] = "EX"
        else:
            tier_map[sym] = "T3"

    # ── 加载 60m parquet ──
    cache_dir = _WORK_AI / "quote_cache"
    klines = {}
    for s in symbols:
        sym = s["symbol"]
        f = cache_dir / f"{sym}_60m.parquet"
        if f.exists():
            try:
                df = pd.read_parquet(f)
                df.columns = [c.lower() for c in df.columns]
                if len(df) >= 60:
                    klines[sym] = df
            except:
                pass

    print(f"{'='*78}")
    print(f"  TOP40 期货 小时线(60m) 假突破反转信号扫描 (future_8)")
    print(f"  {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  数据源: quote_cache parquet (绕过tqsdk)")
    print(f"  品种池: futures_top40.json ({len(symbols)} 个)")
    print(f"  K线命中: {len(klines)} 个品种")
    print(f"{'='*78}")

    # ── 配置参数: 15m → 60m 缩放 ──
    cfg = FakeBreakConfig()
    # 15m→60m 窗口等比缩放 (原15m参数是60根，60m只需15根保持等效)
    cfg.swing_window = 60
    cfg.zone_window = 60
    cfg.signal_lookback = 10
    cfg.post_break_bars = 5

    rows_long, rows_short, errors = [], [], []
    start = time.time()

    for i, sym in enumerate(symbols):
        code, name, ex = sym["symbol"], sym["name"], sym["exchange"]
        tier = tier_map.get(code, "?")
        if code not in klines:
            continue
        df = klines[code]

        print(f"  [{i+1}/{len(symbols)}] {code} {name} [{tier}] ...", end=" ", flush=True)
        try:
            sig = generate_signal(df, cfg)
            if sig.is_valid and sig.levels:
                lv = sig.levels
                tag = "V" if sig.volume_confirm else "x"
                print(f"{sig.pattern} 入={lv.entry} 止={lv.stop} 目={lv.target} RR={lv.reward_risk:.2f} {tag}")
                row = {
                    "code": code, "name": name, "tier": tier,
                    "dir": "做多" if sig.side == SignalSide.LONG else "做空",
                    "pattern": sig.pattern,
                    "entry": lv.entry, "stop": lv.stop, "target": lv.target,
                    "rr": round(lv.reward_risk, 2),
                    "vol_confirm": sig.volume_confirm,
                    "zone": f"{sig.zone.center:.1f}" if sig.zone else "",
                    "reason": sig.reason[:50],
                }
                (rows_long if sig.side == SignalSide.LONG else rows_short).append(row)
            else:
                print(f"-- ({sig.reason[:30]})")
        except Exception as e:
            print(f"ERR {str(e)[:80]}")
            errors.append((code, name, str(e)[:80]))

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
        for c, n, e in errors:
            print(f"    {c} {n}: {e}")

    print(f"\n{'='*78}")


if __name__ == "__main__":
    main()
