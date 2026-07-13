#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
backtest_compare.py
===================
对 future_4 与 future_7 两个"逻辑相同、实现不同"的 EMA 缠绕带放量突破策略，
在【完全相同】的最近 8000 根 30 分钟 K 线 + 【共享】资金/风控/出场模型下做
模拟交易回测，横向评估孰优孰劣。

公平性：
  - 数据统一：future_data.get_klines (xtquant) 拉 futures_top40 全 40 品种 × 8000 根。
  - 资金/风控/出场统一：两个算法共用同一个 simulate 引擎，只有信号生成不同。
  - 合约乘数：futures_data.db 的 futures_top40.合约乘数。

交易规则（用户指定 + 我的出场设计）：
  1. 本金 2,000,000，无手续费、无滑点。
  2. 开仓量 = 动态总权益 × 5% 名义价值（复利口径）：
        qty = max(1, floor(0.05 * equity / (entry_price * multiplier)))
     若 1 手所需资金 > 5% 则开 1 手。
  3. 止盈止损（ATR 模型）：
        初始止损 = entry ∓ 1.5*ATR；
        跟踪止盈：止损线随有利方向跟进 peak/trough ∓ 1.5*ATR；
        ATR 止盈锁定：浮盈达 3*ATR 后把锁定止损收紧到 entry ± 1*ATR；
        当根 high/low 触发，跳空按当根 open 成交（无未来函数）。
  4. 总风控：任一品种浮亏 > 总权益 1.1% → 当根 open 无条件平仓。
  5. 同一品种持仓期间新信号忽略（不叠加）。

账户口径（关键，保证动态权益 / 强平阈值自洽）：
  - 全品种按【真实时间戳】合并成一条全局权益曲线。
  - 每个 30min 时间步：先处理【上一时间步平仓的实现盈亏】更新权益，
    再用【当前权益】决定本步新开仓的 qty 与 1.1% 强平阈值；
  - 这样开仓 qty、强平阈值、权益三者完全自洽（无事后重算）。

