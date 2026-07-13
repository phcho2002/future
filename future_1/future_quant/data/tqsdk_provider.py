"""Data provider for futures K-line data.

历史
====
本模块原本是 future_quant 自带的 tqsdk 封装（symbol 构造 / 单/批量订阅 /
parquet 缓存）。现已把这些逻辑统一到全系统共享的 ``future_data`` 包。
本文件是一个**兼容垫片**，保留原有的公开名（``TqSdkProvider`` /
``build_tq_symbol`` / ``DEFAULT_CACHE_DIR`` 等），底层全部委托给 ``future_data``
（当前默认后端为 xtquant / 迅投 token 模式），这样 engine / backtest /
各 scan 脚本无需改动 import 即可受益于统一入口（含 datetime 修复、TTL 缓存、
具体合约解析）。

为什么用 sys.path.insert：future_quant 以 editable 安装在 future_1 的 venv 里，
但它只把 future_quant 自身加入路径，不会带上 work_ai 根目录，所以这里显式补上，
保证从任意 cwd 启动都能 import 到 sibling 包 future_data。
"""

from __future__ import annotations

import sys
from pathlib import Path

# 让 sibling 包 future_data 可被 import（脚本所在目录的父目录即 work_ai 根目录）
_WORK_AI = Path(__file__).resolve().parents[3]  # .../future_1/future_quant/data -> .../work_ai
if str(_WORK_AI) not in sys.path:
    sys.path.insert(0, str(_WORK_AI))

try:
    from future_data import (  # noqa: E402  (sys.path 调整后才可 import)
        DEFAULT_CACHE_DIR as _UNIFIED_CACHE_DIR,
        EXCHANGE_MAP,
        DataProvider as _UnifiedProvider,
        TqSdkProvider as _UnifiedProviderLegacy,
        build_tq_symbol,
        build_xt_symbol,
        fetch_kline,
        fetch_many,
    )
    from future_data.provider import (  # noqa: E402
        DEFAULT_LENGTH,
        DEFAULT_PERIOD,
        DEFAULT_WAIT_TIMEOUT,
        normalize_kline,
    )
    _HAS_FUTURE_DATA = True
except Exception:
    _HAS_FUTURE_DATA = False
    _UNIFIED_CACHE_DIR = None
    EXCHANGE_MAP = {}
    _UnifiedProvider = None
    _UnifiedProviderLegacy = None
    build_tq_symbol = None
    build_xt_symbol = None
    fetch_kline = None
    fetch_many = None
    DEFAULT_LENGTH = 8000
    DEFAULT_PERIOD = "15"
    DEFAULT_WAIT_TIMEOUT = 30
    normalize_kline = None

# ---------------------------------------------------------------- public API
DEFAULT_CACHE_DIR = _UNIFIED_CACHE_DIR if _HAS_FUTURE_DATA else "data_cache"

# DataProvider 是新名；TqSdkProvider 是向后兼容别名（两者指向同一类）。
if _HAS_FUTURE_DATA:
    DataProvider = _UnifiedProvider
    TqSdkProvider = _UnifiedProviderLegacy
else:
    DataProvider = None
    TqSdkProvider = None

UPPERCASE_EXCHANGES = {"cffex", "czce"}


def _load_token():
    """兼容入口：委托给 future_data.xtquant_provider._load_token。"""
    from future_data.xtquant_provider import _load_token as _impl

    return _impl()


def _kline_to_df(raw):
    """兼容入口：委托给 future_data.normalize_kline。"""
    return normalize_kline(raw)


__all__ = [
    "DataProvider",
    "TqSdkProvider",
    "build_tq_symbol",
    "build_xt_symbol",
    "fetch_kline",
    "fetch_many",
    "normalize_kline",
    "_load_token",
    "_kline_to_df",
    "EXCHANGE_MAP",
    "UPPERCASE_EXCHANGES",
    "DEFAULT_CACHE_DIR",
    "DEFAULT_PERIOD",
    "DEFAULT_LENGTH",
    "DEFAULT_WAIT_TIMEOUT",
]
