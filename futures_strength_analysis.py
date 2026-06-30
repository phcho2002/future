"""
期货强弱分析 - 统一引擎（合并自 futures_hourly_analysis.py 与 top40_daily_strength.py）
=========================================================================
通过 `period` 参数适配任意 K 线周期，执行同一套"K 线五维强弱评分"。

五维评分体系（每维 0-20，总分 100）：
  1. 关键位突破质量
  2. 回调软弱程度
  3. 异常 K 线多空含义
  4. 多周期 K 线共振（均线排列 + 斜率近似高周期，不再依赖 datetime 合成）
  5. 日内动能 / 开盘区分界线

数据源：统一走 future_data.get_klines（tqsdk 后端 + TTL 缓存），akshare 仅作回退。
合约清单：复用数据库 futures_top40 表（已含 exchange），无需本地维护合约字典。

用法：
  python futures_strength_analysis.py                 # 默认日线 (period=1440)
  python futures_strength_analysis.py --period 60     # 小时线
  python futures_strength_analysis.py --period 30     # 30 分钟
  python futures_strength_analysis.py --period 5      # 5 分钟
  python futures_strength_analysis.py --period 1440   # 日线
"""
import os
import sys
import time
import argparse
import sqlite3
import warnings
from datetime import datetime

import pandas as pd
import numpy as np

warnings.filterwarnings('ignore')

# ---- 接入全系统统一行情入口 future_data（tqsdk 后端 + TTL 缓存）----
sys.path.insert(0, r"d:\work_ai")
try:
    from future_data import get_klines as _tq_get_klines
    _HAS_FUTURE_DATA = True
except Exception:  # noqa: BLE001
    _HAS_FUTURE_DATA = False

import akshare as ak

DB_PATH = r"d:\work_ai\futures_data.db"


# ============================================================
# 1. 周期配置：窗口随基线周期自适应缩放
# ============================================================

# 每个 period(min) 对应的默认拉取长度与基线 bar 窗口参数。
# 规则：分钟级给足深历史；日线给 ~半年。
PERIOD_CONFIG = {
    "1":    {"label": "1分钟",  "length": 2000, "short_w": 20, "long_w": 60},
    "5":    {"label": "5分钟",  "length": 2000, "short_w": 20, "long_w": 60},
    "15":   {"label": "15分钟", "length": 2000, "short_w": 20, "long_w": 60},
    "30":   {"label": "30分钟", "length": 2000, "short_w": 20, "long_w": 60},
    "60":   {"label": "小时线",  "length": 1500, "short_w": 20, "long_w": 60},
    "1440": {"label": "日线",    "length": 200,  "short_w": 20, "long_w": 60},
}


def _period_cfg(period):
    """取周期配置，未知 period 走默认。"""
    return PERIOD_CONFIG.get(str(period), {"label": f"{period}分钟",
                                           "length": 1500, "short_w": 20, "long_w": 60})


def _period_prefix(period):
    """period -> 数据库表名前缀。1440 用 daily，其余按分钟约定（60 用 hourly）。"""
    p = str(period)
    if p == "1440":
        return "daily"
    if p == "60":
        return "hourly"
    return f"p{p}"


# ============================================================
# 2. 数据获取层（统一入口 + akshare 回退）
# ============================================================

def _normalize(df):
    """规范化 K 线列名 -> 小写 datetime/open/high/low/close/volume。"""
    if df is None or (hasattr(df, "empty") and df.empty):
        return None
    rename = {c: str(c).strip().lower() for c in df.columns}
    df = df.rename(columns=rename)
    # 兼容 akshare 中文列名
    cn_map = {"日期": "date", "开盘": "open", "最高": "high", "最低": "low",
              "收盘": "close", "成交量": "volume", "持仓量": "hold", "动态结算价": "settle"}
    df = df.rename(columns=cn_map)
    if "datetime" not in df.columns and "date" in df.columns:
        df["datetime"] = pd.to_datetime(df["date"])
    need = ["open", "high", "low", "close", "volume"]
    for col in need:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    if "close" not in df.columns and "settle" in df.columns:
        df["close"] = df["settle"]
    df = df.dropna(subset=["open", "high", "low", "close"]).reset_index(drop=True)
    if "datetime" in df.columns:
        df = df.sort_values("datetime").reset_index(drop=True)
    return df