不修改 future_4 / future_7 源码。
"""
from __future__ import annotations

import os
import sys
import math
import sqlite3
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

# ---- 路径：让 future_4 / future_7 / future_data 都可被 import ----
WORK_AI = "D:/work_ai"
for sub in ("", "future_4", "future_7"):
    p = os.path.join(WORK_AI, sub)
    if p not in sys.path:
        sys.path.insert(0, p)

# =====================================================================
# 全局配置
# =====================================================================
INIT_CAPITAL = 2_000_000.0      # 本金
POSITION_RATIO = 0.05           # 每信号名义价值 = 总权益 * 5%
FLOAT_LOSS_LIMIT = 0.011        # 浮亏 > 总权益 1.1% → 强平
N_BARS = 8000                   # 最近 N 根
PERIOD = "30"                   # 30 分钟

# ATR 出场参数（沿用 future_4，对两个算法公平）
ATR_PERIOD = 14
ATR_STOP_MULT = 1.5             # 初始止损
ATR_TRAIL_MULT = 1.5            # 跟踪止盈
ATR_PROFIT_TRIGGER = 3.0        # 浮盈达 3*ATR 启动锁定
ATR_PROFIT_LOCK = 1.0           # 锁定止损 = entry + 1*ATR (有利方向)

DB_PATH = os.path.join(WORK_AI, "futures_data.db")
OUT_DIR = os.path.join(WORK_AI, "output_compare")


# =====================================================================
# 数据加载
# =====================================================================
def load_multipliers(table: str = "futures_top40") -> dict:
    """{symbol: 合约乘数}。优先 futures_top40.合约乘数，回退 futures_all.multiplier。"""
    conn = sqlite3.connect(DB_PATH)
    try:
        try:
            df = pd.read_sql_query(
                f'SELECT symbol AS s, "合约乘数" AS m FROM "{table}"', conn
            )
        except Exception:
            df = pd.read_sql_query(
                'SELECT symbol AS s, multiplier AS m FROM "futures_all"', conn
            )
    finally:
        conn.close()
    mult = {}
    for _, r in df.iterrows():
        try:
            mult[str(r["s"]).strip()] = float(r["m"])
        except Exception:
            pass
    return mult


def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [c.lower() for c in df.columns]
    if not pd.api.types.is_datetime64_any_dtype(df["datetime"]):
        df["datetime"] = pd.to_datetime(df["datetime"])
    for c in ["open", "high", "low", "close", "volume"]:
        if c in df:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["open", "high", "low", "close"]).sort_values("datetime").reset_index(drop=True)
    return df


def load_all_bars(symbols: list[str], exchanges: dict, length: int = N_BARS) -> dict:
    """逐品种拉取 8000 根 30min K 线。失败品种跳过。返回 {symbol: df}。"""
    from future_data import get_klines  # xtquant 后端 + 共享缓存

    out: dict[str, "pd.DataFrame"] = {}
    n = len(symbols)
    for i, sym in enumerate(symbols, 1):
        ex = exchanges.get(sym)
        if ex is None:
            print(f"[{i}/{n}] {sym}: SKIP (无 exchange)")
            continue
        try:
            df = get_klines(sym, ex, period=PERIOD, length=length, ttl_hours=24, force=False)
            df = _normalize(df)
            if len(df) < 200:
                print(f"[{i}/{n}] {sym}: SKIP (bars={len(df)} 太少)")
                continue
            out[sym] = df
            print(f"[{i}/{n}] {sym}: {len(df)} bars")
        except Exception as e:  # noqa: BLE001
            print(f"[{i}/{n}] {sym}: SKIP ({type(e).__name__}: {e})")
    return out


# =====================================================================
# ATR (Wilder)
# =====================================================================
def atr_series(df: pd.DataFrame, period: int = ATR_PERIOD) -> np.ndarray:
    high, low = df["high"], df["low"]
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [(high - low).abs(), (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1.0 / period, adjust=False).mean().to_numpy()


# =====================================================================
# 信号适配器：两个算法归一化为同一张信号表
#   columns: symbol, entry_idx, entry_time, entry_price, direction(+1/-1)
# =====================================================================
def signals_future4(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """调 future_4.signals.find_signals。EMA=10/26，缠绕带+密度过滤+交叉触发。"""
    import yaml
    from signals import find_signals  # future_4/signals.py

    with open(os.path.join(WORK_AI, "future_4", "config.yaml"), "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    p4 = cfg["strategy"]
    sigs = find_signals(df, p4)
    cols = ["symbol", "entry_idx", "entry_time", "entry_price", "direction"]
    if len(sigs) == 0:
        return pd.DataFrame(columns=cols)
    out = pd.DataFrame({
        "symbol": symbol,
        "entry_idx": sigs["entry_idx"].astype(int).to_numpy(),
        "entry_time": sigs["entry_time"].to_numpy(),
        "entry_price": sigs["entry_price"].astype(float).to_numpy(),
        "direction": sigs["direction"].astype(int).to_numpy(),
    })
    return out


def signals_future7(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """调 future_7.quant_system.generate_signals。EMA=8/21，缠绕带+放量中阳/阴同根确认。"""
    from quant_system import StrategyConfig, generate_signals  # future_7

    res = generate_signals(df, StrategyConfig())
    mask = res["signal"] != ""
    cols = ["symbol", "entry_idx", "entry_time", "entry_price", "direction"]
    if not mask.any():
        return pd.DataFrame(columns=cols)
    sub = res[mask]
    confirm_idx = sub.index.to_numpy()
    entry_idx = np.minimum(confirm_idx + 1, len(res) - 1)
    direction = np.where(sub["signal"].to_numpy() == "long", 1, -1)
    out = pd.DataFrame({
        "symbol": symbol,
        "entry_idx": entry_idx.astype(int),
        "entry_time": sub["entry_time"].to_numpy(),
        "entry_price": pd.to_numeric(sub["entry_price"], errors="coerce").to_numpy(),
        "direction": direction,
    })
    out = out.dropna(subset=["entry_price"]).reset_index(drop=True)
    return out


# =====================================================================
# 持仓对象
# =====================================================================
@dataclass
class Position:
    symbol: str
    direction: int            # +1 / -1
    entry_idx: int            # 开仓 K 线索引（在 df 中）
    entry_price: float
    qty: int
    multiplier: float
    atr_entry: float
    stop: float
    peak: float = 0.0
    trough: float = 0.0
    profit_locked: bool = False
    equity_at_entry: float = 0.0


@dataclass
class SimParams:
    init_capital: float = INIT_CAPITAL
    position_ratio: float = POSITION_RATIO
    float_loss_limit: float = FLOAT_LOSS_LIMIT
    atr_stop_mult: float = ATR_STOP_MULT
    atr_trail_mult: float = ATR_TRAIL_MULT
    atr_profit_trigger: float = ATR_PROFIT_TRIGGER
    atr_profit_lock: float = ATR_PROFIT_LOCK
    atr_period: int = ATR_PERIOD


# =====================================================================
# 全局事件驱动模拟（动态权益自洽）
# =====================================================================
def simulate_portfolio(
    bars: dict[str, pd.DataFrame],
    signals_by_sym: dict[str, pd.DataFrame],
    multipliers: dict[str, float],
    params: SimParams,
    progress_name: str = "",
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    按【真实时间戳】合并所有品种的 K 线，事件驱动推进全局权益：
      - 每个时间步 ts：
          1) 先把【上一时间步】已确定要平仓的实现盈亏计入 equity；
          2) 用当前 equity 决定本步新开仓的 qty 与强平阈值；
          3) 更新每个持仓的跟踪/止盈/止损/浮亏强平判定（结果在下一 ts 生效）。
    这样 qty、1.1% 阈值、权益三者完全自洽，无事后重算。

    平仓优先级（在同一 ts 内）：risk_override / stop 触发 → 用当根 open 成交。
    """
    ATRs = {sym: atr_series(df, params.atr_period) for sym, df in bars.items()}

    # 每品种: 信号按 entry_idx 排序的指针 + 上次平仓 idx
    sig_ptr = {sym: 0 for sym in bars}
    last_exit_idx = {sym: -1 for sym in bars}

    # 把每个品种的 (datetime, sym, idx) 合并，按 datetime 升序遍历
    events = []
    arrs = {}
    for sym, df in bars.items():
        dt = df["datetime"].to_numpy()
        idxs = np.arange(len(df))
        for t, k in zip(dt, idxs):
            events.append((pd.Timestamp(t), sym, int(k)))
        arrs[sym] = {
            "open": df["open"].to_numpy(),
            "high": df["high"].to_numpy(),
            "low": df["low"].to_numpy(),
            "close": df["close"].to_numpy(),
        }
    events.sort(key=lambda e: (e[0], e[1]))

    equity = params.init_capital
    open_positions: dict[str, Position] = {}     # symbol -> 至多一个持仓
    trades: list[dict] = []
    equity_curve: list[tuple] = []               # (ts, equity_after_realized)

    # 收集每个时间步要结算的实现盈亏（来自上一步判定的平仓）
    pending_realized: dict[str, tuple] = {}      # sym -> (exit_idx, exit_price, exit_reason)

    def close_pos(sym: str, exit_idx: int, exit_price: float, exit_reason: str):
        pos = open_positions.pop(sym)
        realized = (exit_price - pos.entry_price) * pos.direction * pos.qty * pos.multiplier
        risk_amt = params.atr_stop_mult * pos.atr_entry * pos.qty * pos.multiplier
        r_mult = realized / risk_amt if risk_amt > 0 else 0.0
        trades.append({
            "symbol": sym,
            "entry_idx": pos.entry_idx,
            "exit_idx": exit_idx,
            "entry_time": bars[sym]["datetime"].iloc[pos.entry_idx],
            "exit_time": bars[sym]["datetime"].iloc[exit_idx],
            "direction": pos.direction,
            "entry_price": pos.entry_price,
            "exit_price": exit_price,
            "qty": pos.qty,
            "multiplier": pos.multiplier,
            "atr_entry": float(pos.atr_entry),
            "pnl": realized,
            "r_mult": r_mult,
            "risk_amt": risk_amt,
            "bars_held": exit_idx - pos.entry_idx,
            "exit_reason": exit_reason,
            "equity_at_entry": pos.equity_at_entry,
        })
        return realized

    prev_ts = None
    for ts, sym, k in events:
        a = ATRs[sym]
        ohlc = arrs[sym]

        # ---- (A) 结算上一时间步判定的本品种平仓（若 pending） ----
        if sym in pending_realized:
            exit_idx, exit_price, exit_reason = pending_realized.pop(sym)
            realized = close_pos(sym, exit_idx, exit_price, exit_reason)
            equity += realized
            last_exit_idx[sym] = exit_idx

        # ---- (B) 推进本品种持仓的逐 K 判定（用当前根 high/low/close） ----
        if sym in open_positions and k > open_positions[sym].entry_idx:
            pos = open_positions[sym]
            a_k = a[k] if np.isfinite(a[k]) and a[k] > 0 else pos.atr_entry
            high_k, low_k, close_k, open_k = ohlc["high"][k], ohlc["low"][k], ohlc["close"][k], ohlc["open"][k]

            # 跟踪止盈更新（用前一根收盘作极值参照）
            prev_c = ohlc["close"][k - 1]
            if pos.direction == 1:
                pos.peak = max(pos.peak, prev_c)
                new_stop = pos.peak - params.atr_trail_mult * a_k
                pos.stop = max(pos.stop, new_stop)
            else:
                pos.trough = min(pos.trough, prev_c)
                new_stop = pos.trough + params.atr_trail_mult * a_k
                pos.stop = min(pos.stop, new_stop)

            # 止盈锁定
            if not pos.profit_locked:
                if pos.direction == 1 and (close_k - pos.entry_price) >= params.atr_profit_trigger * pos.atr_entry:
                    pos.stop = max(pos.stop, pos.entry_price + params.atr_profit_lock * a_k)
                    pos.profit_locked = True
                elif pos.direction == -1 and (pos.entry_price - close_k) >= params.atr_profit_trigger * pos.atr_entry:
                    pos.stop = min(pos.stop, pos.entry_price - params.atr_profit_lock * a_k)
                    pos.profit_locked = True

            # 1.1% 浮亏强平（用当根 close 估浮动，触发则当根 open 平出）
            float_pnl = (close_k - pos.entry_price) * pos.direction * pos.qty * pos.multiplier
            max_loss_amt = params.float_loss_limit * pos.equity_at_entry
            triggered = None
            if float_pnl < -max_loss_amt:
                triggered = (k, float(open_k) if np.isfinite(open_k) else float(close_k), "risk_override")
            else:
                if pos.direction == 1 and low_k <= pos.stop:
                    triggered = (k, pos.stop if open_k > pos.stop else float(open_k), "stop")
                elif pos.direction == -1 and high_k >= pos.stop:
                    triggered = (k, pos.stop if open_k < pos.stop else float(open_k), "stop")

            if triggered is not None:
                # 放入 pending，下一时间步该品种事件时结算（保证权益顺序）
                pending_realized[sym] = triggered
                # 注意：该持仓不再更新（已在待平仓状态）

        # ---- (C) 开新仓（若本根无持仓、无待平、且命中信号） ----
        if sym not in open_positions and sym not in pending_realized:
            sigs = signals_by_sym.get(sym)
            if sigs is not None and len(sigs):
                ptr = sig_ptr[sym]
                # 找到 entry_idx == k 的信号
                while ptr < len(sigs) and int(sigs["entry_idx"].iloc[ptr]) < k:
                    ptr += 1
                sig_ptr[sym] = ptr
                if ptr < len(sigs) and int(sigs["entry_idx"].iloc[ptr]) == k:
                    sig = sigs.iloc[ptr]
                    sig_ptr[sym] = ptr + 1
                    if k <= last_exit_idx[sym]:
                        pass  # 上一笔未平（理论上不会，因为已平才到这）
                    else:
                        a0 = a[k]
                        if np.isfinite(a0) and a0 > 0:
                            entry = float(ohlc["open"][k])
                            if np.isfinite(entry) and entry > 0:
                                direction = int(sig["direction"])
                                notional = params.position_ratio * equity
                                qty = max(1, int(math.floor(notional / (entry * multipliers[sym]))))
                                if direction == 1:
                                    stop = entry - params.atr_stop_mult * a0
                                    pos = Position(sym, 1, k, entry, qty, multipliers[sym],
                                                   a0, stop, peak=entry, trough=entry)
                                else:
                                    stop = entry + params.atr_stop_mult * a0
                                    pos = Position(sym, -1, k, entry, qty, multipliers[sym],
                                                   a0, stop, peak=entry, trough=entry)
                                pos.equity_at_entry = equity
                                open_positions[sym] = pos

        # ---- (D) 记录权益曲线（每个时间步记一次，按 ts 去重保留最后值） ----
        if prev_ts != ts:
            equity_curve.append((ts, equity))
            prev_ts = ts
        else:
            equity_curve[-1] = (ts, equity)

    # ---- 收尾：所有未平持仓按最后一根 close 强平（end） ----
    for sym in list(open_positions.keys()):
        pos = open_positions[sym]
        last_k = len(bars[sym]) - 1
        realized = close_pos(sym, last_k, float(arrs[sym]["close"][last_k]), "end")
        equity += realized
    if equity_curve:
        equity_curve[-1] = (equity_curve[-1][0], equity)
    else:
        equity_curve.append((pd.Timestamp.now(), equity))

    trades_df = pd.DataFrame(trades)
    eq_df = pd.DataFrame(equity_curve, columns=["datetime", "equity"]).drop_duplicates("datetime", keep="last").reset_index(drop=True)
    if progress_name:
        print(f"  [{progress_name}] trades={len(trades_df)}  final_equity={equity:,.0f}  "
              f"net={ (equity-params.init_capital)/params.init_capital*100:.2f}%")
    return trades_df, eq_df


