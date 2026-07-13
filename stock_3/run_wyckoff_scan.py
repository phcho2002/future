"""Entry script for A-Stock Wyckoff scanning using TDX data.

Usage:
    cd /d/work_ai/stock_3
    python run_wyckoff_scan.py
    python run_wyckoff_scan.py --lookback 30
    python run_wyckoff_scan.py --tdx-dir D:/new_tdx/vipdoc
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from stock_wyckoff.config import WyckoffConfig
from stock_wyckoff.scanner import scan_all_a_shares, write_outputs


def main():
    parser = argparse.ArgumentParser(
        description="威科夫量价分析 — A股全市场扫描系统",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python run_wyckoff_scan.py                    # 默认20个交易日
  python run_wyckoff_scan.py --lookback 30      # 最近30个交易日
  python run_wyckoff_scan.py --tdx-dir D:/new_tdx/vipdoc
        """
    )
    parser.add_argument(
        "--lookback",
        type=int,
        default=20,
        help="仅保留最近N个交易日的买入信号，默认20",
    )
    parser.add_argument(
        "--tdx-dir",
        type=str,
        default="D:/new_tdx/vipdoc",
        help="通达信数据目录，默认 D:/new_tdx/vipdoc",
    )
    parser.add_argument(
        "--data-years",
        type=int,
        default=2,
        help="加载历史数据年数，默认2年",
    )
    parser.add_argument(
        "--range-lookback",
        type=int,
        default=30,
        help="区间分析回看周期，默认30",
    )
    parser.add_argument(
        "--min-test",
        type=int,
        default=3,
        help="最少测试次数，默认3",
    )
    parser.add_argument(
        "--rsi-period",
        type=int,
        default=14,
        help="RSI周期，默认14",
    )
    parser.add_argument(
        "--long-rsi-max",
        type=float,
        default=40.0,
        help="做多RSI上限，默认40",
    )
    parser.add_argument(
        "--spring-bars",
        type=int,
        default=3,
        help="Spring收回最大K线数，默认3",
    )
    parser.add_argument(
        "--min-strength",
        type=str,
        default="弱",
        choices=["弱", "中", "强"],
        help="最低信号强度 (弱=全部/中/强)，默认弱",
    )

    args = parser.parse_args()

    # Build configuration from arguments
    config = WyckoffConfig(
        range_lookback=args.range_lookback,
        min_test_count=args.min_test,
        rsi_period=args.rsi_period,
        long_rsi_max=args.long_rsi_max,
        spring_recover_bars=args.spring_bars,
    )

    output_dir = Path(__file__).parent.resolve() / "output"

    print()
    print("╔══════════════════════════════════════════════════════════════════════════╗")
    print("║           威科夫量价分析系统 — A股全市场扫描 (Wyckoff Method)            ║")
    print("║                                                                          ║")
    print("║  策略: Spring(弹簧做多) — 假跌破支撑后迅速收回                           ║")
    print(f"║  数据源: 通达信日线数据 ({args.tdx_dir})                                ║")
    print(f"║  回看周期: {args.lookback:>3d}个交易日  历史数据: {args.data_years:>1d}年  最低强度: {args.min_strength:<2s}                ║")
    print("╚══════════════════════════════════════════════════════════════════════════╝")
    print()

    result = scan_all_a_shares(
        lookback_days=args.lookback,
        config=config,
        data_years=args.data_years,
        min_strength=args.min_strength,
        tdx_dir=args.tdx_dir,
    )

    write_outputs(result, output_dir, args.lookback)

    # Print signal summary
    if result.signals:
        print()
        print("╔══════════════════════════════════════════════════════════════════════════╗")
        print("║                    威科夫量价分析 — 买入信号列表                           ║")
        print("╚══════════════════════════════════════════════════════════════════════════╝")
        for i, s in enumerate(sorted(result.signals, key=lambda x: (x.strength, x.date), reverse=True), 1):
            print()
            print(f"  ┌── 信号 #{i} ──────────────────────────────────────────────────")
            print(f"  │ {s.symbol:6s} {s.name}  [{s.strength}]")
            print(f"  │ 触发日: {s.date}")
            print(f"  │ 阶段: {s.phase_desc}")
            print(f"  │ 入场: {s.entry:>10.2f}  止损: {s.stop:>10.2f}  目标: {s.target:>10.2f}")
            print(f"  │ RSI: {s.rsi:>8.1f}  支撑: {s.support:>10.2f}  阻力: {s.resistance:>10.2f}")
            print(f"  │ 测试: {s.test_count:>4d}次  收敛: {s.contraction_ratio:.3f}  量趋势: {s.volume_trend}")
            print(f"  │ 量枯竭: {'是' if s.volume_ok else '否'}  停止行为: {'是' if s.stop_ok else '否'}  放量: {'是' if s.volume_confirm else '否'}  背离: {'是' if s.effort_divergence else '否'}")
            print(f"  │ 逻辑: {s.reason}")
            print(f"  └────────────────────────────────────────────────────────────────")
    else:
        print()
        print("未检测到符合条件的Spring买入信号。")

    return 0


if __name__ == "__main__":
    sys.exit(main())
