#!/usr/bin/env python3
"""future_8 fakebreak — 多周期(60m+15m)实时扫描 (xtquant 数据源)。

数据源：xtquant token 模式（迅投行情，主力连续 rb00.SF 等）。
要求：D:/work_ai/xt_token.py 配置了迅投投研 token。

策略：60m 定趋势+关键价位（密集区+swing点）→ 价格接近关键位(1ATR内)
      → 15m 找入场形态（阳吞阴/阴吞阳 或 5根合并K实体）。
品种：T1/T2 重点品种（PP/IM/LC/JM/SC/IC）。
用法： python scan_mtf_60m_15m.py
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
from fakebreak.mtf_signal import generate_mtf_signal
from fakebreak.types import SignalSide
from fakebreak.xtquant_provider import get_klines_batch

# ── 目标品种（与 scan_tbpy_60m.py 对齐：T1/T2 重点）──
TARGETS = [
    ("PP0", "聚丙烯", "dce"),
    ("IM0", "中证1000指数", "cffex"),
    ("LC0", "碳酸锂", "gfex"),
    ("JM0", "焦煤", "dce"),
    ("SC0", "上海原油", "ine"),
    ("IC0", "中证500指数", "cffex"),
]

# ── 参数 ──
COUNT_60M = 120          # 60m 拉取根数（需 ≥ swing_window/zone_window）
COUNT_15M = 300          # 15m 拉取根数（需 ≥ merge_bars_n，且覆盖与60m同时段）


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
    print(f"  future_8 多周期(MTF)扫描 — 60m定关键位 + 15m找形态")
    print(f"  数据源: xtquant (迅投行情, 主力连续) | 品种: {len(TARGETS)} 个")
    print(f"  {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*78}")

    cfg = FakeBreakConfig()
    print(f"  参数: proximity={cfg.proximity_atr}ATR engulf>={cfg.engulf_min_body_atr}ATR merge_n={cfg.merge_bars_n} swing_zones={cfg.use_swing_zones}")

    codes = [c for c, _, _ in TARGETS]

    # ── 批量拉取 60m + 15m 两套 ──
    print(f"\n  [1/2] 批量拉取 {len(codes)} 个品种 60m K线（count={COUNT_60M}）...")
    klines_60m = get_klines_batch(codes, freq="60m", count=COUNT_60M)
    print(f"        命中 {len(klines_60m)}/{len(codes)}")

    print(f"  [2/2] 批量拉取 {len(codes)} 个品种 15m K线（count={COUNT_15M}）...")
    klines_15m = get_klines_batch(codes, freq="15m", count=COUNT_15M)
    print(f"        命中 {len(klines_15m)}/{len(codes)} 个品种，开始扫描...\n")

    rows_long, rows_short, errors = [], [], []
    start = time.time()

    for i, (code, name, ex) in enumerate(TARGETS):
        tier = get_tier(code)
        print(f"  [{i+1}/{len(TARGETS)}] {code} {name} [{tier}] ...", end=" ", flush=True)

        df_60m = klines_60m.get(code)
        df_15m = klines_15m.get(code)
        if df_60m is None or df_60m.empty or len(df_60m) < 60:
            print(f"60m数据不足 ({len(df_60m) if df_60m is not None else 0} 行)")
            continue
        if df_15m is None or df_15m.empty or len(df_15m) < 10:
            print(f"15m数据不足 ({len(df_15m) if df_15m is not None else 0} 行)")
            continue

        try:
            sig = generate_mtf_signal(df_60m, df_15m, cfg)
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
                    "zone": f"{sig.zone.center:.1f}({sig.zone.source})" if sig.zone else "",
                    "reason": sig.reason[:80],
                    "bars_60m": len(df_60m), "bars_15m": len(df_15m),
                    "latest_15m": str(df_15m.iloc[-1].get("datetime", "")),
                }
                (rows_long if sig.side == SignalSide.LONG else rows_short).append(row)
            else:
                print(f"-- ({sig.reason[:50]})")
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
    print(f"  【做多候选】↑ 上升趋势+回落+接近支撑+15m阳线形态")
    print(f"{'='*78}")
    if not df_long.empty:
        for _, r in df_long.iterrows():
            print(f"  {r['code']:4s} {r['name'][:10]:12s} [{r['tier']}] 入={r['entry']} 止={r['stop']} 目={r['target']} RR={r['rr']:.2f} {'V' if r['vol_confirm'] else 'x'} zone={r['zone']} [{r['reason']}]")
    else:
        print("  (无)")

    print(f"\n{'='*78}")
    print(f"  【做空候选】↓ 下降趋势+反弹+接近阻力+15m阴线形态")
    print(f"{'='*78}")
    if not df_short.empty:
        for _, r in df_short.iterrows():
            print(f"  {r['code']:4s} {r['name'][:10]:12s} [{r['tier']}] 入={r['entry']} 止={r['stop']} 目={r['target']} RR={r['rr']:.2f} {'V' if r['vol_confirm'] else 'x'} zone={r['zone']} [{r['reason']}]")
    else:
        print("  (无)")

    all_rows = rows_long + rows_short
    if all_rows:
        out = _HERE / "mtf_60m_15m_xtquant_signals.csv"
        pd.DataFrame(all_rows).to_csv(out, index=False, encoding="utf-8-sig")
        print(f"\n  结果已写: {out}")

    if errors:
        print(f"\n  错误 ({len(errors)} 个):")
        for c, e in errors:
            print(f"    {c}: {e}")

    print(f"\n{'='*78}")


if __name__ == "__main__":
    main()