def fetch_klines(symbol, exchange, period, length):
    """
    获取某合约 K 线，返回规范化 DataFrame 或 None。
    主路径：future_data.get_klines（tqsdk + TTL 缓存）。
    回退：akshare（日线 futures_main_sina / 分钟 futures_zh_minute_sina）。
    """
    df = None
    if _HAS_FUTURE_DATA and exchange:
        try:
            df = _tq_get_klines(symbol, exchange, period=str(period), length=int(length))
        except Exception:
            df = None
    df = _normalize(df)

    if df is None or df.empty:
        # akshare 回退
        try:
            if str(period) == "1440":
                end = datetime.now().strftime("%Y%m%d")
                start = (datetime.now() - pd.Timedelta(days=400)).strftime("%Y%m%d")
                raw = ak.futures_main_sina(symbol=symbol, start_date=start, end_date=end)
            else:
                raw = ak.futures_zh_minute_sina(symbol=symbol, period=str(period))
            df = _normalize(raw)
            time.sleep(0.3)
        except Exception:
            df = None
    return df


# ============================================================
# 3. 合约清单层（复用数据库 futures_top40）
# ============================================================

def get_universe(top=40, db_path=DB_PATH):
    """从 futures_top40 读取品种清单（已含 exchange）。"""
    conn = sqlite3.connect(db_path)
    try:
        sql = ("SELECT symbol, name, exchange, 最新价格 AS price, 最新持仓量 AS oi "
               f"FROM futures_top40 ORDER BY 排名 LIMIT {int(top)}")
        df = pd.read_sql(sql, conn)
    finally:
        conn.close()
    if "exchange" not in df.columns:
        df["exchange"] = None
    return df


# ============================================================
# 4. 五维评分层（周期无关，窗口自适应）
# ============================================================

def score_breakthrough(df, short_w=20):
    """一、关键位突破质量 (0-20)：收盘是否放量突破/跌破近期区间。"""
    if len(df) < short_w + 5:
        return 10, "数据不足"
    last = df.iloc[-1]
    recent = df.tail(short_w)
    high_w = recent["high"].max()
    low_w = recent["low"].min()
    avg_vol = recent["volume"].mean()

    body = abs(last["close"] - last["open"])
    kr = last["high"] - last["low"]
    upper_shadow = (last["high"] - max(last["close"], last["open"])) / kr if kr > 0 else 0
    lower_shadow = (min(last["close"], last["open"]) - last["low"]) / kr if kr > 0 else 0
    is_bull = last["close"] > last["open"]
    is_bear = last["close"] < last["open"]
    vol_ratio = last["volume"] / avg_vol if avg_vol > 0 else 1

    breakout = last["close"] >= high_w * 0.995
    breakdown = last["close"] <= low_w * 1.005

    if breakout:
        if is_bull and upper_shadow < 0.25 and vol_ratio > 1.2:
            return 18, f"强势突破：放量阳线站稳近期高点，上影极短(实体占比{body/kr*100:.0f}%)" if kr > 0 else "强势突破：放量阳线站稳近期高点"
        if upper_shadow > 0.45:
            return 8, f"假突破嫌疑：触及高点但长上影({upper_shadow*100:.0f}%)，抛压明显"
        if is_bull:
            return 15, "突破近期高点，阳线收盘但量能/影线一般"
        return 11, "接近近期高点但收阴，突破质量一般"

    if breakdown:
        if is_bear and lower_shadow < 0.25 and vol_ratio > 1.2:
            return 4, "弱势破位：放量阴线跌破近期低点"
        if lower_shadow > 0.45:
            return 9, "假跌破/探底：跌破低点但长下影，有承接"
        return 6, "弱势：收盘接近近期低点"

    rng = high_w - low_w
    dist_to_high = (high_w - last["close"]) / rng if rng > 0 else 0
    dist_to_low = (last["close"] - low_w) / rng if rng > 0 else 0
    if dist_to_high < 0.1 and upper_shadow > 0.4:
        return 7, "临近高点但长上影受阻，多头犹豫"
    if dist_to_low < 0.1 and lower_shadow > 0.4:
        return 14, "临近低点获支撑，下影线明显"
    return 10, "价格在近期区间内震荡，无明确突破信号"


