"""双账本统一系统：趋势主信号 + 震荡旁路 + 环境闸。

账本 A（金融）：股指 + 国债，资金 400 万
账本 B（商品）：其余商品期货，资金 400 万
信号/风控同一套，仅品种池与资金隔离。
"""

from .books import BookId, BookConfig, BOOKS, split_universe
from .runner import SystemResult, run_system
from .ledger import TradeLedger, apply_system_ledger

__all__ = [
    "BookId",
    "BookConfig",
    "BOOKS",
    "split_universe",
    "SystemResult",
    "run_system",
    "TradeLedger",
    "apply_system_ledger",
]