# =====================================================================
# 绩效指标
# =====================================================================
def metrics(trades: pd.DataFrame, eq_curve: pd.DataFrame, init_capital: float = INIT_CAPITAL) -> dict:
    if trades is None or len(trades) == 0:
        return _empty_metrics()
    pnl = trades["pnl"].to_numpy(dtype=float)
    n = len(pnl)
    final_eq = init_capital + pnl.sum()
    net_ret = pnl.sum() / init_capital

    equity = init_capital + np.cumsum(pnl)
    running_max = np.maximum.accumulate(equity)
    with np.errstate(divide="ignore", invalid="ignore"):
        dd = np.where(running_max > 0, (running_max - equity) / running_max, 0.0)
    max_dd = float(dd.max()) if len(dd) else 0.0

    wins = pnl[pnl > 0]
    losses = pnl[pnl < 0]
    win_rate = len(wins) / n if n else 0.0
    gross_win = wins.sum()
    gross_loss = -losses.sum()
    pf = gross_win / gross_loss if gross_loss > 0 else (float("inf") if gross_win > 0 else 0.0)
    avg_win = wins.mean() if len(wins) else 0.0
    avg_loss = losses.mean() if len(losses) else 0.0
    rr = (avg_win / abs(avg_loss)) if avg_loss != 0 else 0.0

    # 每笔收益率（相对开仓时权益）→ 夏普
    eq_at = trades["equity_at_entry"].to_numpy(dtype=float)
    eq_at = np.where(eq_at > 0, eq_at, init_capital)
    rets = pnl / eq_at
    rets = rets[np.isfinite(rets)]
    sharpe = float(rets.mean() / rets.std(ddof=1) * math.sqrt(252 * 8)) if len(rets) > 1 and rets.std(ddof=1) > 0 else 0.0

    # 年化：用首末交易时间跨度
    try:
        span_days = max(1.0, (pd.to_datetime(trades["exit_time"]).max() - pd.to_datetime(trades["entry_time"]).min()).total_seconds() / 86400)
    except Exception:
        span_days = 365.0
    years = span_days / 365.0
    ann = (final_eq / init_capital) ** (1 / years) - 1 if years > 0 and final_eq > 0 else net_ret

    r_mult = trades["r_mult"].to_numpy(dtype=float) if "r_mult" in trades else np.zeros(n)
    expectancy_r = float(r_mult.mean()) if len(r_mult) else 0.0

    override_n = int((trades["exit_reason"] == "risk_override").sum()) if "exit_reason" in trades else 0
    avg_bars = float(trades["bars_held"].mean()) if "bars_held" in trades else 0.0

    by_sym = trades.groupby("symbol")["pnl"].sum()
    worst_sym = str(by_sym.idxmin()) if len(by_sym) else "-"
    worst_pnl = float(by_sym.min()) if len(by_sym) else 0.0

    # 权益曲线最大回撤（更稳健）
    eq_dd = 0.0
    if eq_curve is not None and len(eq_curve):
        eqv = eq_curve["equity"].to_numpy(dtype=float)
        rmax = np.maximum.accumulate(eqv)
        with np.errstate(divide="ignore", invalid="ignore"):
            dd2 = np.where(rmax > 0, (rmax - eqv) / rmax, 0.0)
        eq_dd = float(dd2.max())

    return {
        "final_equity": final_eq,
        "net_return": net_ret,
        "annualized": ann,
        "sharpe": sharpe,
        "max_drawdown": max(eq_dd, max_dd),
        "win_rate": win_rate,
        "profit_factor": pf,
        "reward_risk": rr,
        "expectancy_r": expectancy_r,
        "avg_bars": avg_bars,
        "n_trades": n,
        "risk_overrides": override_n,
        "worst_symbol": worst_sym,
        "worst_symbol_pnl": worst_pnl,
    }


