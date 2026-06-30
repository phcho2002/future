"""TqSdk data provider for futures K-line data.

历史
====
本模块原本是 future_quant 自带的 tqsdk 封装（symbol 构造 / 单/批量订阅 /
parquet 缓存）。现已把这些逻辑统一到全系统共享的 ``D:/work_ai/future_data``
包（见 D:/work_ai/future_data/README.md）。本文件退化为一个**兼容垫片**，
保留原有的公开名（``TqSdkProvider`` / ``build_tq_symbol`` / ``DEFAULT_CACHE_DIR`` /
``_load_auth`` 等），底层全部委托给 ``future_data``，这样 engine / backtest /
各 scan 脚本无需改动 import 即可受益于统一入口（含 datetime 修复、TTL 缓存、
具体合约解析）。

为什么用 sys.path.insert：future_quant 以 editable 安装在 future_1 的 venv 里，
但它只把 future_quant 自身加入路径，不会带上 D:/work_ai，所以这里显式补上，
保证从任意 cwd 启动都能 import 到 sibling 包 future_data。
"""

from __future__ import annotations

import sys
from pathlib import Path

# 让 sibling 包 future_data 可被 import（D:/work_ai 是两者的父目录）
_WORK_AI = Path(__file__).resolve().parents[3]  # .../future_1/future_quant/data -> D:/work_ai
if str(_WORK_AI) not in sys.path:
    sys.path.insert(0, str(_WORK_AI))

try:
    from future_data import (  # noqa: E402  (sys.path 调整后才可 import)
        DEFAULT_CACHE_DIR as _UNIFIED_CACHE_DIR,
        EXCHANGE_MAP,
        TqSdkProvider as _UnifiedProvider,
        build_tq_symbol,
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
    build_tq_symbol = None
    fetch_kline = None
    fetch_many = None
    DEFAULT_LENGTH = 8000
    DEFAULT_PERIOD = "15"
    DEFAULT_WAIT_TIMEOUT = 30
    normalize_kline = None

# ---------------------------------------------------------------- public API
# 原 DEFAULT_CACHE_DIR 指向 future_1/data_cache；统一后改用全系统共享目录。
# 仍保留该名以兼容旧 import；如需读旧缓存可手动指向 future_1/data_cache。
DEFAULT_CACHE_DIR = _UNIFIED_CACHE_DIR if _HAS_FUTURE_DATA else "data_cache"

# TqSdkProvider：直接复用统一入口的实现（接口签名完全一致）。
# 用别名导出，保证 `from future_quant.data.tqsdk_provider import TqSdkProvider` 仍可用。
if _HAS_FUTURE_DATA:
    TqSdkProvider = _UnifiedProvider
else:
    TqSdkProvider = None

# UPPERCASE_EXCHANGES：统一入口的 symbols.py 里是私有名，这里为兼容老代码暴露出来
UPPERCASE_EXCHANGES = {"cffex", "czce"}


def _load_auth():
    """兼容入口：委托给 future_data.provider._load_auth。"""
    from future_data.provider import _load_auth as _impl

    return _impl()


def _kline_to_df(raw):
    """兼容入口：委托给 future_data.normalize_kline（已修复 datetime bug）。"""
    return normalize_kline(raw)


__all__ = [
    "TqSdkProvider",
    "build_tq_symbol",
    "fetch_kline",
    "fetch_many",
    "normalize_kline",
    "_load_auth",
    "_kline_to_df",
    "EXCHANGE_MAP",
    "UPPERCASE_EXCHANGES",
    "DEFAULT_CACHE_DIR",
    "DEFAULT_PERIOD",
    "DEFAULT_LENGTH",
    "DEFAULT_WAIT_TIMEOUT",
]
