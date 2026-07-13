"""
期货活跃前40 - 小时线技术分析

【已迁移】本文件原为独立实现，现已与 top40_daily_strength.py 合并为统一引擎
futures_strength_analysis.py。本文件保留为薄壳入口，行为等价于运行
`python futures_strength_analysis.py --period 60`。

五维评分体系（每维 0-20，总分 100）：
  1. K线突破质量  2. 回调软弱程度  3. 异常K线多空含义
  4. 多周期K线共振  5. 日内动能
"""
import os
import sys

sys.path.insert(0, r"d:\work_ai")


def main():
    from futures_strength_analysis import run
    # period=60 -> 小时线；表名前缀 hourly_*，与旧版兼容
    run(period="60")


def analyze_all():
    """旧入口名兼容（原 futures_hourly_analysis.py 调用的函数）。"""
    return main()


if __name__ == "__main__":
    main()
