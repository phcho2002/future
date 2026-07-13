#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
小时线二次突破趋势跟踪回测
============================

策略逻辑(以空头为例,多头完全镜像):
  入场: 复用 SecondBreakoutEngine(蓄势→假突破→失败→二次突破)
  入场: 收盘价突破近 N 根最低价(创新低) → 开空头
  加仓: 持仓中价格继续突破前低(创新低) → 加仓 = 初次手数 × 1/2
        加仓后用移动止损:跟踪近期高点 + 1.2×ATR(让利润奔跑)
  止盈: 持仓中价格【不能】突破前低(未能创新低) → 全部止盈平仓
  止损: 无加仓时用总资金 0.9% 硬止损;加仓后改用移动止损

资金管理:
  - 初始资金 500 万/品种
  - 开仓手数 = 当前资金 × 8% ÷ (入场价 × 合约乘数 × 保证金率)
  - 无滑点,合约乘数按真实设置

运行: python backtest_daily_breakout.py
"""

import sys
from pathlib import Path
import numpy as np
import pandas as pd

THIS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(THIS_DIR))
WORK_AI = r"d:\work_ai"
sys.path.insert(0, WORK_AI)

import config  # noqa: E402
from backtest_30m import SecondBreakoutEngine  # noqa: E402  复用二次突破状态机
from indicators import Indicators  # noqa: E402

# ============ 参数 ============
LOOKBACK = 20               # 前 N 根高低点(Donchian 通道)
KLINE_LENGTH = 8000         # 拉取根数
KLINE_PERIOD = "60"         # 60 分钟(小时级别)
INITIAL_CAPITAL = 5_000_000 # 初始资金 500 万/品种
RISK_CAPITAL_RATIO = 0.08   # 开仓:当前资金 8%
ADD_CAPITAL_RATIO = 0.03    # 加仓:每次用当前资金 3%
ADD_ATR_STEP = 2.0          # 加仓触发:价格顺向每走 2×ATR 加仓一次(可多次)
NO_NEW_EXTREME_BARS = 999   # 止盈:已禁用(设大数,改为纯移动止损让利润奔跑)
HARD_STOP_LOSS = 0.03       # 灾难止损:总资金 3%(兜底,不频繁干扰)
TRAIL_ATR_MULT = 2.0        # 移动止损(全程):跟踪近期极值 ± 2.0×ATR(给趋势呼吸空间)
INITIAL_STOP_ATR = 1.5      # 开仓初始止损(突破位 ∓ 1.5×ATR)
MARGIN_RATE = 0.12          # 保证金率
COMMISSION_RATE = 0.0003    # 手续费(单边,万分之三)
ATR_PERIOD = 14

OUTPUT_DIR = THIS_DIR / "output"

# 6品种(取自 strategy_config.yaml,优质品种:SN0/LC0顶级 + P0/AO0/CF0/AU0板块分散)
TARGETS = [
    ("SN0", "锡",       "shfe", 1),
    ("LC0", "碳酸锂",   "gfex", 1),
    ("P0",  "棕榈油",   "dce",  10),
    ("AO0", "氧化铝",   "shfe", 20),
    ("CF0", "棉花",     "czce", 5),
    ("AU0", "黄金",     "shfe", 1000),
]


def load_klines(symbol, exchange):
    """加载小时线(period=60)。"""
    from future_data import get_klines
    df = get_klines(symbol, exchange, period=KLINE_PERIOD, length=KLINE_LENGTH, ttl_hours=9999)
    if df is None or df.empty:
        raise RuntimeError(f"{symbol} 无数据")
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["open", "high", "low", "close"]).sort_values("datetime").reset_index(drop=True)
    return df.tail(KLINE_LENGTH).reset_index(drop=True)


def backtest_symbol(df, multiplier):
    """小时线二次突破趋势跟踪回测。

    入场:复用 SecondBreakoutEngine(蓄势→假突破→失败→二次突破)
    出场:全程移动止损(1.2×ATR) + 连续3根未创新极值止盈 + 0.9%硬止损
    加仓:顺向走3×ATR → 用当前资金3%加仓(可多次)
    """
    n = len(df)
    if n < config.PATTERN_LOOKBACK + ATR_PERIOD + 5:
        return {"error": "data too short"}

    # 1) 二次突破状态机产出入场信号(内部会算 atr/ema200/setup_pattern/adx 等)
    engine = SecondBreakoutEngine(df)
    signals = engine.run()   # [(idx, side, level, atr), ...]
    df = engine.df

    # 补充前N根高/低点(用于"未创新极值"止盈判断)
    df["prev_high"] = df["high"].rolling(LOOKBACK).max().shift(1)
    df["prev_low"] = df["low"].rolling(LOOKBACK).min().shift(1)

    capital = float(INITIAL_CAPITAL)
    trades = []
    position = None
    sig_queue = list(signals)
    last_sig_idx = -1

    def open_pos(idx, side, level, atr_val):
        """信号在 idx 收盘确认,idx+1 开盘开仓。"""
        if idx + 1 >= n or atr_val <= 0:
            return None
        entry_bar = df.iloc[idx + 1]
        entry_price = float(entry_bar["open"])
        lots = int(capital * RISK_CAPITAL_RATIO / (entry_price * multiplier * MARGIN_RATE))
        if lots < 1:
            return None
        stop = level - side * atr_val * INITIAL_STOP_ATR
        return {
            "side": side,
            "entry_dt": entry_bar["datetime"],
            "entry_idx": idx + 1,
            "initial_entry": entry_price,
            "initial_lots": lots,
            "lots": [{"entry_price": entry_price, "size": lots}],
            "stop": stop,
            "capital_at_open": capital,
            "added": False,
            "max_lots": lots,
            "last_add_price": entry_price,
            "no_extreme_count": 0,
        }

    def close_all(pos, j, price, reason):
        nonlocal capital
        bar = df.iloc[j]
        pnl = 0.0
        size = 0
        for lot in pos["lots"]:
            pp = (price - lot["entry_price"]) * pos["side"] * lot["size"]
            cc = (lot["entry_price"] + price) * lot["size"] * COMMISSION_RATE
            pnl += (pp - cc) * multiplier
            size += lot["size"]
        capital += pnl
        trades.append({
            "side": "LONG" if pos["side"] == 1 else "SHORT",
            "entry_dt": pos["entry_dt"],
            "exit_dt": bar["datetime"],
            "hold_bars": j - pos["entry_idx"],
            "size": size,
            "max_lots": pos["max_lots"],
            "added": pos["added"],
            "pnl_money": pnl,
            "reason": reason,
        })

    def hard_stop_price(pos):
        max_loss = pos["capital_at_open"] * HARD_STOP_LOSS
        total = sum(l["size"] for l in pos["lots"])
        if total <= 0:
            return None
        avg = sum(l["entry_price"] * l["size"] for l in pos["lots"]) / total
        return avg - pos["side"] * max_loss / (total * multiplier)

    # ===== 逐K线 =====
    for i in range(n):
        bar = df.iloc[i]
        c = float(bar["close"])
        h = float(bar["high"])
        l = float(bar["low"])
        atr = float(bar["atr"]) if np.isfinite(bar["atr"]) else 0.0
        prev_low = float(bar["prev_low"]) if np.isfinite(bar.get("prev_low", np.nan)) else 0.0
        prev_high = float(bar["prev_high"]) if np.isfinite(bar.get("prev_high", np.nan)) else 0.0

        if position is not None:
            side = position["side"]
            exited = False

            # 1) 移动止损:极值 ± 1.2×ATR(全程)
            if atr > 0:
                if side == 1:
                    trail = l - atr * TRAIL_ATR_MULT
                    if trail > position["stop"]:
                        position["stop"] = trail
                    if l <= position["stop"]:
                        close_all(position, i, position["stop"], "trail_stop")
                        position = None; exited = True
                else:
                    trail = h + atr * TRAIL_ATR_MULT
                    if trail < position["stop"]:
                        position["stop"] = trail
                    if h >= position["stop"]:
                        close_all(position, i, position["stop"], "trail_stop")
                        position = None; exited = True

            # 2) 硬止损 0.9%(全程)
            if not exited and position is not None:
                hs = hard_stop_price(position)
                if hs is not None:
                    hit = (side == 1 and l <= hs) or (side == -1 and h >= hs)
                    if hit:
                        close_all(position, i, hs, "hard_stop")
                        position = None; exited = True

            # 3) 连续3根未创新极值 → 止盈
            if not exited and position is not None:
                made_new = (side == -1 and c < prev_low) or (side == 1 and c > prev_high)
                if made_new:
                    position["no_extreme_count"] = 0
                else:
                    position["no_extreme_count"] += 1
                    if position["no_extreme_count"] >= NO_NEW_EXTREME_BARS:
                        close_all(position, i, c, "no_new_extreme_3")
                        position = None; exited = True

            # 4) 加仓:顺向走3×ATR → 资金3%(可多次)
            if not exited and position is not None and i + 1 < n and atr > 0:
                move = (c - position["last_add_price"]) * side
                if move >= ADD_ATR_STEP * atr:
                    add_lots = max(1, int(capital * ADD_CAPITAL_RATIO / (c * multiplier * MARGIN_RATE)))
                    if add_lots >= 1:
                        add_price = float(df.iloc[i + 1]["open"])
                        position["lots"].append({"entry_price": add_price, "size": add_lots})
                        position["added"] = True
                        position["last_add_price"] = c
                        position["max_lots"] = max(position["max_lots"], sum(x["size"] for x in position["lots"]))

        # 入场:消费二次突破信号
        if position is None and sig_queue:
            sig_idx, sig_side, sig_level, sig_atr = sig_queue[0]
            if sig_idx == i and sig_idx > last_sig_idx:
                last_sig_idx = sig_idx
                sig_queue.pop(0)
                position = open_pos(sig_idx, sig_side, sig_level, sig_atr)
            elif sig_idx < i:
                sig_queue.pop(0)

    if position is not None:
        close_all(position, n - 1, float(df.iloc[-1]["close"]), "eod_close")

    summary = _summarize(df, trades, multiplier, capital)
    summary["n_signals"] = len(signals)
    summary["_trades"] = trades
    return summary

def _summarize(df, trades, multiplier, final_capital):
    n_trades = len(trades)
    wins = [t for t in trades if t["pnl_money"] > 0]
    losses = [t for t in trades if t["pnl_money"] <= 0]
    win_rate = len(wins) / n_trades if n_trades > 0 else 0.0
    total_pnl = sum(t["pnl_money"] for t in trades)
    avg_win = np.mean([t["pnl_money"] for t in wins]) if wins else 0.0
    avg_loss = abs(np.mean([t["pnl_money"] for t in losses])) if losses else 0.0
    pf = avg_win / avg_loss if avg_loss > 0 else (float("inf") if avg_win > 0 else 0.0)
    avg_hold = np.mean([t["hold_bars"] for t in trades]) if trades else 0.0
    add_ratio = sum(1 for t in trades if t["added"]) / n_trades if n_trades > 0 else 0.0
    reason_dist = {}
    for t in trades:
        reason_dist[t["reason"]] = reason_dist.get(t["reason"], 0) + 1

    span_days = (df["datetime"].iloc[-1] - df["datetime"].iloc[0]).total_seconds() / 86400.0
    years = span_days / 365.0
    cap_ret = total_pnl / INITIAL_CAPITAL
    annual = (1 + cap_ret) ** (1 / years) - 1 if years > 0 and cap_ret > -1 else 0.0

    return {
        "bars": len(df), "span_days": span_days,
        "start": df["datetime"].iloc[0], "end": df["datetime"].iloc[-1],
        "trades": n_trades,
        "long_trades": sum(1 for t in trades if t["side"] == "LONG"),
        "short_trades": sum(1 for t in trades if t["side"] == "SHORT"),
        "win_rate": win_rate, "total_pnl": total_pnl,
        "cap_return": cap_ret, "annual": annual, "pf": pf,
        "avg_hold": avg_hold, "add_ratio": add_ratio,
        "final_capital": final_capital, "reason_dist": reason_dist,
        "_trades": trades,
    }


def main():
    print("=" * 110)
    print(f"  小时线二次突破趋势跟踪回测 — 波动率前15品种 / 无滑点 / 500万账户 / 8%开仓")
    print("=" * 110)

    data = {}
    for sym, name, ex, mult in TARGETS:
        try:
            df = load_klines(sym, ex)
            data[sym] = (df, mult, name)
        except Exception as e:
            print(f"  [{sym}] 加载失败: {e}")

    print(f"\n  加载 {len(data)} 品种,逐品种回测...\n")

    results = {}
    all_trades = []
    for sym, (df, mult, name) in data.items():
        r = backtest_symbol(df, mult)
        results[sym] = r
        for t in r.get("_trades", []):
            t["symbol"] = sym
            all_trades.append(t)
        if "error" in r:
            print(f"  [{sym}] {name}: 错误 {r['error']}")
        else:
            print(f"  [{sym}] {name}: 交易={r['trades']} 胜率={r['win_rate']*100:.1f}% "
                  f"收益={r['cap_return']*100:+.2f}% 年化={r['annual']*100:+.2f}% 加仓率={r['add_ratio']*100:.0f}%")

    _print_summary(results)
    _save(results, all_trades)


def _print_summary(results):
    print("\n" + "=" * 120)
    print("  小时线二次突破趋势跟踪 — 波动率前15品种(剔除股指)")
    print("=" * 120)
    print(f"  入场: 突破前{LOOKBACK}根高/低点 | 加仓: 顺向走{ADD_ATR_STEP}×ATR +资金3%(可多次) | "
          f"止盈: 连续{NO_NEW_EXTREME_BARS}根未创新极值全平 | 硬止损: {HARD_STOP_LOSS*100:.1f}%(全程) | "
          f"移动止损: 全程 极值±{TRAIL_ATR_MULT}×ATR")
    print(f"  资金: 500万/品种 | 开仓8% | 无滑点 | 手续费{COMMISSION_RATE*10000:.1f}‱")
    print("-" * 120)
    print(f"  {'品种':<6}{'K线':>6}{'天数':>6}{'做多':>6}{'做空':>6}{'交易':>6}"
          f"{'胜率':>7}{'账户收益率':>11}{'年化':>9}{'盈亏比':>8}{'加仓率':>7}{'均持仓':>7}")
    print("  " + "-" * 80)

    agg = {k: 0 for k in ["long", "short", "trades", "wins", "pnl"]}
    for sym, r in results.items():
        if "error" in r:
            print(f"  {sym:<6}  [错误]"); continue
        w = int(round(r["win_rate"] * r["trades"]))
        agg["long"] += r["long_trades"]; agg["short"] += r["short_trades"]
        agg["trades"] += r["trades"]; agg["wins"] += w; agg["pnl"] += r["total_pnl"]
        print(f"  {sym:<6}{r['bars']:>6}{r['span_days']:>6.0f}"
              f"{r['long_trades']:>6}{r['short_trades']:>6}{r['trades']:>6}"
              f"{r['win_rate']*100:>6.1f}%"
              f"{r['cap_return']*100:>10.2f}%"
              f"{r['annual']*100:>8.2f}%"
              f"{r['pf']:>8.2f}{r['add_ratio']*100:>6.0f}%{r['avg_hold']:>7.1f}")

    print("  " + "-" * 80)
    n = agg["trades"]
    wr = agg["wins"] / n if n else 0
    n_acc = sum(1 for r in results.values() if "error" not in r)
    cap_all = agg["pnl"] / (INITIAL_CAPITAL * n_acc) if n_acc else 0
    print(f"  {'合计':<6}{'':>6}{'':>6}{agg['long']:>6}{agg['short']:>6}{n:>6}"
          f"{wr*100:>6.1f}%{cap_all*100:>10.2f}%{'':>9}{'':>8}{'':>7}{'':>7}")
    print(f"  ({n_acc}账户合计: 总盈亏 {agg['pnl']:+,.0f}元 / 本金{n_acc*500}万 → 收益率 {cap_all*100:+.2f}%)")
    print("=" * 120)


def _save(results, all_trades):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    rows = []
    for sym, r in results.items():
        if "error" in r:
            continue
        rows.append({
            "品种": sym, "K线数": r["bars"], "天数": round(r["span_days"], 0),
            "做多": r["long_trades"], "做空": r["short_trades"], "交易": r["trades"],
            "胜率": round(r["win_rate"] * 100, 1), "总盈亏": round(r["total_pnl"], 0),
            "账户收益率%": round(r["cap_return"] * 100, 2), "年化%": round(r["annual"] * 100, 2),
            "盈亏比": round(r["pf"], 2), "加仓率%": round(r["add_ratio"] * 100, 0),
            "均持仓": round(r["avg_hold"], 1), "最终资金": round(r["final_capital"], 0),
            "出场原因": str(r["reason_dist"]),
        })
    pd.DataFrame(rows).to_csv(OUTPUT_DIR / "daily_breakout_summary.csv", index=False, encoding="utf-8-sig")
    if all_trades:
        pd.DataFrame(all_trades).to_csv(OUTPUT_DIR / "daily_breakout_trades.csv", index=False, encoding="utf-8-sig")
    print(f"\n  已保存: daily_breakout_summary.csv / daily_breakout_trades.csv ({len(all_trades)}笔)")


if __name__ == "__main__":
    main()
