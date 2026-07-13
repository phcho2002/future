"""
主程序入口
提供命令行接口
"""

import argparse
from datetime import datetime
from strategy import BreakoutStrategy
import config


def analyze_command(args):
    """分析单个品种"""
    strategy = BreakoutStrategy()

    df = strategy.analyze_symbol(
        symbol=args.symbol,
        start_date=args.start_date
    )

    if not df.empty and df['final_signal'].any():
        signals = df[df['final_signal']]
        print(f"\n{'='*60}")
        print(f"发现 {len(signals)} 个交易信号")
        print(f"{'='*60}")

        print(f"\n最近5个信号:")
        print(signals[['date', 'close', 'volume', 'total_score', 'atr']].tail().to_string())
    else:
        print("\n未发现交易信号")


def scan_command(args):
    """扫描品种池"""
    strategy = BreakoutStrategy()

    # 确定扫描品种
    if args.symbols:
        symbols = args.symbols.split(',')
    elif args.all:
        symbols = config.FUTURES_UNIVERSE
    else:
        # 默认扫描部分活跃品种
        symbols = ["RB0", "HC0", "I0", "J0", "JM0", "ZC0", "MA0", "TA0", "PP0", "RU0"]

    signals_df = strategy.scan_universe(
        symbols=symbols,
        start_date=args.start_date
    )

    if not signals_df.empty:
        print(f"\n{'='*60}")
        print(f"发现的交易信号 (按评分排序)")
        print(f"{'='*60}")

        # 显示结果
        display_cols = ['symbol', 'date', 'close', 'total_score', 'volume', 'atr']
        print(signals_df[display_cols].head(20).to_string(index=False))

        # 保存到文件
        if args.output:
            signals_df.to_csv(args.output, index=False)
            print(f"\n结果已保存至: {args.output}")
    else:
        print("\n未发现交易信号")


def backtest_command(args):
    """运行回测"""
    strategy = BreakoutStrategy()

    # 确定回测品种
    if args.symbols:
        symbols = args.symbols.split(',')
    elif args.all:
        symbols = config.FUTURES_UNIVERSE
    else:
        # 默认回测部分品种
        symbols = ["RB0", "HC0", "I0", "J0", "JM0"]

    results = strategy.backtest(
        symbols=symbols,
        start_date=args.start_date,
        end_date=args.end_date,
        initial_capital=args.capital
    )

    if not results:
        print("回测失败")


def live_command(args):
    """获取实盘信号"""
    strategy = BreakoutStrategy()

    # 确定监控品种
    if args.symbols:
        symbols = args.symbols.split(',')
    elif args.all:
        symbols = config.FUTURES_UNIVERSE
    else:
        symbols = ["RB0", "HC0", "I0", "J0", "JM0", "ZC0", "MA0", "TA0", "PP0", "RU0"]

    print(f"\n{'='*60}")
    print(f"获取最新交易信号 (前 {args.top} 个)")
    print(f"{'='*60}")

    signals = strategy.get_latest_signals(symbols=symbols, top_n=args.top)

    if not signals.empty:
        print(f"\n【最新信号】")
        display_cols = ['symbol', 'date', 'close', 'total_score']
        print(signals[display_cols].to_string(index=False))

        # 显示详细信息
        if args.detail:
            print(f"\n【详细信息】")
            for idx, row in signals.iterrows():
                print(f"\n{row['symbol']}:")
                print(f"  日期: {row['date']}")
                print(f"  收盘价: {row['close']:.2f}")
                print(f"  评分: {row['total_score']:.0f}")
                print(f"  ATR: {row.get('atr', 0):.2f}")
                print(f"  成交量: {row.get('volume', 0):,.0f}")

                # 计算建议入场价和止损
                if 'resistance_level' in row:
                    print(f"  突破位: {row['resistance_level']:.2f}")
    else:
        print("\n当前无交易信号")


def main():
    parser = argparse.ArgumentParser(
        description='期货突破交易策略',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 分析单个品种
  python main.py analyze RB0

  # 扫描品种池
  python main.py scan --symbols RB0,HC0,I0

  # 扫描所有品种
  python main.py scan --all

  # 运行回测
  python main.py backtest --symbols RB0,HC0 --start-date 20240101

  # 获取实盘信号
  python main.py live --top 10 --detail
        """
    )

    subparsers = parser.add_subparsers(dest='command', help='命令')

    # analyze 命令
    analyze_parser = subparsers.add_parser('analyze', help='分析单个品种')
    analyze_parser.add_argument('symbol', help='品种代码 (如 RB0)')
    analyze_parser.add_argument('--start-date', default='20240101', help='开始日期 (默认: 20240101)')

    # scan 命令
    scan_parser = subparsers.add_parser('scan', help='扫描品种池')
    scan_parser.add_argument('--symbols', help='品种列表 (逗号分隔，如 RB0,HC0,I0)')
    scan_parser.add_argument('--all', action='store_true', help='扫描所有品种')
    scan_parser.add_argument('--start-date', default='20240101', help='开始日期 (默认: 20240101)')
    scan_parser.add_argument('--output', help='输出文件路径 (CSV格式)')

    # backtest 命令
    backtest_parser = subparsers.add_parser('backtest', help='运行回测')
    backtest_parser.add_argument('--symbols', help='品种列表 (逗号分隔)')
    backtest_parser.add_argument('--all', action='store_true', help='回测所有品种')
    backtest_parser.add_argument('--start-date', default='20200101', help='开始日期 (默认: 20200101)')
    backtest_parser.add_argument('--end-date', help='结束日期 (默认: 至今)')
    backtest_parser.add_argument('--capital', type=float, default=100000, help='初始资金 (默认: 100000)')

    # live 命令
    live_parser = subparsers.add_parser('live', help='获取实盘信号')
    live_parser.add_argument('--symbols', help='品种列表 (逗号分隔)')
    live_parser.add_argument('--all', action='store_true', help='监控所有品种')
    live_parser.add_argument('--top', type=int, default=10, help='显示前N个信号 (默认: 10)')
    live_parser.add_argument('--detail', action='store_true', help='显示详细信息')

    args = parser.parse_args()

    if args.command == 'analyze':
        analyze_command(args)
    elif args.command == 'scan':
        scan_command(args)
    elif args.command == 'backtest':
        backtest_command(args)
    elif args.command == 'live':
        live_command(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
