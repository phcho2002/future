"""Parquet cache management for backtest / grid search.

Keeps backtests reproducible and avoids hammering TqSdk: data is fetched once
into parquet snapshots, then re-read for every parameter combination.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from future_quant.data.tqsdk_provider import DEFAULT_CACHE_DIR, TqSdkProvider
from future_quant.data.universe import DEFAULT_JSON_PATH, load_top40_tuples

DEFAULT_DB_PATH = Path("D:/work_ai/futures_data.db")


@dataclass
class CacheManager:
    """Build and read the parquet symbol cache used by backtests."""

    cache_dir: Path = field(default_factory=lambda: DEFAULT_CACHE_DIR)
    period: str = "15"
    db_path: Path = field(default_factory=lambda: DEFAULT_DB_PATH)

    def load_symbols(self, limit: int | None = None) -> list[tuple[str, str, str]]:
        """Read the ``(symbol, name, exchange)`` universe from futures_top40.json."""
        return load_top40_tuples(DEFAULT_JSON_PATH, limit=limit)

    def build_cache(
        self,
        symbols: list[tuple[str, str, str]] | None = None,
        length: int = 2000,
        provider: TqSdkProvider | None = None,
    ) -> dict[str, pd.DataFrame]:
        """Fetch all symbols once and persist to parquet.

        TqSdk trial mode returns up to 8964 bars per request, so ``length`` of
        a few thousand bars (a few months of 15-min data) is safe.
        """
        provider = provider or TqSdkProvider(period=self.period, cache_dir=self.cache_dir)
        symbols = symbols if symbols is not None else self.load_symbols()
        return provider.fetch_and_cache(symbols, period=self.period, length=length)

    def load(self, symbol: str) -> pd.DataFrame | None:
        provider = TqSdkProvider(period=self.period, cache_dir=self.cache_dir)
        return provider.load_cache(symbol, period=self.period)

    def load_all(self, symbols: list[tuple[str, str, str]] | None = None) -> dict[str, pd.DataFrame]:
        """Load whatever is cached; symbols without a cache file are skipped."""
        symbols = symbols if symbols is not None else self.load_symbols()
        out: dict[str, pd.DataFrame] = {}
        for sym, _name, _ex in symbols:
            df = self.load(sym)
            if df is not None and not df.empty:
                out[sym] = df
        return out
