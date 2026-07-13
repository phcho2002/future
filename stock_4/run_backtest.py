#!/usr/bin/env python
"""回测入口。

用法:
  python run_backtest.py --symbol 600519        单股回测（打印明细）
  python run_backtest.py --all                  全市场回测（输出汇总CSV）
  python run_backtest.py --symbols 600519 000001  批量回测
"""
import argparse

from backtest import BacktestRunner


def main():
    parser = argparse.ArgumentParser(description="假突破→真突破 股票日线回测")
    parser.add_argument("--config", default="config.yaml", help="配置文件路径")
    parser.add_argument("--symbol", default=None, help="单只股票代码")
    parser.add_argument("--symbols", nargs="*", default=None, help="多只股票代码")
    parser.add_argument("--all", action="store_true", help="全市场回测")
    args = parser.parse_args()

    runner = BacktestRunner(args.config)

    if args.all:
        runner.backtest_all()
    elif args.symbol:
        runner.backtest_symbol(args.symbol, verbose=True)
    elif args.symbols:
        rows = []
        for sym in args.symbols:
            _, stats = runner.backtest_symbol(sym, verbose=False)
            if stats:
                rows.append({"symbol": sym, **stats})
        if rows:
            import pandas as pd
            df = pd.DataFrame(rows).sort_values("total_pnl", ascending=False)
            print(df.to_string(index=False))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
