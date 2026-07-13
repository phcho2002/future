"""
N型主升浪交易系统 (N-Wave Trading System)
============================================

基于周线 N 型结构（L1 → H1 → L2 → 突破）的 A 股交易系统。

快速开始:
    >>> from config import Config
    >>> from data_utils import fetch_stock_hist, add_all_indicators
    >>> from n_pattern import detect_all_signals, get_latest_signal

    >>> cfg = Config()
    >>> df = fetch_stock_hist('600519', period='weekly')
    >>> df = add_all_indicators(df, cfg)
    >>> df = detect_all_signals(df, cfg)
    >>> signal = get_latest_signal(df, cfg)
    >>> print(f'评分: {signal.score} [{signal.grade}]')
"""

__version__ = '1.0.0'
__author__ = 'N-Wave Trading System'
