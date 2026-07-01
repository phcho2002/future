"""
data_loader.py
==============
1. 从 futures_top40.json 读取品种表 (symbol / name / exchange)
2. 通过统一入口 future_data（tqsdk 后端 + TTL 缓存）拉取分钟 K 线；
   失败可回退到 akshare。
3. 本地 parquet 缓存（akshare 回退路径用），超时自动刷新

迁移说明（VPS 版）：
  品种表原从 SQLite (futures_data.db) 读取，现改为 futures_top40.json
  （位于 future_vps 根，部署无需 sqlite）。数据后端默认 tqsdk（全系统共享
  缓存 future_vps/quote_cache），akshare 路径保留为回退。所有路径相对
  future_vps 根推导，环境变量可覆盖。
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Optional

import pandas as pd
import yaml

# 让 sibling 包 future_data（位于 future_vps 根）可被 import
_VPS_ROOT = Path(__file__).resolve().parents[1]  # future_4 -> future_vps
if str(_VPS_ROOT) not in sys.path:
    sys.path.insert(0, str(_VPS_ROOT))

try:
    from future_data import get_klines as _tq_get_klines
    from future_data import inject_klines as _tq_inject_klines
    from future_data import inject_many as _tq_inject_many
    from future_data.universe import build_exchange_map as _tq_exch_map
    _HAS_FUTURE_DATA = True
except Exception:  # noqa: BLE001
    _HAS_FUTURE_DATA = False


def load_config(path: str = "config.yaml") -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _resolve_symbols_json(cfg: dict) -> Path:
    """解析品种表 JSON 路径。

    config 中 ``data.symbols_json`` 若是相对路径，则相对 future_vps 根；
    未配置时回退到 future_data.paths.symbols_json()（默认 future_vps/futures_top40.json）。
    """
    dc = cfg.get("data", {})
    rel = dc.get("symbols_json")
    if rel:
        p = Path(rel)
        if not p.is_absolute():
            p = _VPS_ROOT / p
        return p
    from future_data.paths import symbols_json as _default_json
    return _default_json()


# ---------------------------------------------------------------------
# 品种表
# ---------------------------------------------------------------------
def read_symbols(cfg: Optional[dict] = None, table: Optional[str] = None) -> pd.DataFrame:
    """返回 DataFrame[symbol, name, exchange]（按 排名 升序）。

    从 futures_top40.json 读取。``table`` 参数保留是为了兼容旧调用
    （run_scan/optimize 仍传 ``futures_top40``），JSON 版本下它不再区分表名。
    """
    cfg = cfg or load_config()
    p = _resolve_symbols_json(cfg)
    with open(p, encoding="utf-8") as f:
        data = json.load(f)
    # 兼容两种结构：直接 list[dict]，或 {"symbols": list[...]}。
    if isinstance(data, dict) and "symbols" in data:
        data = data["symbols"]
    rows = list(data)
    if rows and "排名" in rows[0]:
        rows = sorted(rows, key=lambda r: r.get("排名", 0))
    df = pd.DataFrame(rows)
    return df[["symbol", "name", "exchange"]]


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
    # akshare 返回 datetime 为字符串
    if not pd.api.types.is_datetime64_any_dtype(df["datetime"]):
        df["datetime"] = pd.to_datetime(df["datetime"])
    for c in ["open", "high", "low", "close"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    if "volume" in df.columns:
        df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0)
    if "hold" in df.columns:
        df["hold"] = pd.to_numeric(df["hold"], errors="coerce")
    df = df.dropna(subset=["open", "high", "low", "close"]).sort_values("datetime").reset_index(drop=True)
    return df


def fetch_klines_ak(symbol: str, period: str = "30", retries: int = 2) -> pd.DataFrame:
    """通过 akshare 拉取单品种分钟 K 线"""
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
    period: str = "30",
    length: int = 8000,
    ttl_hours: float = 6,
    force: bool = False,
) -> pd.DataFrame:
    """通过统一入口 future_data（tqsdk 后端）拉取单品种分钟 K 线。

    exchange 缺省时从品种表 JSON 自动查（symbol->exchange）。
    返回规范化后的 DataFrame（datetime/open/high/low/close/volume）。

    相对 akshare 的改进：tqsdk 可取深历史（~8964 根 vs sina ~320），
    TTL 缓存全系统共享。
    """
    if not _HAS_FUTURE_DATA:
        raise RuntimeError("future_data 不可用，无法走 tqsdk 路径")
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
    backend: str = "tqsdk",
) -> pd.DataFrame:
    """读取单品种 K 线：缓存优先，超时或失败则联网刷新。

    backend:
      - "tqsdk"（默认）：走统一入口 future_data（含其自身 TTL 缓存 + 全系统共享缓存）。
      - "akshare"：保留原 akshare 路径（兼容/回退用）。
    """
    cfg = cfg or load_config()
    dc = cfg["data"]
    period = dc["period"]
    ttl_hours = dc.get("cache_ttl_hours", 6)
    # config.yaml 的 backend 字段可覆盖函数参数（便于全局切换后端）
    backend = dc.get("backend", backend)

    # tqsdk 路径：统一入口自带 TTL 缓存，直接用它，避免双重缓存
    if backend == "tqsdk" and _HAS_FUTURE_DATA:
        return fetch_klines_tq(
            symbol, period=period,
            length=dc.get("data_length", 8000),
            ttl_hours=ttl_hours,
            force=force_refresh,
        )

    # akshare 回退路径（保留原 parquet 本地缓存逻辑）
    os.makedirs(dc["cache_dir"], exist_ok=True)
    path = _cache_path(cfg, symbol)

    if use_cache and not force_refresh and _cache_fresh(path, dc["cache_ttl_hours"]):
        try:
            return pd.read_parquet(path)
        except Exception:  # noqa: BLE001
            pass  # 缓存损坏则重新拉

    df = fetch_klines_ak(symbol, period=period, retries=dc.get("fetch_retries", 2))
    try:
        df.to_parquet(path, index=False)
    except Exception:  # noqa: BLE001
        pass
    return df


def load_klines_inject(
    symbol: str,
    cfg: Optional[dict] = None,
    exchange: Optional[str] = None,
    length: int = 300,
) -> pd.DataFrame:
    """注入模式读取单品种 K 线（短周期实盘扫描用）。

    与 :func:`load_klines` 的区别：
      - load_klines：TTL 全量缓存（深历史 data_length 根），回测用。
      - load_klines_inject：注入模式滚动窗口（默认 300 根），增量更新、窗口恒定，
        仅适合找「当前信号」的扫描部分，不要用于回测（样本太浅）。

    走 future_data.inject_klines（tqsdk 后端 + inject/ 子目录滚动缓存）。
    """
    cfg = cfg or load_config()
    dc = cfg["data"]
    period = dc["period"]
    if not _HAS_FUTURE_DATA:
        raise RuntimeError("future_data 不可用，注入模式需 tqsdk 后端")
    if exchange is None:
        exchange = _tq_exch_map().get(symbol)
    if exchange is None:
        raise ValueError(f"无法为 {symbol} 解析 exchange，请显式传入")
    df = _tq_inject_klines(symbol, exchange, period=period, length=length)
    return _normalize_klines(df)


def load_all_klines_inject(
    cfg: Optional[dict] = None,
    table: Optional[str] = None,
    length: int = 300,
    progress: bool = True,
) -> dict[str, "pd.DataFrame"]:
    """批量注入模式读取（单连接），返回 {symbol: 滚动窗口 df}。

    用于扫描部分一次性增量更新全部品种，避免逐品种联网。
    """
    cfg = cfg or load_config()
    dc = cfg["data"]
    period = dc["period"]
    if not _HAS_FUTURE_DATA:
        raise RuntimeError("future_data 不可用，注入模式需 tqsdk 后端")
    syms = read_symbols(cfg, table)
    tuples = [(r.symbol, r.name, r.exchange) for r in syms.itertuples(index=False)]
    out_raw = _tq_inject_many(tuples, period=period, length=length)
    out: dict[str, pd.DataFrame] = {}
    n = len(syms)
    for i, r in enumerate(syms.itertuples(index=False), 1):
        sym = r.symbol
        if sym in out_raw:
            out[sym] = _normalize_klines(out_raw[sym])
            if progress:
                print(f"[{i}/{n}] {sym} {r.name}: {len(out[sym])} bars (inject)")
    return out


def load_all_klines(
    cfg: Optional[dict] = None,
    table: Optional[str] = None,
    use_cache: bool = True,
    progress: bool = True,
) -> dict[str, "pd.DataFrame"]:
    """批量读取，返回 {symbol: klines_df}，失败品种记录日志但不中断"""
    cfg = cfg or load_config()
    syms = read_symbols(cfg, table)
    out: dict[str, pd.DataFrame] = {}
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
    print(read_symbols(cfg).head(3))
