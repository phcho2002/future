#!/usr/bin/env python3
"""
N型主升浪交易系统 — 命令行入口
===================================

用法:
    # 分析单只股票
    python main.py analyze 000001

    # 回测单只股票
    python main.py backtest 600519

    # 批量选股
    python main.py screen

    # 选股（指定股票池）
    python main.py screen --pool hs300 --min-score 60

    # 选股（自定义列表）
    python main.py screen --symbols 000001,000002,600519,000858

    # 保存图表
    python main.py analyze 000001 --save chart.png

    # 设置周期
    python main.py analyze 000001 --period daily
"""

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

# 确保模块可导入
sys.path.insert(0, str(Path(__file__).parent))

from config import Config, get_aggressive_config, get_conservative_config
from data_utils import fetch_stock_hist, add_all_indicators
from n_pattern import detect_all_signals, get_latest_signal
from backtest import BacktestEngine
from screener import StockScreener


def cmd_analyze(args):
    """分析单只股票"""
    cfg = _get_config(args)

    print(f'正在获取 {args.symbol} 数据...')
    df = fetch_stock_hist(
        symbol=args.symbol,
        period=args.period,
        start_date='20180101',
        end_date='20991231',
        adjust=cfg.data.adjust,
        cache_dir=cfg.data.cache_dir,
    )

    if df is None or df.empty:
        print(f'❌ 获取 {args.symbol} 数据失败')
        return

    print(f'数据: {len(df)} 根K线 ({df.index[0]} ~ {df.index[-1]})')

    # 计算指标 + 信号
    df = add_all_indicators(df, cfg)
    df = detect_all_signals(df, cfg)

    # 最新信号
    signal = get_latest_signal(df, cfg)

    # 打印分析报告
    print(f'\n{"="*60}')
    print(f'  {args.symbol} — N型主升浪分析')
    print(f'{"="*60}')
    print(f'  最新日期:     {str(signal.date)[:10] if signal.date else "?"}')
    print(f'  收盘价:       {df["close"].iloc[-1]:.2f}')
    print(f'  综合评分:     {signal.score} 分 [{signal.grade}级]')
    print(f'')
    print(f'  N型结构:')
    print(f'    L1 (底部):  {signal.n_pattern.l1_price:.2f}' if not np.isnan(signal.n_pattern.l1_price) else '    L1: --')
    print(f'    H1 (前高):  {signal.n_pattern.h1_price:.2f}' if not np.isnan(signal.n_pattern.h1_price) else '    H1: --')
    print(f'    L2 (回调底):{signal.n_pattern.l2_price:.2f}' if not np.isnan(signal.n_pattern.l2_price) else '    L2: --')
    if signal.n_pattern.ready:
        print(f'    首波涨幅:   {signal.n_pattern.leg_pct:.1f}%')
        print(f'    回调幅度:   {signal.n_pattern.retrace_pct:.1f}%')
    print(f'    阶段:       {signal.n_pattern.phase}')
    print(f'    突破:       {"是 🔥" if signal.n_pattern.breakout else "否"}')
    print(f'')
    print(f'  技术条件:')
    print(f'    均线多头:   {"✅" if signal.ma_bullish else "❌"}')
    print(f'    RSI:        {df["rsi"].iloc[-1]:.1f} (健康: {"✅" if signal.rsi_healthy else "❌"})')
    print(f'    量比:       {df["vol_ratio"].iloc[-1]:.2f}x (放量: {"✅" if signal.vol_burst else "❌"})')
    print(f'    MACD多头:   {"✅" if signal.macd_bullish else "❌"}')
    print(f'')
    print(f'  交易决策:')
    if signal.buy_signal:
        print(f'    🔥 买入信号!')
        print(f'    入场价:     {signal.entry_price:.2f}')
        print(f'    止损:       {signal.stop_loss:.2f}')
        print(f'    止盈1:      {signal.tp1:.2f} (RR={cfg.risk.tp_rr_1})')
        print(f'    止盈2:      {signal.tp2:.2f} (RR={cfg.risk.tp_rr_2}')
    else:
        print(f'    暂无买入信号')
        if signal.n_pattern.ready:
            print(f'    等待突破 H1 ({signal.n_pattern.h1_price:.2f})')
    print(f'{"="*60}')

    # 图表
    if args.save:
        try:
            from visualize import plot_simple
            plot_simple(df.tail(200), title=f'{args.symbol} N型主升浪分析',
                        save_path=args.save)
        except ImportError as e:
            print(f'⚠ 图表模块导入失败: {e}')
    elif not args.no_plot:
        try:
            from visualize import plot_simple
            plot_simple(df.tail(200), title=f'{args.symbol} N型主升浪分析')
        except ImportError as e:
            print(f'⚠ 无法显示图表（缺少 matplotlib）: {e}')


