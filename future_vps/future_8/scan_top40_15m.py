"""TOP40 期货 15分钟 K线 假突破反转信号扫描。

注入模式：先 inject_many 单连接批量注入 15m 缓存（滚动窗口），再逐品种
generate_signal，输出做多/做空候选 + CSV。

输出格式与 future_1/scan_top40_15m.py 对齐，便于横向对比
（future_1 做突破 vs future_8 做假突破反转 —— 二者应在同一位置出现但方向相反）。
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

import numpy as np
import pandas as pd

from future_data import inject_many

from fakebreak.config import load_config
from fakebreak.data_loader import load_top40
from fakebreak.signal import generate_signal
from fakebreak.types import SignalSide


def main():
    print(f"{'='*78}")
    print(f"  TOP40 期货 15分钟 假突破反转信号扫描 (future_8)")
    print(f"  {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*78}")

    cfg = load_config()
    symbols = load_top40()
    print(f"  加载 {len(symbols)} 个品种")

    tuples = [(s["symbol"], s["name"], s["exchange"]) for s in symbols]
    print(f"  注入15分钟K线数据（滚动窗口 {cfg.trend_window + cfg.zone_window + 10} 根）...")
    # 窗口只要够趋势闸+密集区+信号回看用即可
    win = max(cfg.trend_window + 10, cfg.zone_window + 10, 120)
    klines = inject_many(tuples, period="15", length=win)
    print(f"  成功注入 {len(klines)}/{len(symbols)} 个品种，开始扫描...\n")

    rows_long, rows_short, errors = [], [], []
    start = time.time()
    for i, sym in enumerate(symbols):
        code, name, ex = sym["symbol"], sym["name"], sym["exchange"]
        if code not in klines:
            continue
        df = klines[code]
        if df is None or df.empty or len(df) < 60:
            continue

        print(f"  [{i+1}/{len(symbols)}] {code} {name} ...", end=" ", flush=True)
        try:
            sig = generate_signal(df, cfg)
            if sig.is_valid and sig.levels:
                lv = sig.levels
                tag = "✅" if sig.volume_confirm else "·"
                print(f"{sig.pattern} 入={lv.entry} 止={lv.stop} 目={lv.target} RR={lv.reward_risk:.2f} {tag}")
                row = {
                    "代码": code, "名称": name, "方向": "做多" if sig.side == SignalSide.LONG else "做空",
                    "形态": sig.pattern, "入场价": lv.entry, "止损价": lv.stop,
                    "目标价": lv.target, "盈亏比": round(lv.reward_risk, 2),
                    "放量确认": sig.volume_confirm,
                    "密集区": f"{sig.zone.center:.1f}" if sig.zone else "",
                    "信号原因": sig.reason[:50],
                }
                (rows_long if sig.side == SignalSide.LONG else rows_short).append(row)
            else:
                print(f"无信号 ({sig.reason[:30]})")
        except Exception as e:  # noqa: BLE001
            print(f"❌ {str(e)[:60]}")
            errors.append((code, name, str(e)[:60]))

    elapsed = time.time() - start
    print(f"\n  扫描完成: 耗时 {elapsed:.0f}s")
    print(f"  做多信号: {len(rows_long)}  做空信号: {len(rows_short)}  错误: {len(errors)}")

    # 排序：放量确认优先，其次盈亏比
    df_long = pd.DataFrame(rows_long)
    df_short = pd.DataFrame(rows_short)
    for d in (df_long, df_short):
        if not d.empty:
            d.sort_values(["放量确认", "盈亏比"], ascending=[False, False], inplace=True)

    print(f"\n{'='*78}")
    print(f"  【做多候选】⬆️ Spring（假跌破支撑后收回）")
    print(f"{'='*78}")
    if not df_long.empty:
        print(df_long.to_string(index=False))
    else:
        print("  (无)")

    print(f"\n{'='*78}")
    print(f"  【做空候选】⬇️ Upthrust（假突破阻力后收回）")
    print(f"{'='*78}")
    if not df_short.empty:
        print(df_short.to_string(index=False))
    else:
        print("  (无)")

    # CSV
    all_rows = rows_long + rows_short
    if all_rows:
        out = _HERE / "scan_results.csv"
        pd.DataFrame(all_rows).to_csv(out, index=False, encoding="utf-8-sig")
        print(f"\n  结果已写: {out}")

    print(f"\n{'='*78}")


if __name__ == "__main__":
    main()
