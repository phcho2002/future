"""TTL parquet 缓存层 —— 全系统统一的分钟 K 线入口。

底层联网默认 **xtquant**（迅投 token 模式，见 provider.get_backend / FUTURE_DATA_BACKEND），
也可切 akshare（备份）。缓存目录全系统共享（D:/work_ai/quote_cache）。

主入口：
    get_klines(symbol, exchange, period, length, ttl_hours, force)
        -> DataFrame[datetime, open, high, low, close, volume]

语义：缓存命中且未过期 → 直接读盘；否则联网刷新并写盘。
"""

from __future__ import annotations

import time
from pathlib import Path

import pandas as pd

from future_data.provider import (
    DEFAULT_CACHE_DIR,
    DEFAULT_LENGTH,
    DEFAULT_PERIOD,
    DEFAULT_TTL_HOURS,
    fetch_kline,
    fetch_many,
)

OHLC_COLUMNS = ("datetime", "open", "high", "low", "close", "volume")


def _cache_path(cache_dir: Path, symbol: str, period: str) -> Path:
    safe = symbol.replace("/", "_")
    return cache_dir / f"{safe}_{period}m.parquet"


# ---------------------------------------------------------------- injection
# 注入模式（rolling merge）：与 TTL 全量缓存写不同文件，避免互相覆盖。
#   - TTL 全量（回测深历史用）：  quote_cache/{symbol}_{period}m.parquet
#   - 注入滚动窗口（实盘扫描用）：quote_cache/inject/{symbol}_{period}m.parquet
DEFAULT_INJECT_LENGTH = 300  # 滚动窗口默认保留最近 300 根


def _inject_cache_path(cache_dir: Path, symbol: str, period: str) -> Path:
    """注入模式的缓存路径（独立 inject/ 子目录，与 TTL 全量缓存隔离）。"""
    safe = symbol.replace("/", "_")
    return cache_dir / "inject" / f"{safe}_{period}m.parquet"


