"""
backtest.py
===========
对 find_signals() 产生的信号逐笔模拟，使用 ATR 止损 + 跟踪止盈出场，
统计绩效指标 (利润因子/胜率/期望R/最大回撤等)。

出场规则(无未来函数):
  多头:
    初始止损 = entry - atr_stop_mult * ATR(入场根)
    入场后逐根更新: 若启用跟踪, 极值价 = 历史最高收盘,
                    止损 = max(止损, 极值价 - trailing_atr_mult * ATR)
    任一根 low <= 止损 -> 该根 open(跳空)或 stop 触发, 平仓
  空头镜像。

注意: 同一持仓期间新信号忽略(不叠加)，避免重复入场。
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from indicators import attach_indicators, atr
from signals import find_signals


def _simulate_one(df: pd.DataFrame, sig: pd.Series, bt: dict, atr_series: pd.Series) -> dict:
    """模拟单笔交易。返回含出场信息的 dict。"""
    i = int(sig["entry_idx"])
    n = len(df)
    if i >= n - 1:  # 无后续 K 线可持有 -> 不开仓
        return None

    direction = int(sig["direction"])
    entry = float(sig["entry_price"])
    a = float(atr_series.iloc[i]) if not np.isnan(atr_series.iloc[i]) else 0.0
    if a <= 0:
        return None

    mult_stop = bt["atr_stop_mult"]
    mult_trail = bt["trailing_atr_mult"]
    trailing = bt.get("trailing_enable", True)

    if direction == 1:
        stop = entry - mult_stop * a
        peak = entry
    else:
        stop = entry + mult_stop * a
        trough = entry

    exit_idx = n - 1
    exit_price = float(df["close"].iloc[-1])
    exit_reason = "end"

    highs = df["high"].to_numpy()
    lows = df["low"].to_numpy()
    opens = df["open"].to_numpy()
    closes = df["close"].to_numpy()
    atr_arr = atr_series.to_numpy()

    for k in range(i + 1, n):
        a_k = atr_arr[k]
        if np.isnan(a_k):
            a_k = a
        # 跟踪止盈更新(用前一根收盘价作为极值参照, 避免当根穿越假象)
        if trailing and k > i + 1:
            prev_c = closes[k - 1]
            if direction == 1:
                peak = max(peak, prev_c)
                new_stop = peak - mult_trail * a_k
                stop = max(stop, new_stop)
            else:
                trough = min(trough, prev_c)
                new_stop = trough + mult_trail * a_k
                stop = min(stop, new_stop)
        # 触发止损
        if direction == 1 and lows[k] <= stop:
            exit_idx = k
            exit_price = stop if opens[k] > stop else opens[k]
            exit_reason = "stop"
            break
        if direction == -1 and highs[k] >= stop:
            exit_idx = k
            exit_price = stop if opens[k] < stop else opens[k]
            exit_reason = "stop"
            break
    else:
        exit_price = float(closes[-1])

    pnl = (exit_price - entry) * direction
    risk = mult_stop * a  # 初始风险
    r_mult = pnl / risk if risk > 0 else 0.0
    bars_held = exit_idx - i
    return {
        "datetime": sig["datetime"],
        "entry_idx": i,
        "entry_time": sig["entry_time"],
        "exit_idx": exit_idx,
        "exit_time": df["datetime"].iloc[exit_idx],
        "direction": direction,
        "entry_price": entry,
        "exit_price": exit_price,
        "pnl": pnl,
        "r_mult": r_mult,
        "risk": risk,
        "bars_held": bars_held,
        "exit_reason": exit_reason,
        "bonus": bool(sig.get("bonus", False)),
        "twist_cross": int(sig.get("twist_cross", 0)),
    }


def backtest(df: pd.DataFrame, strat_p: dict, bt_p: dict, precomputed: Optional[dict] = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    返回 (trades_df, enriched_df_with_indicators)。
    trades_df: 每笔交易明细。同一持仓期内的新信号被忽略。
    precomputed: 复用的 bonus 极值序列 (网格优化用)。
    """
    d = attach_indicators(df, strat_p, precomputed=precomputed)
    atr_s = atr(d, bt_p["atr_period"])
    sigs = find_signals(df, strat_p, precomputed=precomputed)

    trades = []
    last_exit = -1
    for _, sig in sigs.iterrows():
        ei = int(sig["entry_idx"])
        if ei <= last_exit:  # 上一笔未平仓 -> 忽略
            continue
        if bt_p.get("require_index_atr", True) and ei < bt_p["atr_period"]:
            continue
        res = _simulate_one(df, sig, bt_p, atr_s)
        if res is None:
            continue
        trades.append(res)
        last_exit = res["exit_idx"]
    return pd.DataFrame(trades), d


# ---------------------------------------------------------------------
# 绩效指标
# ---------------------------------------------------------------------
def performance(trades: pd.DataFrame) -> dict:
    if trades is None or len(trades) == 0:
        return {
            "n_trades": 0, "win_rate": 0.0, "profit_factor": 0.0,
            "expectancy_r": 0.0, "max_dd_r": 0.0, "avg_bars": 0, "total_r": 0.0,
        }
    r = trades["r_mult"].to_numpy()
    wins = r[r > 0]
    losses = r[r < 0]
    gross_win = wins.sum()
    gross_loss = -losses.sum()
    pf = gross_win / gross_loss if gross_loss > 0 else (float("inf") if gross_win > 0 else 0.0)
    # 以 R 为单位的权益曲线最大回撤
    equity = np.cumsum(r)
    running_max = np.maximum.accumulate(equity)
    dd = running_max - equity
    max_dd_r = float(dd.max()) if len(dd) else 0.0
    return {
        "n_trades": len(trades),
        "win_rate": float((r > 0).mean()),
        "profit_factor": float(pf),
        "expectancy_r": float(r.mean()),
        "total_r": float(r.sum()),
        "max_dd_r": max_dd_r,
        "avg_bars": float(trades["bars_held"].mean()),
        "bonus_rate": float(trades["bonus"].mean()),
    }


# ---------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------
def _cli():
    import argparse
    from data_loader import load_klines, load_config

    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="TA0")
    ap.add_argument("--config", default="config.yaml")
    args = ap.parse_args()

    cfg = load_config(args.config)
    df = load_klines(args.symbol, cfg)
    trades, _ = backtest(df, cfg["strategy"], cfg["backtest"])
    perf = performance(trades)
    print(f"\n=== {args.symbol}: {perf['n_trades']} trades ===")
    for k, v in perf.items():
        print(f"  {k}: {v:.3f}" if isinstance(v, float) else f"  {k}: {v}")
    if len(trades):
        print(trades[["entry_time", "exit_time", "direction", "entry_price",
                      "exit_price", "r_mult", "exit_reason", "bonus"]].to_string(index=False))


if __name__ == "__main__":
    _cli()