def score_pullback(df, short_w=20):
    """二、回调软弱程度 (0-20)：回调浅、缩量、低点抬高、不破均线则强。"""
    if len(df) < short_w + 5:
        return 10, "数据不足"
    last = df.iloc[-1]
    recent = df.tail(short_w)
    high_w = recent["high"].max()
    low_w = recent["low"].min()
    rng = high_w - low_w
    if rng == 0:
        return 10, "波动极小"
    pos = (last["close"] - low_w) / rng

    closes = df["close"]
    ma5 = closes.rolling(5).mean()
    ma10 = closes.rolling(10).mean()
    last5 = df.tail(5)
    bull_count = (last5["close"] > last5["open"]).sum()
    bear_count = (last5["close"] < last5["open"]).sum()

    last3_vol = df.tail(3)["volume"].mean()
    avg_vol_10 = df.tail(10)["volume"].mean()
    vol_ratio = last3_vol / avg_vol_10 if avg_vol_10 > 0 else 1

    lows_last5 = df.tail(5)["low"].values
    lows_prev5 = df.iloc[-10:-5]["low"].values
    higher_lows = bool(np.all(lows_last5 >= lows_prev5.min())) if len(lows_prev5) > 0 else False

    above_ma10 = last["close"] >= ma10.iloc[-1] * 0.995

    if pos > 0.7 and bull_count >= 3 and higher_lows and above_ma10:
        if vol_ratio < 1.0:
            return 18, "回调极强：缩量小阴/十字星，低点抬高，稳守 10 周期线上方"
        return 17, "回调偏强：低点抬高，守在 10 周期线上方"
    if pos > 0.5 and bull_count >= 3 and above_ma10:
        return 15, "回调健康：依托 10 周期线运行"
    if pos < 0.3 and bear_count >= 3:
        return 5, "回调极弱：连阴下跌，空头主导"
    if pos < 0.5 and bear_count >= 3 and last["close"] < ma10.iloc[-1]:
        return 7, "回调偏弱：跌破 10 周期线，反弹无力"
    return 10, "回调中性：方向不鲜明"


def score_abnormal_kline(df):
    """三、异常 K 线多空含义 (0-20)：长下影收复偏强 / 大阳被吞没偏弱。"""
    if len(df) < 6:
        return 10, "数据不足"
    last = df.iloc[-1]
    last3 = df.tail(3)
    last5 = df.tail(5)

    # 假弱势转强：近期长下影后收复/创新高
    for _, k in last3.iterrows():
        kr = k["high"] - k["low"]
        if kr == 0:
            continue
        lower_shadow = (min(k["close"], k["open"]) - k["low"]) / kr
        body = abs(k["close"] - k["open"]) / kr
        if lower_shadow > 0.5 and body > 0.2 and last["close"] >= k["high"] * 0.995:
            return 18, "假弱势转强：长下影洗盘后快速收复并站上前高"

    # 假强势转弱：近期放量大阳后被连续小阴吞没
    if len(last5) >= 4:
        big_idx = None
        for i in range(len(last5) - 1):
            k = last5.iloc[i]
            kr = k["high"] - k["low"]
            if kr == 0:
                continue
            body = abs(k["close"] - k["open"]) / kr
            if k["close"] > k["open"] and body > 0.6:
                big_idx = i
                break
        if big_idx is not None and big_idx + 2 < len(last5):
            after = last5.iloc[big_idx + 1:]
            mid = (last5.iloc[big_idx]["close"] + last5.iloc[big_idx]["open"]) / 2
            bear_after = (after["close"] < after["open"]).sum()
            if bear_after >= len(after) * 0.6 and last["close"] < mid:
                return 5, "假强势转弱：大阳后连续小阴回吐，多头一日游"

    # 看涨/看跌吞没
    if len(df) >= 2:
        p1, p2 = df.iloc[-2], df.iloc[-1]
        if p1["close"] < p1["open"] and p2["close"] > p2["open"]:
            if p2["open"] < p1["close"] and p2["close"] > p1["open"]:
                return 17, "看涨吞没：多头反包前 K，强势信号"
        if p1["close"] > p1["open"] and p2["close"] < p2["open"]:
            if p2["open"] > p1["close"] and p2["close"] < p1["open"]:
                return 6, "看跌吞没：空头反包前 K，弱势信号"
    return 10, "近期无明确异常 K 线信号"


