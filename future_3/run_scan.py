"""
run_scan.py
===========
主入口: 用当前 config 中的默认参数扫描全部品种的最新有效信号，
输出:
  - output/signals_latest.csv : 最新信号排行(按新鲜度/星级)
  - output/charts/<symbol>.png : 前 N 个品种的信号K线图

评级(stars):
  基础 1 星 (满足缠绕+金叉/死叉+放量K)
  +1 星: 加分项命中 (底部抬高/高点降低)
  +1 星: 放量K实体特别强 (body_ratio >= 2*body_pct) 或 缠绕特别密(twist_cross>=5)
"""
from __future__ import annotations

import os
import sys
from typing import Optional

import numpy as np
import pandas as pd

from data_loader import load_config, load_klines, read_symbols, load_all_klines_inject
from indicators import attach_indicators, atr
from signals import find_signals
from backtest import backtest, performance


def _stars(row: pd.Series, p: dict) -> int:
    s = 1
    if row.get("bonus"):
        s += 1
    body_strong = row.get("body_ratio", 0) >= 2 * p["body_pct"]
    dense_strong = row.get("twist_cross", 0) >= 5
    if body_strong or dense_strong:
        s += 1
    return min(s, 3)


def _enrich_latest(df: pd.DataFrame, p: dict) -> pd.DataFrame:
    """对每个品种, 取最近一次信号并附加行情上下文"""
    d = attach_indicators(df, p)
    sigs = find_signals(df, p)
    if not len(sigs):
        return pd.DataFrame()
    last = sigs.iloc[-1].copy()
    ci = int(last["cross_idx"])
    last["body_ratio"] = float(d["body_ratio"].iloc[int(last["confirm_idx"])]) if pd.notna(last["confirm_idx"]) else 0.0
    last["close_at_cross"] = float(d["close"].iloc[ci])
    last["last_close"] = float(d["close"].iloc[-1])
    last["last_time"] = str(d["datetime"].iloc[-1])
    last["bars_since"] = len(d) - 1 - ci
    last["stars"] = _stars(last, p)
    return pd.DataFrame([last])


def scan(cfg: Optional[dict] = None, table: Optional[str] = None) -> pd.DataFrame:
    cfg = cfg or load_config()
    p = cfg["strategy"]
    syms = read_symbols(cfg, table or cfg["data"]["symbol_table"])
    name_map = dict(zip(syms["symbol"], syms["name"]))

    # 扫描部分：注入模式批量加载（滚动窗口300根），快速找当前信号
    inject_klines = load_all_klines_inject(cfg, table or cfg["data"]["symbol_table"], length=300)

    rows = []
    n = len(syms)
    for i, row in enumerate(syms.itertuples(index=False), 1):
        sym = row.symbol
        # 当前信号用注入窗口（新且快）
        df_scan = inject_klines.get(sym)
        if df_scan is None or df_scan.empty:
            print(f"[{i}/{n}] {sym}: SKIP (无注入数据)")
            continue
        rec = _enrich_latest(df_scan, p)
        if len(rec):
            rec.insert(0, "symbol", sym)
            rec.insert(1, "name", name_map.get(sym, ""))
            rows.append(rec)
        # 回测部分：仍用 TTL 全量深历史，绩效样本不受滚动窗口影响
        try:
            df_full = load_klines(sym, cfg)
            trades, _ = backtest(df_full, p, cfg["backtest"])
            perf = performance(trades)
        except Exception:  # noqa: BLE001
            perf = {"n_trades": 0, "profit_factor": 0.0, "win_rate": 0.0, "expectancy_r": 0.0}
        if rows and rows[-1]["symbol"].iloc[0] == sym:
            rows[-1]["hist_trades"] = perf["n_trades"]
            rows[-1]["hist_pf"] = perf["profit_factor"]
            rows[-1]["hist_winrate"] = perf["win_rate"]
            rows[-1]["hist_exp_r"] = perf["expectancy_r"]
        bars = len(df_scan)
        print(f"[{i}/{n}] {sym} {name_map.get(sym,'')}: {bars} bars(inject), "
              f"hist_trades={perf['n_trades']}, pf={perf['profit_factor']:.2f}")
    if not rows:
        return pd.DataFrame()
    out = pd.concat(rows, ignore_index=True)
    # 排序: bars_since 小优先(新鲜), 星级高优先
    out = out.sort_values(["stars", "bars_since"], ascending=[False, True]).reset_index(drop=True)
    return out


