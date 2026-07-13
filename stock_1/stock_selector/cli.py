from __future__ import annotations

import argparse
from pathlib import Path

from stock_selector.data import AkShareStockProvider
from stock_selector.engine import StockSelector


def _load_symbols(args: argparse.Namespace, provider: AkShareStockProvider) -> list[str]:
    symbols: list[str] = []
    if args.symbols:
        symbols.extend(item.strip() for item in args.symbols.split(",") if item.strip())
    if args.symbol_file:
        path = Path(args.symbol_file)
        symbols.extend(line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
    if args.all:
        symbols.extend(provider.all_a_share_symbols())

    unique: list[str] = []
    seen: set[str] = set()
    for symbol in symbols:
        normalized = symbol.zfill(6)
        if normalized not in seen:
            seen.add(normalized)
            unique.append(normalized)
    return unique


def main() -> None:
    parser = argparse.ArgumentParser(description="AkShare 日线股票缠论均线缠绕突破选股")
    parser.add_argument("--symbols", help="逗号分隔股票代码，例如 000001,600519")
    parser.add_argument("--symbol-file", help="股票代码文件，每行一个代码")
    parser.add_argument("--all", action="store_true", help="扫描 AkShare A 股全市场")
    parser.add_argument("--start-date", default="20200101", help="开始日期，格式 YYYYMMDD")
    parser.add_argument("--end-date", default="20991231", help="结束日期，格式 YYYYMMDD")
    parser.add_argument("--adjust", default="qfq", choices=["", "qfq", "hfq"], help="复权方式")
    parser.add_argument("--output-dir", default="output", help="输出目录，默认 ./output")
    args = parser.parse_args()

    provider = AkShareStockProvider(adjust=args.adjust)
    symbols = _load_symbols(args, provider)
    if not symbols:
        parser.error("请通过 --symbols、--symbol-file 或 --all 指定股票池")

    selector = StockSelector(provider)
    result = selector.analyze_symbols(symbols, args.start_date, args.end_date)
    selector.write_outputs(result, Path(args.output_dir))

    print(f"分析完成: 股票数={len(symbols)}, 信号数={len(result.signals)}, 错误数={len(result.errors)}")
    print(f"输出目录: {Path(args.output_dir).resolve()}")


if __name__ == "__main__":
    main()
