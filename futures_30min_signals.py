#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
期货30分钟K线量化交易系统
==========================

功能:
  1. 从 futures_data.db 读取关注品种
  2. 通过 akshare 获取各品种30分钟K线数据
  3. 运行多指标量化系统，检测开多/开空信号
  4. 以列表形式输出信号

指标体系:
  - EMA 双均线交叉 (12/26) → 趋势信号
  - MACD (12,26,9) → 动量信号
  - RSI (14) → 超买超卖信号
  - 布林带 (20,2) → 突破信号
  - KDJ (9,3,3) → 短期反转信号
  - 成交量异常检测 → 确认信号

信号规则:
  开多: 至少满足2个以上看多条件
  开空: 至少满足2个以上看空条件

使用:
  python futures_30min_signals.py

依赖:
  pip install akshare pandas numpy sqlite3
"""

import akshare as ak
import pandas as pd
import numpy as np
import sqlite3
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
import warnings
import time

warnings.filterwarnings("ignore")

# ---- 接入全系统统一行情入口 future_data（xtquant 后端 + TTL 缓存）----
import sys
sys.path.insert(0, r"d:\work_ai")
try:
    from future_data import get_klines as _tq_get_klines
    from future_data.universe import build_exchange_map as _tq_exch_map
    _HAS_FUTURE_DATA = True
except Exception:  # noqa: BLE001
    _HAS_FUTURE_DATA = False
# 首次用到时懒加载 symbol->exchange 映射
_EXCHANGE_MAP: Optional[Dict[str, str]] = None

# ============================================================
# 配置参数
# ============================================================
DB_PATH = r"d:\work_ai\futures_data.db"
LOOKBACK_BARS = 300          # 回溯K线数量 (30分钟K线)
SIGNAL_CONFIRM_COUNT = 2     # 至少满足N个看多/看空条件才出信号
REQUEST_DELAY = 0.4          # 请求间隔(秒)

# 均线参数
EMA_FAST = 12
EMA_SLOW = 26

# MACD参数
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9

# RSI参数
RSI_PERIOD = 14
RSI_OVERSOLD = 30
RSI_OVERBOUGHT = 70

# 布林带参数
BB_PERIOD = 20
BB_STD = 2.0

# KDJ参数
KDJ_N = 9
KDJ_M1 = 3
KDJ_M2 = 3

# 成交量
VOL_MA_PERIOD = 20
VOL_SURGE_RATIO = 2.0       # 成交量超越均量2倍视为放量


# ============================================================
# 第一步: 从数据库读取关注品种
# ============================================================

def load_tracked_futures(db_path: str = DB_PATH) -> pd.DataFrame:
    """
    从 futures_data.db 读取关注品种列表。
    优先读取 futures_top40 表，若不存在则读取 futures_all。

    Returns:
        DataFrame: [symbol, name, exchange]
    """
    print("=" * 72)
    print("  第一步: 读取关注品种")
    print("=" * 72)

    conn = sqlite3.connect(db_path)

    # 检查表
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [t[0] for t in cursor.fetchall()]
    print(f"  数据库表: {tables}")

    if "futures_top40" in tables:
        df = pd.read_sql("SELECT symbol, name, exchange FROM futures_top40 ORDER BY 排名", conn)
        print(f"  来源: futures_top40 → {len(df)} 个品种")
    elif "futures_all" in tables:
        df = pd.read_sql("SELECT symbol, name, exchange FROM futures_all", conn)
        print(f"  来源: futures_all → {len(df)} 个品种")
    else:
        print("  [错误] 未找到品种数据表")
        conn.close()
        return pd.DataFrame()

    conn.close()

    # 清理
    df["symbol"] = df["symbol"].astype(str).str.strip()
    df["name"] = df["name"].astype(str).str.strip()
    df["exchange"] = df["exchange"].astype(str).str.strip()

    print(f"  {'序号':<5}{'品种代码':<10}{'品种名称':<16}{'交易所':<10}")
    print(f"  {'-' * 41}")
    for i, row in df.iterrows():
        print(f"  {i+1:<5}{row['symbol']:<10}{row['name']:<16}{row['exchange']:<10}")

    return df


# ============================================================
# 第二步: 获取30分钟K线数据
# ============================================================

def _resolve_exchange(symbol: str) -> Optional[str]:
    """从 DB 的 futures_top40 查 symbol 对应的 exchange。"""
    global _EXCHANGE_MAP
    if _EXCHANGE_MAP is None and _HAS_FUTURE_DATA:
        try:
            _EXCHANGE_MAP = _tq_exch_map()
        except Exception:  # noqa: BLE001
            _EXCHANGE_MAP = {}
    return (_EXCHANGE_MAP or {}).get(symbol)


def get_30min_kline(symbol: str) -> Optional[pd.DataFrame]:
    """
    获取指定品种的30分钟K线数据。

    优先走统一入口 future_data（xtquant 后端，可取深历史 + TTL 缓存全系统共享）；
    失败回退到 akshare futures_zh_minute_sina。

    Args:
        symbol: 合约代码, 如 'RB0', 'M0'

    Returns:
        DataFrame [datetime, open, high, low, close, volume, hold] or None
        （xtquant 有 openInterest 字段，此处保留兼容，补 0 占位以兼容下游；下游不实际使用 hold）
    """
    # ---- 主路径：统一入口 future_data（xtquant）----
    if _HAS_FUTURE_DATA:
        ex = _resolve_exchange(symbol)
        if ex is not None:
            try:
                df = _tq_get_klines(symbol, ex, period="30", length=LOOKBACK_BARS)
                if df is not None and not df.empty:
                    df = df.tail(LOOKBACK_BARS).reset_index(drop=True)
                    if "hold" not in df.columns:
                        df["hold"] = 0.0  # （xtquant 有 openInterest 字段，此处保留兼容）
                    return df
            except Exception:
                pass  # xtquant 失败则回退 akshare

    # ---- 回退路径：akshare ----
    try:
        df = ak.futures_zh_minute_sina(symbol=symbol, period="30")
        if df is None or len(df) == 0:
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
            elif "持" in c or "hold" in cl:
                rename_map[c] = "hold"
        df.rename(columns=rename_map, inplace=True)

        for c in ["open", "high", "low", "close", "volume", "hold"]:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")

        df["datetime"] = pd.to_datetime(df["datetime"])
        df.sort_values("datetime", inplace=True)
        df.reset_index(drop=True, inplace=True)
        return df.tail(LOOKBACK_BARS)
    except Exception:
        return None


# ============================================================
# 第三步: 技术指标计算
# ============================================================

def calc_ema(series: pd.Series, period: int) -> pd.Series:
    """计算指数移动平均线"""
    return series.ewm(span=period, adjust=False).mean()


def calc_macd(close: pd.Series) -> pd.DataFrame:
    """计算MACD指标"""
    ema_fast = calc_ema(close, MACD_FAST)
    ema_slow = calc_ema(close, MACD_SLOW)
    dif = ema_fast - ema_slow
    dea = calc_ema(dif, MACD_SIGNAL)
    macd_hist = 2 * (dif - dea)
    return pd.DataFrame({"dif": dif, "dea": dea, "hist": macd_hist})


def calc_rsi(close: pd.Series, period: int = RSI_PERIOD) -> pd.Series:
    """计算RSI"""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1/period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi


def calc_bollinger(close: pd.Series, period: int = BB_PERIOD, std: float = BB_STD) -> pd.DataFrame:
    """计算布林带"""
    ma = close.rolling(period).mean()
    std_dev = close.rolling(period).std()
    upper = ma + std * std_dev
    lower = ma - std * std_dev
    # 带宽
    width = (upper - lower) / ma * 100
    # %B 位置
    pct_b = (close - lower) / (upper - lower)
    return pd.DataFrame({"bb_ma": ma, "bb_upper": upper, "bb_lower": lower,
                         "bb_width": width, "bb_pct_b": pct_b})


def calc_kdj(high: pd.Series, low: pd.Series, close: pd.Series,
             n: int = KDJ_N, m1: int = KDJ_M1, m2: int = KDJ_M2) -> pd.DataFrame:
    """计算KDJ指标"""
    lowest_low = low.rolling(n).min()
    highest_high = high.rolling(n).max()
    rsv = (close - lowest_low) / (highest_high - lowest_low) * 100
    k = rsv.ewm(alpha=1/m1, adjust=False).mean()
    d = k.ewm(alpha=1/m2, adjust=False).mean()
    j = 3 * k - 2 * d
    return pd.DataFrame({"k": k, "d": d, "j": j})


def calc_atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """计算ATR (平均真实波幅)"""
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = abs(high - prev_close)
    tr3 = abs(low - prev_close)
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()


# ============================================================
# 第四步: 信号检测
# ============================================================

def detect_signals(df: pd.DataFrame) -> List[Dict]:
    """
    对单个品种的K线数据运行完整量化分析，返回信号列表。

    检测逻辑:
      看多条件:
        M1: EMA12 上穿 EMA26 (金叉) — 最近3根K线内发生
        M2: MACD 金叉 (DIF 上穿 DEA) — 最近3根K线内发生
        M3: RSI < 35 且拐头向上 (超卖反弹)
        M4: 收盘价 < 布林下轨 且开始回升 (超跌反弹)
        M5: KDJ J值 < 0 且拐头向上 (短期超卖)
        M6: 放量上涨 (成交量 > 2倍均量 且收阳)

      看空条件:
        S1: EMA12 下穿 EMA26 (死叉) — 最近3根K线内发生
        S2: MACD 死叉 (DIF 下穿 DEA) — 最近3根K线内发生
        S3: RSI > 65 且拐头向下 (超买回落)
        S4: 收盘价 > 布林上轨 且开始回落 (超买回落)
        S5: KDJ J值 > 100 且拐头向下 (短期超买)
        S6: 放量下跌 (成交量 > 2倍均量 且收阴)

    Returns:
        list of signal dicts
    """
    if df is None or len(df) < max(EMA_SLOW, BB_PERIOD, KDJ_N) + 10:
        return []

    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df["volume"]
    dt = df["datetime"]

    # --- 计算所有指标 ---
    ema_f = calc_ema(close, EMA_FAST)
    ema_s = calc_ema(close, EMA_SLOW)
    macd = calc_macd(close)
    rsi = calc_rsi(close)
    bb = calc_bollinger(close)
    kdj = calc_kdj(high, low, close)
    vol_ma = volume.rolling(VOL_MA_PERIOD).mean()
    vol_ratio = volume / vol_ma.replace(0, np.nan)

    # --- 看多条件 ---
    long_conditions = {}

    # M1: EMA金叉 (最近3根)
    ema_cross_up = (ema_f.shift(1) <= ema_s.shift(1)) & (ema_f > ema_s)
    m1_recent = ema_cross_up.tail(3).any()
    long_conditions["EMA金叉(12/26)"] = m1_recent

    # M2: MACD金叉
    macd_cross_up = (macd["dif"].shift(1) <= macd["dea"].shift(1)) & (macd["dif"] > macd["dea"])
    m2_recent = macd_cross_up.tail(3).any()
    long_conditions["MACD金叉"] = m2_recent

    # M3: RSI 超卖反弹
    latest_rsi = rsi.iloc[-1]
    prev_rsi = rsi.iloc[-2]
    m3 = latest_rsi < 35 and latest_rsi > prev_rsi
    long_conditions["RSI超卖反弹"] = m3

    # M4: 布林下轨反弹
    latest_bb = bb.iloc[-1]
    prev_bb = bb.iloc[-2]
    m4 = (latest_bb["bb_pct_b"] < 0.05 and latest_bb["bb_pct_b"] > prev_bb["bb_pct_b"])
    long_conditions["布林下轨反弹"] = m4

    # M5: KDJ J值超卖拐头
    latest_j = kdj["j"].iloc[-1]
    prev_j = kdj["j"].iloc[-2]
    m5 = latest_j < 0 and latest_j > prev_j
    long_conditions["KDJ超卖拐头"] = m5

    # M6: 放量上涨
    m6 = (vol_ratio.iloc[-1] > VOL_SURGE_RATIO) and (close.iloc[-1] > close.iloc[-2])
    long_conditions["放量上涨"] = m6

    # --- 看空条件 ---
    short_conditions = {}

    # S1: EMA死叉
    ema_cross_down = (ema_f.shift(1) >= ema_s.shift(1)) & (ema_f < ema_s)
    s1_recent = ema_cross_down.tail(3).any()
    short_conditions["EMA死叉(12/26)"] = s1_recent

    # S2: MACD死叉
    macd_cross_down = (macd["dif"].shift(1) >= macd["dea"].shift(1)) & (macd["dif"] < macd["dea"])
    s2_recent = macd_cross_down.tail(3).any()
    short_conditions["MACD死叉"] = s2_recent

    # S3: RSI 超买回落
    s3 = latest_rsi > 65 and latest_rsi < prev_rsi
    short_conditions["RSI超买回落"] = s3

    # S4: 布林上轨回落
    s4 = (latest_bb["bb_pct_b"] > 0.95 and latest_bb["bb_pct_b"] < prev_bb["bb_pct_b"])
    short_conditions["布林上轨回落"] = s4

    # S5: KDJ J值超买拐头
    s5 = latest_j > 100 and latest_j < prev_j
    short_conditions["KDJ超买拐头"] = s5

    # S6: 放量下跌
    s6 = (vol_ratio.iloc[-1] > VOL_SURGE_RATIO) and (close.iloc[-1] < close.iloc[-2])
    short_conditions["放量下跌"] = s6

    # --- 汇总信号 ---
    signals = []

    long_count = sum(1 for v in long_conditions.values() if v)
    short_count = sum(1 for v in short_conditions.values() if v)

    long_details = [k for k, v in long_conditions.items() if v]
    short_details = [k for k, v in short_conditions.items() if v]

    latest_dt = dt.iloc[-1].strftime("%Y-%m-%d %H:%M") if hasattr(dt.iloc[-1], 'strftime') else str(dt.iloc[-1])

    if long_count >= SIGNAL_CONFIRM_COUNT:
        signals.append({
            "signal": "开多 📈",
            "signal_en": "LONG",
            "count": long_count,
            "details": " + ".join(long_details),
            "price": round(close.iloc[-1], 2),
            "ema_fast": round(ema_f.iloc[-1], 2),
            "ema_slow": round(ema_s.iloc[-1], 2),
            "rsi": round(rsi.iloc[-1], 1),
            "macd_dif": round(macd["dif"].iloc[-1], 4),
            "macd_dea": round(macd["dea"].iloc[-1], 4),
            "kdj_k": round(kdj["k"].iloc[-1], 1),
            "kdj_d": round(kdj["d"].iloc[-1], 1),
            "kdj_j": round(kdj["j"].iloc[-1], 1),
            "bb_pct_b": round(bb["bb_pct_b"].iloc[-1], 3),
            "vol_ratio": round(vol_ratio.iloc[-1], 2),
            "datetime": latest_dt,
        })

    if short_count >= SIGNAL_CONFIRM_COUNT:
        signals.append({
            "signal": "开空 📉",
            "signal_en": "SHORT",
            "count": short_count,
            "details": " + ".join(short_details),
            "price": round(close.iloc[-1], 2),
            "ema_fast": round(ema_f.iloc[-1], 2),
            "ema_slow": round(ema_s.iloc[-1], 2),
            "rsi": round(rsi.iloc[-1], 1),
            "macd_dif": round(macd["dif"].iloc[-1], 4),
            "macd_dea": round(macd["dea"].iloc[-1], 4),
            "kdj_k": round(kdj["k"].iloc[-1], 1),
            "kdj_d": round(kdj["d"].iloc[-1], 1),
            "kdj_j": round(kdj["j"].iloc[-1], 1),
            "bb_pct_b": round(bb["bb_pct_b"].iloc[-1], 3),
            "vol_ratio": round(vol_ratio.iloc[-1], 2),
            "datetime": latest_dt,
        })

    return signals


# ============================================================
# 输出
# ============================================================

def print_signal_header():
    """打印信号表头"""
    print()
    print("=" * 72)
    print("  量化交易信号 — 30分钟K线 多指标共振系统")
    print("=" * 72)
    print(f"  运行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  指标: EMA({EMA_FAST}/{EMA_SLOW}) | MACD({MACD_FAST},{MACD_SLOW},{MACD_SIGNAL})")
    print(f"        RSI({RSI_PERIOD}) | 布林带({BB_PERIOD},{BB_STD}) | KDJ({KDJ_N},{KDJ_M1},{KDJ_M2})")
    print(f"  信号规则: >= {SIGNAL_CONFIRM_COUNT} 个同向条件同时满足")


def print_results(all_signals: List[Dict], n_scanned: int):
    """格式化打印所有信号"""
    print_signal_header()
    print()
    print(f"  扫描品种: {n_scanned} 个 | 发现信号: {len(all_signals)} 个")
    print()

    if not all_signals:
        print("  >>> 当前未检测到任何开多/开空信号。")
        return

    long_sigs = [s for s in all_signals if s["signal_en"] == "LONG"]
    short_sigs = [s for s in all_signals if s["signal_en"] == "SHORT"]

    # ========== 简洁列表 ==========
    print(f"  {'─' * 68}")
    print(f"  📈 开多信号 ({len(long_sigs)} 个)")
    print(f"  {'─' * 68}")
    if long_sigs:
        print(f"  {'品种':<14}{'代码':<8}{'价格':<10}{'条件数':<8}{'RSI':<8}{'时间':<20}")
        print(f"  {'-' * 68}")
        for s in sorted(long_sigs, key=lambda x: -x["count"]):
            print(f"  {s.get('name',''):<14}{s.get('symbol',''):<8}"
                  f"{s['price']:<10}{s['count']:<8}{s['rsi']:<8}{s['datetime']:<20}")
        print()
        print(f"  触发条件详情:")
        for s in long_sigs:
            print(f"    {s.get('name',''):<14} [{s.get('symbol','')}] → {s['details']}")

    print()
    print(f"  {'─' * 68}")
    print(f"  📉 开空信号 ({len(short_sigs)} 个)")
    print(f"  {'─' * 68}")
    if short_sigs:
        print(f"  {'品种':<14}{'代码':<8}{'价格':<10}{'条件数':<8}{'RSI':<8}{'时间':<20}")
        print(f"  {'-' * 68}")
        for s in sorted(short_sigs, key=lambda x: -x["count"]):
            print(f"  {s.get('name',''):<14}{s.get('symbol',''):<8}"
                  f"{s['price']:<10}{s['count']:<8}{s['rsi']:<8}{s['datetime']:<20}")
        print()
        print(f"  触发条件详情:")
        for s in short_sigs:
            print(f"    {s.get('name',''):<14} [{s.get('symbol','')}] → {s['details']}")

    # ========== 详细表格 ==========
    print()
    print(f"  {'=' * 68}")
    print(f"  📊 信号详细数据")
    print(f"  {'=' * 68}")
    print(f"  {'品种':<14}{'信号':<10}{'价格':<10}{'RSI':<7}{'KDJ-K':<8}"
          f"{'KDJ-D':<8}{'KDJ-J':<8}{'BB%b':<7}{'量比':<7}{'时间':<20}")
    print(f"  {'-' * 68}")
    for s in all_signals:
        print(f"  {s.get('name',''):<14}{s['signal']:<10}{s['price']:<10}"
              f"{s['rsi']:<7}{s['kdj_k']:<8}{s['kdj_d']:<8}{s['kdj_j']:<8}"
              f"{s['bb_pct_b']:<7}{s['vol_ratio']:<7}{s['datetime']:<20}")


def export_csv(all_signals: List[Dict], path: str):
    """导出CSV"""
    cols_cn = ["品种名称", "品种代码", "信号", "价格", "条件数", "触发条件",
               "RSI", "KDJ_K", "KDJ_D", "KDJ_J", "BB%b", "量比", "时间"]
    cols_en = ["name", "symbol", "signal", "price", "count", "details",
               "rsi", "kdj_k", "kdj_d", "kdj_j", "bb_pct_b", "vol_ratio", "datetime"]

    if not all_signals:
        pd.DataFrame(columns=cols_cn).to_csv(path, index=False, encoding="utf-8-sig")
    else:
        pd.DataFrame(all_signals)[cols_en].rename(
            columns=dict(zip(cols_en, cols_cn))
        ).to_csv(path, index=False, encoding="utf-8-sig")
    print(f"\n  结果已导出: {path}")


# ============================================================
# 主入口
# ============================================================

def main():
    t0 = datetime.now()
    print()
    print("=" * 72)
    print("  期货30分钟量化交易信号系统")
    print(f"  启动时间: {t0.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 72)

    # 1. 加载关注品种
    futures_df = load_tracked_futures(DB_PATH)
    if futures_df.empty:
        print("  [错误] 未找到品种数据，退出。")
        return

    # 2. 构建symbol映射
    print()
    print("=" * 72)
    print("  第二步: 30分钟K线信号扫描")
    print("=" * 72)
    print(f"  K线周期: 30分钟 | 回溯: {LOOKBACK_BARS} 根 | 信号要求: >= {SIGNAL_CONFIRM_COUNT} 条件共振")

    # 获取symbol映射 (用于查询K线)
    try:
        display = ak.futures_display_main_sina()
        sym_map = {}
        for _, row in display.iterrows():
            sym = str(row["symbol"]).strip()
            name = str(row["name"]).strip()
            if sym.endswith("0") and len(sym) >= 2:
                sym_map[sym[:-1].upper()] = sym
            sym_map[sym.upper()] = sym
        # 扩展: 直接按symbol匹配
        for _, row in futures_df.iterrows():
            s = row["symbol"]
            if s not in sym_map:
                sym_map[s.upper()] = s
    except Exception as e:
        print(f"  [警告] symbol映射失败: {e}")
        sym_map = {row["symbol"].upper(): row["symbol"] for _, row in futures_df.iterrows()}

    print(f"  品种->symbol 映射: {len(sym_map)} 条")

    # 3. 逐品种扫描
    print()
    all_signals = []
    ok = fail = 0
    n = len(futures_df)

    for i, row in futures_df.iterrows():
        pc = row["symbol"]
        pn = row["name"]
        sym = sym_map.get(pc.upper(), pc)

        print(f"  [{i+1}/{n}] {pc:<10} {pn:<16}", end=" ", flush=True)
        try:
            kline = get_30min_kline(sym)
            if kline is None or len(kline) < 30:
                print("-> K线数据不足")
                fail += 1
                continue

            sigs = detect_signals(kline)
            if sigs:
                for s in sigs:
                    s["name"] = pn
                    s["symbol"] = pc
                all_signals.extend(sigs)
                print(f"-> {len(sigs)} 个信号!")
            else:
                print("-> 无信号")
            ok += 1
        except Exception as e:
            print(f"-> 错误: {e}")
            fail += 1
        time.sleep(REQUEST_DELAY)

    print(f"\n  扫描完成: 成功={ok}, 失败={fail}")

    # 4. 输出结果
    print_results(all_signals, n)

    # 5. 导出CSV
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    export_csv(all_signals, f"futures_30min_signals_{ts}.csv")

    # 6. 摘要
    elapsed = (datetime.now() - t0).total_seconds()
    print(f"\n{'=' * 72}")
    print(f"  执行摘要")
    print(f"{'=' * 72}")
    print(f"  检查品种: {n} | 发现信号: {len(all_signals)} | 耗时: {elapsed:.1f}s")
    if all_signals:
        long_count = sum(1 for s in all_signals if s["signal_en"] == "LONG")
        short_count = sum(1 for s in all_signals if s["signal_en"] == "SHORT")
        uniq = set(s["symbol"] for s in all_signals)
        print(f"  开多信号: {long_count} | 开空信号: {short_count}")
        print(f"  涉及品种: {', '.join(sorted(uniq))}")
    print(f"{'=' * 72}")


if __name__ == "__main__":
    main()
