#!/usr/bin/env python3
"""
综合交易监控卡片生成器
========================
数据源：xtquant (迅投行情)
系统：Top40 日线/小时线强弱 + future_2 (Wyckoff) + future_4 (双均线) + future_6 (Renko)

用法：
    python composite_card_xtquant.py
    
输出：
    - 终端表格汇总
    - d:/work_ai/singal_h/composite_card_<timestamp>.json
"""
from __future__ import annotations

import json
import os
import sys
import time
import sqlite3
import warnings
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

# ── 路径 ──
WORK_AI = Path("D:/work_ai")
DB_PATH = WORK_AI / "futures_data.db"
TOP40_JSON = WORK_AI / "futures_top40.json"
OUT_DIR = WORK_AI / "singal_h"
OUT_DIR.mkdir(exist_ok=True)

sys.path.insert(0, str(WORK_AI))

# ── 统一行情入口 ──
try:
    from future_data import get_klines, inject_many
    from future_data.xtquant_provider import _ensure_init
    _ensure_init()
    print("[OK] xtquant 初始化成功")
except Exception as e:
    print(f"[FAIL] xtquant 初始化失败: {e}")
    sys.exit(1)


# ══════════════════════════════════════════════════════════════
# 1. Top40 五维强弱评分（日线 + 小时线）
# ══════════════════════════════════════════════════════════════

def load_top40():
    """加载 Top40 品种清单"""
    if TOP40_JSON.exists():
        with open(TOP40_JSON, encoding="utf-8") as f:
            data = json.load(f)
        rows = data.get("symbols", [])
        return [(r[0], r[1], r[2]) for r in rows if isinstance(r, (list, tuple)) and len(r) >= 3]
    # 回退数据库
    conn = sqlite3.connect(DB_PATH)
    try:
        df = pd.read_sql("SELECT symbol, name, exchange FROM futures_top40 ORDER BY 排名 LIMIT 40", conn)
        return list(df.itertuples(index=False, name=None))
    finally:
        conn.close()


def score_breakthrough(df, short_w=20):
    """一、关键位突破质量 (0-20)"""
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
            return 18, f"强势突破：放量阳线站稳近期高点"
        if upper_shadow > 0.45:
            return 8, f"假突破嫌疑：触及高点但长上影"
        if is_bull:
            return 15, "突破近期高点，阳线收盘"
        return 11, "接近近期高点但收阴"
    if breakdown:
        if is_bear and lower_shadow < 0.25 and vol_ratio > 1.2:
            return 4, "弱势破位：放量阴线跌破近期低点"
        if lower_shadow > 0.45:
            return 9, "假跌破/探底：跌破低点但长下影"
        return 6, "弱势：收盘接近近期低点"
    return 10, "区间内震荡"


def score_pullback(df, short_w=20):
    """二、回调软弱程度 (0-20)"""
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
    ma10 = df["close"].rolling(10).mean()
    last5 = df.tail(5)
    bull_count = (last5["close"] > last5["open"]).sum()
    bear_count = (last5["close"] < last5["open"]).sum()
    lows_last5 = df.tail(5)["low"].values
    lows_prev5 = df.iloc[-10:-5]["low"].values
    higher_lows = bool(np.all(lows_last5 >= lows_prev5.min())) if len(lows_prev5) > 0 else False
    above_ma10 = last["close"] >= ma10.iloc[-1] * 0.995
    if pos > 0.7 and bull_count >= 3 and higher_lows and above_ma10:
        return 17, "回调偏强：低点抬高，守在10周期线上方"
    if pos > 0.5 and bull_count >= 3 and above_ma10:
        return 15, "回调健康：依托10周期线运行"
    if pos < 0.3 and bear_count >= 3:
        return 5, "回调极弱：连阴下跌，空头主导"
    if pos < 0.5 and bear_count >= 3 and last["close"] < ma10.iloc[-1]:
        return 7, "回调偏弱：跌破10周期线"
    return 10, "回调中性"