def plot_symbol(df: pd.DataFrame, p: dict, bt_p: dict, symbol: str, name: str, out_path: str, lookback: int = 120) -> None:
    """画 K线 + 双EMA + 缠绕带 + 金叉/死叉 + 放量K"""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    # 中文字体
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "SimSun", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False

    d = attach_indicators(df, p).tail(lookback).reset_index(drop=True)
    sigs = find_signals(df, p)
    # 仅保留 lookback 区间内的信号(基于原索引)
    cutoff = len(df) - lookback
    sigs = sigs[sigs["cross_idx"] >= cutoff].copy()
    for c in ["cross_idx", "confirm_idx", "entry_idx"]:
        if len(sigs):
            sigs[c] = sigs[c].astype(int) - cutoff

    fig, (ax, axv) = plt.subplots(2, 1, figsize=(13, 7), gridspec_kw={"height_ratios": [3, 1]}, sharex=True)
    x = np.arange(len(d))
    # K线
    up = d["close"] >= d["open"]
    ax.vlines(x[up], d["low"][up], d["high"][up], color="#d62728", lw=0.8)
    ax.vlines(x[~up], d["low"][~up], d["high"][~up], color="#2ca02c", lw=0.8)
    for i in x:
        o, c = d["open"].iloc[i], d["close"].iloc[i]
        col = "#d62728" if c >= o else "#2ca02c"
        ax.add_patch(Rectangle((i - 0.3, min(o, c)), 0.6, abs(c - o) or 0.01, color=col, lw=0))
    ax.plot(x, d["ema_fast"], label=f"EMA{p['ema_fast']}", color="#ff7f0e", lw=1.2)
    ax.plot(x, d["ema_slow"], label=f"EMA{p['ema_slow']}", color="#1f77b4", lw=1.2)
    # 缠绕带高亮
    twist = d["twist_cross"] >= p["min_crossings"]
    for i in x:
        if twist.iloc[i]:
            ax.axvspan(i - 0.5, i + 0.5, color="#ffdd44", alpha=0.10, lw=0)
    # 信号标注
    for _, s in sigs.iterrows():
        ci = int(s["cross_idx"])
        kfi = int(s["confirm_idx"]) if pd.notna(s["confirm_idx"]) else ci
        if 0 <= kfi < len(d):
            mk = "^" if s["direction"] == 1 else "v"
            col = "#d62728" if s["direction"] == 1 else "#2ca02c"
            ax.scatter(kfi, d["low"].iloc[kfi] * 0.995, marker=mk, color=col, s=90, zorder=5, edgecolors="black", linewidths=0.5)
    ax.set_title(f"{symbol} {name} — 双均线缠绕放量突破 ({p['ema_fast']}/{p['ema_slow']})")
    ax.legend(loc="best", fontsize=8)
    ax.grid(alpha=0.25)

    # 成交量
    vol = d["volume"]
    vma = d["vol_ma"]
    axv.bar(x, vol, color=np.where(up, "#d62728", "#2ca02c"), width=0.7, alpha=0.7)
    axv.plot(x, vma, color="gray", lw=1, label=f"vol_ma{p['vol_ma']}")
    # 放量K高亮
    volbar = (vol > p["vol_factor"] * vma) & (d["body_ratio"] >= p["body_pct"])
    axv.bar(x[volbar], vol[volbar], color="#ffdd44", width=0.7, alpha=0.9)
    axv.legend(loc="best", fontsize=8)
    axv.grid(alpha=0.25)

    plt.tight_layout()
    fig.savefig(out_path, dpi=110)
    plt.close(fig)


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--table", default=None, help="品种表, 默认 data.symbol_table")
    ap.add_argument("--no-plot", action="store_true")
    args = ap.parse_args()

    cfg = load_config(args.config)
    print("=== 扫描最新信号 ===")
    sig_df = scan(cfg, args.table)
    out_dir = cfg["output"]["dir"]
    os.makedirs(out_dir, exist_ok=True)

    if len(sig_df) == 0:
        print("无有效信号。")
        return

    sig_path = os.path.join(out_dir, cfg["output"]["signals_csv"])
    sig_df.to_csv(sig_path, index=False)
    print(f"\n最新信号已保存: {sig_path} ({len(sig_df)} 条)")
    show_cols = ["symbol", "name", "datetime", "direction", "stars", "bars_since",
                 "entry_price", "last_close", "bonus", "hist_pf", "hist_winrate"]
    show_cols = [c for c in show_cols if c in sig_df.columns]
    print(sig_df[show_cols].to_string(index=False))

    if not args.no_plot:
        chart_dir = os.path.join(out_dir, cfg["output"]["charts_dir"])
        os.makedirs(chart_dir, exist_ok=True)
        top_n = cfg["output"]["plot_top_symbols"]
        for sym in sig_df["symbol"].head(top_n):
            name = sig_df.loc[sig_df["symbol"] == sym, "name"].iloc[0]
            try:
                df = load_klines(sym, cfg)
                out_path = os.path.join(chart_dir, f"{sym}.png")
                plot_symbol(df, cfg["strategy"], cfg["backtest"], sym, name, out_path)
                print(f"  图表: {out_path}")
            except Exception as e:  # noqa: BLE001
                print(f"  画图失败 {sym}: {e}")


if __name__ == "__main__":
    main()
