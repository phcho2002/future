"""
run_scan.py
===========
主入口：扫描 top40 全部品种，生成 Renko 砖块大小表 + 最新有效信号汇总 +
各品种密集成交区 + RSI 超买超卖风险名单 + 前 N 个品种的 Renko 图表。

输出（写入 future_5/output/）：
  - brick_sizes.csv      各品种自适应砖块大小 + ATR + 方向统计
  - signals_latest.csv   最新有效信号（每品种最多取最近 1 条，按类型分组）
  - zones_latest.csv     各品种活跃密集成交区（最近价位附近的带）
  - rsi_extremes.csv     RSI 严重超买/超卖风险品种名单
  - charts/<symbol>.png  Renko 图 + RSI + 信号标注

评级(stars)：
  基础 1 星（任一信号触发）
  +1：信号方向与 Renko-RSI 共振（如多信号 + RSI 未超买）
  +1：信号处于"新鲜"区间（entry_brick_idx 在最近 30 块内）
"""
from __future__ import annotations

import argparse
import os
from typing import Optional

import numpy as np
import pandas as pd

from data_loader import load_config, load_klines, read_symbols
from renko import compute_brick_size, build_renko, renko_rsi, atr as renko_atr
from signals import find_all_signals


# ---------------------------------------------------------------------
# 单品种处理：返回砖块大小、信号、活跃 zone、RSI 风险
# ---------------------------------------------------------------------
def analyze_symbol(df: pd.DataFrame, cfg: dict, symbol: str) -> dict:
    bs = compute_brick_size(df, cfg["brick"], symbol=symbol)
    rk = build_renko(df, bs)
    res = find_all_signals(rk, cfg)
    a_val = float(renko_atr(df, period=int(cfg["brick"]["atr_period"])).iloc[-1])
    last_close = float(df["close"].iloc[-1])
    last_time = str(df["datetime"].iloc[-1])
    dirs = rk.bricks["dir"].to_numpy() if len(rk.bricks) else np.array([])
    n_up = int((dirs == 1).sum())
    n_dn = int((dirs == -1).sum())
    last_rsi = float(res["rsi"].iloc[-1]) if len(res["rsi"]) and not np.isnan(res["rsi"].iloc[-1]) else float("nan")

    # 最新砖时间/价格
    if len(rk.bricks):
        last_brick_idx = int(rk.bricks["brick_idx"].iloc[-1])
        last_brick_dir = int(rk.bricks["dir"].iloc[-1])
        last_brick_close = float(rk.bricks["close"].iloc[-1])
        last_brick_time = str(rk.bricks["close_dt"].iloc[-1])
    else:
        last_brick_idx = -1
        last_brick_dir = 0
        last_brick_close = last_close
        last_brick_time = last_time

    return {
        "brick_size": bs, "renko": rk, "sig": res,
        "meta": {
            "bars": len(df), "bricks": len(rk.bricks),
            "atr": a_val, "last_close": last_close, "last_time": last_time,
            "n_up": n_up, "n_down": n_dn, "last_rsi": last_rsi,
            "last_brick_idx": last_brick_idx, "last_brick_dir": last_brick_dir,
            "last_brick_close": last_brick_close, "last_brick_time": last_brick_time,
        },
    }


def _stars(sig_row: dict, meta: dict, cfg: dict) -> int:
    s = 1
    rsi = meta.get("last_rsi", float("nan"))
    direction = sig_row.get("direction", 0)
    # +1：信号与 RSI 共振（多信号且未超买 / 空信号且未超卖）
    if not np.isnan(rsi):
        eb = cfg["renko_rsi"]["extreme_overbought"]
        es = cfg["renko_rsi"]["extreme_oversold"]
        if direction == 1 and rsi < eb:
            s += 1
        elif direction == -1 and rsi > es:
            s += 1
    # +1：信号新鲜（最近 30 块内）
    if sig_row.get("entry_brick_idx", -1) >= meta["last_brick_idx"] - 30:
        s += 1
    return min(s, 3)


