"""future_2 的 data provider —— 指向 future_data 的兼容垫片。

历史
====
本模块原本是 future_2/wyckoff_quant 自己的一份 tqsdk 封装，与 future_1 的
实现高度重复。现已统一到全系统共享的 D:/work_ai/future_data 包（当前默认
后端为 xtquant / 迅投 token 模式）。

本文件保留原公开名（build_tq_symbol / fetch_kline），底层委托给 future_data，
使 scanner.py / 各入口脚本无需改动 import 即可受益于统一入口。
"""

from __future__ import annotations

import sys
from pathlib import Path

# D:/work_ai 是 future_2 与 future_data 的共同父目录
_WORK_AI = Path(__file__).resolve().parents[3]  # .../future_2/wyckoff_quant/data -> D:/work_ai
if str(_WORK_AI) not in sys.path:
    sys.path.insert(0, str(_WORK_AI))

from future_data import (  # noqa: E402
    EXCHANGE_MAP,
    build_tq_symbol,
    build_xt_symbol,
    fetch_kline,
    fetch_many,
)
from future_data.provider import (  # noqa: E402
    normalize_kline,
)


def _load_token():
    """兼容入口：委托给 future_data.xtquant_provider._load_token。"""
    from future_data.xtquant_provider import _load_token as _impl

    return _impl()


# 兼容：原模块顶层定义的 UPPERCASE_EXCHANGES（future_2 老代码若引用）
UPPERCASE_EXCHANGES = {"cffex", "czce"}


__all__ = [
    "_load_token",
    "build_tq_symbol",
    "build_xt_symbol",
    "fetch_kline",
    "fetch_many",
    "normalize_kline",
    "EXCHANGE_MAP",
    "UPPERCASE_EXCHANGES",
]
