"""CLI: 拉 9 品种 × 3000 根 15min K 线 → ZigZag 切分 → 验证 + 画图。

用法（在 work_ai 根目录）:
    python -m future_zigzag.run                 # 用默认 depth=1.0
    python -m future_zigzag.run --depth 1.5     # 调 depth
    python -m future_zigzag.run --depth 1.0 --period 60

输出到 future_zigzag/output/:
    {symbol}_overview.png   全景图
    {symbol}_closeup.png    特写图
    zigzag_report.csv       9 品种验证汇总
    depth_sensitivity.csv   depth 敏感度
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pandas as pd

# 注入 work_ai 根，以便 import future_data / future_zigzag
_SCRIPT_DIR = Path(__file__).resolve().parent
_WORK_AI = _SCRIPT_DIR.parent
sys.path.insert(0, str(_WORK_AI))

from future_data import get_klines  # noqa: E402

from future_zigzag.config import SYMBOLS, ZigZagConfig  # noqa: E402
from future_zigzag.zigzag import detect_zigzag  # noqa: E402
from future_zigzag import validate  # noqa: E402
from future_zigzag import visualize  # noqa: E402

OUTPUT_DIR = _SCRIPT_DIR / "output"
MIN_BARS = 3000


def fetch_one(symbol: str, exchange: str, period: str, length: int) -> pd.DataFrame:
    """拉单品种 K 线（force=True 走 xtquant 全量，支持深历史）。"""
    df = get_klines(symbol, exchange, period=period, length=length, force=True)
    if df is None or df.empty:
        raise RuntimeError(f"{symbol} 无数据")
    # 标准 schema: datetime/open/high/low/close/volume，升序
    df = df.reset_index(drop=True)
    return df


def run(period: str = "15", length: int = MIN_BARS, depth: float = 1.0,
        reversal_mode: str = "extreme") -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    cfg = ZigZagConfig(depth_atr_multiple=depth, reversal_mode=reversal_mode)

    print("=" * 78)
    print(f"  ZigZag 验证  period={period}  length={length}  depth={depth}×ATR  mode={reversal_mode}")
    print("=" * 78)

    reports = []
    sens_rows = []
    n_ok = 0

    for i, (sym, name, exch) in enumerate(SYMBOLS):
        print(f"\n[{i+1}/{len(SYMBOLS)}] {sym} {name} ({exch}) 拉数据...", flush=True)
        t0 = time.time()
        try:
            df = fetch_one(sym, exch, period=period, length=length)
        except Exception as e:
            print(f"  ❌ 拉取失败: {e}")
            continue
        print(f"  拿到 {len(df)} 根 ({len(df)/ (time.time()-t0):.0f} 根/s)")

        if len(df) < MIN_BARS:
            print(f"  ⚠️ 数据不足 {MIN_BARS} 根 (实际 {len(df)})，跳过")
            continue
        if len(df) > MIN_BARS:
            df = df.tail(MIN_BARS).reset_index(drop=True)

        # 切分 + 验证
        res = detect_zigzag(df, cfg)
        rep = validate.full_report(sym, res)
        reports.append(rep)
        validate.print_report(sym, res)

        # depth 敏感度（只对第一个品种做，避免重复耗时；这里每个品种都做以便对比）
        try:
            sens = validate.depth_sensitivity(df, cfg)
            sens.insert(0, "symbol", sym)
            sens_rows.append(sens)
        except Exception as e:
            print(f"  ⚠️ depth敏感度失败: {e}")

        # 画图
        try:
            p1 = visualize.plot_overview(res, sym, name, OUTPUT_DIR)
            p2 = visualize.plot_closeup(res, sym, name, OUTPUT_DIR)
            print(f"  保存 {p1.name}, {p2.name}")
        except Exception as e:
            print(f"  ⚠️ 画图失败: {e}")

        n_ok += 1

    # 汇总
    print("\n" + "=" * 78)
    print("  汇总")
    print("=" * 78)
    if reports:
        df_rep = pd.DataFrame(reports)
        print(df_rep.to_string(index=False))
        df_rep.to_csv(OUTPUT_DIR / "zigzag_report.csv", index=False, encoding="utf-8-sig")
        print(f"\n  已存 {OUTPUT_DIR/'zigzag_report.csv'}")
        print(f"\n  均值段(ATR倍) 跨品种范围: "
              f"{df_rep['mean_seg_atr'].min():.2f} ~ {df_rep['mean_seg_atr'].max():.2f}")
        print(f"  交替率(应=1.0): {df_rep['alternation'].min():.3f} ~ {df_rep['alternation'].max():.3f}")
        print(f"  phantom率(应=0): {df_rep['phantom_rate'].min():.3f} ~ {df_rep['phantom_rate'].max():.3f}")

    if sens_rows:
        df_sens = pd.concat(sens_rows, ignore_index=True)
        df_sens.to_csv(OUTPUT_DIR / "depth_sensitivity.csv", index=False, encoding="utf-8-sig")
        print(f"\n  depth 敏感度已存 {OUTPUT_DIR/'depth_sensitivity.csv'}")

    print(f"\n  成功 {n_ok}/{len(SYMBOLS)} 个品种")
    return 0 if n_ok == len(SYMBOLS) else 1


def main(argv: list[str] | None = None) -> int:
    import argparse
    p = argparse.ArgumentParser(description="ZigZag 波段切分验证")
    p.add_argument("--period", default="15", help="K线周期 (15/30/60/...)")
    p.add_argument("--length", type=int, default=MIN_BARS, help="回看根数")
    p.add_argument("--depth", type=float, default=1.0, help="depth = 该值×ATR")
    p.add_argument("--mode", default="extreme", choices=["extreme", "close"],
                   help="反转触发模式")
    args = p.parse_args(argv)
    return run(period=args.period, length=args.length, depth=args.depth,
               reversal_mode=args.mode)


if __name__ == "__main__":
    sys.exit(main())