def score_abnormal_kline(df):
    """三、异常K线多空含义 (0-20)"""
    if len(df) < 6:
        return 10, "数据不足"
    last = df.iloc[-1]
    last3 = df.tail(3)
    last5 = df.tail(5)
    for _, k in last3.iterrows():
        kr = k["high"] - k["low"]
        if kr == 0:
            continue
        lower_shadow = (min(k["close"], k["open"]) - k["low"]) / kr
        body = abs(k["close"] - k["open"]) / kr
        if lower_shadow > 0.5 and body > 0.2 and last["close"] >= k["high"] * 0.995:
            return 18, "假弱势转强：长下影洗盘后收复前高"
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
                return 5, "假强势转弱：大阳后连续小阴回吐"
    if len(df) >= 2:
        p1, p2 = df.iloc[-2], df.iloc[-1]
        if p1["close"] < p1["open"] and p2["close"] > p2["open"]:
            if p2["open"] < p1["close"] and p2["close"] > p1["open"]:
                return 17, "看涨吞没：多头反包前K"
        if p1["close"] > p1["open"] and p2["close"] < p2["open"]:
            if p2["open"] > p1["close"] and p2["close"] < p1["open"]:
                return 6, "看跌吞没：空头反包前K"
    return 10, "近期无明确异常K线"


def score_resonance(df, long_w=60):
    """四、多周期K线共振 (0-20)"""
    if len(df) < long_w:
        return 10, "数据不足"
    closes = df["close"]
    ma5 = closes.rolling(5).mean()
    ma10 = closes.rolling(10).mean()
    ma20 = closes.rolling(20).mean()
    ma60 = closes.rolling(long_w).mean()
    last = df.iloc[-1]
    ma_bull = last["close"] > ma5.iloc[-1] > ma10.iloc[-1] > ma20.iloc[-1] > ma60.iloc[-1]
    ma_bear = last["close"] < ma5.iloc[-1] < ma10.iloc[-1] < ma20.iloc[-1] < ma60.iloc[-1]
    if ma_bull:
        return 19, "多周期共振强势：均线多头排列"
    if ma_bear:
        return 4, "多周期共振弱势：均线空头排列"
    if last["close"] > ma20.iloc[-1]:
        return 12, "价格在20周期线上方，中期偏强"
    return 8, "价格在20周期线下方，中期偏弱"


def score_intraday_momentum(df):
    """五、日内动能 (0-20)"""
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
        return 18, f"动能极强：连续收阳且收盘高位，量能放大"
    if pos > 0.6 and bull_count >= 3:
        return 15, "动能偏强：多数K收阳且收盘位于中高位"
    if pos < 0.3 and bull_count <= 1:
        return 5, "动能极弱：连续收阴且收盘位于低位"
    if pos < 0.4 and bull_count <= 2:
        return 8, "动能偏弱：多数K收阴"
    return 10, "动能中性"


def _verdict(total):
    if total >= 80: return "极强"
    if total >= 68: return "强势"
    if total >= 56: return "中性偏强"
    if total >= 44: return "中性偏弱"
    if total >= 32: return "弱势"
    return "极弱"


def run_strength(symbols, period, label):
    """运行五维强弱评分"""
    print(f"\n{'='*70}")
    print(f"  {label} 五维强弱评分 (period={period})")
    print(f"{'='*70}")
    
    length = 200 if period == "1440" else 1500
    results = []
    
    for i, (sym, name, exch) in enumerate(symbols, 1):
        try:
            df = get_klines(sym, exch, period=period, length=length)
            if df is None or len(df) < 30:
                print(f"  [{i:2d}/{len(symbols)}] {sym} {name}: 数据不足")
                continue
            
            s1, d1 = score_breakthrough(df)
            s2, d2 = score_pullback(df)
            s3, d3 = score_abnormal_kline(df)
            s4, d4 = score_resonance(df)
            s5, d5 = score_intraday_momentum(df)
            total = s1 + s2 + s3 + s4 + s5
            
            results.append({
                "symbol": sym, "name": name,
                "total": total, "verdict": _verdict(total),
                "s1": s1, "s2": s2, "s3": s3, "s4": s4, "s5": s5,
                "close": float(df.iloc[-1]["close"]),
                "d1": d1, "d2": d2, "d3": d3, "d4": d4, "d5": d5,
            })
            print(f"  [{i:2d}/{len(symbols)}] {sym} {name}: {total}分 {_verdict(total)}")
        except Exception as e:
            print(f"  [{i:2d}/{len(symbols)}] {sym} {name}: ERR {str(e)[:40]}")
    
    return pd.DataFrame(results)


# ══════════════════════════════════════════════════════════════
# 2. future_4 双均线缠绕信号
# ══════════════════════════════════════════════════════════════

