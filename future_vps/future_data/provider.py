"""行情 provider 分发层。

默认后端：**xtquant**（迅投 token 模式，直连行情服务器）。
可通过环境变量切换：

    FUTURE_DATA_BACKEND=xtquant   # 默认，迅投 token 模式
    FUTURE_DATA_BACKEND=akshare   # 新浪期货 K 线（备份，无需账号）

公开 API（schema 固定）：
    fetch_kline(symbol, exchange, period, length) -> DataFrame
    fetch_many(symbols, period, length)           -> dict[str, DataFrame]

路径策略（VPS 友好）：缓存目录由 future_data.paths.cache_dir() 推导
（默认 ``<future_vps>/quote_cache``，环境变量 QUOTE_CACHE_DIR 可覆盖）。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from future_data.paths import cache_dir
from future_data.symbols import resolve_xt_symbol

# ---------------------------------------------------------------- defaults
# 全系统共享缓存目录（相对 future_vps 根推导，环境变量 QUOTE_CACHE_DIR 可覆盖）。
DEFAULT_CACHE_DIR = cache_dir()
DEFAULT_PERIOD = "15"
DEFAULT_LENGTH = 200
DEFAULT_TTL_HOURS = 2
DEFAULT_WAIT_TIMEOUT = 20.0
# 默认 xtquant；设 FUTURE_DATA_BACKEND=akshare 可切到新浪备份
DEFAULT_BACKEND = os.environ.get("FUTURE_DATA_BACKEND", "xtquant").strip().lower()

# 规范化后的列契约（全系统统一）
OHLC_COLUMNS = ("open", "high", "low", "close")


def normalize_kline(raw: pd.DataFrame) -> pd.DataFrame:
    """把原始 K 线 DataFrame 规范化为 [datetime, open, high, low, close, volume]。

    保留向后兼容：原 tqsdk normalize_kline 的调用方仍可使用。
    内部委托 xtquant_provider.normalize_xtquant。
    """
    from future_data.xtquant_provider import normalize_xtquant

    return normalize_xtquant(raw)


def get_backend() -> str:
    """当前行情后端：xtquant | akshare。"""
    return os.environ.get("FUTURE_DATA_BACKEND", DEFAULT_BACKEND).strip().lower() or "xtquant"


def fetch_kline_xtquant(
    symbol: str,
    exchange: str,
    period: str = DEFAULT_PERIOD,
    length: int = DEFAULT_LENGTH,
    **_kw,
) -> pd.DataFrame:
    """拉取单品种 K 线（xtquant）。委托到 xtquant_provider。"""
    from future_data.xtquant_provider import fetch_kline_xtquant as _fetch

    return _fetch(symbol, exchange, period=period, length=length)


def fetch_many_xtquant(
    symbols: list[tuple[str, str, str]],
    period: str = DEFAULT_PERIOD,
    length: int = DEFAULT_LENGTH,
    verbose: bool = False,
    **_kw,
) -> dict[str, pd.DataFrame]:
    """批量拉取（xtquant 单连接）。委托到 xtquant_provider。"""
    from future_data.xtquant_provider import fetch_many_xtquant as _fetch

    return _fetch(symbols, period=period, length=length, verbose=verbose)


def fetch_kline(
    symbol: str,
    exchange: str,
    period: str = DEFAULT_PERIOD,
    length: int = DEFAULT_LENGTH,
    auth=None,
    wait_timeout: float = DEFAULT_WAIT_TIMEOUT,
    backend: str | None = None,
) -> pd.DataFrame:
    """拉取单品种 K 线（默认 xtquant）。

    Parameters
    ----------
    symbol : "RB0" / "RB2610"
    exchange : cffex/shfe/...（akshare 可忽略，保留兼容）
    period : "15"/"30"/"60"/"1440"
    length : 最近根数
    backend : 覆盖环境变量，"xtquant" | "akshare"
    auth, wait_timeout : 向后兼容参数（xtquant 不需要，忽略）
    """
    be = (backend or get_backend()).lower()
    if be in ("xt", "xtquant", "qmt", "thinktrader", "迅投"):
        return fetch_kline_xtquant(symbol, exchange, period=period, length=length)
    if be in ("ak", "akshare", "sina"):
        from future_data.akshare_provider import fetch_kline_akshare

        return fetch_kline_akshare(symbol, exchange, period=period, length=length)
    raise ValueError(f"未知 FUTURE_DATA_BACKEND={be!r}，请用 xtquant 或 akshare")


def fetch_many(
    symbols: list[tuple[str, str, str]],
    period: str = DEFAULT_PERIOD,
    length: int = DEFAULT_LENGTH,
    auth=None,
    wait_timeout: float = DEFAULT_WAIT_TIMEOUT,
    backend: str | None = None,
    verbose: bool = False,
) -> dict[str, pd.DataFrame]:
    """批量拉取。默认 xtquant（单次连接）。"""
    be = (backend or get_backend()).lower()
    if be in ("xt", "xtquant", "qmt", "thinktrader", "迅投"):
        return fetch_many_xtquant(
            symbols, period=period, length=length, verbose=verbose
        )
    if be in ("ak", "akshare", "sina"):
        from future_data.akshare_provider import fetch_many_akshare

        return fetch_many_akshare(
            symbols, period=period, length=length, verbose=verbose
        )
    raise ValueError(f"未知 FUTURE_DATA_BACKEND={be!r}，请用 xtquant 或 akshare")


@dataclass
class DataProvider:
    """有状态的封装：缓存/回测场景用，普通扫描直接用 get_klines/fetch_many 即可。

    旧名 TqSdkProvider 保留为别名（= DataProvider），便于平滑替换。
    """

    period: str = DEFAULT_PERIOD
    data_length: int = DEFAULT_LENGTH
    cache_dir: Path = field(default_factory=lambda: DEFAULT_CACHE_DIR)
    auth: object | None = None
    wait_timeout: float = DEFAULT_WAIT_TIMEOUT

    def fetch_kline(self, symbol, exchange, period=None, length=None):
        period = period or self.period
        length = length or self.data_length
        return fetch_kline(
            symbol, exchange, period, length, auth=self.auth, wait_timeout=self.wait_timeout
        )

    def fetch_many(self, symbols, period=None, length=None):
        period = period or self.period
        length = length or self.data_length
        return fetch_many(
            symbols, period, length, auth=self.auth, wait_timeout=self.wait_timeout
        )

    # ----- 缓存层（"回测快照"语义：skip_if_cached 时不再联网）-----
    def cache_path(self, symbol: str, period: str | None = None) -> Path:
        period = period or self.period
        safe = symbol.replace("/", "_")
        return self.cache_dir / f"{safe}_{period}m.parquet"

    def fetch_and_cache(
        self,
        symbols: list[tuple[str, str, str]],
        period: str | None = None,
        length: int | None = None,
        skip_if_cached: bool = True,
    ) -> dict[str, pd.DataFrame]:
        """批量拉取并落盘。skip_if_cached=True 时已有缓存文件就只读盘。"""
        period = period or self.period
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        out: dict[str, pd.DataFrame] = {}
        to_fetch: list[tuple[str, str, str]] = []
        for sym, name, ex in symbols:
            path = self.cache_path(sym, period)
            if skip_if_cached and path.exists():
                out[sym] = pd.read_parquet(path)
            else:
                to_fetch.append((sym, name, ex))

        if to_fetch:
            fetched = self.fetch_many(to_fetch, period=period, length=length)
            for sym, df in fetched.items():
                df.to_parquet(self.cache_path(sym, period))
                out[sym] = df
        return out

    def load_cache(self, symbol: str, period: str | None = None) -> pd.DataFrame | None:
        path = self.cache_path(symbol, period)
        return pd.read_parquet(path) if path.exists() else None


# 向后兼容别名：旧代码 from future_data import TqSdkProvider
TqSdkProvider = DataProvider