# ---------------------------------------------------------------------
# 扫描全品种
# ---------------------------------------------------------------------
def scan(cfg: Optional[dict] = None, table: Optional[str] = None,
         limit: Optional[int] = None, progress: bool = True) -> dict:
    cfg = cfg or load_config()
    syms = read_symbols(cfg, table or cfg["data"]["symbol_table"])
    if limit:
        syms = syms.head(int(limit))
    name_map = dict(zip(syms["symbol"], syms["name"]))
    n = len(syms)

    brick_rows, sig_rows, zone_rows, rsi_rows = [], [], [], []
    per_symbol_data = {}  # 供画图复用，避免重复算

    for i, row in enumerate(syms.itertuples(index=False), 1):
        sym = row.symbol
        name = name_map.get(sym, "")
        try:
            df = load_klines(sym, cfg)
            a = analyze_symbol(df, cfg, sym)
        except Exception as e:  # noqa: BLE001
            if progress:
                print(f"[{i}/{n}] {sym} {name}: SKIP ({e})")
            continue
        meta = a["meta"]
        rk = a["renko"]
        res = a["sig"]
        per_symbol_data[sym] = a

        # 砖块大小表
        brick_rows.append({
            "symbol": sym, "name": name,
            "brick_size": meta["brick_size"] if False else a["brick_size"],
            "atr": round(meta["atr"], 4),
            "last_close": round(meta["last_close"], 4),
            "bars": meta["bars"], "bricks": meta["bricks"],
            "n_up": meta["n_up"], "n_down": meta["n_down"],
            "last_rsi": round(meta["last_rsi"], 1) if not np.isnan(meta["last_rsi"]) else None,
            "last_brick_dir": "UP" if meta["last_brick_dir"] == 1 else ("DN" if meta["last_brick_dir"] == -1 else "-"),
            "last_time": meta["last_time"],
        })

        # 最新有效信号：取每类信号最近 1 条（合并表）
        all_sig = res["all"]
        if len(all_sig):
            recent = all_sig.groupby("type", group_keys=False).tail(1).copy()
            for _, s in recent.iterrows():
                sig_rows.append({
                    "symbol": sym, "name": name,
                    "type": s["type"],
                    "direction": s["direction"],
                    "entry_brick_idx": s["entry_brick_idx"],
                    "entry_time": s["entry_time"],
                    "entry_price": round(float(s["entry_price"]), 4),
                    "bricks_since": meta["last_brick_idx"] - int(s["entry_brick_idx"]),
                    "stars": _stars(s.to_dict(), meta, cfg),
                    "last_close": round(meta["last_close"], 4),
                    "last_rsi": round(meta["last_rsi"], 1) if not np.isnan(meta["last_rsi"]) else None,
                    "pullback_ratio": float(s["pullback_ratio"]) if "pullback_ratio" in s else None,
                })

        # 活跃密集成交区：取距离最新价最近的若干条（|中价-last_close| 最小）
        zones = res["zones"]
        if len(zones):
            z = zones.copy()
            z["mid"] = (z["price_lo"] + z["price_hi"]) / 2.0
            z["dist"] = (z["mid"] - meta["last_close"]).abs()
            z = z.sort_values("dist").head(3)
            for _, zz in z.iterrows():
                zone_rows.append({
                    "symbol": sym, "name": name,
                    "zone_id": int(zz["zone_id"]),
                    "price_lo": round(float(zz["price_lo"]), 4),
                    "price_hi": round(float(zz["price_hi"]), 4),
                    "n_touch": int(zz["n_touch"]),
                    "n_up": int(zz["n_up"]),
                    "n_down": int(zz["n_down"]),
                    "last_close": round(meta["last_close"], 4),
                    "side": "阻力" if zz["mid"] > meta["last_close"] else "支撑",
                })

        # RSI 风险：仅当最新 RSI 处于极端区才入选
        if not np.isnan(meta["last_rsi"]):
            eb = cfg["renko_rsi"]["extreme_overbought"]
            es = cfg["renko_rsi"]["extreme_oversold"]
            if meta["last_rsi"] >= eb or meta["last_rsi"] <= es:
                rsi_rows.append({
                    "symbol": sym, "name": name,
                    "last_rsi": round(meta["last_rsi"], 1),
                    "risk": "严重超买" if meta["last_rsi"] >= eb else "严重超卖",
                    "direction": -1 if meta["last_rsi"] >= eb else 1,
                    "last_close": round(meta["last_close"], 4),
                    "last_brick_dir": meta["last_brick_dir"],
                })

        if progress:
            print(f"[{i}/{n}] {sym} {name}: {meta['bricks']}砖 bs={a['brick_size']} "
                  f"rsi={meta['last_rsi']:.0f} 信号={len(all_sig)} zones={len(zones)}")

    return {
        "bricks": pd.DataFrame(brick_rows),
        "signals": pd.DataFrame(sig_rows),
        "zones": pd.DataFrame(zone_rows),
        "rsi": pd.DataFrame(rsi_rows),
        "_data": per_symbol_data,
    }