def run_future4(symbols):
    """运行 future_4 双均线缠绕扫描"""
    print(f"\n{'='*70}")
    print(f"  future_4 双均线缠绕信号")
    print(f"{'='*70}")
    
    # 导入 future_4 模块
    f4_path = WORK_AI / "future_4"
    # 确保 future_4 路径在最前面，避免与 future_6 的模块冲突
    sys.path = [str(f4_path)] + [p for p in sys.path if "future_4" not in p and "future_6" not in p]
    try:
        old_cwd = os.getcwd()
        os.chdir(str(f4_path))
        from data_loader import load_config, load_all_klines_inject
        from indicators import attach_indicators
        from signals import find_signals, find_alerts
        from backtest import backtest, performance
        os.chdir(old_cwd)
        
        cfg = load_config(str(f4_path / "config.yaml"))
        p = cfg["strategy"]
        
        # 注入模式批量加载 (需要 db 连接)
        # 用 xtquant 直接拉取替代
        inject_klines = {}
        for sym, name, exch in symbols:
            try:
                df = get_klines(sym, exch, period="15", length=300)
                if df is not None and len(df) >= 50:
                    inject_klines[sym] = df
            except:
                pass
        
        rows = []
        n = len(symbols)
        name_map = {s[0]: s[1] for s in symbols}
        for i, (sym, name, exch) in enumerate(symbols, 1):
            df_scan = inject_klines.get(sym)
            if df_scan is None or df_scan.empty:
                continue
            try:
                d = attach_indicators(df_scan, p)
                sigs = find_signals(df_scan, p)
                if len(sigs):
                    last = sigs.iloc[-1].copy()
                    ci = int(last["cross_idx"])
                    last_close = float(d["close"].iloc[-1])
                    bars_since = len(d) - 1 - ci
                    # 星级
                    s = 1
                    if last.get("bonus"):
                        s += 1
                    body_strong = last.get("body_ratio", 0) >= 2 * p.get("body_pct", 0.15)
                    dense_strong = last.get("twist_cross", 0) >= 5
                    if body_strong or dense_strong:
                        s += 1
                    s = min(s, 3)
                    
                    rows.append({
                        "symbol": sym, "name": name_map.get(sym, ""),
                        "direction": "多" if last.get("side", 1) == 1 else "空",
                        "stars": s,
                        "bars_since": bars_since,
                        "entry": float(d["close"].iloc[ci]),
                        "last_close": last_close,
                    })
                    dir_str = "多" if last.get("side", 1) == 1 else "空"
                    print(f"  [{i:2d}/{n}] {sym}: {dir_str} {s}星 距今{bars_since}根")
            except Exception as e:
                pass
        
        return pd.DataFrame(rows)
    except Exception as e:
        print(f"  [FAIL] future_4 导入失败: {e}")
        return pd.DataFrame()


# ══════════════════════════════════════════════════════════════
# 3. future_6 Renko 信号
# ══════════════════════════════════════════════════════════════

def run_future6(symbols):
    """运行 future_6 Renko 扫描"""
    print(f"\n{'='*70}")
    print(f"  future_6 Renko 信号")
    print(f"{'='*70}")
    
    f6_path = WORK_AI / "future_6"
    # 隔离 future_6 导入，避免 future_4 的 indicators 干扰
    original_path = sys.path[:]
    sys.path = [str(f6_path)] + [p for p in sys.path if "future_4" not in p and "future_6" not in p]
    try:
        old_cwd = os.getcwd()
        os.chdir(str(f6_path))
        # 清理可能的缓存模块
        for m in ['indicators', 'signals', 'data_loader', 'renko']:
            if m in sys.modules:
                del sys.modules[m]
        from data_loader import load_config, read_symbols as f6_read_symbols
        from indicators import atr, round_brick_size
        from renko import build_renko
        from signals import generate_signals
        os.chdir(old_cwd)
        
        cfg = load_config(str(f6_path / "config.yaml"))
        renko_cfg = cfg.get("renko", {})
        
        rows = []
        n = len(symbols)
        for i, (sym, name, exch) in enumerate(symbols, 1):
            try:
                df = get_klines(sym, exch, period="60", length=2000)
                if df is None or len(df) < 100:
                    continue
                
                # 砖块大小
                atr_val = float(atr(df, period=renko_cfg.get("atr_period", 14)).iloc[-1])
                last_price = float(df["close"].iloc[-1])
                raw_size = max(atr_val * renko_cfg.get("brick_atr_mult", 1.0),
                               renko_cfg.get("min_brick_size", 0),
                               last_price * renko_cfg.get("min_brick_ratio", 0))
                brick_size = round_brick_size(raw_size, last_price, min_size=renko_cfg.get("min_brick_size", 0))
                
                # 构建 Renko
                renko_df = build_renko(df, brick_size)
                if renko_df is None or renko_df.empty:
                    continue
                
                # 生成信号
                signals = generate_signals(renko_df, cfg)
                if signals:
                    latest = signals[-1]
                    dir_str = "多" if latest.direction == 1 else "空"
                    rows.append({
                        "symbol": sym, "name": name,
                        "signal_type": latest.signal_type,
                        "direction": dir_str,
                        "strength": latest.strength_score,
                        "rsi": latest.rsi_brick,
                        "entry": latest.entry_price,
                        "last_close": latest.last_close,
                    })
                    print(f"  [{i:2d}/{n}] {sym}: {latest.signal_type} {dir_str} 强度{latest.strength_score:.2f}")
            except Exception as e:
                pass
        
        return pd.DataFrame(rows)
    except Exception as e:
        print(f"  [FAIL] future_6 导入失败: {e}")
        import traceback; traceback.print_exc()
        return pd.DataFrame()