def score_resonance(df, long_w=60):
    """四、多周期 K 线共振 (0-20)：均线排列 + 均线斜率近似高周期趋势。
    不再依赖 datetime 合成高周期，对分钟/小时/日线通用。"""
    if len(df) < long_w:
        return 10, "数据不足，难以判断多周期共振"
    closes = df["close"]
    ma5 = closes.rolling(5).mean()
    ma10 = closes.rolling(10).mean()
    ma20 = closes.rolling(20).mean()
    ma60 = closes.rolling(long_w).mean()

    last = df.iloc[-1]
    ma_bull = last["close"] > ma5.iloc[-1] and ma5.iloc[-1] > ma10.iloc[-1] > ma20.iloc[-1] > ma60.iloc[-1]
    ma_bear = last["close"] < ma5.iloc[-1] and ma5.iloc[-1] < ma10.iloc[-1] < ma20.iloc[-1] < ma60.iloc[-1]
    above_all = last["close"] > ma5.iloc[-1] > ma10.iloc[-1] > ma20.iloc[-1] > ma60.iloc[-1]
    below_all = last["close"] < ma5.iloc[-1] < ma10.iloc[-1] < ma20.iloc[-1] < ma60.iloc[-1]

    def _slope(series, back):
        prev = series.iloc[-1 - back]
        return (series.iloc[-1] - prev) / prev * 100 if prev > 0 else 0

    ma5_slope = _slope(ma5, 5)
    ma20_slope = _slope(ma20, 20)

    if ma_bull and ma5_slope > 0 and ma20_slope > 0:
        return 19, "多周期共振强势：均线多头排列，短/中期斜率向上"
    if ma_bear and ma5_slope < 0 and ma20_slope < 0:
        return 4, "多周期共振弱势：均线空头排列，短/中期斜率向下"
    if above_all and ma5_slope > 0:
        return 16, "均线多头排列，趋势偏强"
    if below_all and ma5_slope < 0:
        return 6, "均线空头排列，趋势偏弱"
    if last["close"] > ma20.iloc[-1]:
        return 12, "价格在 20 周期线上方，中期偏强"
    return 8, "价格在 20 周期线下方，中期偏弱"


def score_intraday_momentum(df):
    """五、日内动能 (0-20)：收盘在区间位置 + 连续实体方向 + 量能配合。"""
    if len(df) < 10:
        return 10, "数据不足"
    last = df.iloc[-1]
    last5 = df.tail(5)
    kr = last["high"] - last["low"]
    pos = (last["close"] - last["low"]) / kr if kr > 0 else 0.5
    bull_count = (last5["close"] > last5["open"]).sum()
    avg_vol_10 = df.tail(10)["volume"].mean()
    vol_ratio = last["volume"] / avg_vol_10 if avg_vol_10 > 0 else 1

    if pos > 0.7 and bull_count >= 4 and vol_ratio > 1.0:
        return 18, f"动能极强：连续收阳且收盘高位，量能放大({vol_ratio:.2f}倍)"
    if pos > 0.6 and bull_count >= 3:
        return 15, "动能偏强：多数 K 收阳且收盘位于中高位"
    if pos < 0.3 and bull_count <= 1:
        return 5, "动能极弱：连续收阴且收盘位于低位"
    if pos < 0.4 and bull_count <= 2:
        return 8, "动能偏弱：多数 K 收阴"
    return 10, "动能中性：收盘位置居中，多空均衡"


def _verdict(total):
    if total >= 80:
        return "★★★★★ 极强"
    if total >= 68:
        return "★★★★ 强势"
    if total >= 56:
        return "★★★ 中性偏强"
    if total >= 44:
        return "★★ 中性偏弱"
    if total >= 32:
        return "★ 弱势"
    return "☆ 极弱"


