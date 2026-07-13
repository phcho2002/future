"""Entry script for Wyckoff Method futures scanning using TqSDK.

Usage:
    cd /d/work_ai/future_2
    python tqsdk_wyckoff_scan.py --period 15
    python tqsdk_wyckoff_scan.py --period 60
    python tqsdk_wyckoff_scan.py --period 240
"""
import argparse
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from wyckoff_quant.config import WyckoffConfig
from wyckoff_quant.scanner import scan_all


def main():
    parser = argparse.ArgumentParser(
        description="威科夫量价分析 — 期货扫描系统",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python tqsdk_wyckoff_scan.py --period 15    # 15分钟周期
  python tqsdk_wyckoff_scan.py --period 30    # 30分钟周期
  python tqsdk_wyckoff_scan.py --period 60    # 1小时周期
  python tqsdk_wyckoff_scan.py --period 240   # 4小时周期
        """
    )
    parser.add_argument(
        "--period",
        type=str,
        default="15",
        help="K线周期（分钟），默认15",
    )
    parser.add_argument(
        "--data-length",
        type=int,
        default=200,
        help="获取K线数量，默认200",
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
        "--short-rsi-min",
        type=float,
        default=60.0,
        help="做空RSI下限，默认60",
    )
    parser.add_argument(
        "--spring-bars",
        type=int,
        default=3,
        help="Spring收回最大K线数，默认3",
    )
    parser.add_argument(
        "--upthrust-bars",
        type=int,
        default=3,
        help="Upthrust回落最大K线数，默认3",
    )

    args = parser.parse_args()

    # Build configuration from arguments
    config = WyckoffConfig(
        range_lookback=args.range_lookback,
        min_test_count=args.min_test,
        rsi_period=args.rsi_period,
        long_rsi_max=args.long_rsi_max,
        short_rsi_min=args.short_rsi_min,
        spring_recover_bars=args.spring_bars,
        upthrust_recover_bars=args.upthrust_bars,
    )

    print()
    print("╔══════════════════════════════════════════════════════════════════════════╗")
    print("║           威科夫量价分析系统 (Wyckoff Method Quantitative)               ║")
    print("║                                                                          ║")
    print("║  策略: Spring(弹簧做多) / Upthrust(上冲回落做空)                         ║")
    print(f"║  周期: {args.period:>6s}分钟K线  数据长度: {args.data_length:>3d}根                                    ║")
    print("╚══════════════════════════════════════════════════════════════════════════╝")
    print()

    signals = scan_all(
        period=args.period,
        config=config,
        data_length=args.data_length,
    )

    return 0 if signals else 0


if __name__ == "__main__":
    sys.exit(main())
