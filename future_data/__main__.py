"""命令行入口：

    python -m future_data status                 # 查看缓存新鲜度
    python -m future_data refresh --period 15     # 预热/刷新全品种（盘前跑一次）
    python -m future_data refresh --symbol RB0 --exchange shfe
    python -m future_data inject --period 15      # 注入模式：增量更新、滚动窗口（短周期实盘扫描）
    python -m future_data clear                   # 清空缓存
    python -m future_data bench                   # 性能基准对比
    python -m future_data explain RB0 shfe        # 解释符号解析

建议用法：
  - refresh：盘前跑一次，预热全品种 TTL 缓存（深历史，回测/扫描复用）。
  - inject ：盘中每隔几分钟跑一次，短周期实盘扫描用滚动窗口（默认 300 根）。
"""

from __future__ import annotations

import argparse
import sys
import time

from future_data.cache import (
    DEFAULT_INJECT_LENGTH,
    cache_status,
    clear_cache,
    get_klines,
    inject_many,
    refresh_many,
)
from future_data.symbols import explain as explain_symbol
from future_data.universe import read_symbols


def _cmd_status(args) -> int:
    syms = read_symbols(table=args.table, limit=args.limit)
    df = cache_status(syms, period=args.period)
    if df.empty:
        print("（无品种/无缓存）")
        return 0
    fresh = int(df["fresh"].sum()) if "fresh" in df.columns else 0
    print(f"品种数: {len(df)}  缓存命中: {int(df['cached'].sum())}  新鲜(<TTL): {fresh}")
    print(df.to_string(index=False))
    return 0


def _cmd_refresh(args) -> int:
    period = args.period
    length = args.length

    if args.symbol:
        print(f"刷新单个: {args.symbol}({args.exchange}) {period}min x{length}")
        t0 = time.time()
        df = get_klines(
            args.symbol, args.exchange, period=period, length=length, force=True
        )
        print(f"  -> {len(df)} bars, {time.time()-t0:.1f}s")
        return 0

    syms = read_symbols(table=args.table, limit=args.limit)
    print(f"预热全品种: {len(syms)} 个, {period}min x{length}（单连接批量）")
    t0 = time.time()
    out = refresh_many(syms, period=period, length=length)
    dt = time.time() - t0
    print(f"完成: 成功 {len(out)}/{len(syms)}, 耗时 {dt:.1f}s（{(dt/len(syms)):.2f}s/品种）")
    missing = [s for s, _, _ in syms if s not in out]
    if missing:
        print(f"失败品种: {missing}")
    return 0


def _cmd_inject(args) -> int:
    """注入模式：增量拉新数据注入缓存、删最老的，滚动窗口恒定 length 根。

    首次（缓存不存在）自动全量拉 length 根建仓；之后每次只增量拉少量新数据。
    适合短周期（5/15/30m）实盘扫描，盘中每隔几分钟跑一次即可。
    """
    period = args.period
    length = args.length

    syms = read_symbols(table=args.table, limit=args.limit)
    print(f"注入模式: {len(syms)} 个品种, {period}min 滚动窗口 {length} 根（单连接批量）")
    t0 = time.time()
    out = inject_many(syms, period=period, length=length, inject_size=args.inject_size)
    dt = time.time() - t0
    print(f"完成: 成功 {len(out)}/{len(syms)}, 耗时 {dt:.1f}s（{(dt/len(syms)):.2f}s/品种）")
    missing = [s for s, _, _ in syms if s not in out]
    if missing:
        print(f"失败品种: {missing}")
    return 0


def _cmd_clear(args) -> int:
    if args.symbol:
        n = clear_cache(symbol=args.symbol, period=args.period)
    else:
        n = clear_cache(period=args.period)
    print(f"删除缓存文件: {n}")
    return 0


def _cmd_explain(args) -> int:
    print(explain_symbol(args.symbol, args.exchange))
    return 0


def main() -> int:
    p = argparse.ArgumentParser(
        prog="future_data",
        description="统一期货行情数据入口（tqsdk 后端 + TTL 缓存）",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    ps = sub.add_parser("status", help="查看缓存新鲜度")
    ps.add_argument("--period", default="15")
    ps.add_argument("--table", default="futures_top40")
    ps.add_argument("--limit", type=int, default=None)
    ps.set_defaults(func=_cmd_status)

    pr = sub.add_parser("refresh", help="预热/刷新缓存（盘前跑一次）")
    pr.add_argument("--period", default="15")
    pr.add_argument("--length", type=int, default=200)
    pr.add_argument("--table", default="futures_top40")
    pr.add_argument("--limit", type=int, default=None)
    pr.add_argument("--symbol", default=None, help="只刷新单个品种")
    pr.add_argument("--exchange", default=None, help="配合 --symbol")
    pr.set_defaults(func=_cmd_refresh)

    pi = sub.add_parser("inject", help="注入模式：增量注入新数据、滚动窗口（短周期实盘扫描用）")
    pi.add_argument("--period", default="15")
    pi.add_argument(
        "--length",
        type=int,
        default=DEFAULT_INJECT_LENGTH,
        help=f"滚动窗口大小（默认 {DEFAULT_INJECT_LENGTH} 根）",
    )
    pi.add_argument(
        "--inject-size",
        type=int,
        default=None,
        help="每次增量拉的根数（默认按周期自动取，约 1.5 小时量）",
    )
    pi.add_argument("--table", default="futures_top40")
    pi.add_argument("--limit", type=int, default=None)
    pi.set_defaults(func=_cmd_inject)

    pc = sub.add_parser("clear", help="清空缓存")
    pc.add_argument("--symbol", default=None)
    pc.add_argument("--period", default=None)
    pc.set_defaults(func=_cmd_clear)

    pe = sub.add_parser("explain", help="解释符号解析结果")
    pe.add_argument("symbol")
    pe.add_argument("exchange")
    pe.set_defaults(func=_cmd_explain)

    pb = sub.add_parser("bench", help="性能基准对比（akshare vs tqsdk vs 缓存）")
    pb.add_argument("--limit", type=int, default=10, help="品种数（默认 10，跑全量改大）")
    pb.set_defaults(func=lambda a: _cmd_bench(a))

    args = p.parse_args()
    return args.func(args)


def _cmd_bench(args) -> int:
    """性能基准：对比 akshare 逐品种 / tqsdk 批量 / 缓存命中 三种路径。"""
    from future_data.bench_cache import run_benchmark

    run_benchmark(n_symbols=args.limit)
    return 0


if __name__ == "__main__":
    sys.exit(main())
