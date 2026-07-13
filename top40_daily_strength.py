"""
期货 Top40 日线级别强弱分析

【已迁移】本文件原为独立实现，现已与 futures_hourly_analysis.py 合并为统一引擎
futures_strength_analysis.py。本文件保留为薄壳入口，行为等价于运行
`python futures_strength_analysis.py --period 1440`。

读取 futures_data.db 中的 futures_top40 表，按 K 线五维强弱原则评分，
输出最强3、最弱3品种及操作建议。
"""
import sys

sys.path.insert(0, r"d:\work_ai")


def main():
    from futures_strength_analysis import run
    # period=1440 -> 日线；表名前缀 daily_*，与旧版兼容
    run(period="1440")


if __name__ == "__main__":
    main()
