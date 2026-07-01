"""
optimize.py
===========
对品种表做网格搜索，按利润因子选出全局最优参数组合，写回 config.yaml。

评估:
  - 聚合全部品种的交易，按 metric(默认 profit_factor) 排序
  - 约束: 全局样本数 >= min_trades 且 胜率 >= min_winrate
  - 副指标: 期望R、最大回撤R

产出:
  - output/optimization_report.xlsx : Top10 全局组合 + 每品种最优 + 最优组合交易明细
  - output/optimization_all.csv : 全部组合结果
  - output/optimization_checkpoint.csv : 中间结果，支持断点续跑
  - 把全局最优写回 config.yaml 的 strategy 段

性能:
  - K线只读一次(从 parquet 缓存)，参数循环纯内存计算
  - bonus 极值序列预计算，与 EMA 参数无关，可复用
"""
from __future__ import annotations

import itertools
import os
import time
from typing import Optional

import numpy as np
import pandas as pd
import yaml

from data_loader import load_config, load_all_klines, read_symbols
from backtest import backtest, performance
from indicators import has_higher_lows, has_lower_highs


# 参与优化的参数键（同时用于写回 config.yaml）
_OPT_KEYS = ["ema_fast", "ema_slow", "vol_factor", "prev_vol_factor", "body_pct"]


def _precompute_bonus(klines_all: dict, strat_base: dict) -> dict:
    """对每个品种预计算 bonus 极值序列(只依赖 twist_window/swing_lookback, 与 ema 无关)。"""
    tw = strat_base["twist_window"]
    sl = strat_base.get("swing_lookback", 5)
    pre = {}
    for sym, df in klines_all.items():
        pre[sym] = {
            "higher_low": has_higher_lows(df["low"], tw, sl),
            "lower_high": has_lower_highs(df["high"], tw, sl),
        }
    return pre


def _build_params(strat_base: dict, combo: dict, fixed: dict) -> dict:
    """合成一组完整 strategy 参数"""
    p = dict(strat_base)
    p.update(combo)
    p.update(fixed)
    p.setdefault("bonus_higher_low", True)
    p.setdefault("bonus_lower_high", True)
    p.setdefault("swing_lookback", 5)
    p.setdefault("twist_band_ratio", 0.015)
    return p


def _grid_combos(grid: dict) -> list[dict]:
    keys = list(grid.keys())
    return [dict(zip(keys, vals)) for vals in itertools.product(*[grid[k] for k in keys])]


def evaluate_combo(klines_all: dict, combo: dict, strat_base: dict, fixed: dict, bt_p: dict, pre: Optional[dict] = None) -> tuple[dict, pd.DataFrame]:
    """聚合所有品种交易，返回 (perf_dict, all_trades_df)"""
    p = _build_params(strat_base, combo, fixed)
    frames = []
    for sym, df in klines_all.items():
        try:
            trades, _ = backtest(df, p, bt_p, precomputed=pre.get(sym) if pre else None)
        except Exception:  # noqa: BLE001
            continue
        if len(trades):
            trades = trades.copy()
            trades["symbol"] = sym
            frames.append(trades)
    if frames:
        all_t = pd.concat(frames, ignore_index=True)
    else:
        all_t = pd.DataFrame()
    perf = performance(all_t)
    perf.update(combo)
    return perf, all_t


def _score(perf: dict, metric: str, min_trades: int, min_winrate: float) -> float:
    """不满足约束返回 -inf"""
    if perf["n_trades"] < min_trades:
        return float("-inf")
    if perf["win_rate"] < min_winrate:
        return float("-inf")
    val = perf.get(metric, 0.0)
    if metric == "profit_factor" and val == float("inf"):
        return 1e6
    return float(val)


def write_back_config(config_path: str, best: dict) -> None:
    """把最优参数写回 config.yaml 的 strategy 段，保留行尾注释。"""
    import re

    def cast(k, v):
        if k in ("ema_fast", "ema_slow"):
            return str(int(round(float(v))))
        return str(v)

    repl = {k: cast(k, best[k]) for k in _OPT_KEYS if k in best}
    with open(config_path, "r", encoding="utf-8") as f:
        lines = f.readlines()
    out = []
    for line in lines:
        m = re.match(r"^(\s*)(" + "|".join(_OPT_KEYS) + r"):(\s*)(\S+)(\s*)(#.*)?$", line)
        if m and m.group(2) in repl:
            indent, key, sp1, _old, sp2, comment = m.groups()
            comment = comment or ""
            if comment and not comment.startswith(" "):
                comment = " " + comment
            # 保留值与注释之间的间距（至少两个空格）
            sep = "  " if comment else ""
            out.append(f"{indent}{key}:{sp1}{repl[key]}{sep}{comment.lstrip()}\n" if comment else f"{indent}{key}:{sp1}{repl[key]}\n")
        else:
            out.append(line)
    with open(config_path, "w", encoding="utf-8") as f:
        f.writelines(out)


