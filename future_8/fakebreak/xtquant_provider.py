"""xtquant 行情数据适配层 —— future_8 标准格式。

替代原 tbpy_provider.py，把 future_data 统一入口（xtquant 后端）封装成
generate_signal() / generate_mtf_signal() 能直接吃的 DataFrame：
    升序排列 | 列: datetime, open, high, low, close, volume

数据源：xtquant token 模式（迅投行情），无需 tbquant3.exe 或 MiniQMT 客户端。
所有品种映射和联网逻辑委托给 future_data 统一入口。

与原 tbpy_provider 的接口完全兼容：
    get_klines_batch(symbols, freq, count) -> dict[str, DataFrame]   # 推荐
    get_kline(future8_symbol, freq, count) -> DataFrame
    fetch_many(symbols, freq, count, interval_sec) -> dict           # 兼容降级
"""
from __future__ import annotations

import time

import pandas as pd

# future_8 品种代码 → exchange 映射（从 futures_top40.json 提取）。
# future_data 统一入口需要 (symbol, name, exchange) 三元组，
# 扫描脚本只给品种代码（如 'PP0'），需由此补全 exchange。
_EXCHANGE_MAP = {
    # 中金所 CFFEX（金融）
    "IM": "cffex", "IC": "cffex", "IF": "cffex", "IH": "cffex",
    "TF": "cffex", "TS": "cffex", "T": "cffex", "TL": "cffex",
    # 上期所 SHFE（商品）
    "RB": "shfe", "HC": "shfe", "SS": "shfe", "CU": "shfe",
    "AL": "shfe", "ZN": "shfe", "PB": "shfe", "NI": "shfe",
    "SN": "shfe", "AU": "shfe", "AG": "shfe", "FU": "shfe",
    "RU": "shfe", "BU": "shfe", "BC": "shfe", "SP": "shfe",
    "AO": "shfe", "BR": "shfe",
    # 大商所 DCE（商品）
    "PP": "dce", "JM": "dce", "J": "dce", "M": "dce",
    "A": "dce", "C": "dce", "L": "dce", "P": "dce",
    "V": "dce", "I": "dce", "Y": "dce", "FB": "dce",
    "BB": "dce", "JD": "dce", "LH": "dce", "EG": "dce",
    "EB": "dce", "PG": "dce", "RR": "dce", "B": "dce",
    # 能源中心 INE（商品）
    "SC": "ine", "LU": "ine", "NR": "ine", "BC": "ine",
    # 广期所 GFEX（商品）
    "LC": "gfex", "SI": "gfex",
    # 郑商所 CZCE（商品）
    "MA": "czce", "TA": "czce", "SR": "czce", "CF": "czce",
    "CY": "czce", "AP": "czce", "CJ": "czce", "RI": "czce",
    "WH": "czce", "PM": "czce", "FG": "czce", "OI": "czce",
    "RM": "czce", "RS": "czce", "SF": "czce", "SM": "czce",
    "SA": "czce", "UR": "czce", "PF": "czce", "SH": "czce",
    "PK": "czce", "PX": "czce",
}

# future_8 freq 字符串 → 系统内部分钟周期
# tbpy 用 '15m'/'60m'/'1d'，future_data 用 '15'/'60'/'1440'
_FREQ_MAP = {
    "1m": "1", "5m": "5", "15m": "15", "30m": "30",
    "60m": "60", "1h": "60", "2h": "120",
    "1d": "1440", "day": "1440", "D": "1440",
}


def _resolve_exchange(future8_symbol: str) -> str | None:
    """从品种代码推断交易所（如 PP0 → dce）。"""
    s = future8_symbol.strip().upper().rstrip("0123456789")
    return _EXCHANGE_MAP.get(s)


def _freq_to_period(freq: str) -> str:
    """tbpy 风格 freq ('15m'/'60m') → 系统内部分钟周期 ('15'/'60')。"""
    f = freq.strip().lower()
    return _FREQ_MAP.get(f, "60")  # 默认 60 分钟


def _ensure_future_data():
    """导入 future_data 统一入口（补 sys.path）。"""
    import sys
    from pathlib import Path

    _here = Path(__file__).resolve()
    _work_ai = _here.parents[2]  # future_8/fakebreak/ → work_ai/
    if str(_work_ai) not in sys.path:
        sys.path.insert(0, str(_work_ai))
    from future_data.provider import fetch_many as _fetch_many
    from future_data.provider import fetch_kline as _fetch_kline
    return _fetch_many, _fetch_kline


def get_klines_batch(
    future8_symbols: list[str],
    freq: str = "60m",
    count: int = 300,
) -> dict[str, pd.DataFrame]:
    """一次性批量拉取多个品种（推荐）。

    委托 future_data.fetch_many（xtquant 后端单次连接），一次拿全部品种。

    Parameters
    ----------
    future8_symbols : 品种代码列表，如 ['PP0', 'IM0']
    freq : '15m' / '60m' / '1d'
    count : 拉取根数

    Returns
    -------
    dict[future8_symbol, DataFrame]，映射失败或无数据的品种不在结果中。
    """
    fetch_many, _ = _ensure_future_data()

    if not future8_symbols:
        return {}

    period = _freq_to_period(freq)
    # 构建 (symbol, name, exchange) 三元组
    tuples: list[tuple[str, str, str]] = []
    for sym in future8_symbols:
        ex = _resolve_exchange(sym)
        if ex is None:
            print(f"  [xtquant] {sym} 无法映射到交易所，跳过")
            continue
        tuples.append((sym, sym, ex))

    if not tuples:
        return {}

    try:
        out = fetch_many(tuples, period=period, length=count, verbose=False)
    except Exception as e:  # noqa: BLE001
        print(f"  [xtquant] 批量查询异常: {str(e)[:80]}")
        return {}

    return out


def get_kline(
    future8_symbol: str,
    freq: str = "60m",
    count: int = 300,
) -> pd.DataFrame:
    """拉取某品种主力连续 K 线，返回 future_8 标准 DataFrame。

    Parameters
    ----------
    future8_symbol : 品种代码，如 'IM0' 'PP0'
    freq : '15m' / '60m' / '1d'
    count : 要拉的根数

    Returns
    -------
    DataFrame[datetime, open, high, low, close, volume]，升序。
        失败返回空 DataFrame（列已就位）。
    """
    _, fetch_kline = _ensure_future_data()

    empty = pd.DataFrame(columns=["datetime", "open", "high", "low", "close", "volume"])
    ex = _resolve_exchange(future8_symbol)
    if ex is None:
        print(f"  [xtquant] {future8_symbol} 无法映射到交易所")
        return empty

    period = _freq_to_period(freq)
    try:
        df = fetch_kline(future8_symbol, ex, period=period, length=count)
        if df is None or df.empty:
            return empty
        return df
    except Exception as e:  # noqa: BLE001
        print(f"  [xtquant] {future8_symbol} 查询异常: {str(e)[:60]}")
        return empty


def fetch_many(
    symbols: list[str],
    freq: str = "60m",
    count: int = 300,
    interval_sec: float = 2.0,
) -> dict[str, pd.DataFrame]:
    """逐个拉取多个品种（兼容接口，实际委托 get_klines_batch）。

    保留与 tbpy_provider.fetch_many 相同的签名，方便直接替换。
    interval_sec 参数保留但被忽略（xtquant 无需限频）。
    """
    return get_klines_batch(symbols, freq=freq, count=count)