# ══════════════════════════════════════════════════════════════
# 4. 综合评分 + 交易监控卡片
# ══════════════════════════════════════════════════════════════

def compute_composite(daily_df, hourly_df, f4_df, f6_df):
    """综合评分：系统分×60% + 强弱分×40% + 共振加分"""
    
    scores = {}
    
    # 日线强弱分 (归一化到 0-100)
    if not daily_df.empty:
        for _, r in daily_df.iterrows():
            scores[r["symbol"]] = {
                "symbol": r["symbol"],
                "name": r["name"],
                "daily_score": r["total"],
                "daily_verdict": r["verdict"],
                "close": r["close"],
                "f4_dir": None, "f4_stars": 0,
                "f6_dir": None, "f6_signal": None,
            }
    
    # 小时线强弱分
    if not hourly_df.empty:
        for _, r in hourly_df.iterrows():
            sym = r["symbol"]
            if sym in scores:
                scores[sym]["hourly_score"] = r["total"]
                scores[sym]["hourly_verdict"] = r["verdict"]
            else:
                scores[sym] = {
                    "symbol": sym, "name": r["name"],
                    "hourly_score": r["total"], "hourly_verdict": r["verdict"],
                    "close": r["close"],
                    "daily_score": 0, "daily_verdict": "-",
                    "f4_dir": None, "f4_stars": 0,
                    "f6_dir": None, "f6_signal": None,
                }
    
    # future_4 信号
    if not f4_df.empty:
        for _, r in f4_df.iterrows():
            sym = r["symbol"]
            if sym in scores:
                scores[sym]["f4_dir"] = r["direction"]
                scores[sym]["f4_stars"] = r["stars"]
                scores[sym]["f4_bars"] = r["bars_since"]
    
    # future_6 信号
    if not f6_df.empty:
        for _, r in f6_df.iterrows():
            sym = r["symbol"]
            if sym in scores:
                scores[sym]["f6_dir"] = r["direction"]
                scores[sym]["f6_signal"] = r["signal_type"]
                scores[sym]["f6_strength"] = r["strength"]
    
    # 综合评分
    for sym, s in scores.items():
        daily = s.get("daily_score", 0)
        hourly = s.get("hourly_score", 0)
        
        # 基础分 = 日线×60% + 小时线×40%
        base = daily * 0.6 + hourly * 0.4
        
        # 共振加分
        bonus = 0
        dirs = []
        if s.get("f4_dir"):
            dirs.append(s["f4_dir"])
        if s.get("f6_dir"):
            dirs.append(s["f6_dir"])
        
        # 多系统方向一致 → +5
        if len(dirs) >= 2 and len(set(dirs)) == 1:
            bonus += 5
        
        # 日线强势 + 小时线强势 → +3
        if daily >= 68 and hourly >= 68:
            bonus += 3
        
        s["composite"] = min(base + bonus, 100)
        s["bonus"] = bonus
    
    return scores