def run_optimization(config_path: str = "config.yaml", write_back: bool = True) -> dict:
    cfg = load_config(config_path)
    oc = cfg["optimize"]
    print(f"[opt] 读取品种表 {oc['run_table']} ...")
    syms_df = read_symbols(cfg, oc["run_table"])
    print(f"[opt] 拉取/载入 {len(syms_df)} 个品种的 30 分钟 K 线 (优先缓存) ...")
    klines_all = load_all_klines(cfg, table=oc["run_table"], use_cache=True, progress=True)
    print(f"[opt] 成功载入 {len(klines_all)} 个品种")

    strat_base = cfg["strategy"]
    bt_p = cfg["backtest"]
    fixed = oc["fixed"]
    grid = oc["grid"]
    metric = oc["metric"]
    min_trades = oc["min_trades"]
    min_winrate = oc["min_winrate"]

    combos = _grid_combos(grid)
    print(f"[opt] 网格组合数: {len(combos)}, 指标={metric}, min_trades={min_trades}, min_winrate={min_winrate}", flush=True)

    print("[opt] 预计算 bonus 极值序列 (每品种一次) ...", flush=True)
    pre = _precompute_bonus(klines_all, strat_base)

    results = []
    best_score = float("-inf")
    best = None
    best_trades = None
    t0 = time.time()
    out_dir = cfg["output"]["dir"]
    os.makedirs(out_dir, exist_ok=True)
    ckpt_path = os.path.join(out_dir, "optimization_checkpoint.csv")
    for idx, combo in enumerate(combos, 1):
        perf, trades = evaluate_combo(klines_all, combo, strat_base, fixed, bt_p, pre=pre)
        sc = _score(perf, metric, min_trades, min_winrate)
        perf["score"] = sc
        results.append(perf)
        if sc > best_score:
            best_score = sc
            best = combo
            best_trades = trades
        if idx % 16 == 0 or idx == len(combos):
            elapsed = time.time() - t0
            print(f"[opt] {idx}/{len(combos)}  当前最优 score={best_score:.3f} {best}  ({elapsed:.0f}s)", flush=True)
            try:
                pd.DataFrame(results).sort_values("score", ascending=False).to_csv(ckpt_path, index=False)
            except Exception:  # noqa: BLE001
                pass

    res_df = pd.DataFrame(results).sort_values("score", ascending=False).reset_index(drop=True)
    per_symbol_best = _per_symbol_optimal(klines_all, res_df.head(20), strat_base, fixed, bt_p, metric, pre=pre)
    _write_report(cfg, res_df, per_symbol_best, best_trades)

    print("\n===== 全局 Top10 =====")
    cols = _OPT_KEYS + ["n_trades", "win_rate", "profit_factor", "expectancy_r", "max_dd_r"]
    print(res_df.head(10)[[c for c in cols if c in res_df.columns]].to_string(index=False))

    if best is not None:
        print(f"\n[opt] 全局最优: {best}  score={best_score:.3f}")
        if write_back:
            write_back_config(config_path, best)
            print(f"[opt] 已写回 {config_path} -> strategy 段")
    else:
        print("\n[opt] 无组合满足约束(min_trades/min_winrate)，未写回默认值。可放宽约束后重跑。")
    return {"best": best, "best_score": best_score, "top10": res_df.head(10), "per_symbol": per_symbol_best}


