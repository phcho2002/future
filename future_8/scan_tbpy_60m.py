#!/usr/bin/env python3
"""future_8 fakebreak — 小时线(60m)实时扫描 (xtquant 数据源)。

数据源：xtquant token 模式（迅投行情，主力连续 rb00.SF 等）。
要求：D:/work_ai/xt_token.py 配置了迅投投研 token。

品种：T1/T2 重点品种（PP/IM/LC/JM/SC/IC）。
周期：60m（小时线），15m 参数按周期等比缩放。
用法： python scan_tbpy_60m.py
"""
from __future__ import annotations
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_WORK_AI = _HERE.parent
for _p in (str(_WORK_AI), str(_HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pandas as pd

# 友好提示：xtquant 需 pip install xtquant
try:
    import xtquant  # noqa: F401
except ImportError:
    print("[错误] 未找到 xtquant。请安装: pip install xtquant")
    sys.exit(1)

from fakebreak.config import FakeBreakConfig, TIER1, TIER2, EXCLUDED
from fakebreak.signal import generate_signal
from fakebreak.types import SignalSide
from fakebreak.xtquant_provider import get_klines_batch

# ── 目标品种（与 scan_specific_60m.py 对齐：T1/T2 重点）──
TARGETS = [
    ("PP0", "聚丙烯", "dce"),
    ("IM0", "中证1000指数", "cffex"),
    ("LC0", "碳酸锂", "gfex"),
    ("JM0", "焦煤", "dce"),
    ("SC0", "上海原油", "ine"),
    ("IC0", "中证500指数", "cffex"),
]

# ── 参数（15m → 60m 等比缩放，与 scan_specific_60m.py 一致）──
COUNT = 300          # 拉取根数


def get_tier(symbol: str) -> str:
    if symbol in TIER1:
        return "T1"
    if symbol in TIER2:
        return "T2"
    if symbol in EXCLUDED:
        return "EX"
    return "T3"


def main():
    print(f"{'='*78}")
    print(f"  future_8 假突破反转 — 小时线(60m)实时扫描")
    print(f"  数据源: xtquant (迅投行情, 主力连续) | 品种: {len(TARGETS)} 个")
    print(f"  {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*78}")

    # 配置参数
    cfg = FakeBreakConfig()
    cfg.swing_window = 60
    cfg.zone_window = 60
    cfg.signal_lookback = 10
    cfg.post_break_bars = 5

    # ── 一次性批量拉取所有品种（算 1 次限频查询，几秒搞定）──
    codes = [c for c, _, _ in TARGETS]
    print(f"  批量拉取 {len(codes)} 个品种 60m K线（count={COUNT}）...")
    klines = get_klines_batch(codes, freq="60m", count=COUNT)
    print(f"  命中 {len(klines)}/{len(codes)} 个品种，开始扫描...\n")

    rows_long, rows_short, errors = [], [], []
    start = time.time()

    for i, (code, name, ex) in enumerate(TARGETS):
        tier = get_tier(code)
        print(f"  [{i+1}/{len(TARGETS)}] {code} {name} [{tier}] ...", end=" ", flush=True)

        df = klines.get(code)
        if df is None or df.empty or len(df) < 60:
            print(f"数据不足 ({len(df) if df is not None else 0} 行)")
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
        except Exception as e:  # noqa: BLE001
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
        out = _HERE / "fakebreak_60m_xtquant_signals.csv"
        pd.DataFrame(all_rows).to_csv(out, index=False, encoding="utf-8-sig")
        print(f"\n  结果已写: {out}")

    if errors:
        print(f"\n  错误 ({len(errors)} 个):")
        for c, e in errors:
            print(f"    {c}: {e}")

    print(f"\n{'='*78}")


if __name__ == "__main__":
    main()
