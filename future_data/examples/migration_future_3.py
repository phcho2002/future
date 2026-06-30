"""示例：future_3 data_loader 改用统一入口。

future_3 本来用 akshare + 自己的 TTL 缓存（data_loader.py）。
改造后底层走 tqsdk，缓存逻辑委托给统一入口（避免双重缓存）。

注意：tqsdk 单次可取约 8964 根，远超 akshare 的 ~320 根，
所以 backtest/optimize 的回测深度会变 fuller（结果数值会变，属正向收益）。
"""

from __future__ import annotations

import sys

sys.path.insert(0, "D:/work_ai")

import pandas as pd
import yaml

from future_data import get_klines
from future_data.universe import read_symbols


def load_config(path: str = "config.yaml") -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_klines(symbol: str, exchange: str, cfg: dict | None = None) -> pd.DataFrame:
    """改造版 load_klines：直接委托给统一入口的 TTL 缓存。

    原 data_loader.py 自己维护 parquet + mtime 判新鲜度，
    现在全部交给 get_klines（同样的 TTL 语义，缓存目录统一到 D:/work_ai/quote_cache）。
    """
    cfg = cfg or load_config()
    dc = cfg["data"]
    period = dc.get("period", "30")
    ttl = dc.get("cache_ttl_hours", 6)
    length = dc.get("data_length", 8000)  # tqsdk 可拿深历史，default 提高
    return get_klines(
        symbol, exchange, period=period, length=length, ttl_hours=ttl
    )


def load_all_klines(cfg: dict | None = None, progress: bool = True):
    """批量加载（用统一入口的 fetch_many 单连接，而非逐品种 akshare）。"""
    cfg = cfg or load_config()
    period = cfg["data"].get("period", "30")
    length = cfg["data"].get("data_length", 8000)

    syms = read_symbols(table=cfg["data"].get("symbol_table", "futures_top40"))
    out = {}
    for i, (sym, name, ex) in enumerate(syms, 1):
        try:
            out[sym] = load_klines(sym, ex, cfg)
            if progress:
                print(f"[{i}/{len(syms)}] {sym} {name}: {len(out[sym])} bars")
        except Exception as e:
            if progress:
                print(f"[{i}/{len(syms)}] {sym} {name}: SKIP ({e})")
    return out


if __name__ == "__main__":
    cfg = load_config()
    # exchange 需从 DB 查；这里直接用 AU0 的已知交易所演示
    df = load_klines("AU0", "shfe", cfg)
    print(df.tail(3))
    print("cols:", list(df.columns), "rows:", len(df))
