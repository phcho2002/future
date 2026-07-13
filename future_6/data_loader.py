"""
data_loader.py
==============
1. 从 D:/work_ai/futures_data.db 读取 futures_top40 品种表。
2. 通过 future_data 统一入口拉取 60 分钟 K 线（TTL 缓存优先）。
3. 规范化列名与类型，返回 {symbol: DataFrame}。
"""
from __future__ import annotations

import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Optional

import pandas as pd
import yaml

_WORK_AI = Path(__file__).resolve().parents[1]
if str(_WORK_AI) not in sys.path:
    sys.path.insert(0, str(_WORK_AI))

try:
    from future_data import get_klines
    from future_data.universe import build_exchange_map

    _HAS_FUTURE_DATA = True
except Exception:  # noqa: BLE001
    _HAS_FUTURE_DATA = False


def fetch_klines_akshare(
    symbol: str,
    exchange: Optional[str] = None,
    period: str = "60",
    length: int = 2000,
) -> pd.DataFrame:
    """通过 akshare/Sina 拉取单品种 K 线。"""
    from akshare_provider import fetch_klines_akshare as _ak_fetch

    return _ak_fetch(symbol, exchange=exchange, period=period, length=length)


def load_config(path: str = "config.yaml") -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def read_symbols(cfg: Optional[dict] = None, table: Optional[str] = None) -> pd.DataFrame:
    """返回 DataFrame[symbol, name, exchange]"""
    cfg = cfg or load_config()
    dc = cfg["data"]
    table = table or dc["symbol_table"]
    db_path = dc.get("db_path", "futures_data.db")
    conn = sqlite3.connect(db_path)
    try:
        df = pd.read_sql_query(
            f'SELECT "{dc["symbol_col"]}" AS symbol, '
            f'"{dc["name_col"]}" AS name, '
            f'"{dc["exchange_col"]}" AS exchange '
            f'FROM "{table}" ORDER BY 排名',
            conn,
        )
    finally:
        conn.close()
    return df


def _normalize_klines(df: pd.DataFrame) -> pd.DataFrame:
    """统一列名/类型，并按时间升序"""
    df = df.copy()
    df.columns = [c.lower() for c in df.columns]
    if not pd.api.types.is_datetime64_any_dtype(df["datetime"]):
        df["datetime"] = pd.to_datetime(df["datetime"])
    for c in ["open", "high", "low", "close"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    if "volume" in df.columns:
        df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0)
    df = df.dropna(subset=["open", "high", "low", "close"]).sort_values("datetime").reset_index(drop=True)
    return df


def fetch_klines_tq(
    symbol: str,
    exchange: Optional[str] = None,
    period: str = "60",
    length: int = 2000,
    ttl_hours: float = 6,
    force: bool = False,
) -> pd.DataFrame:
    """通过 future_data 统一入口拉取单品种 K 线（xtquant 后端）。"""
    if not _HAS_FUTURE_DATA:
        raise RuntimeError("future_data 不可用，无法走 xtquant 路径")
    if exchange is None:
        exchange = build_exchange_map().get(symbol)
    if exchange is None:
        raise ValueError(f"无法为 {symbol} 解析 exchange，请显式传入")
    df = get_klines(symbol, exchange, period=period, length=length, ttl_hours=ttl_hours, force=force)
    return _normalize_klines(df)


def fetch_klines_bq(
    symbol: str,
    exchange: Optional[str] = None,
    period: str = "60",
    length: int = 2000,
    ttl_hours: float = 6,
    force: bool = False,
) -> pd.DataFrame:
    """通过 BigQuant DAI 后端拉取单品种 K 线（1m → 60m 重采样）。

    临时切换数据账号期间使用（账号 bq5wec8s）。详见 bigquant_provider.py。
    返回值与 tqsdk 路径完全一致，下游零感知。
    """
    if exchange is None:
        exchange = build_exchange_map().get(symbol)
    if exchange is None:
        raise ValueError(f"无法为 {symbol} 解析 exchange，请显式传入")
    from bigquant_provider import get_klines as bq_get_klines

    df = bq_get_klines(
        symbol, exchange, period=period, length=length, ttl_hours=ttl_hours, force=force
    )
    return _normalize_klines(df)


def load_klines(
    symbol: str,
    cfg: Optional[dict] = None,
    exchange: Optional[str] = None,
    force_refresh: bool = False,
) -> pd.DataFrame:
    """读取单品种 60m K 线：按 data.backend 路由，TTL 缓存优先。

    支持 backend:
      - "xtquant"   (默认) 经 future_data 走 xtquant（迅投 token 模式）
      - "akshare"   经 akshare/Sina 拉 K 线（备份）
    两条路径共用 D:/work_ai/quote_cache 缓存目录，切换 backend 不需清缓存。
    """
    cfg = cfg or load_config()
    dc = cfg["data"]
    period = dc["period"]
    ttl_hours = dc.get("cache_ttl_hours", 6)
    length = dc.get("data_length", 2000)

    backend = dc.get("backend", "xtquant")
    if backend == "akshare":
        if exchange is None:
            exchange = build_exchange_map().get(symbol)
        if exchange is None:
            raise ValueError(f"无法为 {symbol} 解析 exchange，请显式传入")
        return fetch_klines_akshare(
            symbol,
            exchange=exchange,
            period=period,
            length=length,
        )
    if backend in ("xtquant", "tqsdk") and _HAS_FUTURE_DATA:
        return fetch_klines_tq(
            symbol,
            exchange=exchange,
            period=period,
            length=length,
            ttl_hours=ttl_hours,
            force=force_refresh,
        )
    raise RuntimeError(f"不支持的数据后端: {backend}")


def load_all_klines(
    cfg: Optional[dict] = None,
    table: Optional[str] = None,
    limit: Optional[int] = None,
    progress: bool = True,
) -> dict[str, pd.DataFrame]:
    """批量读取，返回 {symbol: klines_df}，失败品种记录日志但不中断。"""
    cfg = cfg or load_config()
    syms = read_symbols(cfg, table)
    if limit:
        syms = syms.head(limit)
    out: dict[str, pd.DataFrame] = {}
    n = len(syms)
    for i, row in enumerate(syms.itertuples(index=False), 1):
        sym = row.symbol
        try:
            out[sym] = load_klines(sym, cfg, exchange=row.exchange)
            if progress:
                print(f"[{i}/{n}] {sym} {row.name}: {len(out[sym])} bars")
        except Exception as e:  # noqa: BLE001
            if progress:
                print(f"[{i}/{n}] {sym} {row.name}: SKIP ({e})")
    return out


if __name__ == "__main__":
    cfg = load_config()
    print("Symbols:", len(read_symbols(cfg)))
    df = load_klines("AU0", cfg)
    print(df.tail(3).to_string(index=False))
    print("cols:", list(df.columns), "rows:", len(df))
