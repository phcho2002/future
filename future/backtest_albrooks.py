#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Al Brooks 三推反转系统 — 回测脚本
================================
对指定品种在30分钟K线上运行 Al Brooks 三推反转系统，
检测信号并模拟交易:
  - 开多信号: Three Pushes DOWN → Bullish Reversal
  - 开空信号: Three Pushes UP → Bearish Reversal
  - 每次交易一手
  - 止损/止盈: 使用Al Brooks系统预设 (RiskCalculator计算)
  - 数据范围: 2025-06-01 ~ 2026-06-11

用法:
  python backtest_albrooks.py
"""

import sys
import os
import io
import time
import warnings
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
import akshare as ak

# Fix Windows GBK encoding for emoji
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

warnings.filterwarnings('ignore')

# Add parent to path for local imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from albrooks_reversal import ReversalEngine, EngineConfig
from albrooks_reversal.engine import ReversalSignal

# ---- 接入全系统统一行情入口 future_data（xtquant 后端 + TTL 缓存）----
from pathlib import Path
_SCRIPT_DIR = Path(__file__).resolve().parent
_WORK_AI = _SCRIPT_DIR.parent
sys.path.insert(0, str(_WORK_AI))
try:
    from future_data import get_klines as _tq_get_klines
    from future_data.universe import build_exchange_map as _tq_exch_map
    _HAS_FUTURE_DATA = True
except Exception:  # noqa: BLE001
    _HAS_FUTURE_DATA = False
_EXCHANGE_MAP: Optional[Dict[str, str]] = None


def _resolve_exchange(symbol: str) -> Optional[str]:
    """从 DB futures_top40 查 symbol 对应 exchange（连续主力如 OI0/C0）。"""
    global _EXCHANGE_MAP
    if _EXCHANGE_MAP is None and _HAS_FUTURE_DATA:
        try:
            _EXCHANGE_MAP = _tq_exch_map()
        except Exception:  # noqa: BLE001
            _EXCHANGE_MAP = {}
    return (_EXCHANGE_MAP or {}).get(symbol)

# ============================================================================
# 回测配置
# ============================================================================

START_DATE = "2025-06-01"
END_DATE = "2026-06-11"

USE_SYSTEM_STOP_TP = True  # True=使用系统预设止损止盈, False=使用固定ATR倍数
ATR_STOP_MULT = 0.5      # ATR 止损倍数 (仅 USE_SYSTEM_STOP_TP=False 时生效)
ATR_TARGET_MULT = 1.6    # ATR 止盈倍数 (仅 USE_SYSTEM_STOP_TP=False 时生效)
SCAN_STEP = 10           # 每隔N根K线扫描一次
MIN_BARS = 200           # 最小K线数 (用于系统初始化)

# 合约乘数 (一手=多少吨/克/点价值)
MULTIPLIER_MAP = {
    "V": 5, "P": 10, "B": 10, "M": 10, "I": 100, "JD": 5, "L": 5,
    "PP": 5, "FB": 10, "Y": 10, "C": 10, "A": 10, "J": 100, "JM": 60,
    "CS": 10, "EG": 10, "RR": 10, "EB": 5, "PG": 20, "LH": 16,
    "LG": 90, "BZ": 5,
    "TA": 5, "OI": 10, "RS": 10, "RM": 10, "WH": 20, "JR": 20,
    "SR": 10, "CF": 5, "RI": 20, "MA": 10, "FG": 20, "LR": 20,
    "SF": 5, "SM": 5, "CY": 5, "AP": 10, "CJ": 5, "UR": 20,
    "SA": 20, "PF": 5, "PK": 5, "SH": 5, "PX": 5, "PR": 5, "PL": 5,
    "FU": 10, "AL": 5, "RU": 10, "ZN": 5, "CU": 5, "AU": 1000,
    "RB": 10, "PB": 5, "AG": 15, "BU": 10, "HC": 10, "SN": 1,
    "NI": 1, "SP": 10, "SS": 5, "AO": 20, "BR": 5, "AD": 25, "OP": 10,
    "SC": 1000, "NR": 10, "LU": 10, "BC": 5, "EC": 50,
    "IF": 300, "IH": 300, "IC": 200, "IM": 200,
    "TS": 20000, "TF": 10000, "T": 10000, "TL": 10000,
    "SI": 5, "LC": 1, "PS": 3, "PT": 1, "PD": 1,
}

# ============================================================================
# 关注品种 (从信号扫描结果中选出前3做多 + 前3做空)
# 使用连续合约symbol获取历史数据
# ============================================================================

# 做多品种 (Three Pushes DOWN → Bullish Reversal) - Top 3 by probability
LONG_PRODUCTS = [
    {"name": "菜油",     "continuous": "OI0", "contract": "OI2609", "multiplier": 10},
    {"name": "拳击印刷纸", "continuous": "OP0", "contract": "OP2608", "multiplier": 10},
    {"name": "硅铁",     "continuous": "SF0", "contract": "SF2607", "multiplier": 5},
]

# 做空品种 (Three Pushes UP → Bearish Reversal) - Top 3 by probability
SHORT_PRODUCTS = [
    {"name": "丙烯",     "continuous": "PL0", "contract": "PL2609", "multiplier": 5},
    {"name": "对二甲苯",  "continuous": "PX0", "contract": "PX2609", "multiplier": 5},
    {"name": "玉米",     "continuous": "C0",  "contract": "C2607",  "multiplier": 10},
]

ALL_PRODUCTS = LONG_PRODUCTS + SHORT_PRODUCTS

# ============================================================================
# 数据结构
# ============================================================================

@dataclass
class Trade:
    """单个交易记录"""
    product_name: str
    symbol: str
    direction: str           # 'long' or 'short'
    entry_time: str
    exit_time: str
    entry_price: float
    exit_price: float
    exit_reason: str         # 'stop_loss', 'take_profit', 'end_of_data'
    pnl_points: float        # 价格变动
    pnl_money: float         # 金额变动 = pnl_points * multiplier
    multiplier: int
    atr_at_entry: float
    stop_loss_price: float
    take_profit_price: float
    probability: float
    bars_held: int


@dataclass
class BacktestResult:
    """完整回测结果"""
    product_name: str
    symbol: str
    direction: str
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    total_pnl: float
    avg_pnl: float
    max_win: float
    max_loss: float
    profit_factor: float
    trades: List[Trade]


# ============================================================================
# ATR 计算
# ============================================================================

def compute_atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> np.ndarray:
    """计算 ATR (Wilder's smoothing)"""
    n = len(close)
    tr = np.zeros(n)
    tr[0] = high[0] - low[0]
    for i in range(1, n):
        tr[i] = max(
            high[i] - low[i],
            abs(high[i] - close[i - 1]),
            abs(low[i] - close[i - 1])
        )

    atr = np.zeros(n)
    atr[period] = np.mean(tr[1:period + 1])
    for i in range(period + 1, n):
        atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period
    atr[:period] = atr[period]
    return atr


# ============================================================================
# 数据获取
# ============================================================================

def fetch_historical_30min(symbol: str) -> Optional[pd.DataFrame]:
    """
    获取30分钟历史K线。

    优先走统一入口 future_data（xtquant 后端，可取深历史 + TTL 缓存）；
    失败回退到 akshare futures_zh_minute_sina。
    返回 DataFrame [datetime, open, high, low, close, volume]，按 [START_DATE, END_DATE] 过滤。

    注：xtquant 单次可取 ~8964 根（akshare 仅 ~320），所以这里的深度回测
    会比 akshare 时代更完整（结果数值会变，属正向收益）。
    """
    # 日期范围过滤（两条路径都用）
    start_dt = pd.Timestamp(START_DATE)
    end_dt = pd.Timestamp(END_DATE) + pd.Timedelta(days=1)

    # ---- 主路径：统一入口 future_data（xtquant）----
    if _HAS_FUTURE_DATA:
        ex = _resolve_exchange(symbol)
        if ex is not None:
            try:
                # 取尽可能多的历史以覆盖完整回测区间
                df = _tq_get_klines(symbol, ex, period="30", length=8000)
                if df is not None and not df.empty:
                    df = df[(df["datetime"] >= start_dt) & (df["datetime"] < end_dt)]
                    return df.reset_index(drop=True) if not df.empty else None
            except Exception as e:
                print(f"    [xtquant] 获取失败 [{symbol}]: {e}，回退 akshare")

    # ---- 回退路径：akshare ----
    try:
        df = ak.futures_zh_minute_sina(symbol=symbol, period="30")
        if df is None or df.empty:
            return None

        # 标准化列名
        rename_map = {}
        for c in df.columns:
            cl = c.lower()
            if "时间" in c or "date" in cl:
                rename_map[c] = "datetime"
            elif "开" in c or "open" in cl:
                rename_map[c] = "open"
            elif "高" in c or "high" in cl:
                rename_map[c] = "high"
            elif "低" in c or "low" in cl:
                rename_map[c] = "low"
            elif "收" in c or "close" in cl:
                rename_map[c] = "close"
            elif "量" in c or "vol" in cl:
                rename_map[c] = "volume"
        df.rename(columns=rename_map, inplace=True)

        for c in ["open", "high", "low", "close", "volume"]:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")

        df["datetime"] = pd.to_datetime(df["datetime"])
        df.sort_values("datetime", inplace=True)
        df.reset_index(drop=True, inplace=True)

        # 按日期范围过滤
        df = df[(df["datetime"] >= start_dt) & (df["datetime"] < end_dt)]

        return df
    except Exception as e:
        print(f"    获取数据失败 [{symbol}]: {e}")
        return None


# ============================================================================
# Al Brooks 引擎
# ============================================================================

def create_engine() -> ReversalEngine:
    """创建针对30分钟级别优化的引擎"""
    config = EngineConfig(
        swing_window=4,
        min_pushes=2,
        momentum_decay_threshold=0.12,
        divergence_lookback=20,
        model_bias=-1.8,
        model_calibration=4.0,
    )
    return ReversalEngine(config)


# ============================================================================
# 交易模拟
# ============================================================================

def simulate_trade(
    df: pd.DataFrame,
    entry_idx: int,
    direction: str,
    engine: ReversalEngine,
    preset_stop_loss: Optional[float] = None,
    preset_take_profit: Optional[float] = None,
) -> Optional[Trade]:
    """
    模拟一笔交易: 在 entry_idx 的下一根K线开盘入场,
    使用系统预设或 ATR 倍数止损止盈, 跟踪到离场。

    Args:
        preset_stop_loss: 系统预设止损价 (None=使用ATR计算)
        preset_take_profit: 系统预设止盈价 (None=使用ATR计算)

    Returns Trade or None.
    """
    n = len(df)
    if entry_idx >= n - 1:
        return None

    # 入场: 下一根K线开盘价
    entry_bar = entry_idx + 1
    entry_price = float(df["open"].iloc[entry_bar])
    entry_time = str(df["datetime"].iloc[entry_bar])

    open_arr = df["open"].values.astype(np.float64)
    high_arr = df["high"].values.astype(np.float64)
    low_arr = df["low"].values.astype(np.float64)
    close_arr = df["close"].values.astype(np.float64)

    # 计算入场时的 ATR (用于显示)
    atr_arr = compute_atr(high_arr, low_arr, close_arr, period=14)
    atr_at_entry = float(atr_arr[entry_bar])

    if USE_SYSTEM_STOP_TP and preset_stop_loss is not None and preset_take_profit is not None:
        # 使用系统预设止损止盈距离，应用到实际入场价
        # 系统输出的 target/stop 是基于信号bar收盘价，我们提取相对距离
        mid_price = (preset_take_profit + preset_stop_loss) / 2.0
        target_dist = abs(preset_take_profit - mid_price)
        stop_dist = abs(preset_stop_loss - mid_price)

        if direction == "long":
            stop_loss = entry_price - stop_dist
            take_profit = entry_price + target_dist
        else:
            stop_loss = entry_price + stop_dist
            take_profit = entry_price - target_dist
    else:
        # 使用固定 ATR 倍数
        if atr_at_entry <= 0:
            return None
        stop_atr = ATR_STOP_MULT * atr_at_entry
        target_atr = ATR_TARGET_MULT * atr_at_entry
        if direction == "long":
            stop_loss = entry_price - stop_atr
            take_profit = entry_price + target_atr
        else:
            stop_loss = entry_price + stop_atr
            take_profit = entry_price - target_atr

    # 逐根K线跟踪
    exit_idx = entry_bar
    exit_reason = "end_of_data"

    for i in range(entry_bar + 1, n):
        if direction == "long":
            if low_arr[i] <= stop_loss:
                exit_price = stop_loss
                exit_idx = i
                exit_reason = "stop_loss"
                break
            if high_arr[i] >= take_profit:
                exit_price = take_profit
                exit_idx = i
                exit_reason = "take_profit"
                break
        else:  # short
            if high_arr[i] >= stop_loss:
                exit_price = stop_loss
                exit_idx = i
                exit_reason = "stop_loss"
                break
            if low_arr[i] <= take_profit:
                exit_price = take_profit
                exit_idx = i
                exit_reason = "take_profit"
                break
    else:
        # 未触发任何条件, 用最后收盘价退出
        exit_price = float(close_arr[-1])
        exit_idx = n - 1
        exit_reason = "end_of_data"

    exit_time = str(df["datetime"].iloc[exit_idx])

    if direction == "long":
        pnl_points = exit_price - entry_price
    else:
        pnl_points = entry_price - exit_price

    bars_held = exit_idx - entry_bar

    return Trade(
        product_name="",
        symbol="",
        direction=direction,
        entry_time=entry_time,
        exit_time=exit_time,
        entry_price=round(entry_price, 4),
        exit_price=round(exit_price, 4),
        exit_reason=exit_reason,
        pnl_points=round(pnl_points, 4),
        pnl_money=0.0,  # 稍后填充
        multiplier=1,
        atr_at_entry=round(atr_at_entry, 4),
        stop_loss_price=round(stop_loss, 4),
        take_profit_price=round(take_profit, 4),
        probability=0.0,
        bars_held=bars_held,
    )


# ============================================================================
# 单品种回测
# ============================================================================

def backtest_product(
    product: Dict,
    expected_direction: str,  # 'long' or 'short'
    engine: ReversalEngine,
) -> BacktestResult:
    """
    对单个品种执行完整回测。

    Steps:
    1. 获取30分钟K线历史数据
    2. 每隔 SCAN_STEP 根K线扫描一次 Al Brooks 信号
    3. 当检测到匹配方向的信号时, 模拟一笔交易
    4. 交易期间跳过新信号 (避免重叠持仓)
    """
    name = product["name"]
    symbol = product["continuous"]
    multiplier = product["multiplier"]

    print(f"\n{'='*80}")
    print(f"  回测 [{name}] {symbol} — 方向: {'做多' if expected_direction == 'long' else '做空'}")
    print(f"{'='*80}")

    # 获取数据
    print(f"  [1] 获取30分钟K线数据...")
    df = fetch_historical_30min(symbol)
    if df is None or len(df) < MIN_BARS:
        print(f"  [X] 数据不足 (需要 >= {MIN_BARS} 根K线)")
        return BacktestResult(
            product_name=name, symbol=symbol, direction=expected_direction,
            total_trades=0, winning_trades=0, losing_trades=0, win_rate=0.0,
            total_pnl=0.0, avg_pnl=0.0, max_win=0.0, max_loss=0.0,
            profit_factor=0.0, trades=[]
        )

    n_bars = len(df)
    date_range = f"{df['datetime'].iloc[0]} ~ {df['datetime'].iloc[-1]}"
    print(f"  [1] 获取到 {n_bars} 根K线 ({date_range})")

    # 扫描信号
    print(f"  [2] 扫描 Al Brooks 三推反转信号 (步长={SCAN_STEP})...")

    trades = []
    in_trade_until = -1  # 持仓直到此K线索引
    scan_positions = list(range(MIN_BARS, n_bars - 5, SCAN_STEP))

    for idx in scan_positions:
        # 如果还在持仓中, 跳过
        if idx < in_trade_until:
            continue

        # 提取窗口数据
        window_end = min(idx + 1, n_bars)
        window_start = max(0, window_end - MIN_BARS)

        win_high = df["high"].values[window_start:window_end].astype(np.float64)
        win_low = df["low"].values[window_start:window_end].astype(np.float64)
        win_close = df["close"].values[window_start:window_end].astype(np.float64)
        win_open = df["open"].values[window_start:window_end].astype(np.float64)

        if len(win_close) < 100:
            continue

        try:
            signal = engine.analyze(win_high, win_low, win_close, win_open)
        except Exception:
            continue

        # 检查信号是否匹配
        if not signal.pattern_detected:
            continue

        signal_dir = "long" if signal.direction == "bullish" else \
                     "short" if signal.direction == "bearish" else None

        if signal_dir != expected_direction:
            continue

        # 信号匹配 → 模拟交易 (使用系统预设止损止盈)
        preset_stop = float(signal.stop_loss) if signal.stop_loss else None
        preset_tp = float(signal.target_price) if signal.target_price else None

        trade = simulate_trade(df, idx, expected_direction, engine,
                               preset_stop_loss=preset_stop,
                               preset_take_profit=preset_tp)
        if trade is None:
            continue

        trade.product_name = name
        trade.symbol = symbol
        trade.multiplier = multiplier
        trade.pnl_money = trade.pnl_points * multiplier
        trade.probability = signal.probability

        trades.append(trade)
        in_trade_until = trade.bars_held + idx + 1  # 持仓结束索引

        emoji = "📈" if expected_direction == "long" else "📉"
        print(f"    {emoji} #{len(trades):>3} | 入场={trade.entry_time[:16]} | "
              f"出场={trade.exit_time[:16]} | "
              f"方向={trade.direction:>5} | "
              f"盈亏={trade.pnl_money:>+8.0f}元 | "
              f"原因={trade.exit_reason:<12} | "
              f"概率={trade.probability:.1f}% | "
              f"持有={trade.bars_held}bar")

    # 计算结果
    if trades:
        wins = [t for t in trades if t.pnl_money > 0]
        losses = [t for t in trades if t.pnl_money <= 0]
        total_pnl = sum(t.pnl_money for t in trades)
        win_rate = len(wins) / len(trades) * 100
        avg_pnl = total_pnl / len(trades)
        max_win = max(t.pnl_money for t in trades) if wins else 0
        max_loss = min(t.pnl_money for t in trades) if losses else 0
        total_wins = sum(t.pnl_money for t in wins) if wins else 0
        total_losses = abs(sum(t.pnl_money for t in losses)) if losses else 0
        profit_factor = total_wins / total_losses if total_losses > 0 else float('inf')
    else:
        total_pnl = 0.0
        win_rate = 0.0
        avg_pnl = 0.0
        max_win = 0.0
        max_loss = 0.0
        profit_factor = 0.0

    result = BacktestResult(
        product_name=name,
        symbol=symbol,
        direction=expected_direction,
        total_trades=len(trades),
        winning_trades=len([t for t in trades if t.pnl_money > 0]),
        losing_trades=len([t for t in trades if t.pnl_money <= 0]),
        win_rate=round(win_rate, 1),
        total_pnl=round(total_pnl, 2),
        avg_pnl=round(avg_pnl, 2),
        max_win=round(max_win, 2),
        max_loss=round(max_loss, 2),
        profit_factor=round(profit_factor, 2),
        trades=trades,
    )

    return result


# ============================================================================
# 主流程
# ============================================================================

def main():
    t0 = datetime.now()
    print()
    print("=" * 80)
    print("  Al Brooks 三推反转系统 — 回测")
    print(f"  时间: {t0.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  数据范围: {START_DATE} ~ {END_DATE}")
    print(f"  止损/止盈: 系统预设 (RiskCalculator) | 每次交易: 1手 | 扫描步长: {SCAN_STEP} 根K线")
    print("=" * 80)

    engine = create_engine()

    # ─── 做多回测 ───
    print("\n" + "█" * 80)
    print("█  Part 1: 做多信号回测 (Three Pushes DOWN → Bullish Reversal)")
    print("█" * 80)

    long_results = []
    for prod in LONG_PRODUCTS:
        result = backtest_product(prod, "long", engine)
        long_results.append(result)
        time.sleep(0.5)

    # ─── 做空回测 ───
    print("\n" + "█" * 80)
    print("█  Part 2: 做空信号回测 (Three Pushes UP → Bearish Reversal)")
    print("█" * 80)

    short_results = []
    for prod in SHORT_PRODUCTS:
        result = backtest_product(prod, "short", engine)
        short_results.append(result)
        time.sleep(0.5)

    # ====================================================================
    # 汇总报告
    # ====================================================================
    all_results = long_results + short_results

    print()
    print("=" * 80)
    print("  📊 回测汇总报告")
    print("=" * 80)

    # ─── 做多汇总 ───
    print()
    print("  ┌─ 做多品种 (LONG) ─────────────────────────────────────┐")
    print(f"  │ {'品种':<14}{'交易次数':>6}{'胜率':>8}{'总盈亏':>12}{'均盈亏':>10}{'最大赢':>10}{'最大亏':>10}{'盈亏比':>8} │")
    print(f"  │ {'-'*14}{'-'*6}{'-'*8}{'-'*12}{'-'*10}{'-'*10}{'-'*10}{'-'*8} │")

    long_total_pnl = 0
    long_all_trades = []
    for r in long_results:
        print(f"  │ {r.product_name:<14}{r.total_trades:>6}{r.win_rate:>7.1f}%{r.total_pnl:>+11.0f}{r.avg_pnl:>+9.0f}{r.max_win:>+9.0f}{r.max_loss:>+9.0f}{r.profit_factor:>7.2f} │")
        long_total_pnl += r.total_pnl
        long_all_trades.extend(r.trades)

    long_win_trades = sum(1 for t in long_all_trades if t.pnl_money > 0)
    long_total = len(long_all_trades)
    print(f"  │ {'─'*76} │")
    print(f"  │ {'合计':<14}{long_total:>6}{long_win_trades/long_total*100 if long_total else 0:>7.1f}%{long_total_pnl:>+11.0f}{'':>10}{'':>10}{'':>10}{'':>8} │")
    print(f"  └{'─'*78}┘")

    # ─── 做空汇总 ───
    print()
    print("  ┌─ 做空品种 (SHORT) ────────────────────────────────────┐")
    print(f"  │ {'品种':<14}{'交易次数':>6}{'胜率':>8}{'总盈亏':>12}{'均盈亏':>10}{'最大赢':>10}{'最大亏':>10}{'盈亏比':>8} │")
    print(f"  │ {'-'*14}{'-'*6}{'-'*8}{'-'*12}{'-'*10}{'-'*10}{'-'*10}{'-'*8} │")

    short_total_pnl = 0
    short_all_trades = []
    for r in short_results:
        print(f"  │ {r.product_name:<14}{r.total_trades:>6}{r.win_rate:>7.1f}%{r.total_pnl:>+11.0f}{r.avg_pnl:>+9.0f}{r.max_win:>+9.0f}{r.max_loss:>+9.0f}{r.profit_factor:>7.2f} │")
        short_total_pnl += r.total_pnl
        short_all_trades.extend(r.trades)

    short_win_trades = sum(1 for t in short_all_trades if t.pnl_money > 0)
    short_total = len(short_all_trades)
    print(f"  │ {'─'*76} │")
    print(f"  │ {'合计':<14}{short_total:>6}{short_win_trades/short_total*100 if short_total else 0:>7.1f}%{short_total_pnl:>+11.0f}{'':>10}{'':>10}{'':>10}{'':>8} │")
    print(f"  └{'─'*78}┘")

    # ─── 总汇总 ───
    total_pnl = long_total_pnl + short_total_pnl
    all_trades = long_all_trades + short_all_trades
    total_trades = len(all_trades)
    total_wins = sum(1 for t in all_trades if t.pnl_money > 0)
    total_losses = sum(1 for t in all_trades if t.pnl_money <= 0)
    total_win_rate = total_wins / total_trades * 100 if total_trades else 0
    gross_profit = sum(t.pnl_money for t in all_trades if t.pnl_money > 0)
    gross_loss = abs(sum(t.pnl_money for t in all_trades if t.pnl_money <= 0))
    pf = gross_profit / gross_loss if gross_loss > 0 else float('inf')

    print()
    print("  ╔══════════════════════════════════════════════════════════╗")
    print(f"  ║  📈 总回测结果                                           ║")
    print(f"  ╠══════════════════════════════════════════════════════════╣")
    print(f"  ║  回测区间: {START_DATE} ~ {END_DATE}                    ║")
    print(f"  ║  交易品种: {len(ALL_PRODUCTS)} 个 (做多{len(LONG_PRODUCTS)}, 做空{len(SHORT_PRODUCTS)})                           ║")
    print(f"  ║  总交易次数: {total_trades}                                          ║")
    print(f"  ║  盈利次数: {total_wins} | 亏损次数: {total_losses}                            ║")
    print(f"  ║  胜率: {total_win_rate:.1f}%                                            ║")
    print(f"  ║  总盈亏: {total_pnl:+,.0f} 元                                      ║")
    print(f"  ║  总盈利: {gross_profit:+,.0f} 元 | 总亏损: {-gross_loss:+,.0f} 元                ║")
    print(f"  ║  盈亏比 (Profit Factor): {pf:.2f}                                 ║")
    if total_trades > 0:
        avg_trade = total_pnl / total_trades
        print(f"  ║  平均每笔盈亏: {avg_trade:+,.0f} 元                                    ║")
    print(f"  ╚══════════════════════════════════════════════════════════╝")

    # ─── 详细交易记录 ───
    if all_trades:
        print()
        print("  ┌─ 详细交易记录 ─────────────────────────────────────────────────────────────┐")
        print(f"  │ {'品种':<14}{'方向':>5}{'入场时间':<18}{'出场时间':<18}{'入场价':>8}{'出场价':>8}{'盈亏(元)':>10}{'原因':<12}{'概率':>6}{'持有':>5} │")
        print(f"  │ {'-'*14}{'-'*5}{'-'*18}{'-'*18}{'-'*8}{'-'*8}{'-'*10}{'-'*12}{'-'*6}{'-'*5} │")
        for t in all_trades:
            dir_str = "做多" if t.direction == "long" else "做空"
            print(f"  │ {t.product_name:<14}{dir_str:>5}{t.entry_time[:16]:<18}{t.exit_time[:16]:<18}"
                  f"{t.entry_price:>8.2f}{t.exit_price:>8.2f}{t.pnl_money:>+10.0f}"
                  f"{t.exit_reason:<12}{t.probability:>5.1f}%{t.bars_held:>5} │")
        print(f"  └{'─'*92}┘")

    elapsed = (datetime.now() - t0).total_seconds()
    print(f"\n  回测完成 | 耗时: {elapsed:.1f}s")
    print("=" * 80)


if __name__ == "__main__":
    main()