def generate_card(scores):
    """生成交易监控卡片"""
    # 排序
    sorted_scores = sorted(scores.values(), key=lambda x: x.get("composite", 0), reverse=True)
    
    # 强势 TOP8
    top8 = [s for s in sorted_scores if s.get("composite", 0) >= 50][:8]
    
    # 弱势 BOTTOM8
    bottom8 = [s for s in sorted_scores if s.get("composite", 0) <= 50]
    bottom8 = sorted(bottom8, key=lambda x: x.get("composite", 0))[:8]
    
    # 多系统共振
    resonance_long = []
    resonance_short = []
    for s in sorted_scores:
        dirs = []
        if s.get("f4_dir"):
            dirs.append(s["f4_dir"])
        if s.get("f6_dir"):
            dirs.append(s["f6_dir"])
        if len(dirs) >= 2 and len(set(dirs)) == 1:
            if dirs[0] == "多":
                resonance_long.append(s)
            else:
                resonance_short.append(s)
    
    card = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "data_source": "xtquant",
        "top8_strongest": [
            {
                "symbol": s["symbol"],
                "name": s["name"],
                "composite": s.get("composite", 0),
                "daily": s.get("daily_score", 0),
                "hourly": s.get("hourly_score", 0),
                "f4": f"{s['f4_dir']}{s['f4_stars']}星" if s.get("f4_dir") else "-",
                "f6": f"{s['f6_dir']}/{s['f6_signal']}" if s.get("f6_dir") else "-",
            }
            for s in top8
        ],
        "bottom8_weakest": [
            {
                "symbol": s["symbol"],
                "name": s["name"],
                "composite": s.get("composite", 0),
                "daily": s.get("daily_score", 0),
                "hourly": s.get("hourly_score", 0),
                "f4": f"{s['f4_dir']}{s['f4_stars']}星" if s.get("f4_dir") else "-",
                "f6": f"{s['f6_dir']}/{s['f6_signal']}" if s.get("f6_dir") else "-",
            }
            for s in bottom8
        ],
        "resonance_long": [
            {"symbol": s["symbol"], "name": s["name"], "composite": s.get("composite", 0)}
            for s in resonance_long
        ],
        "resonance_short": [
            {"symbol": s["symbol"], "name": s["name"], "composite": s.get("composite", 0)}
            for s in resonance_short
        ],
    }
    
    return card


def print_card(card):
    """打印交易监控卡片"""
    print(f"\n{'='*70}")
    print(f"  📊 交易监控卡片 | {card['timestamp']}")
    print(f"  数据源: {card['data_source']}")
    print(f"{'='*70}")
    
    print(f"\n  🔥 强势 TOP8")
    print(f"  {'品种':>6} {'名称':>10} {'综合':>5} {'日线':>5} {'小时':>5} {'f4':>8} {'f6':>15}")
    print(f"  " + "-" * 55)
    for s in card["top8_strongest"]:
        print(f"  {s['symbol']:>6} {s['name']:>10} {s['composite']:>5.0f} {s['daily']:>5} {s['hourly']:>5} {s['f4']:>8} {s['f6']:>15}")
    
    print(f"\n  ❄️ 弱势 BOTTOM8")
    print(f"  {'品种':>6} {'名称':>10} {'综合':>5} {'日线':>5} {'小时':>5} {'f4':>8} {'f6':>15}")
    print(f"  " + "-" * 55)
    for s in card["bottom8_weakest"]:
        print(f"  {s['symbol']:>6} {s['name']:>10} {s['composite']:>5.0f} {s['daily']:>5} {s['hourly']:>5} {s['f4']:>8} {s['f6']:>15}")
    
    if card["resonance_long"]:
        print(f"\n  🟢 多系统共振（做多）")
        for s in card["resonance_long"]:
            print(f"    {s['symbol']} {s['name']} 综合{s['composite']:.0f}")
    
    if card["resonance_short"]:
        print(f"\n  🔴 多系统共振（做空）")
        for s in card["resonance_short"]:
            print(f"    {s['symbol']} {s['name']} 综合{s['composite']:.0f}")
    
    print(f"\n{'='*70}")


# ══════════════════════════════════════════════════════════════
# 主流程
# ══════════════════════════════════════════════════════════════

def main():
    print("=" * 70)
    print("  综合交易监控卡片生成器 (xtquant 数据源)")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)
    
    # 加载品种
    symbols = load_top40()
    print(f"\n[OK] Top40 品种池: {len(symbols)} 个")
    
    # 1. 日线强弱
    daily_df = run_strength(symbols, "1440", "日线")
    
    # 2. 小时线强弱
    hourly_df = run_strength(symbols, "60", "小时线")
    
    # 3. future_4 双均线缠绕
    f4_df = run_future4(symbols)
    
    # 4. future_6 Renko
    f6_df = run_future6(symbols)
    
    # 5. 综合评分
    scores = compute_composite(daily_df, hourly_df, f4_df, f6_df)
    
    # 6. 生成卡片
    card = generate_card(scores)
    
    # 7. 打印 + 保存
    print_card(card)
    
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = OUT_DIR / f"composite_card_{ts}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(card, f, ensure_ascii=False, indent=2)
    print(f"\n  卡片已保存: {out_path}")
    
    # 8. 同时保存最新一份
    latest_path = OUT_DIR / "composite_card_latest.json"
    with open(latest_path, "w", encoding="utf-8") as f:
        json.dump(card, f, ensure_ascii=False, indent=2)
    print(f"  最新卡片: {latest_path}")


if __name__ == "__main__":
    main()
