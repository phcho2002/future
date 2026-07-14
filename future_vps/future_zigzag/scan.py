"""实盘扫描入口 —— 基于已验证的 ZigZag→ThreePush→收缩→best_signal 链路。

对 TOP40（或指定品种）逐个跑完整信号链路，输出当前有信号的品种及其
入场/止损/目标/盈亏比。适合 cron 定时运行（每 15/30 分钟）。

用法:
    python -m future_zigzag.scan                    # 默认 TOP40, 15min
    python -m future_zigzag.scan --symbols PP0 AG0 SN0
    python -m future_zigzag.scan --period 30 --top 5
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

_SCRIPT_DIR = Path(__file__).resolve().parent
_WORK_AI = _SCRIPT_DIR.parent
sys.path.insert(0, str(_WORK_AI))

from future_data import get_klines  # noqa: E402

from future_zigzag.config import (  # noqa: E402
    SYMBOLS, ZigZagConfig, ThreePushConfig, ContractionConfig, SignalConfig,
    BacktestConfig, DEFAULT_MULTIPLIERS,
)
from future_zigzag.zigzag import detect_zigzag  # noqa: E402
from future_zigzag.three_push import detect_three_push  # noqa: E402
from future_zigzag.contraction import evaluate_contraction  # noqa: E402
from future_zigzag.signals import best_signal  # noqa: E402

TOP40_JSON = _WORK_AI / "futures_top40.json"
OUTPUT_DIR = _SCRIPT_DIR / "output"


def load_symbols(symbols_arg=None):
    """加载品种列表：命令行指定 > TOP40 全部。返回 [(symbol,name,exchange)]。"""
    if symbols_arg:
        # 命令行指定品种，从 top40.json 补全 name/exchange
        with open(TOP40_JSON, encoding="utf-8") as f:
            all_syms = {s[0]: (s[1], s[2]) for s in json.load(f)["symbols"]}
        out = []
        for s in symbols_arg:
            name, exch = all_syms.get(s, (s, ""))
            out.append((s, name, exch))
        return out
    with open(TOP40_JSON, encoding="utf-8") as f:
        return [(s[0], s[1], s[2]) for s in json.load(f)["symbols"]]


def scan_one(symbol, name, exchange, period, length, zcfg, tcfg, ccfg, scfg, bcfg):
    """扫描单品种，返回信号行 dict 或 None。"""
    try:
        df = get_klines(symbol, exchange, period=period, length=length, force=True)
    except Exception as e:
        return {"代码": symbol, "名称": name, "错误": str(e)[:60]}
    if df is None or df.empty or len(df) < 100:
        return None
    df = df.tail(min(len(df), length)).reset_index(drop=True)
    last_close = float(df["close"].iloc[-1])
    last_dt = df["datetime"].iloc[-1]

    zz = detect_zigzag(df, zcfg)
    patterns = detect_three_push(zz, tcfg)
    atr = zz.atr

    # 只看最近一个有效三推模式（最新结构）
    best_pat = None
    for p in reversed(patterns):
        if p.score.hard_gate_passed and p.score.total >= tcfg.valid_score_threshold:
            best_pat = p
            break
    if best_pat is None:
        return None

    cr = evaluate_contraction(best_pat, atr, len(df), ccfg)
    sig = best_signal(best_pat, df, atr, scfg)
    if sig is None:
        return None

    # 计算入场/止损/目标（结构与回测一致：次根开盘入场 + 滑点）
    mult = DEFAULT_MULTIPLIERS.get(symbol, bcfg.multiplier)
    is_long = sig.side == "long"
    entry = float(df["open"].iloc[-1])  # 提示入场价（次根开盘实盘时才确定）
    slip = bcfg.slippage_points
    entry_exec = entry + slip if is_long else entry - slip
    stop = sig.stop
    risk = abs(entry_exec - stop)
    if risk < 1e-9:
        return None
    target_rr = bcfg.target_rr_by_type.get(sig.signal_type, 2.0)
    target = entry_exec + target_rr * risk if is_long else entry_exec - target_rr * risk
    rr = target_rr
    a = float(atr.iloc[-1]) if np.isfinite(atr.iloc[-1]) else float("nan")

    side_cn = "做多" if is_long else "做空"
    return {
        "代码": symbol, "名称": name, "方向": side_cn,
        "信号类型": sig.signal_type, "side": sig.side,
        "tp分": round(best_pat.score.total, 2),
        "收缩分": round(cr.score, 2),
        "现价": round(last_close, 1),
        "入场": round(entry_exec, 1),
        "止损": round(stop, 1),
        "目标": round(target, 1),
        "盈亏比": round(rr, 1),
        "ATR": round(a, 1),
        "时间": str(last_dt),
        "detail": sig.detail,
    }


def main(argv=None):
    import argparse
    p = argparse.ArgumentParser(description="实盘信号扫描")
    p.add_argument("--symbols", nargs="*", default=None, help="指定品种(空格分隔)，默认TOP40")
    p.add_argument("--period", default="15", help="K线周期")
    p.add_argument("--length", type=int, default=2000, help="回看根数")
    p.add_argument("--depth", type=float, default=1.5, help="ZigZag depth×ATR")
    p.add_argument("--min-score", type=float, default=0.45, help="三推有效分门槛")
    p.add_argument("--top", type=int, default=3, help="每方向显示前N名")
    args = p.parse_args(argv)

    symbols = load_symbols(args.symbols)
    zcfg = ZigZagConfig(depth_atr_multiple=args.depth)
    tcfg = ThreePushConfig(zigzag=zcfg, valid_score_threshold=args.min_score)
    ccfg = ContractionConfig()
    scfg = SignalConfig()
    bcfg = BacktestConfig(stop_mode="structure")

    print("=" * 90)
    print(f"  信号扫描  品种={len(symbols)}  周期={args.period}min  depth={args.depth}×ATR")
    print(f"  {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 90)

    results_long, results_short, errors = [], [], []
    t0 = time.time()
    for i, (sym, name, exch) in enumerate(symbols):
        print(f"  [{i+1}/{len(symbols)}] {sym} {name} ...", end=" ", flush=True)
        row = scan_one(sym, name, exch, args.period, args.length, zcfg, tcfg, ccfg, scfg, bcfg)
        if row is None:
            print("无信号")
            continue
        if "错误" in row:
            print(f"❌ {row['错误']}")
            errors.append(row)
            continue
        print(f"{row['方向']} {row['信号类型']} tp={row['tp分']} 收缩={row['收缩分']} "
              f"入场={row['入场']} 止损={row['止损']} 目标={row['目标']}")
        if row["方向"] == "做多":
            results_long.append(row)
        else:
            results_short.append(row)

    elapsed = time.time() - t0
    print(f"\n  扫描完成: 耗时 {elapsed:.0f}s  做多={len(results_long)} 做空={len(results_short)} 错误={len(errors)}")

    # 排序：tp分降序
    results_long.sort(key=lambda r: r["tp分"], reverse=True)
    results_short.sort(key=lambda r: r["tp分"], reverse=True)

    _print_section("做多", "⬆️", results_long, args.top)
    _print_section("做空", "⬇️", results_short, args.top)

    # 存 CSV
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    all_rows = results_long + results_short
    if all_rows:
        ts = pd.Timestamp.now().strftime("%Y%m%d_%H%M")
        out_csv = OUTPUT_DIR / f"scan_{args.period}min_{ts}.csv"
        pd.DataFrame(all_rows).to_csv(out_csv, index=False, encoding="utf-8-sig")
        print(f"\n  已存 {out_csv.name}")

    if errors:
        print(f"\n  错误 {len(errors)} 个:")
        for e in errors:
            print(f"    {e['代码']} {e['名称']}: {e['错误']}")
    print("=" * 90)
    return 0


def _print_section(label, arrow, rows, top_n):
    print(f"\n{'='*90}")
    print(f"  【{label} TOP {top_n}】{arrow}")
    print(f"{'='*90}")
    if not rows:
        print("  (无信号)")
        return
    hdr = f"  {'代码':<6}{'名称':<10}{'信号':<16}{'tp分':>5}{'收缩':>5}{'现价':>9}{'入场':>9}{'止损':>9}{'目标':>9}{'盈亏比':>6}"
    print(hdr)
    print(f"  {'-'*88}")
    for r in rows[:top_n]:
        print(f"  {r['代码']:<6}{r['名称']:<10}{r['信号类型']:<16}{r['tp分']:>5}"
              f"{r['收缩分']:>5}{r['现价']:>9}{r['入场']:>9}{r['止损']:>9}{r['目标']:>9}{r['盈亏比']:>5}R")


if __name__ == "__main__":
    sys.exit(main())