def _empty_metrics() -> dict:
    return {
        "final_equity": INIT_CAPITAL, "net_return": 0.0, "annualized": 0.0, "sharpe": 0.0,
        "max_drawdown": 0.0, "win_rate": 0.0, "profit_factor": 0.0, "reward_risk": 0.0,
        "expectancy_r": 0.0, "avg_bars": 0.0, "n_trades": 0, "risk_overrides": 0,
        "worst_symbol": "-", "worst_symbol_pnl": 0.0,
    }


# =====================================================================
# 单引擎运行
# =====================================================================
def run_engine(name: str, sig_func, bars: dict, mult: dict, params: SimParams) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """生成各品种信号 → 组合模拟 → 返回 (trades, equity_curve, per_symbol)。"""
    signals_by_sym: dict[str, pd.DataFrame] = {}
    per_symbol = []
    for sym, df in bars.items():
        try:
            sigs = sig_func(df, sym)
        except Exception as e:  # noqa: BLE001
            print(f"  [{name}] {sym} signal ERR: {type(e).__name__}: {e}")
            sigs = pd.DataFrame(columns=["symbol", "entry_idx", "entry_time", "entry_price", "direction"])
        signals_by_sym[sym] = sigs
        per_symbol.append({"symbol": sym, "n_signals": len(sigs)})

    trades, eq = simulate_portfolio(bars, signals_by_sym, mult, params, progress_name=name)

    # 每品种汇总
    if len(trades):
        agg = trades.groupby("symbol").agg(
            n_trades=("pnl", "size"), pnl=("pnl", "sum"),
            wins=("pnl", lambda x: int((x > 0).sum())),
        ).reset_index()
        agg_map = {r["symbol"]: r for _, r in agg.iterrows()}
    else:
        agg_map = {}
    for row in per_symbol:
        a = agg_map.get(row["symbol"])
        row["n_trades"] = int(a["n_trades"]) if a is not None else 0
        row["pnl"] = float(a["pnl"]) if a is not None else 0.0
        row["wins"] = int(a["wins"]) if a is not None else 0
    return trades, eq, pd.DataFrame(per_symbol)


