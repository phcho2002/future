"""缓存性能基准：量化"本地分钟数据缓存"的提速收益。

对比三条路径（同一批 top40 品种、同一 period/length）：
    (a) akshare 逐品种拉取（原 future_3/futures_30min_signals 的模式，sleep 0.3s/品种）
    (b) tqsdk fetch_many 批量拉取（单连接）
    (c) 缓存命中（首次冷拉后二次纯读盘）

预期（实测会因网络/品种数不同）：
    (a) ≈ 40-60s  （0.3s sleep + 网络往返 × N）
    (b) ≈ 10-20s  （单连接批量）
    (c) ≈ 0.3-1s  （纯 parquet 读盘）
    → 缓存命中相对 akshare 提速 50-100×
"""

from __future__ import annotations

import time

import pandas as pd

from future_data.cache import get_klines
from future_data.provider import fetch_many
from future_data.universe import read_symbols


def _bench_akshare(symbols, period):
    """akshare 逐品种（复刻原 future_3 模式）。"""
    try:
        import akshare as ak
    except ImportError:
        return None, "akshare 未安装，跳过"
    total = 0.0
    ok = 0
    for sym, name, ex in symbols:
        t0 = time.time()
        try:
            df = ak.futures_zh_minute_sina(symbol=sym, period=period)
            if df is not None and len(df) > 0:
                ok += 1
        except Exception:
            pass
        total += time.time() - t0
        time.sleep(0.3)  # 复刻原脚本的限速
    return (total, ok), None


def _bench_tqsdk(symbols, period, length):
    """tqsdk 单连接批量。"""
    t0 = time.time()
    try:
        out = fetch_many(symbols, period=period, length=length)
        dt = time.time() - t0
        return (dt, len(out)), None
    except Exception as e:
        return None, f"tqsdk 失败: {e}"


def _bench_cache(symbols, period, length, ttl_hours=9999):
    """缓存命中（用大 TTL 保证只读盘）。先 ensure 冷拉一次。"""
    # 先确保有缓存（冷拉，不计入）
    for sym, _n, ex in symbols:
        try:
            get_klines(sym, ex, period=period, length=length, ttl_hours=ttl_hours)
        except Exception:
            pass
    # 再计时读盘
    t0 = time.time()
    ok = 0
    for sym, _n, ex in symbols:
        try:
            df = get_klines(sym, ex, period=period, length=length, ttl_hours=ttl_hours)
            if df is not None and not df.empty:
                ok += 1
        except Exception:
            pass
    return (time.time() - t0, ok), None


def run_benchmark(n_symbols: int = 10, period: str = "15", length: int = 200):
    """跑三轮并打印对比表。"""
    print("=" * 64)
    print(f"  缓存性能基准  |  {n_symbols} 品种  |  {period}min x {length}")
    print("=" * 64)

    syms = read_symbols(limit=n_symbols)
    print(f"品种: {[s for s, _, _ in syms]}\n")

    rows = []

    print("[1/3] akshare 逐品种（含 0.3s sleep/品种）...")
    res, err = _bench_akshare(syms, period)
    if err:
        print(f"  跳过: {err}")
        rows.append({"路径": "akshare 逐品种", "耗时(s)": None, "成功": "-", "备注": err})
    else:
        dt, ok = res
        print(f"  -> {dt:.2f}s, {ok}/{len(syms)} 成功")
        rows.append({"路径": "akshare 逐品种", "耗时(s)": round(dt, 2), "成功": f"{ok}/{len(syms)}", "备注": "0.3s sleep/品种"})

    print("[2/3] tqsdk 批量（单连接）...")
    res, err = _bench_tqsdk(syms, period, length)
    if err:
        print(f"  {err}")
        rows.append({"路径": "tqsdk 批量", "耗时(s)": None, "成功": "-", "备注": err})
    else:
        dt, ok = res
        print(f"  -> {dt:.2f}s, {ok}/{len(syms)} 成功")
        rows.append({"路径": "tqsdk 批量", "耗时(s)": round(dt, 2), "成功": f"{ok}/{len(syms)}", "备注": "单连接"})

    print("[3/3] 缓存命中（纯读盘）...")
    res, err = _bench_cache(syms, period, length)
    dt, ok = res
    print(f"  -> {dt:.2f}s, {ok}/{len(syms)} 成功")
    rows.append({"路径": "缓存命中", "耗时(s)": round(dt, 2), "成功": f"{ok}/{len(syms)}", "备注": "parquet 读盘"})

    print("\n" + "=" * 64)
    print("  汇总")
    print("=" * 64)
    df = pd.DataFrame(rows)
    print(df.to_string(index=False))

    # 提速倍数
    ak_t = rows[0]["耗时(s)"]
    cache_t = rows[-1]["耗时(s)"]
    tq_t = rows[1]["耗时(s)"]
    print()
    if isinstance(ak_t, (int, float)) and isinstance(cache_t, (int, float)) and cache_t > 0:
        print(f"  缓存命中 vs akshare 提速: {ak_t/cache_t:.0f}×")
    if isinstance(tq_t, (int, float)) and isinstance(cache_t, (int, float)) and cache_t > 0:
        print(f"  缓存命中 vs tqsdk 批量提速: {tq_t/cache_t:.0f}×")


if __name__ == "__main__":
    import sys
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    run_benchmark(n_symbols=n)
