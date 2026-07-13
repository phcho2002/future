"""
快速测试脚本
演示系统完整功能
"""

from strategy import BreakoutStrategy
import config

def main():
    print("="*70)
    print("期货突破交易系统 - 完整功能演示")
    print("="*70)

    strategy = BreakoutStrategy()

    # 测试1：分析单个品种（螺纹钢）
    print("\n【测试1】分析螺纹钢（RB0）- 2024年至今")
    print("-"*70)
    df = strategy.analyze_symbol("RB0", start_date="20240101")

    if not df.empty:
        # 显示数据概况
        print(f"\n数据概况:")
        print(f"  总K线数: {len(df)}")
        print(f"  日期范围: {df['date'].min()} - {df['date'].max()}")
        print(f"  价格范围: {df['close'].min():.2f} - {df['close'].max():.2f}")

        # 显示技术指标
        if 'ma20' in df.columns:
            latest = df.iloc[-1]
            print(f"\n最新技术指标 ({latest['date']}):")
            print(f"  收盘价: {latest['close']:.2f}")
            print(f"  MA20: {latest['ma20']:.2f}")
            print(f"  ATR: {latest['atr']:.2f}")
            print(f"  ADX: {latest['adx']:.2f}")

        # 显示突破信号
        if 'breakout_signal' in df.columns:
            breakouts = df[df['breakout_signal']]
            print(f"\n突破信号统计:")
            print(f"  总突破次数: {len(breakouts)}")

            if len(breakouts) > 0:
                print(f"\n最近3次突破:")
                for idx, row in breakouts.tail(3).iterrows():
                    print(f"  {row['date']}: 价格 {row['close']:.2f}, 成交量 {row['volume']:,.0f}")

        # 显示最终交易信号
        if 'final_signal' in df.columns:
            signals = df[df['final_signal']]
            if len(signals) > 0:
                print(f"\n[OK] 发现 {len(signals)} 个高质量交易信号")
                print("\n信号详情:")
                for idx, row in signals.tail(3).iterrows():
                    print(f"\n  日期: {row['date']}")
                    print(f"  价格: {row['close']:.2f}")
                    print(f"  评分: {row['total_score']:.0f}")
                    print(f"  ATR: {row['atr']:.2f}")
                    print(f"  突破位: {row.get('resistance_level', 0):.2f}")
            else:
                print(f"\n[NO] 未发现符合条件的交易信号")

    # 测试2：扫描多个品种
    print("\n\n【测试2】扫描品种池 - 黑色系品种")
    print("-"*70)
    symbols = ["RB0", "HC0", "I0", "J0", "JM0"]
    signals = strategy.scan_universe(symbols=symbols, start_date="20240101")

    if not signals.empty:
        print(f"\n[OK] 发现 {len(signals)} 个交易机会")
        print("\n按评分排序的信号:")
        display_cols = ['symbol', 'date', 'close', 'total_score', 'volume']
        print(signals[display_cols].head(10).to_string(index=False))
    else:
        print("\n[NO] 当前无交易信号")

    # 测试3：获取实盘信号
    print("\n\n【测试3】获取最新实盘信号")
    print("-"*70)
    latest_signals = strategy.get_latest_signals(symbols=symbols, top_n=5)

    if not latest_signals.empty:
        print(f"\n[OK] 最新交易信号 (前5个):")
        for idx, row in latest_signals.iterrows():
            print(f"\n  [{row['symbol']}]")
            print(f"    日期: {row['date']}")
            print(f"    价格: {row['close']:.2f}")
            print(f"    评分: {row['total_score']:.0f} 分")
            quality = "HIGH" if row['total_score'] >= 90 else "MEDIUM" if row['total_score'] >= 85 else "NORMAL"
            print(f"    质量: {quality}")
    else:
        print("\n[NO] 当前无最新信号")

    # 系统配置信息
    print("\n\n【系统配置】")
    print("-"*70)
    print(f"初始资金: {config.INITIAL_CAPITAL:,.0f} 元")
    print(f"初始仓位: {config.INITIAL_POSITION*100:.0f}%")
    print(f"最大仓位: {config.MAX_POSITION*100:.0f}%")
    print(f"单笔风险: {config.MAX_SINGLE_LOSS*100:.0f}%")
    print(f"最低评分: {config.MIN_ENTRY_SCORE} 分")
    print(f"监控品种数: {len(config.FUTURES_UNIVERSE)}")

    print("\n\n" + "="*70)
    print("测试完成！系统运行正常 [OK]")
    print("="*70)

    print("\n使用说明:")
    print("  python main.py analyze <品种>     # 分析单个品种")
    print("  python main.py scan --all         # 扫描所有品种")
    print("  python main.py live --top 10      # 获取实盘信号")
    print("  python main.py backtest --all     # 运行完整回测")
    print("\n详细文档请查看 README.md 和 USAGE.md")


if __name__ == "__main__":
    main()