# =====================================================================
# Renko 绘图
# =====================================================================
def plot_renko(rk, res, meta, symbol: str, name: str, cfg: dict,
               out_path: str, lookback_bricks: int = 120) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "SimSun", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False

    b = rk.bricks.tail(lookback_bricks).reset_index(drop=True)
    cutoff = max(0, len(rk.bricks) - lookback_bricks)

    fig, (axp, axr) = plt.subplots(
        2, 1, figsize=(14, 8), gridspec_kw={"height_ratios": [3, 1]}, sharex=True)
    x = np.arange(len(b))
    bs = rk.brick_size
    for i in x:
        d = int(b["dir"].iloc[i])
        o = float(b["open"].iloc[i])
        c = float(b["close"].iloc[i])
        col = "#d62728" if d == 1 else "#2ca02c"
        axp.add_patch(Rectangle((i - 0.4, min(o, c)), 0.8,
                                abs(c - o) or bs * 0.5,
                                facecolor=col, edgecolor="#222",
                                linewidth=0.3, alpha=0.92))
    # 密集成交区水平带（最近价位附近的 3 条）
    zones = res["zones"]
    if len(zones):
        z = zones.copy()
        z["mid"] = (z["price_lo"] + z["price_hi"]) / 2.0
        z["dist"] = (z["mid"] - meta["last_close"]).abs()
        for _, zz in z.sort_values("dist").head(3).iterrows():
            axp.axhspan(zz["price_lo"], zz["price_hi"],
                        color="#1f77b4", alpha=0.10)
            axp.axhline(zz["mid"], color="#1f77b4", lw=0.5, ls="--", alpha=0.5)

    # 信号标注（仅落在 lookback 内的）
    def _annotate(sig_df, marker, color, y_mul):
        if not len(sig_df):
            return
        s = sig_df[sig_df["entry_brick_idx"] >= cutoff].copy()
        if not len(s):
            return
        for _, r in s.iterrows():
            pos = int(r["entry_brick_idx"]) - cutoff
            if 0 <= pos < len(b):
                price = float(b["open"].iloc[pos]) * y_mul
                axp.scatter(pos, price, marker=marker, color=color,
                            s=85, zorder=5, edgecolors="black", linewidths=0.5)
    _annotate(res["trend_sig"], "^", "#d62728", 0.995)
    _annotate(res["zone_sig"], "v", "#9467bd", 1.005)
    _annotate(res["rsi_risk"], "*", "#ff7f0e", 0.99)

    axp.set_title(
        f"{symbol} {name} — Renko Chart  (bs={bs}, 砖={meta['bricks']}, "
        f"RSI={meta['last_rsi']:.1f})")
    axp.set_ylabel("价格")
    axp.grid(alpha=0.25)

    # RSI 子图
    rsi = res["rsi"]
    if len(rsi):
        # rsi 的 index 是 brick_idx
        rsi_recent = rsi[rsi.index >= cutoff]
        axr.plot(rsi_recent.index.values - cutoff, rsi_recent.values,
                 color="#1f77b4", lw=1.2)
        eb = cfg["renko_rsi"]["extreme_overbought"]
        es = cfg["renko_rsi"]["extreme_oversold"]
        ob = cfg["renko_rsi"]["overbought"]
        os_ = cfg["renko_rsi"]["oversold"]
        axr.axhline(eb, color="#d62728", lw=0.6, ls="--", alpha=0.6)
        axr.axhline(es, color="#2ca02c", lw=0.6, ls="--", alpha=0.6)
        axr.axhline(ob, color="#d62728", lw=0.4, ls=":", alpha=0.4)
        axr.axhline(os_, color="#2ca02c", lw=0.4, ls=":", alpha=0.4)
        axr.axhspan(eb, 100, color="#d62728", alpha=0.08)
        axr.axhspan(0, es, color="#2ca02c", alpha=0.08)
    axr.set_ylim(0, 100)
    axr.set_ylabel(f"Renko-RSI{cfg['renko_rsi']['period']}")
    axr.set_xlabel("砖块序号 (回看窗口)")
    axr.grid(alpha=0.25)

    plt.tight_layout()
    fig.savefig(out_path, dpi=110)
    plt.close(fig)


