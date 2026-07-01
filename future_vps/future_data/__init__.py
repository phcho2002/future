"""future_data — 全系统统一的期货行情数据入口（tqsdk 后端 + TTL 缓存）。

设计目标
========
本系统原本有多个分散的行情数据入口（tqsdk / akshare），鉴权/连续合约映射/缓存
逻辑各抄一遍。本包把这些集中到一个入口：

    from future_data import get_klines          # 单品种
    df = get_klines("RB0", "shfe", period="15")  # TTL 缓存优先，过期才联网

    from future_data import fetch_many          # 批量（单连接）
    out = fetch_many([("RB0","螺纹","shfe"), ...], period="15")

返回契约（与原 future_1/future_2 tqsdk provider 完全一致，下游分析器零改动）：

    DataFrame[datetime, open, high, low, close, volume]
    - datetime : pandas Timestamp（注意：不是字符串，避免 str[:10] 陷阱）
    - open/high/low/close/volume : float64
    - 升序、整数 RangeIndex

支持两种输入符号风格：
    - 主力连续：  "RB0" / "AU0"  →  KQ.m@SHFE.rb  （futures_top40.json 里就是这种）
    - 具体合约：  "RB2610"      →  SHFE.rb2610   （小时分析/Albrooks 回测用）

路径策略（VPS 友好）：所有运行时路径相对 future_vps 根推导，环境变量可覆盖。
    - 缓存目录：默认 ``<future_vps>/quote_cache``；环境变量 ``QUOTE_CACHE_DIR`` 可覆盖
    - TqAuth：默认 ``<future_vps>/tq_auth.py``；环境变量 ``TQ_AUTH_PATH`` 可覆盖
    - 品种表：默认 ``<future_vps>/futures_top40.json``；环境变量 ``FUTURES_TOP40_JSON`` 可覆盖
"""

from future_data.symbols import resolve_symbol, build_tq_symbol, EXCHANGE_MAP
from future_data.provider import (
    TqSdkProvider,
    fetch_kline,
    fetch_many,
    normalize_kline,
    DEFAULT_CACHE_DIR,
    DEFAULT_PERIOD,
    DEFAULT_LENGTH,
    DEFAULT_TTL_HOURS,
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
    # 主入口
    "get_klines",
    "fetch_many",
    "fetch_kline",
    "refresh_klines",
    "load_cached",
    "clear_cache",
    # 注入模式（短周期实盘扫描）
    "inject_klines",
    "inject_many",
    "DEFAULT_INJECT_LENGTH",
    # 品种表
    "read_symbols",
    "build_exchange_map",
    "DEFAULT_JSON_PATH",
    # 符号解析
    "resolve_symbol",
    "build_tq_symbol",
    "EXCHANGE_MAP",
    # provider
    "TqSdkProvider",
    "normalize_kline",
    "DEFAULT_CACHE_DIR",
    "DEFAULT_PERIOD",
    "DEFAULT_LENGTH",
    "DEFAULT_TTL_HOURS",
]

__version__ = "0.2.0"