# =====================================================================
# 输出
# =====================================================================
def fmt(v, w=12, pct=False, money=False):
    if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
        return str(v).rjust(w)
    if pct:
        return f"{v*100:.2f}%".rjust(w)
    if money:
        return f"{v:,.0f}".rjust(w)
    if isinstance(v, float):
        return f"{v:.3f}".rjust(w)
    return str(v).rjust(w)


REPORT_ROWS = [
    ("期末权益", "final_equity", True, False),
    ("净收益率", "net_return", False, True),
    ("年化收益", "annualized", False, True),
    ("夏普(年化)", "sharpe", False, False),
    ("最大回撤", "max_drawdown", False, True),
    ("胜率", "win_rate", False, True),
    ("盈亏比PF", "profit_factor", False, False),
    ("单笔盈亏比", "reward_risk", False, False),
    ("期望R", "expectancy_r", False, False),
    ("平均持仓根", "avg_bars", False, False),
    ("总交易数", "n_trades", False, False),
    ("1.1%强平数", "risk_overrides", False, False),
    ("最差品种", "worst_symbol", False, False),
    ("最差品种盈亏", "worst_symbol_pnl", True, False),
]


def print_compare(m4, m7):
    print("\n" + "=" * 64)
    print(f"{'指标':<16}{'future_4':>22}{'future_7':>22}")
    print("-" * 64)
    for label, key, money, pct in REPORT_ROWS:
        v4, v7 = m4.get(key), m7.get(key)
        if pct:
            print(f"{label:<16}{fmt(v4,22,pct=True)}{fmt(v7,22,pct=True)}")
        elif money:
            print(f"{label:<16}{fmt(v4,22,money=True)}{fmt(v7,22,money=True)}")
        elif key in ("n_trades", "risk_overrides"):
            print(f"{label:<16}{str(int(v4)).rjust(22)}{str(int(v7)).rjust(22)}")
        elif key == "worst_symbol":
            print(f"{label:<16}{str(v4).rjust(22)}{str(v7).rjust(22)}")
        else:
            print(f"{label:<16}{fmt(v4,22)}{fmt(v7,22)}")
    print("=" * 64)