# =====================================================================
# main
# =====================================================================
def main():
    ap = argparse.ArgumentParser(description="Renko 量化系统 — top40 扫描")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--table", default=None, help="品种表，默认 data.symbol_table")
    ap.add_argument("--limit", type=int, default=None, help="仅扫描前 N 个品种")
    ap.add_argument("--no-plot", action="store_true")
    args = ap.parse_args()

    cfg = load_config(args.config)
    print("=== Renko 系统扫描 top40 ===")
    res = scan(cfg, args.table, limit=args.limit)

    out_dir = cfg["output"]["dir"]
    os.makedirs(out_dir, exist_ok=True)

    # 砖块大小表
    bdf = res["bricks"]
    if len(bdf):
        bp = os.path.join(out_dir, cfg["output"]["brick_size_csv"])
        bdf.to_csv(bp, index=False)
        print(f"\n砖块大小表: {bp} ({len(bdf)} 品种)")
        print(bdf[["symbol", "name", "brick_size", "atr", "bricks",
                   "n_up", "n_down", "last_rsi", "last_brick_dir"]].to_string(index=False))

    # 信号汇总
    sdf = res["signals"]
    if len(sdf):
        sdf = sdf.sort_values(["stars", "bricks_since"], ascending=[False, True]).reset_index(drop=True)
        sp = os.path.join(out_dir, cfg["output"]["signals_csv"])
        sdf.to_csv(sp, index=False)
        print(f"\n最新信号: {sp} ({len(sdf)} 条)")
        show = sdf.head(cfg["output"].get("show_recent_signals", 30))
        print(show[["symbol", "name", "type", "direction", "stars",
                    "bricks_since", "entry_price", "last_close", "last_rsi"]].to_string(index=False))

    # 密集区
    zdf = res["zones"]
    if len(zdf):
        zp = os.path.join(out_dir, cfg["output"]["zones_csv"])
        zdf.to_csv(zp, index=False)
        print(f"\n密集成交区: {zp} ({len(zdf)} 条)")

    # RSI 风险
    rdf = res["rsi"]
    if len(rdf):
        rp = os.path.join(out_dir, cfg["output"]["rsi_extremes_csv"])
        rdf.to_csv(rp, index=False)
        print(f"\nRSI 超买超卖风险: {rp} ({len(rdf)} 品种)")
        print(rdf.to_string(index=False))
    else:
        print("\nRSI 超买超卖风险: 无")

    # 画图
    if not args.no_plot and len(sdf):
        chart_dir = os.path.join(out_dir, cfg["output"]["charts_dir"])
        os.makedirs(chart_dir, exist_ok=True)
        top_n = cfg["output"]["plot_top_symbols"]
        # 优先画有信号的品种（按 stars 排序）
        plot_syms = sdf["symbol"].unique().tolist()[:top_n]
        for sym in plot_syms:
            a = res["_data"].get(sym)
            if a is None:
                continue
            name = sdf.loc[sdf["symbol"] == sym, "name"].iloc[0]
            try:
                plot_renko(a["renko"], a["sig"], a["meta"], sym, name, cfg,
                           os.path.join(chart_dir, f"{sym}.png"))
                print(f"  图表: {sym}.png")
            except Exception as e:  # noqa: BLE001
                print(f"  画图失败 {sym}: {e}")


if __name__ == "__main__":
    main()