def analyze_symbol(symbol, name, exchange, period, short_w=20, long_w=60):
    """对单个品种执行五维评分，返回结果 dict 或 None。"""
    cfg = _period_cfg(period)
    df = fetch_klines(symbol, exchange, period, cfg["length"])
    if df is None or len(df) < 30:
        return None

    s1, d1 = score_breakthrough(df, short_w)
    s2, d2 = score_pullback(df, short_w)
    s3, d3 = score_abnormal_kline(df)
    s4, d4 = score_resonance(df, long_w)
    s5, d5 = score_intraday_momentum(df)

    total = s1 + s2 + s3 + s4 + s5
    latest = df.iloc[-1]
    recent = df.tail(short_w)
    return {
        "symbol": symbol,
        "name": name,
        "最新日期": pd.Timestamp(latest["datetime"]).strftime("%Y-%m-%d %H:%M") if pd.notna(latest["datetime"]) else "",
        "最新价": round(float(latest["close"]), 2),
        "20周期最高": round(float(recent["high"].max()), 2),
        "20周期最低": round(float(recent["low"].min()), 2),
        "突破质量": s1, "回调强弱": s2, "异常K线": s3,
        "多周期共振": s4, "日内动能": s5,
        "总分": total, "综合判定": _verdict(total),
        "突破说明": d1, "回调说明": d2, "异常K说明": d3,
        "共振说明": d4, "日内说明": d5,
    }


# ============================================================
# 5. 操作建议（与周期无关，沿用日线脚本逻辑）
# ============================================================

def suggest_action(row):
    total = row["总分"]
    close = row["最新价"]
    low = row["20周期最低"]
    high = row["20周期最高"]

    if total >= 56:
        stop_loss = round(low * 0.985, 2)
        if "突破" in row["突破说明"] and "强势" in row["突破说明"]:
            entry = "突破追涨/回踩突破位做多"
            tip = "若次根高开高走可轻仓追；若回踩前高或 5 周期线不破加仓。"
        elif total >= 68:
            entry = "回踩低吸"
            tip = "等待缩量回调至 5/10 周期线附近低吸，不追高高开。"
        else:
            entry = "回踩轻仓试多"
            tip = "评分偏强但未进入极强区，小仓试多，等待放量突破确认。"
        return (f"方向：偏多（{entry}）\n"
                f"逻辑：{row['共振说明']}；{row['回调说明']}；{row['异常K说明']}。\n"
                f"入场：{entry}。\n"
                f"止损：收盘跌破 {stop_loss}（约区间低点下方 1.5%）。\n"
                f"目标：先看 {round(high*1.03,2)}，突破再看 {round(high*1.06,2)}。\n"
                f"仓位：轻仓试多，确认放量突破后加仓。\n"
                f"提示：{tip}")
    if total <= 45:
        stop_loss = round(high * 1.015, 2)
        if "破位" in row["突破说明"] or "弱势" in row["突破说明"]:
            entry = "反弹遇阻试空/观望"
            tip = "若缩量反弹至 5/10 周期线受阻，可轻仓试空；新手建议观望。"
        elif total <= 32:
            entry = "顺势轻仓试空"
            tip = "极弱结构，反弹即是减仓/试空机会。"
        else:
            entry = "观望为主，不抄底"
            tip = "趋势偏弱，等待企稳信号（放量长阳/长下影/底背离）出现。"
        return (f"方向：偏空/观望（{entry}）\n"
                f"逻辑：{row['共振说明']}；{row['回调说明']}；{row['突破说明']}。\n"
                f"入场：{entry}。\n"
                f"止损(空)：收盘站上 {stop_loss}（约区间高点上方 1.5%）。\n"
                f"目标：下方先看 {round(low*0.97,2)}，再看 {round(low*0.94,2)}。\n"
                f"仓位：严格轻仓，防超跌反弹。\n"
                f"提示：{tip}")
    return ("方向：震荡观望\n"
            "逻辑：评分中性，方向不明，等待关键位突破后再操作。")


# ============================================================
# 6. 存储层（表名按周期隔离）
# ============================================================

def _table_names(prefix):
    return {
        "all":   f"{prefix}_analysis_all",
        "top3":  f"{prefix}_strong_top3",
        "bot3":  f"{prefix}_weak_bottom3",
        "track": f"{prefix}_top3_tracking",
    }


