"""实盘扫描入口 —— 两高两低形态 → 整理质量 → 顺势放量突破。

对 TOP40 / future_trend_rank（或指定品种）逐个跑完整信号链路，输出当前有信号
的品种及其入场/止损/目标/盈亏比。适合 cron 定时运行（每 15/30 分钟）。

管线：detect_zigzag → detect_two_high → evaluate_quality(过阈值) → detect_breakout

用法:
    python -m future_twohigh.scan                         # 默认 TOP40, 15min
    python -m future_twohigh.scan --symbols PP0 AG0 SN0
    python -m future_twohigh.scan --period 30 --top 5
    python -m future_twohigh.scan --source trend_rank --period 30 --recent 5
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

from future_data import inject_many, inject_klines  # noqa: E402

from future_zigzag.config import ZigZagConfig  # noqa: E402
from future_zigzag.zigzag import detect_zigzag  # noqa: E402

from future_twohigh.config import (  # noqa: E402
    TwoHighConfig, ConsolidationConfig, BreakoutConfig, BacktestConfig,
    DEFAULT_MULTIPLIERS,
)
from future_twohigh.pattern import detect_two_high  # noqa: E402
from future_twohigh.quality import evaluate_quality  # noqa: E402
from future_twohigh.signals import detect_breakout  # noqa: E402

TOP40_JSON = _WORK_AI / "futures_top40.json"
TREND_RANK_JSON = _WORK_AI / "future_trend_rank.json"
OUTPUT_DIR = _SCRIPT_DIR / "output"


def load_symbols(symbols_arg=None, source="top20"):
    """加载品种列表：命令行指定 > 数据源全部。返回 [(symbol,name,exchange)]。

    source:
        "top20"      —— future_trend_rank.json 的 top 字段（20 个高动量品种，默认）
        "trend_rank" —— future_trend_rank.json 的 full_ranking（78 品种）
        "top40"      —— futures_top40.json（40 大品种）
    """
    if source in ("top20", "trend_rank"):
        with open(TREND_RANK_JSON, encoding="utf-8") as f:
            data = json.load(f)
        key = "top" if source == "top20" else "full_ranking"
        all_syms = [(r["symbol"], r["name"], r["exchange"])
                    for r in data.get(key, data.get("top", []))]
        if symbols_arg:
            want = set(symbols_arg)
            return [(s, n, e) for s, n, e in all_syms if s in want]
        return all_syms

    if symbols_arg:
        with open(TOP40_JSON, encoding="utf-8") as f:
            all_syms = {s[0]: (s[1], s[2]) for s in json.load(f)["symbols"]}
        out = []
        for s in symbols_arg:
            name, exch = all_syms.get(s, (s, ""))
            out.append((s, name, exch))
        return out
    with open(TOP40_JSON, encoding="utf-8") as f:
        return [(s[0], s[1], s[2]) for s in json.load(f)["symbols"]]


def load_inject_data(symbols, period, length):
    """一次性批量注入加载，返回 {symbol: df}。失败返回空字典。"""
    if not symbols:
        return {}
    try:
        return inject_many(symbols, period=period, length=length)
    except Exception as e:
        print(f"  [WARN] inject_many 失败: {e}; 将逐品种 inject_klines 回退")
        return {}


def scan_one(symbol, name, exchange, period, length, zcfg, tcfg, ccfg, scfg, bcfg, data_map=None):
    """扫描单品种，返回信号行 dict 或 None。

    取最近一个通过整理质量门槛 + 已触发顺势突破的形态。
    """
    try:
        df = None
        if data_map is not None:
            df = data_map.get(symbol)
        if df is None or df.empty:
            df = inject_klines(symbol, exchange, period=period, length=length)
    except Exception as e:
        return {"代码": symbol, "名称": name, "错误": str(e)[:60]}
    if df is None or df.empty or len(df) < 100:
        return None
    df = df.tail(min(len(df), length)).reset_index(drop=True)
    last_close = float(df["close"].iloc[-1])
    last_dt = df["datetime"].iloc[-1]

    zz = detect_zigzag(df, zcfg)
    patterns = detect_two_high(zz, tcfg)
    atr = zz.atr

    # 只看最近一个有效形态（最新结构）—— 逆序找第一个质量合格且已突破的
    for p in reversed(patterns):
        cr = evaluate_quality(p, df, ccfg)
        if not cr.passed:
            continue
        sig = detect_breakout(p, df, atr, scfg)
        if sig is None:
            continue
        return _signal_row(symbol, name, p, cr, sig, df, atr, bcfg, last_close, last_dt,
                           bars_ago=len(df) - 1 - sig.trigger_idx)
    return None


def _signal_row(symbol, name, pattern, cr, sig, df, atr, bcfg,
                last_close, last_dt, bars_ago=None):
    """构造信号输出行。"""
    mult = DEFAULT_MULTIPLIERS.get(symbol, bcfg.multiplier)
    is_long = sig.side == "long"
    entry = float(df["open"].iloc[-1])   # 提示入场价（次根开盘实盘时才确定）
    slip = bcfg.slippage_points
    entry_exec = entry + slip if is_long else entry - slip
    stop = sig.stop
    risk = abs(entry_exec - stop)
    if risk < 1e-9:
        return None
    target_rr = bcfg.target_rr_by_type.get(sig.signal_type, 2.0)
    target = entry_exec + target_rr * risk if is_long else entry_exec - target_rr * risk
    a = float(atr.iloc[-1]) if np.isfinite(atr.iloc[-1]) else float("nan")

    side_cn = "做多" if is_long else "做空"
    row = {
        "代码": symbol, "名称": name, "方向": side_cn,
        "形态": pattern.kind, "side": sig.side,
        "整理分": round(cr.score, 2),
        "现价": round(last_close, 1),
        "入场": round(entry_exec, 1),
        "止损": round(stop, 1),
        "目标": round(target, 1),
        "盈亏比": round(target_rr, 1),
        "ATR": round(a, 1),
        "时间": str(last_dt),
        "detail": sig.detail,
    }
    if bars_ago is not None:
        row["bars_ago"] = bars_ago
        row["触发时间"] = str(df.iloc[sig.trigger_idx]["datetime"])
    return row


def scan_one_recent(symbol, name, exchange, period, length, zcfg, tcfg, ccfg, scfg, bcfg, recent_n, data_map=None):
    """扫描单品种（recent 模式），返回最近 recent_n 根 K 线内所有触发的信号行列表。"""
    try:
        df = None
        if data_map is not None:
            df = data_map.get(symbol)
        if df is None or df.empty:
            df = inject_klines(symbol, exchange, period=period, length=length)
    except Exception as e:
        return [{"代码": symbol, "名称": name, "错误": str(e)[:60]}]
    if df is None or df.empty or len(df) < 100:
        return []
    df = df.tail(min(len(df), length)).reset_index(drop=True)
    last_close = float(df["close"].iloc[-1])
    last_dt = df["datetime"].iloc[-1]

    zz = detect_zigzag(df, zcfg)
    patterns = detect_two_high(zz, tcfg)
    atr = zz.atr

    rows = []
    for p in patterns:
        cr = evaluate_quality(p, df, ccfg)
        if not cr.passed:
            continue
        sig = detect_breakout(p, df, atr, scfg)
        if sig is None:
            continue
        if sig.trigger_idx < len(df) - recent_n:
            continue
        row = _signal_row(symbol, name, p, cr, sig, df, atr, bcfg, last_close, last_dt,
                          bars_ago=len(df) - 1 - sig.trigger_idx)
        if row is not None:
            rows.append(row)
    return rows


def main(argv=None):
    import argparse
    p = argparse.ArgumentParser(description="两高两低顺势突破 实盘信号扫描")
    p.add_argument("--symbols", nargs="*", default=None, help="指定品种(空格分隔)，默认按source加载")
    p.add_argument("--source", default="top20", choices=["top20", "trend_rank", "top40"],
                   help="品种数据源: top20=趋势排名Top20(默认), trend_rank=78品种, top40=40大品种")
    p.add_argument("--period", default="15", help="K线周期")
    p.add_argument("--length", type=int, default=300, help="回看根数 (inject 滚动窗口)")
    p.add_argument("--depth", type=float, default=1.5, help="ZigZag depth×ATR")
    p.add_argument("--quality-min", type=float, default=0.40, help="整理质量分门槛")
    p.add_argument("--top", type=int, default=5, help="每方向显示前N名")
    p.add_argument("--recent", type=int, default=None,
                   help="只看最近N根K线内有信号的品种（recent模式）")
    args = p.parse_args(argv)

    symbols = load_symbols(args.symbols, source=args.source)
    zcfg = ZigZagConfig(depth_atr_multiple=args.depth, min_bars_between_pivots=3)
    tcfg = TwoHighConfig(zigzag=zcfg)
    ccfg = ConsolidationConfig(consolidation_threshold=args.quality_min)
    scfg = BreakoutConfig()
    bcfg = BacktestConfig(stop_mode="structure")

    mode_tag = f" recent={args.recent}" if args.recent else ""
    print("=" * 92)
    print(f"  两高两低顺势突破扫描  来源={args.source}  品种={len(symbols)}  "
          f"周期={args.period}min  depth={args.depth}×ATR  质量门槛={args.quality_min}{mode_tag}")
    print(f"  {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 92)

    if args.recent:
        return _run_recent(symbols, args, zcfg, tcfg, ccfg, scfg, bcfg)
    return _run_normal(symbols, args, zcfg, tcfg, ccfg, scfg, bcfg)


def _run_normal(symbols, args, zcfg, tcfg, ccfg, scfg, bcfg):
    results_long, results_short, errors = [], [], []
    t0 = time.time()
    data_map = load_inject_data(symbols, args.period, args.length)
    for i, (sym, name, exch) in enumerate(symbols):
        print(f"  [{i+1}/{len(symbols)}] {sym} {name} ...", end=" ", flush=True)
        row = scan_one(sym, name, exch, args.period, args.length, zcfg, tcfg, ccfg, scfg, bcfg,
                       data_map=data_map)
        if row is None:
            print("无信号")
            continue
        if "错误" in row:
            print(f"[ERROR] {row['错误']}")
            errors.append(row)
            continue
        print(f"{row['方向']} {row['形态']} 质量分={row['整理分']} "
              f"入场={row['入场']} 止损={row['止损']} 目标={row['目标']}")
        if row["方向"] == "做多":
            results_long.append(row)
        else:
            results_short.append(row)

    elapsed = time.time() - t0
    print(f"\n  扫描完成: 耗时 {elapsed:.0f}s  做多={len(results_long)} 做空={len(results_short)} 错误={len(errors)}")

    results_long.sort(key=lambda r: r["整理分"], reverse=True)
    results_short.sort(key=lambda r: r["整理分"], reverse=True)

    _print_section("做多", "[LONG]", results_long, args.top)
    _print_section("做空", "[SHORT]", results_short, args.top)

    _save_csv(results_long + results_short, args, suffix="scan")
    _print_errors(errors)
    print("=" * 92)
    return 0


def _run_recent(symbols, args, zcfg, tcfg, ccfg, scfg, bcfg):
    """recent 模式：扫描所有品种，只保留最近 N 根 K 内有信号的。"""
    recent_n = args.recent
    all_rows, errors = [], []
    t0 = time.time()
    data_map = load_inject_data(symbols, args.period, args.length)
    for i, (sym, name, exch) in enumerate(symbols):
        print(f"  [{i+1}/{len(symbols)}] {sym} {name} ...", end=" ", flush=True)
        rows = scan_one_recent(sym, name, exch, args.period, args.length,
                               zcfg, tcfg, ccfg, scfg, bcfg, recent_n,
                               data_map=data_map)
        if rows and "错误" in rows[0]:
            print(f"❌ {rows[0]['错误']}")
            errors.append(rows[0])
            continue
        if not rows:
            print("无近期信号")
            continue
        latest = min(r["bars_ago"] for r in rows)
        print(f"近期信号={len(rows)} 最近={latest}根前")
        all_rows.extend(rows)

    elapsed = time.time() - t0
    all_rows.sort(key=lambda r: (r["bars_ago"], -r["整理分"]))
    hit_symbols = sorted(set(r["代码"] for r in all_rows))
    n_long = sum(1 for r in all_rows if r["方向"] == "做多")
    n_short = sum(1 for r in all_rows if r["方向"] == "做空")

    print(f"\n  扫描完成: 耗时 {elapsed:.0f}s  品种总数={len(symbols)}  "
          f"有信号品种={len(hit_symbols)}  信号数={len(all_rows)}  "
          f"做多={n_long} 做空={n_short} 错误={len(errors)}")

    print(f"\n{'='*108}")
    print(f"  【最近 {recent_n} 根 K 线内有信号的品种】")
    print(f"{'='*108}")
    if not all_rows:
        print("  (无信号)")
    else:
        hdr = (f"  {'代码':<6}{'名称':<10}{'方向':<5}{'形态':<10}"
               f"{'质量':>5}{'bars':>5}{'现价':>9}{'入场':>9}"
               f"{'止损':>9}{'目标':>9}{'盈亏比':>6}  触发时间")
        print(hdr)
        print(f"  {'-'*106}")
        for r in all_rows:
            print(f"  {r['代码']:<6}{r['名称']:<10}{r['方向']:<5}{r['形态']:<10}"
                  f"{r['整理分']:>5}{r['bars_ago']:>5}"
                  f"{r['现价']:>9}{r['入场']:>9}{r['止损']:>9}"
                  f"{r['目标']:>9}{r['盈亏比']:>5}R  {r['触发时间']}")

    if hit_symbols:
        print(f"\n  有信号品种列表: {', '.join(hit_symbols)}")

    _save_csv(all_rows, args, suffix=f"scan_recent{recent_n}")
    _print_errors(errors)
    print("=" * 108)
    return 0


def _save_csv(rows, args, suffix):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    if rows:
        ts = pd.Timestamp.now().strftime("%Y%m%d_%H%M")
        out_csv = OUTPUT_DIR / f"{suffix}_{args.period}min_{ts}.csv"
        pd.DataFrame(rows).to_csv(out_csv, index=False, encoding="utf-8-sig")
        print(f"\n  已存 {out_csv.name}")


def _print_errors(errors):
    if errors:
        print(f"\n  错误 {len(errors)} 个:")
        for e in errors:
            print(f"    {e['代码']} {e['名称']}: {e['错误']}")


def _print_section(label, arrow, rows, top_n):
    print(f"\n{'='*92}")
    print(f"  【{label} TOP {top_n}】{arrow}")
    print(f"{'='*92}")
    if not rows:
        print("  (无信号)")
        return
    hdr = (f"  {'代码':<6}{'名称':<10}{'形态':<10}{'质量':>5}"
           f"{'现价':>9}{'入场':>9}{'止损':>9}{'目标':>9}{'盈亏比':>6}")
    print(hdr)
    print(f"  {'-'*90}")
    for r in rows[:top_n]:
        print(f"  {r['代码']:<6}{r['名称']:<10}{r['形态']:<10}{r['整理分']:>5}"
              f"{r['现价']:>9}{r['入场']:>9}{r['止损']:>9}{r['目标']:>9}{r['盈亏比']:>5}R")


if __name__ == "__main__":
    sys.exit(main())
