"""双账本：金融 400 万 / 商品 400 万。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

WORK_AI = Path(__file__).resolve().parents[1]
TOP40_JSON = WORK_AI / "futures_top40.json"

# 股指 + 国债（CFFEX 金融期货）
FINANCIAL_SYMBOLS = frozenset({
    "IM0", "IC0", "IF0", "IH0",  # 股指
    "TF0", "TS0",                 # 国债（5年/2年）
})


class BookId(str, Enum):
    FINANCIAL = "financial"   # 股指+国债
    COMMODITY = "commodity"   # 商品


@dataclass(frozen=True)
class BookConfig:
    book_id: BookId
    name: str
    capital: float
    # 趋势腿目标年化波动（主信号）
    trend_vol_annual: float
    # 震荡腿目标年化波动（旁路，更小）
    range_vol_annual: float
    symbols: frozenset[str]


# 默认：各 400 万；趋势腿约 12%、震荡腿约 5%（合计风险预算不过分叠加）
BOOKS: dict[BookId, BookConfig] = {
    BookId.FINANCIAL: BookConfig(
        book_id=BookId.FINANCIAL,
        name="金融（股指+国债）",
        capital=4_000_000.0,
        trend_vol_annual=0.12,
        range_vol_annual=0.05,
        symbols=FINANCIAL_SYMBOLS,
    ),
    BookId.COMMODITY: BookConfig(
        book_id=BookId.COMMODITY,
        name="商品",
        capital=4_000_000.0,
        trend_vol_annual=0.15,
        range_vol_annual=0.05,
        symbols=frozenset(),  # 运行时 = top40 - financial
    ),
}


def load_top40(path: Path | None = None) -> list[tuple[str, str, str]]:
    p = path or TOP40_JSON
    with open(p, encoding="utf-8") as f:
        raw = json.load(f)
    return [(a, b, c) for a, b, c in raw["symbols"]]


def split_universe(
    path: Path | None = None,
) -> dict[BookId, list[tuple[str, str, str]]]:
    """按账本拆分 top40。"""
    all_syms = load_top40(path)
    fin = [(s, n, e) for s, n, e in all_syms if s in FINANCIAL_SYMBOLS]
    com = [(s, n, e) for s, n, e in all_syms if s not in FINANCIAL_SYMBOLS]
    return {
        BookId.FINANCIAL: fin,
        BookId.COMMODITY: com,
    }


def book_config(book_id: BookId, path: Path | None = None) -> BookConfig:
    """返回带完整 symbol 集合的账本配置。"""
    base = BOOKS[book_id]
    uni = split_universe(path)[book_id]
    symbols = frozenset(s for s, _, _ in uni)
    return BookConfig(
        book_id=base.book_id,
        name=base.name,
        capital=base.capital,
        trend_vol_annual=base.trend_vol_annual,
        range_vol_annual=base.range_vol_annual,
        symbols=symbols,
    )
