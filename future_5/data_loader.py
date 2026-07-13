"""
data_loader.py
==============
1. 从 D:/work_ai/futures_data.db 读取品种表 (symbol / name / exchange)
2. 通过全系统统一入口 future_data.get_klines（xtquant 后端 + TTL 缓存）
   拉取小时线 K 线；失败可回退到 akshare。
3. 列契约：DataFrame[datetime, open, high, low, close, volume]

与 future_4 的差异：默认 period="60"（小时线），用于 Renko 砖块生成。
"""
from __future__ import annotations

import os
import sys
import sqlite3
import time
from pathlib import Path
from typing import Optional

import pandas as pd
import yaml

# 让 sibling 包 future_data（位于 D:/work_ai）可被 import
_WORK_AI = Path(__file__).resolve().parents[1]  # future_5 -> D:/work_ai
if str(_WORK_AI) not in sys.path:
    sys.path.insert(0, str(_WORK_AI))

try:
    from future_data import get_klines as _tq_get_klines
    from future_data.universe import build_exchange_map as _tq_exch_map
    _HAS_FUTURE_DATA = True
except Exception:  # noqa: BLE001
    _HAS_FUTURE_DATA = False


def load_config(path: str = "config.yaml") -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------
# 品种表
# ---------------------------------------------------------------------
def read_symbols(cfg: Optional[dict] = None, table: Optional[str] = None) -> pd.DataFrame:
    """返回 DataFrame[symbol, name, exchange]"""
    cfg = cfg or load_config()
    dc = cfg["data"]
    table = table or dc["symbol_table"]
    conn = sqlite3.connect(dc["db_path"])
    try:
        df = pd.read_sql_query(
            f'SELECT "{dc["symbol_col"]}" AS symbol, '
            f'"{dc["name_col"]}" AS name, '
            f'"{dc["exchange_col"]}" AS exchange '
            f'FROM "{table}"',
            conn,
        )
    finally:
        conn.close()
    return df


# ---------------------------------------------------------------------
# K 线拉取 + 缓存
# ---------------------------------------------------------------------
def _cache_path(cfg: dict, symbol: str) -> str:
    period = cfg["data"]["period"]
    return os.path.join(cfg["data"]["cache_dir"], f"{symbol}_{period}m.parquet")


def _cache_fresh(path: str, ttl_hours: float) -> bool:
    if not os.path.exists(path):
        return False
    age = (time.time() - os.path.getmtime(path)) / 3600.0
    return age < ttl_hours


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


def fetch_klines_ak(symbol: str, period: str = "60", retries: int = 2) -> pd.DataFrame:
    """akshare 拉取单品种小时线（回退路径；单次约 ~320 根）"""
    import akshare as ak
    last_err = None
    for i in range(retries + 1):
        try:
            df = ak.futures_zh_minute_sina(symbol=symbol, period=period)
            if df is None or len(df) == 0:
                raise ValueError(f"empty data for {symbol}")
            return _normalize_klines(df)
        except Exception as e:  # noqa: BLE001
            last_err = e
            time.sleep(0.8 * (i + 1))
    raise RuntimeError(f"fetch {symbol} failed after retries: {last_err}")


def fetch_klines_tq(
    symbol: str,
    exchange: Optional[str] = None,
    period: str = "60",
    length: int = 3000,
    ttl_hours: float = 6,
    force: bool = False,
) -> pd.DataFrame:
    """通过统一入口 future_data（xtquant 后端）拉取单品种小时线。

    exchange 缺省时从 DB 的 futures_top40 自动查（symbol->exchange）。
    返回规范化后的 DataFrame（datetime/open/high/low/close/volume）。
    """
    if not _HAS_FUTURE_DATA:
        raise RuntimeError("future_data 不可用，无法走 xtquant 路径")
    if exchange is None:
        exchange = _tq_exch_map().get(symbol)
    if exchange is None:
        raise ValueError(f"无法为 {symbol} 解析 exchange，请显式传入")
    df = _tq_get_klines(
        symbol, exchange, period=period, length=length,
        ttl_hours=ttl_hours, force=force,
    )
    return _normalize_klines(df)


def load_klines(
    symbol: str,
    cfg: Optional[dict] = None,
    use_cache: bool = True,
    force_refresh: bool = False,
    backend: str = "xtquant",
) -> pd.DataFrame:
    """读取单品种小时 K 线：缓存优先，超时或失败则联网刷新。"""
    cfg = cfg or load_config()
    dc = cfg["data"]
    period = dc["period"]
    ttl_hours = dc.get("cache_ttl_hours", 6)
    backend = dc.get("backend", backend)

    if backend in ("xtquant", "tqsdk") and _HAS_FUTURE_DATA:
        try:
            return fetch_klines_tq(
                symbol, period=period,
                length=dc.get("data_length", 3000),
                ttl_hours=ttl_hours,
                force=force_refresh,
            )
        except Exception as e:  # noqa: BLE001
            # xtquant 失败则回退 akshare
            print(f"  [warn] {symbol} xtquant 失败，尝试 akshare: {e}")

    # akshare 路径（含本地 parquet 缓存）
    os.makedirs(dc["cache_dir"], exist_ok=True)
    path = _cache_path(cfg, symbol)
    if use_cache and not force_refresh and _cache_fresh(path, dc["cache_ttl_hours"]):
        try:
            return pd.read_parquet(path)
        except Exception:  # noqa: BLE001
            pass
    df = fetch_klines_ak(symbol, period=period, retries=dc.get("fetch_retries", 2))
    try:
        df.to_parquet(path, index=False)
    except Exception:  # noqa: BLE001
        pass
    return df


def load_all_klines(
    cfg: Optional[dict] = None,
    table: Optional[str] = None,
    use_cache: bool = True,
    progress: bool = True,
) -> dict:
    """批量读取，返回 {symbol: klines_df}，失败品种跳过不中断"""
    cfg = cfg or load_config()
    syms = read_symbols(cfg, table)
    out = {}
    n = len(syms)
    for i, row in enumerate(syms.itertuples(index=False), 1):
        sym = row.symbol
        try:
            out[sym] = load_klines(sym, cfg, use_cache=use_cache)
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
    print(df.tail(3))
    print("cols:", list(df.columns), "rows:", len(df))