def save_results(results_df, prefix, db_path=DB_PATH, run_key=None, period_label=""):
    """写库：全量表 + 强弱前3 + 快照追踪表 + 运行日志。"""
    tn = _table_names(prefix)
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        # 全量表（replace，仅保留本次结果）
        results_df.to_sql(tn["all"], conn, if_exists="replace", index=False)
        strong3 = results_df.head(3).copy()
        strong3["强弱势排名"] = [1, 2, 3]
        weak3 = results_df.tail(3).copy()
        weak3["强弱势排名"] = [len(results_df) - 2, len(results_df) - 1, len(results_df)]
        strong3.to_sql(tn["top3"], conn, if_exists="replace", index=False)
        weak3.to_sql(tn["bot3"], conn, if_exists="replace", index=False)

        # 快照追踪表（按 run_key 主键 upsert）
        run_key = run_key or datetime.now().strftime("%Y%m%d_%H%M%S")
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS {tn['track']} (
                run_key TEXT PRIMARY KEY, run_time TEXT, period_label TEXT,
                s1_symbol TEXT, s1_name TEXT, s1_score INTEGER, s1_price REAL,
                s2_symbol TEXT, s2_name TEXT, s2_score INTEGER, s2_price REAL,
                s3_symbol TEXT, s3_name TEXT, s3_score INTEGER, s3_price REAL,
                w1_symbol TEXT, w1_name TEXT, w1_score INTEGER, w1_price REAL,
                w2_symbol TEXT, w2_name TEXT, w2_score INTEGER, w2_price REAL,
                w3_symbol TEXT, w3_name TEXT, w3_score INTEGER, w3_price REAL,
                max_score INTEGER, max_name TEXT, min_score INTEGER, min_name TEXT, avg_score REAL
            )
        """)
        s = strong3.reset_index(drop=True)
        w = weak3.reset_index(drop=True)
        if len(s) >= 3 and len(w) >= 3:
            cur.execute(f"""
                INSERT OR REPLACE INTO {tn['track']}
                (run_key, run_time, period_label,
                 s1_symbol,s1_name,s1_score,s1_price, s2_symbol,s2_name,s2_score,s2_price, s3_symbol,s3_name,s3_score,s3_price,
                 w1_symbol,w1_name,w1_score,w1_price, w2_symbol,w2_name,w2_score,w2_price, w3_symbol,w3_name,w3_score,w3_price,
                 max_score,max_name,min_score,min_name,avg_score)
                VALUES (?,?,?,
                        ?,?,?,?, ?,?,?,?, ?,?,?,?,
                        ?,?,?,?, ?,?,?,?, ?,?,?,?,
                        ?,?,?,?,?)
            """, (
                run_key, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), period_label,
                s.at[0, "symbol"], s.at[0, "name"], int(s.at[0, "总分"]), float(s.at[0, "最新价"]),
                s.at[1, "symbol"], s.at[1, "name"], int(s.at[1, "总分"]), float(s.at[1, "最新价"]),
                s.at[2, "symbol"], s.at[2, "name"], int(s.at[2, "总分"]), float(s.at[2, "最新价"]),
                w.at[0, "symbol"], w.at[0, "name"], int(w.at[0, "总分"]), float(w.at[0, "最新价"]),
                w.at[1, "symbol"], w.at[1, "name"], int(w.at[1, "总分"]), float(w.at[1, "最新价"]),
                w.at[2, "symbol"], w.at[2, "name"], int(w.at[2, "总分"]), float(w.at[2, "最新价"]),
                int(results_df["总分"].max()), str(results_df.loc[results_df["总分"].idxmax(), "name"]),
                int(results_df["总分"].min()), str(results_df.loc[results_df["总分"].idxmin(), "name"]),
                round(float(results_df["总分"].mean()), 1),
            ))

        # 统一运行日志
        cur.execute("""CREATE TABLE IF NOT EXISTS run_log
                       (run_key TEXT PRIMARY KEY, prefix TEXT, period TEXT, run_at TEXT, count INTEGER)""")
        cur.execute("INSERT OR REPLACE INTO run_log VALUES (?,?,?,?,?)",
                    (run_key, prefix, period_label,
                     datetime.now().strftime("%Y-%m-%d %H:%M:%S"), len(results_df)))
        conn.commit()
    finally:
        conn.close()


# ============================================================
# 7. 报告层
# ============================================================

def _print_block(title):
    print("=" * 100)
    print(f"  {title}")
    print("=" * 100)


def render_report(results_df, strong3, weak3, period_label, errors, db_path, report_path=None):
    """终端打印摘要 + 写 UTF-8 报告文件。"""
    lines = []
    lines.append("=" * 90)
    lines.append(f"  期货 Top40 强弱分析报告 ({period_label})")
    lines.append("  " + datetime.now().strftime("%Y-%m-%d %H:%M"))
    lines.append("=" * 90)

    def block_row(row, idx):
        out = [""]
        out.append(f"【{idx}】{row['symbol']} {row['name']}  最新价:{row['最新价']}  "
                   f"总分:{row['总分']}  {row['综合判定']}")
        out.append(f"  突破:{row['突破说明']}")
        out.append(f"  回调:{row['回调说明']}")
        out.append(f"  异常K:{row['异常K说明']}")
        out.append(f"  共振:{row['共振说明']}")
        out.append(f"  动能:{row['日内说明']}")
        out.append("")
        out.append("  --- 操作建议 ---")
        out.append("  " + suggest_action(row).replace("\n", "\n  "))
        return out

    lines.append("")
    lines.append("=" * 90)
    lines.append(f"  {period_label} 强势 TOP 3")
    lines.append("=" * 90)
    for _, r in strong3.iterrows():
        lines += block_row(r, int(r["强弱势排名"]))

    lines.append("")
    lines.append("=" * 90)
    lines.append(f"  {period_label} 弱势 BOTTOM 3")
    lines.append("=" * 90)
    for _, r in weak3.iterrows():
        lines += block_row(r, int(r["强弱势排名"]))

    # 整体统计
    t = results_df["总分"]
    lines.append("")
    lines.append("=" * 90)
    lines.append("  整体统计")
    lines.append("=" * 90)
    lines.append(f"  极强(80+):       {(t >= 80).sum()} 个")
    lines.append(f"  强势(68-79):     {((t >= 68) & (t < 80)).sum()} 个")
    lines.append(f"  中性偏强(56-67): {((t >= 56) & (t < 68)).sum()} 个")
    lines.append(f"  中性偏弱(44-55): {((t >= 44) & (t < 56)).sum()} 个")
    lines.append(f"  弱势(32-43):     {((t >= 32) & (t < 44)).sum()} 个")
    lines.append(f"  极弱(<32):       {(t < 32).sum()} 个")
    lines.append(f"  最高分: {t.max()} ({results_df.loc[t.idxmax(),'name']})")
    lines.append(f"  最低分: {t.min()} ({results_df.loc[t.idxmin(),'name']})")
    if errors:
        lines.append("")
        lines.append(f"  分析失败 {len(errors)} 个:")
        for sym, name, err in errors:
            lines.append(f"    {sym} {name}: {err}")
    lines.append("")
    lines.append("=" * 90)
    lines.append("  免责声明：以上分析仅基于历史 K 线技术规则，不构成投资建议。")
    lines.append("  期货交易杠杆高、风险大，请独立判断并严格控制仓位与止损。")
    lines.append("=" * 90)

    # 写文件
    if report_path is None:
        safe = period_label.replace("/", "_")
        report_path = (r"d:\work_ai\strength_report_"
                       + safe + "_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".txt")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    # 终端：ASCII 摘要表
    _print_block(f"  {period_label} 强势 TOP 3")
    hdr = f"{'排名':>4} {'合约':>6} {'名称':<12} {'突破':>4} {'回调':>4} {'异常':>4} {'共振':>4} {'动能':>4} {'总分':>4}  {'判定'}"
    print(hdr)
    print("-" * 100)
    for _, r in strong3.iterrows():
        print(f"{int(r['强弱势排名']):>4} {r['symbol']:>6} {str(r['name']):<12} "
              f"{r['突破质量']:>4} {r['回调强弱']:>4} {r['异常K线']:>4} "
              f"{r['多周期共振']:>4} {r['日内动能']:>4} {r['总分']:>4}  {r['综合判定']}")
    _print_block(f"  {period_label} 弱势 BOTTOM 3")
    print(hdr)
    print("-" * 100)
    for _, r in weak3.iterrows():
        print(f"{int(r['强弱势排名']):>4} {r['symbol']:>6} {str(r['name']):<12} "
              f"{r['突破质量']:>4} {r['回调强弱']:>4} {r['异常K线']:>4} "
              f"{r['多周期共振']:>4} {r['日内动能']:>4} {r['总分']:>4}  {r['综合判定']}")

    _print_block(f"  {period_label} 完整排名 (按总分)")
    print(hdr)
    print("-" * 100)
    for i, (_, r) in enumerate(results_df.iterrows(), 1):
        print(f"{i:>4} {r['symbol']:>6} {str(r['name']):<12} "
              f"{r['突破质量']:>4} {r['回调强弱']:>4} {r['异常K线']:>4} "
              f"{r['多周期共振']:>4} {r['日内动能']:>4} {r['总分']:>4}  {r['综合判定']}")

    if errors:
        print(f"\n警告: {len(errors)} 个合约分析失败:")
        for sym, name, err in errors:
            print(f"  {sym} ({name}): {err}")

    print(f"\n报告文件: {report_path}")
    print(f"数据库:   {db_path}")


# ============================================================
# 8. 主流程
# ============================================================

def run(period="1440", top=40, length=None, db_path=DB_PATH, save=True, render=True):
    """统一入口：按 period 执行五维强弱分析。"""
    period = str(period)
    cfg = _period_cfg(period)
    if length is None:
        length = cfg["length"]
    prefix = _period_prefix(period)
    period_label = cfg["label"]
    short_w, long_w = cfg["short_w"], cfg["long_w"]

    _print_block(f"  期货强弱分析 - {period_label} (period={period})")
    print(f"  数据源: {'future_data(tqsdk+缓存)' if _HAS_FUTURE_DATA else 'akshare'} | "
          f"length={length} | top={top}")

    universe = get_universe(top=top, db_path=db_path)
    print(f"\n从 futures_top40 读取 {len(universe)} 个品种")

    results, errors = [], []
    print(f"\n[1/2] 获取 {period_label} 数据并评分...")
    for i, (_, r) in enumerate(universe.iterrows()):
        sym, name, ex = r["symbol"], r["name"], r.get("exchange")
        print(f"  [{i+1}/{len(universe)}] {sym} {name} ...", end=" ", flush=True)
        try:
            res = analyze_symbol(sym, name, ex, period, short_w=short_w, long_w=long_w)
            if res:
                results.append(res)
                print(f"总分={res['总分']} {res['综合判定']}")
            else:
                errors.append((sym, name, "数据不足"))
                print("数据不足")
        except Exception as e:
            errors.append((sym, name, str(e)))
            print(f"错误: {e}")
        time.sleep(0.05)

    if not results:
        print("\n无有效结果，退出。")
        return None

    results_df = pd.DataFrame(results)
    results_df.sort_values("总分", ascending=False, inplace=True)
    results_df.reset_index(drop=True, inplace=True)
    results_df["强弱势排名"] = range(1, len(results_df) + 1)
    strong3 = results_df.head(3).copy()
    weak3 = results_df.tail(3).copy()

    run_key = None
    if save:
        print("\n[2/2] 写入数据库...")
        run_key = datetime.now().strftime("%Y%m%d_%H%M%S")
        save_results(results_df, prefix, db_path=db_path,
                     run_key=run_key, period_label=period_label)
        print(f"  已写入 {prefix}_analysis_all / strong_top3 / weak_bottom3 / top3_tracking")

    if render:
        print("\n生成报告...")
        render_report(results_df, strong3, weak3, period_label, errors, db_path)

    return results_df


def main():
    parser = argparse.ArgumentParser(description="期货 K 线强弱五维评分（任意周期）")
    parser.add_argument("--period", default="1440",
                        help="K 线周期(分钟)：5/15/30/60/1440(日线)，默认 1440")
    parser.add_argument("--top", type=int, default=40, help="分析品种数，默认 40")
    parser.add_argument("--length", type=int, default=None, help="拉取 K 线根数，留空按周期自动")
    parser.add_argument("--db", default=DB_PATH, help="数据库路径")
    parser.add_argument("--no-save", action="store_true", help="不写库")
    parser.add_argument("--no-render", action="store_true", help="不输出报告")
    args = parser.parse_args()
    run(period=args.period, top=args.top, length=args.length,
        db_path=args.db, save=not args.no_save, render=not args.no_render)


if __name__ == "__main__":
    main()
