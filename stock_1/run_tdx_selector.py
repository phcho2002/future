"""
使用通达信日线数据运行选股程序，输出信号仅保留最近30天内的买入信号。
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

from stock_selector.engine import SelectionResult, StockSelector
from stock_selector.tdx_data import TDXStockProvider


def main():
    output_dir = Path(__file__).parent.resolve() / "output"
    output_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now()
    cutoff = now - timedelta(days=30)
    cutoff_str = cutoff.strftime("%Y-%m-%d")

    print(f"[{now:%H:%M:%S}] 通达信日线选股启动")
    print(f"数据源: D:/new_tdx/vipdoc  |  输出: {output_dir}")
    print(f"信号过滤: 仅保留 {cutoff_str} 之后出现的买入信号")
    print()

    provider = TDXStockProvider()
    symbols = provider.all_a_share_symbols()
    print(f"共加载 {len(symbols)} 只股票")
    print()

    # 最近 2 年数据足够（最长均线参数 60 日）
    start_date = "20230101"
    end_date = "20991231"

    selector = StockSelector(provider)
    result = selector.analyze_symbols(symbols, start_date, end_date)

    # --- 过滤: 仅保留最近 30 天的信号和分析记录 ---
    filtered_signals = [s for s in result.signals if s.date >= cutoff_str]
    filtered_analyses = [a for a in result.analyses if a.date >= cutoff_str]

    filtered_result = SelectionResult(
        analyses=filtered_analyses,
        signals=filtered_signals,
        errors=result.errors,
    )

    # 写出（内部按 dataslot 结构输出 CSV）
    selector.write_outputs(filtered_result, output_dir)

    print(f"分析完成")
    print(f"  股票总数: {len(symbols)}")
    print(f"  原始信号: {len(result.signals)}")
    print(f"  最近30天信号: {len(filtered_signals)}  ← 写入 CSV")
    print(f"  最近30天分析记录: {len(filtered_analyses)}")
    print(f"  错误数: {len(result.errors)}")
    print()

    # 列出输出文件
    for f in sorted(output_dir.iterdir()):
        print(f"    {f.name} ({f.stat().st_size / 1024:.1f} KB)")

    # 打印表格预览
    if filtered_signals:
        print()
        print("最近30天买入信号（按日期排序）:")
        print(f"{'symbol':>8} {'signal_type':8} {'date':12} {'trigger':>8} {'confidence':6}  reason")
        print("-" * 100)
        for s in sorted(filtered_signals, key=lambda x: x.date, reverse=True)[:30]:
            print(f"{s.symbol:>8} {s.signal_type:8} {s.date:12} {s.trigger_price:>8.2f} {s.confidence:6}  {s.reason[:50]}")


if __name__ == "__main__":
    main()