def finalize_from_checkpoint(config_path: str = "config.yaml", write_back: bool = True) -> dict:
    """从 checkpoint CSV 恢复, 只完成 per-symbol + 报告 + 写回 (跳过网格搜索)"""
    cfg = load_config(config_path)
    oc = cfg["optimize"]
    out_dir = cfg["output"]["dir"]
    ckpt_path = os.path.join(out_dir, "optimization_checkpoint.csv")
    if not os.path.exists(ckpt_path):
        raise FileNotFoundError(f"无 checkpoint: {ckpt_path}, 请先跑完整 optimize.py")
    res_df = pd.read_csv(ckpt_path).sort_values("score", ascending=False).reset_index(drop=True)
    print(f"[opt] 从 checkpoint 恢复 {len(res_df)} 个组合结果", flush=True)

    strat_base = cfg["strategy"]
    bt_p = cfg["backtest"]
    fixed = oc["fixed"]
    metric = oc["metric"]
    klines_all = load_all_klines(cfg, table=oc["run_table"], use_cache=True, progress=False)
    pre = _precompute_bonus(klines_all, strat_base)

    per_symbol_best = _per_symbol_optimal(klines_all, res_df.head(20), strat_base, fixed, bt_p, metric, pre=pre)

    best_row = res_df.iloc[0]
    best = {k: best_row[k] for k in _OPT_KEYS if k in best_row}
    best_score = float(best_row["score"])
    _, best_trades = evaluate_combo(klines_all, best, strat_base, fixed, bt_p, pre=pre)

    _write_report(cfg, res_df, per_symbol_best, best_trades)
    print("\n===== 全局 Top10 =====")
    cols = _OPT_KEYS + ["n_trades", "win_rate", "profit_factor", "expectancy_r", "max_dd_r"]
    print(res_df.head(10)[[c for c in cols if c in res_df.columns]].to_string(index=False))
    print(f"\n[opt] 全局最优: {best}  score={best_score:.3f}")
    if write_back:
        write_back_config(config_path, best)
        print(f"[opt] 已写回 {config_path} -> strategy 段")
    return {"best": best, "best_score": best_score, "top10": res_df.head(10), "per_symbol": per_symbol_best}


def _write_report(cfg, res_df, per_symbol_best, best_trades):
    out_dir = cfg["output"]["dir"]
    os.makedirs(out_dir, exist_ok=True)
    top10 = res_df.head(10)
    try:
        with pd.ExcelWriter(os.path.join(out_dir, "optimization_report.xlsx")) as xw:
            top10.to_excel(xw, sheet_name="global_top10", index=False)
            per_symbol_best.to_excel(xw, sheet_name="per_symbol_best", index=False)
            if best_trades is not None and len(best_trades):
                best_trades.to_excel(xw, sheet_name="best_combo_trades", index=False)
    except Exception:  # noqa: BLE001
        top10.to_csv(os.path.join(out_dir, "optimization_global_top10.csv"), index=False)
        per_symbol_best.to_csv(os.path.join(out_dir, "optimization_per_symbol.csv"), index=False)
    res_df.to_csv(os.path.join(out_dir, "optimization_all.csv"), index=False)
    print(f"[opt] 报告已写入 {out_dir}/optimization_report.xlsx (及 csv 备份)", flush=True)


def _per_symbol_optimal(klines_all, top_rows, strat_base, fixed, bt_p, metric, pre=None):
    """对每个品种，在全局Top组合里挑 metric 最优的"""
    rows = []
    for sym, df in klines_all.items():
        best = None
        best_val = float("-inf")
        for _, r in top_rows.iterrows():
            combo = {k: r[k] for k in _OPT_KEYS if k in r}
            p = _build_params(strat_base, combo, fixed)
            try:
                trades, _ = backtest(df, p, bt_p, precomputed=pre.get(sym) if pre else None)
            except Exception:  # noqa: BLE001
                continue
            perf = performance(trades)
            val = perf.get(metric, 0.0)
            if metric == "profit_factor" and val == float("inf"):
                val = 1e6
            if perf["n_trades"] >= 2 and val > best_val:
                best_val = val
                best = {**combo, **{f"sym_{k}": v for k, v in perf.items()}}
        if best:
            best["symbol"] = sym
            rows.append(best)
    if not rows:
        return pd.DataFrame()
    out = pd.DataFrame(rows)
    out.columns = [c[4:] if c.startswith("sym_") else c for c in out.columns]
    return out


if __name__ == "__main__":
    import sys
    args = sys.argv[1:]
    resume = "--resume" in args
    cfg_path = next((a for a in args if not a.startswith("--")), "config.yaml")
    if resume:
        finalize_from_checkpoint(cfg_path, write_back=True)
    else:
        run_optimization(cfg_path, write_back=True)
