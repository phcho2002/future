#!/usr/bin/env python3
"""
future_8 假突破反转系统 — 60分钟数据，akshare 数据源
结果写入 fakebreak_60m_signals_<ts>.csv
"""
import os, sys, time, json, sqlite3
from datetime import datetime
from pathlib import Path

import akshare as ak
import pandas as pd
import numpy as np

BASE = Path("D:/work_ai")
F8 = BASE / "future_8"
DB_PATH = BASE / "futures_data.db"

# 确保 future_8/fakebreak 可被 import
sys.path.insert(0, str(F8))

from fakebreak.config import FakeBreakConfig
from fakebreak.signal import generate_signal
from fakebreak.types import SignalSide


def get_universe(top=40):
    """从 futures_top40.json 读品种池"""
    p = BASE / "futures_top40.json"
    if p.exists():
        with open(p, encoding="utf-8") as f:
            raw = json.load(f)
        rows = raw.get("symbols", [])
        syms = []
        for i, r in enumerate(rows, 1):
            if isinstance(r, (list, tuple)) and len(r) >= 3:
                syms.append({"排名": i, "symbol": r[0], "name": r[1], "exchange": r[2]})
            elif isinstance(r, dict):
                syms.append(r)
        return syms[:top]
    # 回退 futures_data.db
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(f"SELECT symbol, name, exchange FROM futures_top40 ORDER BY 排名 LIMIT {top}")
    rows = [{"排名": i + 1, "symbol": r[0], "name": r[1], "exchange": r[2]} for i, r in enumerate(cur.fetchall())]
    conn.close()
    return rows


def fetch_60m(symbol: str, length: int = 300) -> pd.DataFrame | None:
    """ak新浪获取60分钟K线并规范化"""
    try:
        raw = ak.futures_zh_minute_sina(symbol=symbol, period="60")
        if raw is None or raw.empty:
            return None
        df = raw.copy()
        # 列名规范化
        col_map = {}
        cn = {"日期": "date", "开盘": "open", "最高": "high", "最低": "low",
              "收盘": "close", "成交量": "volume", "持仓量": "hold", "动态结算价": "settle"}
        for c in df.columns:
            c2 = str(c).strip().lower()
            if c2 in cn:
                col_map[c] = cn[c2]
            elif c2 in ("date", "datetime", "open", "high", "low", "close", "volume"):
                col_map[c] = c2
            else:
                col_map[c] = c2
        df = df.rename(columns=col_map)
        if "datetime" not in df.columns and "date" in df.columns:
            df["datetime"] = pd.to_datetime(df["date"], errors="coerce")
        else:
            df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce")
        need = ["open", "high", "low", "close", "volume"]
        for c in need:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")
        df = df.dropna(subset=["datetime", "open", "high", "low", "close"])
        df = df.sort_values("datetime").reset_index(drop=True)
        if len(df) > length:
            df = df.tail(length).reset_index(drop=True)
        return df
    except:
        return None


def main():
    print("=" * 78)
    print("  future_8 假突破反转 — 60分钟 (akshare)")
    print(f"  {datetime.now():%Y-%m-%d %H:%M:%S}")
    print("=" * 78)

    symbols = get_universe(40)
    print(f"\n  品种池: {len(symbols)} 个\n")

    # 配置: 60m 参数
    cfg = FakeBreakConfig()
    cfg.swing_window = 60
    cfg.zone_window = 60
    cfg.signal_lookback = 12
    cfg.post_break_bars = 5

    long_sigs, short_sigs, errors, empty = [], [], [], 0

    for i, s in enumerate(symbols, 1):
        sym, name, ex = s["symbol"], s["name"], s["exchange"]
        print(f"  [{i:2d}/{len(symbols)}] {sym:6s} {name:10s}  ... ", end="", flush=True)

        df = fetch_60m(sym, length=300)
        if df is None or len(df) < 60:
            print("无/少数据")
            empty += 1
            continue

        try:
            sig = generate_signal(df, cfg)
            if sig is not None and sig.is_valid and sig.levels:
                lv = sig.levels
                tag = "V" if sig.volume_confirm else "x"
                print(f"{sig.pattern} 入={lv.entry} 止={lv.stop} 目={lv.target} RR={lv.reward_risk:.2f} {tag}")
                row = {
                    "code": sym, "name": name, "tier": s.get("排名", ""),
                    "dir": "做多" if sig.side == SignalSide.LONG else "做空",
                    "pattern": sig.pattern,
                    "entry": lv.entry, "stop": lv.stop, "target": lv.target,
                    "rr": round(lv.reward_risk, 2),
                    "vol_confirm": sig.volume_confirm,
                    "zone": f"{sig.zone.center:.1f}" if sig.zone else "",
                    "reason": sig.reason[:80],
                }
                (long_sigs if sig.side == SignalSide.LONG else short_sigs).append(row)
            else:
                reason = sig.reason[:40] if sig else "未知"
                print(f"-- ({reason})")
        except Exception as e:
            print(f"ERR {str(e)[:60]}")
            errors.append((sym, name, str(e)[:60]))

        time.sleep(0.5)  # 新浪限速

    # ──结果──
    print(f"\n  扫描完成: 做多 {len(long_sigs)}  做空 {len(short_sigs)}  错误 {len(errors)}  无数据 {empty}")

    # 排序
    df_long = pd.DataFrame(long_sigs)
    df_short = pd.DataFrame(short_sigs)
    for d in (df_long, df_short):
        if not d.empty:
            d.sort_values(["vol_confirm", "rr"], ascending=[False, False], inplace=True)

    print(f"\n{'='*78}")
    print("  ↑ 做多 (Spring: 假跌破支撑后收回)")
    print(f"{'='*78}")
    if not df_long.empty:
        for _, r in df_long.iterrows():
            print(f"  {r['code']:4s} {r['name'][:10]:12s} 入={r['entry']} 止={r['stop']} 目={r['target']} RR={r['rr']:.2f} {'V' if r['vol_confirm'] else 'x'} [{r['reason']}]")
    else:
        print("  (无做多信号)")

    print(f"\n{'='*78}")
    print("  ↓ 做空 (Upthrust: 假突破阻力后回落)")
    print(f"{'='*78}")
    if not df_short.empty:
        for _, r in df_short.iterrows():
            print(f"  {r['code']:4s} {r['name'][:10]:12s} 入={r['entry']} 止={r['stop']} 目={r['target']} RR={r['rr']:.2f} {'V' if r['vol_confirm'] else 'x'} [{r['reason']}]")
    else:
        print("  (无做空信号)")

    all_rows = long_sigs + short_sigs
    if all_rows:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_file = F8 / f"fakebreak_60m_signals_{ts}.csv"
        pd.DataFrame(all_rows).to_csv(out_file, index=False, encoding="utf-8-sig")
        print(f"\n  结果写入: {out_file}")

    if errors:
        print(f"\n  错误 ({len(errors)} 个):")
        for c, n, e in errors[:5]:
            print(f"    {c} {n}: {e}")


if __name__ == "__main__":
    main()
