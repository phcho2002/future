"""future_data — 全系统统一的期货行情数据入口。

默认后端：**xtquant**（迅投 token 模式，直连行情服务器）。
备份后端：**akshare**（新浪），通过环境变量 ``FUTURE_DATA_BACKEND=akshare`` 切换。

    from future_data import get_klines
    df = get_klines("RB0", "shfe", period="60")  # 缓存优先，过期用 xtquant 刷新

返回契约：
    DataFrame[datetime, open, high, low, close, volume]
"""

from future_data.symbols import (
    resolve_xt_symbol,
    build_xt_symbol,
    resolve_symbol,
    build_tq_symbol,
    EXCHANGE_MAP,
)
from future_data.provider import (
    DataProvider,
    TqSdkProvider,
    fetch_kline,
    fetch_many,
    normalize_kline,
    get_backend,
    DEFAULT_CACHE_DIR,
    DEFAULT_PERIOD,
    DEFAULT_LENGTH,
    DEFAULT_TTL_HOURS,
    DEFAULT_BACKEND,
)
from future_data.cache import (
    get_klines,
    refresh_klines,
    load_cached,
    clear_cache,
    inject_klines,
    inject_many,
    DEFAULT_INJECT_LENGTH,
)
from future_data.universe import read_symbols, build_exchange_map, DEFAULT_DB_PATH, DEFAULT_TABLE

__all__ = [
    "get_klines",
    "fetch_many",
    "fetch_kline",
    "refresh_klines",
    "load_cached",
    "clear_cache",
    "inject_klines",
    "inject_many",
    "DEFAULT_INJECT_LENGTH",
    "read_symbols",
    "build_exchange_map",
    "DEFAULT_DB_PATH",
    "DEFAULT_TABLE",
    "resolve_xt_symbol",
    "build_xt_symbol",
    "resolve_symbol",
    "build_tq_symbol",
    "EXCHANGE_MAP",
    "DataProvider",
    "TqSdkProvider",
    "normalize_kline",
    "get_backend",
    "DEFAULT_BACKEND",
    "DEFAULT_CACHE_DIR",
    "DEFAULT_PERIOD",
    "DEFAULT_LENGTH",
    "DEFAULT_TTL_HOURS",
]

__version__ = "0.2.0"