def cmd_backtest(args):
    """回测单只股票"""
    cfg = _get_config(args)

    print(f'正在获取 {args.symbol} 数据...')
    df = fetch_stock_hist(
        symbol=args.symbol,
        period=args.period,
        start_date='20180101',
        end_date='20991231',
        adjust=cfg.data.adjust,
        cache_dir=cfg.data.cache_dir,
    )

    if df is None or df.empty:
        print(f'❌ 获取 {args.symbol} 数据失败')
        return

    # 计算指标
    df = add_all_indicators(df, cfg)

    print(f'数据: {len(df)} 根K线 ({df.index[0]} ~ {df.index[-1]})')
    print(f'正在回测...')

    engine = BacktestEngine(cfg)
    result = engine.run(df, symbol=args.symbol)

    print(engine.summary(result))

    # 保存详细交易记录
    if args.save_trades:
        import pandas as pd
        trades_data = [{
            'entry_date': t.entry_date,
            'exit_date': t.exit_date,
            'entry_price': t.entry_price,
            'exit_price': t.exit_price,
            'shares': t.shares,
            'pnl': t.pnl,
            'pnl_pct': t.pnl_pct,
            'exit_reason': t.exit_reason,
            'holding_bars': t.holding_bars,
            'score': t.score,
            'grade': t.grade,
        } for t in result.trades]
        pd.DataFrame(trades_data).to_csv(args.save_trades, index=False)
        print(f'交易记录已保存至: {args.save_trades}')

    # 权益曲线
    if args.save_equity:
        result.equity_curve.to_csv(args.save_equity)
        print(f'权益曲线已保存至: {args.save_equity}')


def cmd_screen(args):
    """批量选股"""
    cfg = _get_config(args)

    # 覆盖参数
    if args.min_score is not None:
        cfg.screener.min_score = args.min_score
    if args.pool is not None:
        cfg.screener.stock_pool = args.pool
    if args.top is not None:
        cfg.screener.top_n = args.top

    screener = StockScreener(cfg)

    # 自定义符号列表
    symbols = None
    if args.symbols:
        symbols = [s.strip() for s in args.symbols.split(',')]

    results = screener.run(symbols=symbols, show_progress=not args.no_progress)
    screener.print_results(results)

    # 保存结果
    if args.output:
        results.to_csv(args.output, index=False, encoding='utf-8-sig')
        print(f'结果已保存至: {args.output}')
    elif not results.empty and args.save:
        path = f'n_wave_screen_{pd.Timestamp.now().strftime("%Y%m%d")}.csv'
        results.to_csv(path, index=False, encoding='utf-8-sig')
        print(f'结果已保存至: {path}')


def _get_config(args) -> Config:
    """根据参数获取配置"""
    if hasattr(args, 'aggressive') and args.aggressive:
        return get_aggressive_config()
    if hasattr(args, 'conservative') and args.conservative:
        return get_conservative_config()
    return Config()


# ═══════════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description='N型主升浪交易系统',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python main.py analyze 600519              # 分析贵州茅台
  python main.py backtest 000001 --aggressive # 回测平安银行（激进参数）
  python main.py screen --pool hs300          # 筛选沪深300
  python main.py screen --symbols 000001,600519,000858  # 自定义列表
        """,
    )

    subparsers = parser.add_subparsers(dest='command', help='子命令')

    # --- analyze ---
    p_analyze = subparsers.add_parser('analyze', help='分析单只股票')
    p_analyze.add_argument('symbol', help='股票代码，如 000001, 600519')
    p_analyze.add_argument('--period', default='weekly',
                           choices=['daily', 'weekly', 'monthly'],
                           help='K线周期（默认weekly）')
    p_analyze.add_argument('--save', help='保存图表路径')
    p_analyze.add_argument('--no-plot', action='store_true', help='不显示图表')
    p_analyze.add_argument('--aggressive', action='store_true', help='激进参数')
    p_analyze.add_argument('--conservative', action='store_true', help='保守参数')

    # --- backtest ---
    p_bt = subparsers.add_parser('backtest', help='回测单只股票')
    p_bt.add_argument('symbol', help='股票代码')
    p_bt.add_argument('--period', default='weekly',
                      choices=['daily', 'weekly', 'monthly'],
                      help='K线周期（默认weekly）')
    p_bt.add_argument('--save-trades', help='保存交易记录CSV路径')
    p_bt.add_argument('--save-equity', help='保存权益曲线CSV路径')
    p_bt.add_argument('--aggressive', action='store_true', help='激进参数')
    p_bt.add_argument('--conservative', action='store_true', help='保守参数')

    # --- screen ---
    p_screen = subparsers.add_parser('screen', help='批量选股')
    p_screen.add_argument('--pool', choices=['all', 'hs300', 'zz500', 'custom'],
                          default='all', help='股票池（默认all）')
    p_screen.add_argument('--symbols', help='自定义股票列表，逗号分隔')
    p_screen.add_argument('--min-score', type=int, default=None,
                          help='最低评分（默认50）')
    p_screen.add_argument('--top', type=int, default=None,
                          help='显示前N只（默认20）')
    p_screen.add_argument('--output', help='输出CSV路径')
    p_screen.add_argument('--save', action='store_true',
                          help='自动保存到带日期的CSV')
    p_screen.add_argument('--no-progress', action='store_true',
                          help='不显示进度条')
    p_screen.add_argument('--aggressive', action='store_true', help='激进参数')
    p_screen.add_argument('--conservative', action='store_true', help='保守参数')

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        return

    try:
        import akshare  # noqa: F401
    except ImportError:
        print('❌ 请先安装 akshare: pip install akshare')
        print('   或安装所有依赖: pip install -r requirements.txt')
        return

    if args.command == 'analyze':
        cmd_analyze(args)
    elif args.command == 'backtest':
        cmd_backtest(args)
    elif args.command == 'screen':
        cmd_screen(args)


if __name__ == '__main__':
    main()
