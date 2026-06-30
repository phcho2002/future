"""示例：顶层 akshare 分钟脚本改用统一入口（含 datetime bug 修复）。

覆盖 5 处 ak.futures_zh_minute_sina 替换 + futures_hourly_analysis 的 .str[:10] bug：
    - futures_30min_signals.py:144
    - futures_hourly_analysis.py:448  (+ L309/L380 datetime 修复)
    - future/futures_signal.py:190
    - future/run_futures_30m.py:158
    - future/backtest_albrooks.py:171
"""

from __future__ import annotations

import sys

sys.path.insert(0, "D:/work_ai")

import pandas as pd

from future_data import get_klines
from future_data.universe import read_symbols


# -----------------------------------------------------------------
# 工具：从 DB 查 symbol -> exchange 映射（顶层脚本原本只有 sina symbol）
# -----------------------------------------------------------------
def build_exchange_map() -> dict[str, str]:
    """返回 {symbol: exchange}，给那些只有 symbol 没有 exchange 的脚本用。"""
    return {sym: ex for sym, _name, ex in read_symbols()}


# -----------------------------------------------------------------
# 示例 1：futures_30min_signals.py:144 替换
# -----------------------------------------------------------------
def get_30min_kline_migrated(symbol: str, exchange: str, lookback: int = 300) -> pd.DataFrame:
    """原：ak.futures_zh_minute_sina(symbol=symbol, period='30') + df.tail(300)"""
    df = get_klines(symbol, exchange, period="30", length=lookback)
    return df.tail(lookback).reset_index(drop=True)


# -----------------------------------------------------------------
# 示例 2：futures_hourly_analysis.py:448 替换 + L309/L380 bug 修复
# -----------------------------------------------------------------
def get_60min_kline_migrated(contract: str, exchange: str) -> pd.DataFrame:
    """原：ak.futures_zh_minute_sina(symbol=contract, period='60')

    注意 contract 可能是具体合约如 'TA2609'，统一入口的 resolve_symbol 会自动识别。
    """
    return get_klines(contract, exchange, period="60", length=1000)


def safe_day_series(df_h: pd.DataFrame) -> pd.Series:
    """修复 futures_hourly_analysis.py L309/L380 的 datetime 字符串切片 bug。

    原（tqsdk 返回 Timestamp 会崩）：
        df_h['day'] = df_h['datetime'].str[:10]

    改（Timestamp 安全）：
    """
    dt = df_h["datetime"]
    if pd.api.types.is_datetime64_any_dtype(dt):
        return dt.dt.strftime("%Y-%m-%d")
    # 兜底：仍是字符串（旧缓存）的情况
    return dt.astype(str).str[:10]


# -----------------------------------------------------------------
# 示例 3：Albrooks 回测（run_futures_30m / backtest_albrooks）替换
# -----------------------------------------------------------------
def fetch_30min_for_albrooks(symbol: str, exchange: str) -> pd.DataFrame:
    """原：ak.futures_zh_minute_sina(symbol=contract, period='30')

    Albrooks engine 只要 OHLC 四列数组（high/low/close/open），不要 volume/datetime。
    统一入口返回的 DataFrame 直接切列即可。
    """
    df = get_klines(symbol, exchange, period="30", length=2000)
    # albrooks engine.analyze(high, low, close, open_) 取这四列
    return df[["high", "low", "close", "open"]].copy()


if __name__ == "__main__":
    ex_map = build_exchange_map()
    print("已加载 symbol->exchange 映射:", len(ex_map), "个")

    # 演示主力
    df = get_30min_kline_migrated("RB0", ex_map.get("RB0", "shfe"))
    print("RB0 30min:", len(df), "bars")
    print(df.tail(2))

    # 演示 datetime 安全的 day 序列
    print("day 列:", safe_day_series(df).tail(3).tolist())