def _default_inject_size(period: str) -> int:
    """增量注入根数：约覆盖 1.5 小时（90 分钟）的新数据，保证接上且防漏。

    5m → 18 根 / 15m → 6 根 / 30m → 3 根 / 60m → 2 根
    """
    try:
        mins = int(period)
    except (TypeError, ValueError):
        mins = 15
    return max(1, -(-90 // mins))  # ceil(90/period)


def _merge_rolling(
    cached: pd.DataFrame | None, fresh: pd.DataFrame, length: int = DEFAULT_INJECT_LENGTH
) -> pd.DataFrame:
    """把新拉到的 fresh 合并进 cached，按 datetime 去重（保留 fresh 即最新值，
    处理未收盘的当前K线），升序排序后只保留最新 ``length`` 根。

    即「注入新数据、删最老的」：返回的行数恒定为 length（数据充足时）。
    """
    pieces = [p for p in (cached, fresh) if p is not None and not p.empty]
    if not pieces:
        return pd.DataFrame(columns=list(OHLC_COLUMNS))
    merged = pd.concat(pieces, ignore_index=True)
    # 同一 datetime 出现多次时，保留 fresh 的版本（覆盖 cached 里的旧值，含未收盘K线）
    merged = merged.drop_duplicates(subset=["datetime"], keep="last")
    merged = (
        merged.sort_values("datetime")
        .reset_index(drop=True)
        .tail(length)
        .reset_index(drop=True)
    )
    return merged


def _is_fresh(path: Path, ttl_hours: float) -> bool:
    if not path.exists():
        return False
    age_hours = (time.time() - path.stat().st_mtime) / 3600.0
    return age_hours < ttl_hours


def load_cached(
    symbol: str,
    period: str = DEFAULT_PERIOD,
    cache_dir: Path | str | None = None,
) -> pd.DataFrame | None:
    """只读缓存，不联网。命中返回 DataFrame，否则 None。"""
    cdir = Path(cache_dir) if cache_dir else DEFAULT_CACHE_DIR
    path = _cache_path(cdir, symbol, period)
    if not path.exists():
        return None
    try:
        return pd.read_parquet(path)
    except Exception:
        return None


def get_klines(
    symbol: str,
    exchange: str,
    period: str = DEFAULT_PERIOD,
    length: int = DEFAULT_LENGTH,
    ttl_hours: float = DEFAULT_TTL_HOURS,
    force: bool = False,
    cache_dir: Path | str | None = None,
) -> pd.DataFrame:
    """全系统统一入口：TTL 缓存优先，过期/缺失/force 时联网刷新。

    Parameters
    ----------
    symbol, exchange, period, length : 同 future_data.fetch_kline。
    ttl_hours : float
        缓存新鲜期（小时）。盘前/盘中拉一次，期内全系统复用。
        默认 2 小时；盘后批量回测可设很大（如 9999）以纯读盘。
    force : bool
        True 时无视缓存直接联网刷新（强制更新用）。
    cache_dir : 覆盖默认缓存目录。

    Returns
    -------
    DataFrame[datetime, open, high, low, close, volume]

    Examples
    --------
    >>> df = get_klines("RB0", "shfe", period="15")           # 命中缓存或刷新
    >>> df = get_klines("RB0", "shfe", period="15", force=True)  # 强制刷新
    """
    cdir = Path(cache_dir) if cache_dir else DEFAULT_CACHE_DIR
    cdir.mkdir(parents=True, exist_ok=True)
    path = _cache_path(cdir, symbol, period)

    if not force and _is_fresh(path, ttl_hours):
        try:
            df = pd.read_parquet(path)
            if df is not None and not df.empty:
                return df
        except Exception:
            pass  # 缓存损坏 → 重新拉

    df = fetch_kline(symbol, exchange, period=period, length=length)
    try:
        df.to_parquet(path, index=False)
    except Exception:
        pass  # 写盘失败不影响返回
    return df


def refresh_klines(
    symbol: str,
    exchange: str,
    period: str = DEFAULT_PERIOD,
    length: int = DEFAULT_LENGTH,
    cache_dir: Path | str | None = None,
) -> pd.DataFrame:
    """强制联网刷新单个品种并写盘（等价于 get_klines(force=True)。"""
    return get_klines(
        symbol, exchange, period=period, length=length, force=True, cache_dir=cache_dir
    )


def refresh_many(
    symbols: list[tuple[str, str, str]],
    period: str = DEFAULT_PERIOD,
    length: int = DEFAULT_LENGTH,
    cache_dir: Path | str | None = None,
) -> dict[str, pd.DataFrame]:
    """批量强制刷新（单连接），用于盘前预热全品种。

    与 get_klines 不同：此函数总是联网，跳过 TTL 判断，适合一次性预热。
    """
    cdir = Path(cache_dir) if cache_dir else DEFAULT_CACHE_DIR
    cdir.mkdir(parents=True, exist_ok=True)
    fetched = fetch_many(symbols, period=period, length=length)
    for sym, df in fetched.items():
        try:
            df.to_parquet(_cache_path(cdir, sym, period), index=False)
        except Exception:
            pass
    return fetched


def clear_cache(
    symbol: str | None = None,
    period: str | None = None,
    cache_dir: Path | str | None = None,
) -> int:
    """清理缓存。返回删除的文件数。

    不带参数：清空整个 cache_dir。
    带 symbol：删该品种（可再带 period 缩小范围）。
    """
    cdir = Path(cache_dir) if cache_dir else DEFAULT_CACHE_DIR
    if not cdir.exists():
        return 0
    n = 0
    if symbol is None:
        for p in cdir.glob("*.parquet"):
            p.unlink()
            n += 1
    else:
        pattern = f"{symbol.replace('/', '_')}_*.parquet"
        if period:
            pattern = f"{symbol.replace('/', '_')}_{period}m.parquet"
        for p in cdir.glob(pattern):
            p.unlink()
            n += 1
    return n


def cache_status(
    symbols: list[tuple[str, str, str]] | None = None,
    period: str = DEFAULT_PERIOD,
    cache_dir: Path | str | None = None,
) -> pd.DataFrame:
    """返回各品种缓存新鲜度报告，用于预热前检查。

    列：symbol, name, cached(bool), age_hours, fresh(bool)
    """
    cdir = Path(cache_dir) if cache_dir else DEFAULT_CACHE_DIR
    rows = []
    if symbols is None:
        for p in sorted(cdir.glob("*.parquet")):
            age = (time.time() - p.stat().st_mtime) / 3600.0
            rows.append({"file": p.name, "age_hours": round(age, 2)})
        return pd.DataFrame(rows)

    for sym, name, _ex in symbols:
        path = _cache_path(cdir, sym, period)
        if path.exists():
            age = (time.time() - path.stat().st_mtime) / 3600.0
            rows.append(
                {
                    "symbol": sym,
                    "name": name,
                    "cached": True,
                    "age_hours": round(age, 2),
                    "fresh": age < DEFAULT_TTL_HOURS,
                }
            )
        else:
            rows.append(
                {"symbol": sym, "name": name, "cached": False, "age_hours": None, "fresh": False}
            )
    return pd.DataFrame(rows)


# ================================================================ injection
# 注入模式（rolling merge）：短周期实盘扫描专用。
# 语义：读一部分新数据注入缓存、删最老的，滚动窗口恒定 length 根（默认 300）。
# 与 get_klines 的区别：
#   - get_klines（TTL）：缓存命中直接读盘，过期才整盘联网覆盖（深历史回测用）。
#   - inject_klines：每次都只增量拉 inject_size 根新数据，merge 进缓存，永不整盘刷新。
# 缓存文件落在 inject/ 子目录，与 TTL 全量缓存隔离，避免互相覆盖。


def inject_klines(
    symbol: str,
    exchange: str,
    period: str = DEFAULT_PERIOD,
    length: int = DEFAULT_INJECT_LENGTH,
    inject_size: int | None = None,
    cache_dir: Path | str | None = None,
) -> pd.DataFrame:
    """注入模式：增量拉新数据注入缓存，滚动保留最近 ``length`` 根。

    首次（缓存文件不存在）：全量拉 ``length`` 根建仓。
    之后每次：读缓存 → 仅增量拉 ``inject_size`` 根新数据 → merge（丢最老的）→ 写回。

    Parameters
    ----------
    length : int
        滚动窗口大小（默认 300 根）。窗口恒定，注入新K线的同时删等量最老的。
    inject_size : int | None
        每次增量拉的根数；None 时按周期自动取（约 1.5 小时量：5m→18/15m→6/30m→3）。

    Returns
    -------
    DataFrame[datetime, open, high, low, close, volume] —— 注入后的滚动窗口。
    """
    cdir = Path(cache_dir) if cache_dir else DEFAULT_CACHE_DIR
    path = _inject_cache_path(cdir, symbol, period)
    path.parent.mkdir(parents=True, exist_ok=True)

    cached = None
    if path.exists():
        try:
            cached = pd.read_parquet(path)
        except Exception:
            cached = None  # 缓存损坏 → 视为首次，全量重建

    if cached is None or cached.empty:
        # 首次：全量拉满 length 根建仓
        df = fetch_kline(symbol, exchange, period=period, length=length)
    else:
        # 增量：只拉 inject_size 根新数据
        n = inject_size if inject_size and inject_size > 0 else _default_inject_size(period)
        fresh = fetch_kline(symbol, exchange, period=period, length=n)
        df = _merge_rolling(cached, fresh, length=length)

    try:
        df.to_parquet(path, index=False)
    except Exception:
        pass  # 写盘失败不影响返回
    return df


def inject_many(
    symbols: list[tuple[str, str, str]],
    period: str = DEFAULT_PERIOD,
    length: int = DEFAULT_INJECT_LENGTH,
    inject_size: int | None = None,
    cache_dir: Path | str | None = None,
) -> dict[str, pd.DataFrame]:
    """批量注入（**单连接**）：短周期实盘扫描的核心提速点。

    每个品种的注入逻辑同 :func:`inject_klines`，但联网部分用一次
    :func:`fetch_many` 连接完成，避免逐品种重连。

    策略：
      - 无缓存品种：记入 ``first_run``，统一全量拉 ``length`` 根建仓。
      - 有缓存品种：统一增量拉 ``inject_size`` 根，各自 merge 进缓存。

    Parameters
    ----------
    symbols : list[(symbol, name, exchange)]
    length : 滚动窗口大小（默认 300 根）。
    inject_size : 增量根数；None 时按周期自动取。
    """
    cdir = Path(cache_dir) if cache_dir else DEFAULT_CACHE_DIR
    n_inject = inject_size if inject_size and inject_size > 0 else _default_inject_size(period)

    out: dict[str, pd.DataFrame] = {}
    # 读已有缓存，区分首次/增量
    cached_map: dict[str, pd.DataFrame | None] = {}
    first_run: list[tuple[str, str, str]] = []  # 无缓存品种（需全量）
    delta_run: list[tuple[str, str, str]] = []  # 有缓存品种（需增量）
    for sym, name, ex in symbols:
        path = _inject_cache_path(cdir, sym, period)
        df_c = None
        if path.exists():
            try:
                df_c = pd.read_parquet(path)
            except Exception:
                df_c = None
        cached_map[sym] = df_c
        (delta_run if (df_c is not None and not df_c.empty) else first_run).append((sym, name, ex))

    # 首次全量建仓（一批）+ 增量刷新（一批），各用一次 fetch_many 连接
    if first_run:
        fetched = fetch_many(first_run, period=period, length=length)
        for sym, df in fetched.items():
            merged = _merge_rolling(None, df, length=length)
            out[sym] = merged
            try:
                merged.to_parquet(_inject_cache_path(cdir, sym, period), index=False)
            except Exception:
                pass
    if delta_run:
        fetched = fetch_many(delta_run, period=period, length=n_inject)
        for sym, df in fetched.items():
            merged = _merge_rolling(cached_map[sym], df, length=length)
            out[sym] = merged
            try:
                merged.to_parquet(_inject_cache_path(cdir, sym, period), index=False)
            except Exception:
                pass
    return out
