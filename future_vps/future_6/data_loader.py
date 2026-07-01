"""
data_loader.py
==============
1. 从 futures_top40.json 读取 futures_top40 品种表。
2. 通过 future_data 统一入口拉取 60 分钟 K 线（TTL 缓存优先），akshare 作回退。
3. 规范化列名与类型，返回 {symbol: DataFrame}。

VPS 版：品种表改读 JSON（无需 sqlite），路径相对 future_vps 根推导。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Optional

import pandas as pd
import yaml

_VPS_ROOT = Path(__file__).resolve().parents[1]  # future_6 -> future_vps
if str(_VPS_ROOT) not in sys.path:
    sys.path.insert(0, str(_VPS_ROOT))

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


def _resolve_symbols_json(cfg: dict) -> Path:
    """解析品种表 JSON 路径（相对 future_vps 根；默认 future_vps/futures_top40.json）。"""
    dc = cfg.get("data", {})
    rel = dc.get("symbols_json")
    if rel:
        p = Path(rel)
        if not p.is_absolute():
            p = _VPS_ROOT / p
        return p
    from future_data.paths import symbols_json as _default_json
    return _default_json()


def read_symbols(cfg: Optional[dict] = None, table: Optional[str] = None) -> pd.DataFrame:
    """返回 DataFrame[symbol, name, exchange]（按 排名 升序）。

    从 futures_top40.json 读取。``table`` 参数保留兼容旧调用，JSON 版本忽略它。
    """
    cfg = cfg or load_config()
    p = _resolve_symbols_json(cfg)
    with open(p, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict) and "symbols" in data:
        data = data["symbols"]
    rows = list(data)
    if rows and "排名" in rows[0]:
        rows = sorted(rows, key=lambda r: r.get("排名", 0))
    df = pd.DataFrame(rows)
    return df[["symbol", "name", "exchange"]]


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
    """通过 future_data 统一入口（tqsdk 后端）拉取单品种 K 线。"""
    if not _HAS_FUTURE_DATA:
        raise RuntimeError("future_data 不可用，无法走 tqsdk 路径")
    if exchange is None:
        exchange = build_exchange_map().get(symbol)
    if exchange is None:
        raise ValueError(f"无法为 {symbol} 解析 exchange，请显式传入")
    df = get_klines(symbol, exchange, period=period, length=length, ttl_hours=ttl_hours, force=force)
    return _normalize_klines(df)


def load_klines(
    symbol: str,
    cfg: Optional[dict] = None,
    exchange: Optional[str] = None,
    force_refresh: bool = False,
) -> pd.DataFrame:
    """读取单品种 60m K 线：按 data.backend 路由，TTL 缓存优先。

    支持 backend:
      - "tqsdk"  (默认) 经 future_data 走 tqsdk
      - "akshare" 经 akshare/Sina 拉（无需账号，作回退/备选）
    两条路径共用 future_vps/quote_cache 缓存目录，切换 backend 不需清缓存。
    """
    cfg = cfg or load_config()
    dc = cfg["data"]
    period = dc["period"]
    ttl_hours = dc.get("cache_ttl_hours", 6)
    length = dc.get("data_length", 2000)

    backend = dc.get("backend", "tqsdk")
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
    if backend == "tqsdk" and _HAS_FUTURE_DATA:
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
    print(read_symbols(cfg).head(3))