def write_report(m4, m7, bars, symbols):
    lines = []
    lines.append("future_4 vs future_7 回测对比报告")
    lines.append(f"本金={INIT_CAPITAL:,.0f}  开仓=动态权益*{POSITION_RATIO:.0%}  "
                 f"浮亏强平线=权益*{FLOAT_LOSS_LIMIT:.1%}")
    lines.append(f"数据=最近{N_BARS}根/{PERIOD}min  品种成功={len(bars)}/{len(symbols)}")
    lines.append(f"出场: ATR止损={ATR_STOP_MULT}*ATR  跟踪={ATR_TRAIL_MULT}*ATR  "
                 f"止盈触发={ATR_PROFIT_TRIGGER}*ATR  锁定={ATR_PROFIT_LOCK}*ATR")
    lines.append("=" * 64)
    lines.append(f"{'指标':<16}{'future_4':>22}{'future_7':>22}")
    lines.append("-" * 64)
    for label, key, money, pct in REPORT_ROWS:
        v4, v7 = m4.get(key), m7.get(key)
        if pct:
            lines.append(f"{label:<16}{fmt(v4,22,pct=True)}{fmt(v7,22,pct=True)}")
        elif money:
            lines.append(f"{label:<16}{fmt(v4,22,money=True)}{fmt(v7,22,money=True)}")
        elif key in ("n_trades", "risk_overrides"):
            lines.append(f"{label:<16}{str(int(v4)).rjust(22)}{str(int(v7)).rjust(22)}")
        elif key == "worst_symbol":
            lines.append(f"{label:<16}{str(v4).rjust(22)}{str(v7).rjust(22)}")
        else:
            lines.append(f"{label:<16}{fmt(v4,22)}{fmt(v7,22)}")
    lines.append("=" * 64)
    lines.append("")
    lines.append("结论:")
    winner = "future_4" if m4["net_return"] > m7["net_return"] else "future_7"
    loser = "future_7" if winner == "future_4" else "future_4"
    lines.append(f"  - 净收益: {winner} ({max(m4['net_return'],m7['net_return'])*100:.2f}%) > "
                 f"{loser} ({min(m4['net_return'],m7['net_return'])*100:.2f}%)")
    bs = "future_4" if m4["sharpe"] > m7["sharpe"] else "future_7"
    lines.append(f"  - 风险调整(夏普): {bs} 更优 ({max(m4['sharpe'],m7['sharpe']):.3f})")
    bd = "future_4" if m4["max_drawdown"] < m7["max_drawdown"] else "future_7"
    lines.append(f"  - 回撤控制: {bd} 更小 ({min(m4['max_drawdown'],m7['max_drawdown'])*100:.2f}%)")
    bw = "future_4" if m4["win_rate"] > m7["win_rate"] else "future_7"
    lines.append(f"  - 胜率: {bw} 更高 ({max(m4['win_rate'],m7['win_rate'])*100:.2f}%)")
    with open(os.path.join(OUT_DIR, "comparison_report.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


# =====================================================================
# main
# =====================================================================
def main():
    os.makedirs(OUT_DIR, exist_ok=True)

    print("=" * 70)
    print("future_4 vs future_7 回测对比")
    print(f"本金={INIT_CAPITAL:,.0f}  开仓=动态权益*{POSITION_RATIO:.0%}  "
          f"浮亏强平线=权益*{FLOAT_LOSS_LIMIT:.1%}  数据=最近{N_BARS}根/{PERIOD}min")
    print("=" * 70)

    from future_data.universe import build_exchange_map, read_symbols

    mult = load_multipliers("futures_top40")
    exchanges = build_exchange_map()
    sym_tuples = read_symbols(DB_PATH, "futures_top40")
    symbols = [s[0] for s in sym_tuples]
    print(f"\n品种数={len(symbols)}  有乘数={len(mult)}  有交易所={len(exchanges)}")

    print(f"\n[1/3] 拉取 {N_BARS} 根 K 线 ...")
    bars = load_all_bars(symbols, exchanges, length=N_BARS)
    print(f"成功品种: {len(bars)}/{len(symbols)}")
    if len(bars) == 0:
        print("无可用数据，退出。")
        return

    params = SimParams()

    print("\n[2/3] future_4 引擎 (EMA10/26) ...")
    t4, eq4, ps4 = run_engine("future_4", signals_future4, bars, mult, params)

    print("\n[2/3] future_7 引擎 (EMA8/21) ...")
    t7, eq7, ps7 = run_engine("future_7", signals_future7, bars, mult, params)

    print("\n[3/3] 计算绩效 ...")
    m4 = metrics(t4, eq4)
    m7 = metrics(t7, eq7)
    print_compare(m4, m7)

    # 写文件
    if len(t4):
        t4.to_csv(os.path.join(OUT_DIR, "future4_trades.csv"), index=False, encoding="utf-8-sig")
    if len(t7):
        t7.to_csv(os.path.join(OUT_DIR, "future7_trades.csv"), index=False, encoding="utf-8-sig")
    if len(ps4):
        ps4.to_csv(os.path.join(OUT_DIR, "future4_per_symbol.csv"), index=False, encoding="utf-8-sig")
    if len(ps7):
        ps7.to_csv(os.path.join(OUT_DIR, "future7_per_symbol.csv"), index=False, encoding="utf-8-sig")
    if len(eq4):
        eq4.to_csv(os.path.join(OUT_DIR, "future4_equity.csv"), index=False, encoding="utf-8-sig")
    if len(eq7):
        eq7.to_csv(os.path.join(OUT_DIR, "future7_equity.csv"), index=False, encoding="utf-8-sig")
    write_report(m4, m7, bars, symbols)

    print(f"\n输出目录: {OUT_DIR}")
    print("  future4_trades.csv / future7_trades.csv")
    print("  future4_per_symbol.csv / future7_per_symbol.csv")
    print("  future4_equity.csv / future7_equity.csv")
    print("  comparison_report.txt")


if __name__ == "__main__":
    main()
