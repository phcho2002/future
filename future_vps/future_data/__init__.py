"""future_data — 全系统统一的期货行情数据入口。

默认后端：**xtquant**（迅投 token 模式，直连行情服务器）。
备份后端：**akshare**（新浪），通过环境变量 ``FUTURE_DATA_BACKEND=akshare`` 切换。

    from future_data import get_klines
    df = get_klines("RB0", "shfe", period="60")  # 缓存优先，过期用 xtquant 刷新

返回契约：
    DataFrame[datetime, open, high, low, close, volume]

路径策略（VPS 友好）：所有运行时路径相对 future_vps 根推导，环境变量可覆盖。
    - 缓存目录：默认 ``<future_vps>/quote_cache``；环境变量 ``QUOTE_CACHE_DIR`` 可覆盖
    - 迅投 token：默认 ``<future_vps>/xt_token.py``；环境变量 ``XT_TOKEN_PATH`` 可覆盖
    - 品种表：默认 ``<future_vps>/futures_top40.json``；环境变量 ``FUTURES_TOP40_JSON`` 可覆盖
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
from future_data.universe import read_symbols, build_exchange_map, DEFAULT_JSON_PATH

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
    "DEFAULT_JSON_PATH",
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
