#!/usr/bin/env python
"""扫描入口：python run_scan.py

可选参数:
  --symbols 600519 000001   只扫指定股票（调试）
  --workers 4               并行进程数（默认从 config 读取）
  --serial                  串行扫描（调试）
"""
import argparse
import sys

from scanner import Scanner


def main():
    parser = argparse.ArgumentParser(description="假突破→真突破 股票日线扫描")
    parser.add_argument("--config", default="config.yaml", help="配置文件路径")
    parser.add_argument("--symbols", nargs="*", default=None, help="只扫指定股票代码")
    parser.add_argument("--workers", type=int, default=None, help="并行进程数")
    parser.add_argument("--serial", action="store_true", help="串行扫描（调试）")
    args = parser.parse_args()

    scanner = Scanner(args.config)
    workers = 1 if args.serial else args.workers
    scanner.scan_and_save(symbols=args.symbols, workers=workers)


if __name__ == "__main__":
    main()
