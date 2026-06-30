"""future_data — 全系统统一的期货行情数据入口（tqsdk 后端 + TTL 缓存）。

设计目标
========
本系统原本有 8+ 个分散的行情数据入口（future_1/future_2 用 tqsdk，future_3/各顶层
脚本用 akshare），鉴权/连续合约映射/缓存逻辑各抄一遍。本包把这些集中到一个入口：

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
    - 主力连续：  "RB0" / "AU0"  →  KQ.m@SHFE.rb  （DB futures_top40 里就是这种）
    - 具体合约：  "RB2610"      →  SHFE.rb2610   （小时分析/Albrooks 回测用）

缓存默认目录 D:/work_ai/quote_cache，全系统共享，TTL 默认 6 小时。

背景
====
mootdx 期货行情已实测不可用（扩展市场接口失效），见 README.md「mootdx 测试结论」。
故继续用 tqsdk 作为后端。
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
from future_data.universe import read_symbols, build_exchange_map, DEFAULT_DB_PATH, DEFAULT_TABLE

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
    "DEFAULT_DB_PATH",
    "DEFAULT_TABLE",
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

__version__ = "0.1.0"
