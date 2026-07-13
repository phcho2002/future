#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
期货信号提示程序
================

功能:
  1. 获取期货市场沉淀资金排名前30的主力品种
     沉淀资金 = sum(各合约持仓量 x 每手保证金)
  2. 对每个品种获取最近200根60分钟K线
  3. 检测水平价位信号:
     a) 最高价接近水平: 200根K线中有 >=2根的最高价差值 < 阈值
        → 提示水平阻力位
     b) 最低价接近水平: 200根K线中有 >=2根的最低价差值 < 阈值
        → 提示水平支撑位
  4. 输出: 品种名称、信号出现日期、信号类型

使用:
  python futures_signal.py

依赖:
  pip install akshare pandas numpy
"""

import akshare as ak
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import List, Dict, Optional
import warnings
import time

# ---- 接入全系统统一行情入口 future_data（xtquant 后端 + TTL 缓存）----
import sys as _sys
from pathlib import Path

# 兼容 Linux/Windows：从脚本所在目录向上推导到 work_ai 根目录
_SCRIPT_DIR = Path(__file__).resolve().parent
_WORK_AI = _SCRIPT_DIR.parent
_sys.path.insert(0, str(_WORK_AI))
try:
    from future_data import get_klines as _tq_get_klines
    from future_data.universe import build_exchange_map as _tq_exch_map
    _HAS_FUTURE_DATA = True
except Exception:  # noqa: BLE001
    _HAS_FUTURE_DATA = False
_EXCHANGE_MAP: Optional[Dict[str, str]] = None


def _resolve_exchange(symbol: str) -> Optional[str]:
    """从 DB futures_top40 查 symbol 对应 exchange。"""
    global _EXCHANGE_MAP
    if _EXCHANGE_MAP is None and _HAS_FUTURE_DATA:
        try:
            _EXCHANGE_MAP = _tq_exch_map()
        except Exception:  # noqa: BLE001
            _EXCHANGE_MAP = {}
    return (_EXCHANGE_MAP or {}).get(symbol)

warnings.filterwarnings("ignore")

# ============================================================
# 配置参数
# ============================================================
TOP_N = 30                 # 取沉淀资金前N名
LOOKBACK_BARS = 200        # 回溯60分钟K线数量
HIGH_K = 2                 # 最高价检测: 最少K线数
LOW_K = 2                  # 最低价检测: 最少K线数
HIGH_THRESHOLD = 0.15      # 最高价接近阈值 (%)
LOW_THRESHOLD = 0.20       # 最低价接近阈值 (%)


# ============================================================
# 第一步: 获取沉淀资金前N名品种
# ============================================================

def get_top30_by_capital() -> pd.DataFrame:
    """
    获取沉淀资金排名前30的期货品种。

    方法:
      1. 调用 futures_fees_info() 获取所有合约的费率和保证金信息
      2. 每个合约的沉淀资金 = 持仓量 x 每手保证金
      3. 按品种代码汇总, 取总沉淀资金前30名
      4. 主力合约 = 该品种中持仓量最大的合约

    Returns:
        DataFrame: [product_code, product_name, total_capital_yi,
                    main_contract, total_oi]
    """
    print("=" * 65)
    print("  第一步: 获取沉淀资金排名前30品种")
    print("=" * 65)

    fees_df = None
    used_fallback = False

    # --- 方案A: futures_fees_info (精确, 含每手保证金) ---
    try:
        print("  [1.1] 获取合约信息 (futures_fees_info)...")
        fees_df = ak.futures_fees_info()
        if fees_df is None or len(fees_df) == 0:
            raise ValueError("返回空数据")
        print(f"         OK — 获取到 {len(fees_df)} 条合约记录")
    except Exception as e:
        print(f"         futures_fees_info 失败: {e}")
        fees_df = None

    if fees_df is not None:
        # 列索引 (akshare 1.16.95)
        col_contract = fees_df.columns[1]   # 合约代码
        col_product = fees_df.columns[3]    # 品种代码
        col_name = fees_df.columns[4]       # 品种名称
        col_oi = fees_df.columns[21]        # 持仓量
        col_margin = fees_df.columns[25]    # 买方保证金/手

        oi = pd.to_numeric(fees_df[col_oi], errors="coerce")
        margin = pd.to_numeric(fees_df[col_margin], errors="coerce")
        capital = oi * margin  # 沉淀资金

        df = pd.DataFrame({
            "contract": fees_df[col_contract].astype(str).str.strip(),
            "product_code": fees_df[col_product].astype(str).str.strip(),
            "product_name": fees_df[col_name].astype(str).str.strip(),
            "open_interest": oi,
            "capital": capital,
        })
        df = df.dropna(subset=["capital", "product_code"])
        df = df[df["capital"] > 0]

        # 按品种代码汇总
        grp = df.groupby("product_code")
        summary = grp.agg(
            total_capital=("capital", "sum"),
            product_name=("product_name", "first"),
            total_oi=("open_interest", "sum"),
        ).reset_index()

        # 主力合约 (持仓量最大的合约)
        main_contracts = df.loc[df.groupby("product_code")["open_interest"].idxmax()]
        mc_map = dict(zip(main_contracts["product_code"], main_contracts["contract"]))

        summary["main_contract"] = summary["product_code"].map(mc_map)
        summary["capital_yi"] = summary["total_capital"] / 1e8
        summary = summary.sort_values("total_capital", ascending=False)
        top30 = summary.head(TOP_N).reset_index(drop=True)
        top30.index = range(1, len(top30) + 1)

        print(f"  [1.2] 沉淀资金排名前{len(top30)}品种 (亿元):")
        print(f"         {'排名':<5}{'品种代码':<8}{'品种名称':<14}{'沉淀资金(亿)':<15}{'主力合约':<12}{'总持仓':>10}")
        print(f"         {'-' * 66}")
        for idx, row in top30.iterrows():
            print(f"         {idx:<5}{row['product_code']:<8}{row['product_name']:<14}"
                  f"{row['capital_yi']:<15.2f}{row['main_contract']:<12}"
                  f"{int(row['total_oi']):>10,}")
        return top30

    # --- 方案B: futures_zh_spot (实时行情, 无合约乘数, 近似估算) ---
    print("  [回退] 方案B: 实时行情近似估算...")
    used_fallback = True

    try:
        display = ak.futures_display_main_sina()
        symbol_list = ",".join(display["symbol"].tolist())
        spot = ak.futures_zh_spot(symbol=symbol_list, market="CF", adjust="0")

        spot["price"] = pd.to_numeric(spot["current_price"], errors="coerce")
        spot["hold"] = pd.to_numeric(spot["hold"], errors="coerce")
        spot["capital"] = spot["price"] * spot["hold"] * 10  # 乘数默认10, 近似
        spot = spot.dropna(subset=["capital"])
        spot = spot.sort_values("capital", ascending=False)
        top30 = spot.head(TOP_N).reset_index(drop=True)
        top30.index = range(1, len(top30) + 1)

        # 构建名称映射
        name_map = dict(zip(display["symbol"], display["name"]))

        print(f"  [回退] 沉淀资金前{len(top30)}品种 (近似):")
        for idx, row in top30.iterrows():
            sym = row["symbol"]
            name = name_map.get(sym, sym)
            cap_yi = row["capital"] / 1e8
            print(f"         {idx:<5}{sym:<8}{name:<14}{cap_yi:<15.2f}"
                  f"{sym:<12}{int(row['hold']):>10,}")

        result = pd.DataFrame({
            "product_code": top30["symbol"].tolist(),
            "product_name": [name_map.get(s, s) for s in top30["symbol"]],
            "total_capital": top30["capital"].tolist(),
            "capital_yi": (top30["capital"] / 1e8).tolist(),
            "main_contract": top30["symbol"].tolist(),
            "total_oi": top30["hold"].tolist(),
        })
        result.index = range(1, len(result) + 1)
        return result

    except Exception as e:
        print(f"  [致命] 所有方案均失败: {e}")
        raise SystemExit(1)


# ============================================================
# 第二步: 获取60分钟K线数据
# ============================================================

def get_60min_kline(symbol: str) -> Optional[pd.DataFrame]:
    """
    获取指定品种的60分钟K线数据 (最近 LOOKBACK_BARS 根)。

    Args:
        symbol: 主力合约代码, 如 'RB0', 'M0'

    Returns:
        DataFrame [datetime, open, high, low, close, volume, hold] or None
        （xtquant 路径有 openInterest 字段，此处保留兼容，补 0 占位；下游 detect_horizontal_* 不使用 hold）
    """
    # ---- 主路径：统一入口 future_data（xtquant）----
    if _HAS_FUTURE_DATA:
        ex = _resolve_exchange(symbol)
        if ex is not None:
            try:
                df = _tq_get_klines(symbol, ex, period="60", length=LOOKBACK_BARS)
                if df is not None and not df.empty:
                    df = df.tail(LOOKBACK_BARS).reset_index(drop=True)
                    if "hold" not in df.columns:
                        df["hold"] = 0.0
                    return df
            except Exception:
                pass  # xtquant 失败回退 akshare

    # ---- 回退路径：akshare ----
    try:
        df = ak.futures_zh_minute_sina(symbol=symbol, period="60")
        if df is None or len(df) == 0:
            return None
        # 标准化列名
        df.columns = [c.lower() for c in df.columns]
        for c in ["open", "high", "low", "close", "volume", "hold"]:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")
        return df.tail(LOOKBACK_BARS).reset_index(drop=True)
    except Exception:
        return None


def build_symbol_map() -> Dict[str, str]:
    """构建 品种代码 -> 主力连续symbol 映射表。

    例如: {'RB': 'RB0', 'M': 'M0', ...}
    """
    try:
        display = ak.futures_display_main_sina()
        smap = {}
        for _, row in display.iterrows():
            sym = str(row["symbol"]).strip()
            if sym.endswith("0") and len(sym) >= 2:
                smap[sym[:-1].upper()] = sym
            smap[sym.upper()] = sym
        return smap
    except Exception:
        return {}


# ============================================================
# 第三步: 信号检测算法
# ============================================================

def detect_horizontal_highs(kline: pd.DataFrame) -> Optional[Dict]:
    """
    检测最高价连线接近水平的信号 (水平阻力位)。

    算法 (排序聚类):
      1. 取最近 LOOKBACK_BARS 根K线的最高价, 从低到高排序
      2. 滑动窗口找价格差值百分比 <= HIGH_THRESHOLD 的最大聚类
      3. 如果聚类大小 >= HIGH_K (2) → 信号成立

    返回: {type, price_level, k_count, date, first_date, pct_range}
    """
    if kline is None or len(kline) < HIGH_K:
        return None

    highs = kline["high"].dropna().values
    dates = kline["datetime"].values
    if len(highs) < HIGH_K:
        return None

    idx_sort = np.argsort(highs)
    s_highs = highs[idx_sort]
    s_dates = dates[idx_sort]

    best_n, best_info = 0, None
    n = len(s_highs)

    for i in range(n - HIGH_K + 1):
        j = i + HIGH_K - 1
        while j < n:
            chunk = s_highs[i:j+1]
            rng = chunk.max() - chunk.min()
            avg = chunk.mean()
            if avg > 0:
                pct = (rng / avg) * 100
                if pct <= HIGH_THRESHOLD:
                    sz = j - i + 1
                    if sz > best_n:
                        best_n = sz
                        best_info = {
                            "price_level": float(avg),
                            "pct_diff": float(pct),
                            "dates": s_dates[i:j+1],
                        }
                    j += 1
                    continue
            break  # 已排序, 后面的差值只会更大

    if best_info is None:
        return None

    dts = pd.to_datetime(best_info["dates"])
    return {
        "type": "最高价接近水平",
        "price_level": round(best_info["price_level"], 2),
        "k_count": best_n,
        "date": dts.max().strftime("%Y-%m-%d %H:%M"),
        "first_date": dts.min().strftime("%Y-%m-%d %H:%M"),
        "pct_range": round(best_info["pct_diff"], 4),
    }


def detect_horizontal_lows(kline: pd.DataFrame) -> Optional[Dict]:
    """
    检测最低价连线接近水平的信号 (水平支撑位)。

    算法与 detect_horizontal_highs 相同, 但作用于最低价序列。
    要求至少 LOW_K (2) 根K线的最低价处于同一狭窄区间。
    """
    if kline is None or len(kline) < LOW_K:
        return None

    lows = kline["low"].dropna().values
    dates = kline["datetime"].values
    if len(lows) < LOW_K:
        return None

    idx_sort = np.argsort(lows)
    s_lows = lows[idx_sort]
    s_dates = dates[idx_sort]

    best_n, best_info = 0, None
    n = len(s_lows)

    for i in range(n - LOW_K + 1):
        j = i + LOW_K - 1
        while j < n:
            chunk = s_lows[i:j+1]
            rng = chunk.max() - chunk.min()
            avg = chunk.mean()
            if avg > 0:
                pct = (rng / avg) * 100
                if pct <= LOW_THRESHOLD:
                    sz = j - i + 1
                    if sz > best_n:
                        best_n = sz
                        best_info = {
                            "price_level": float(avg),
                            "pct_diff": float(pct),
                            "dates": s_dates[i:j+1],
                        }
                    j += 1
                    continue
            break

    if best_info is None:
        return None

    dts = pd.to_datetime(best_info["dates"])
    return {
        "type": "最低价接近水平",
        "price_level": round(best_info["price_level"], 2),
        "k_count": best_n,
        "date": dts.max().strftime("%Y-%m-%d %H:%M"),
        "first_date": dts.min().strftime("%Y-%m-%d %H:%M"),
        "pct_range": round(best_info["pct_diff"], 4),
    }


# ============================================================
# 单品种分析
# ============================================================

def analyze_one(row: pd.Series, sym_map: Dict[str, str]) -> List[Dict]:
    """对单个品种执行完整信号检测, 返回信号列表。"""
    pc = row["product_code"]
    pn = row["product_name"]
    mc = row.get("main_contract", "")

    # K线查询用的symbol
    sym = sym_map.get(pc.upper(), pc + "0")

    signals = []
    kline = get_60min_kline(sym)
    if kline is None:
        return signals

    # 最高价检测
    hs = detect_horizontal_highs(kline)
    if hs:
        hs.update({"product_code": pc, "product_name": pn,
                    "symbol": sym, "main_contract": mc})
        signals.append(hs)

    # 最低价检测
    ls = detect_horizontal_lows(kline)
    if ls:
        ls.update({"product_code": pc, "product_name": pn,
                    "symbol": sym, "main_contract": mc})
        signals.append(ls)

    return signals


# ============================================================
# 输出
# ============================================================

def print_results(signals: List[Dict], n_products: int):
    """格式化打印所有信号。"""
    print()
    print("=" * 65)
    print("  第三步: 信号检测结果")
    print("=" * 65)
    print(f"  扫描品种: {n_products} 个")
    print(f"  K线: 60分钟, 回溯 {LOOKBACK_BARS} 根")
    print(f"  最高价检测: >= {HIGH_K} 根K线, 差值 < {HIGH_THRESHOLD}%")
    print(f"  最低价检测: >= {LOW_K} 根K线, 差值 < {LOW_THRESHOLD}%")
    print()

    if not signals:
        print("  >>> 未检测到任何信号。")
        return

    hi = [s for s in signals if "最高价" in s["type"]]
    lo = [s for s in signals if "最低价" in s["type"]]

    print(f"  >>> 共发现 {len(signals)} 个信号 "
          f"(阻力位: {len(hi)}, 支撑位: {len(lo)})")
    print()

    COL_W = {"name": 14, "code": 8, "mc": 12, "price": 12, "k": 10, "pct": 13, "first": 20, "last": 20}

    def print_table(title, data, price_label):
        if not data:
            return
        print(f"  {'─' * 60}")
        print(f"  [{title}]")
        print(f"  {'─' * 60}")
        hdr = (f"  {'品种':<{COL_W['name']}}{'代码':<{COL_W['code']}}"
               f"{'合约':<{COL_W['mc']}}{price_label:<{COL_W['price']}}"
               f"{'K线数':<{COL_W['k']}}{'范围%':<{COL_W['pct']}}"
               f"{'首次触及':<{COL_W['first']}}{'最近触及':<{COL_W['last']}}")
        print(hdr)
        print(f"  {'-' * (sum(COL_W.values()) + 2)}")
        for s in data:
            print(f"  {s['product_name']:<{COL_W['name']}}"
                  f"{s['product_code']:<{COL_W['code']}}"
                  f"{s.get('main_contract', ''):<{COL_W['mc']}}"
                  f"{s['price_level']:<{COL_W['price']}.2f}"
                  f"{s['k_count']:<{COL_W['k']}}"
                  f"{s['pct_range']:<{COL_W['pct']}.4f}"
                  f"{s['first_date']:<{COL_W['first']}}"
                  f"{s['date']:<{COL_W['last']}}")
        print()

    print_table("最高价接近水平 — 水平阻力位", hi, "阻力价位")
    print_table("最低价接近水平 — 水平支撑位", lo, "支撑价位")

    # 汇总表
    print(f"  {'=' * 65}")
    print(f"  [汇总]")
    for s in signals:
        print(f"  {s['product_name']:<14} | {s['type']:<16} | "
              f"价位={s['price_level']:<10.2f} | "
              f"K线数={s['k_count']:<4} | "
              f"最近={s['date']} | 范围={s['pct_range']}%")


def export_csv(signals: List[Dict], path: str):
    """导出CSV。"""
    cols_cn = ["品种名称", "品种代码", "主力合约", "信号类型",
               "价位", "涉及K线数", "首次触及日期", "最近触及日期", "价格变动范围%"]
    cols_en = ["product_name", "product_code", "main_contract", "type",
               "price_level", "k_count", "first_date", "date", "pct_range"]

    if not signals:
        pd.DataFrame(columns=cols_cn).to_csv(path, index=False, encoding="utf-8-sig")
    else:
        pd.DataFrame(signals)[cols_en].rename(
            columns=dict(zip(cols_en, cols_cn))
        ).to_csv(path, index=False, encoding="utf-8-sig")
    print(f"\n  结果已导出: {path}")


# ============================================================
# 主入口
# ============================================================

def main():
    t0 = datetime.now()
    print()
    print("=" * 65)
    print("  期货信号提示程序 — 水平阻力/支撑位检测")
    print(f"  {t0.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 65)
    print()

    # 1. 沉淀资金排名
    top30 = get_top30_by_capital()

    # 2. symbol映射
    print()
    print("=" * 65)
    print("  第二步: 60分钟K线信号扫描")
    print("=" * 65)
    sym_map = build_symbol_map()
    print(f"  品种->symbol 映射: {len(sym_map)} 条")
    print()

    # 3. 逐品种扫描
    all_sig = []
    ok = fail = 0
    for idx, row in top30.iterrows():
        pc, pn = row["product_code"], row["product_name"]
        print(f"  [{idx}/{len(top30)}] {pc:<8} {pn:<14}", end=" ", flush=True)
        try:
            sigs = analyze_one(row, sym_map)
            if sigs:
                all_sig.extend(sigs)
                print(f"-> {len(sigs)} 个信号!")
            else:
                print("-> 无")
            ok += 1
        except Exception as e:
            print(f"-> 错误: {e}")
            fail += 1
        time.sleep(0.3)  # 控制请求频率

    print(f"\n  扫描完成: 成功={ok}, 失败={fail}")

    # 4. 结果
    print_results(all_sig, len(top30))
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    export_csv(all_sig, f"futures_signal_{ts}.csv")

    # 5. 摘要
    elapsed = (datetime.now() - t0).total_seconds()
    print(f"\n{'=' * 65}")
    print(f"  执行摘要")
    print(f"{'=' * 65}")
    print(f"  检查品种: {len(top30)} | 发现信号: {len(all_sig)} | 耗时: {elapsed:.1f}s")
    if all_sig:
        uniq = set(s["product_code"] for s in all_sig)
        print(f"  涉及品种: {', '.join(sorted(uniq))}")
    print(f"{'=' * 65}")


if __name__ == "__main__":
    main()
