"""品种表读取 —— 统一从 futures_data.db 读 futures_top40。

原 future_1/future_2 的 scanner、cache、各顶层脚本都各自连库读一遍同一张表，
这里收口到一个函数。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

DEFAULT_DB_PATH = Path("D:/work_ai/futures_data.db")
DEFAULT_TABLE = "futures_top40"


def read_symbols(
    db_path: Path | str | None = None,
    table: str = DEFAULT_TABLE,
    limit: int | None = None,
    order_by_rank: bool = True,
) -> list[tuple[str, str, str]]:
    """读品种表，返回 [(symbol, name, exchange)]。

    Parameters
    ----------
    db_path : 默认 D:/work_ai/futures_data.db。
    table : futures_top40（默认）/ futures_all。
    limit : 仅取前 N（按排名）。
    order_by_rank : True 则 ORDER BY 排名（top40 表有此列）。

    Returns
    -------
    list[(symbol, name, exchange)]
        symbol 形如 "RB0"/"AU0"/"IM0"（主力占位符），可直接喂给 get_klines。
    """
    db = Path(db_path) if db_path else DEFAULT_DB_PATH
    conn = sqlite3.connect(str(db))
    try:
        cur = conn.cursor()
        order = "ORDER BY 排名" if order_by_rank else ""
        lim = f"LIMIT {int(limit)}" if limit else ""
        sql = f"SELECT symbol, name, exchange FROM {table} {order} {lim}".strip()
        cur.execute(sql)
        return [(r[0], r[1], r[2]) for r in cur.fetchall()]
    finally:
        conn.close()


def build_exchange_map(
    db_path: Path | str | None = None,
    table: str = DEFAULT_TABLE,
) -> dict[str, str]:
    """返回 {symbol: exchange}，给那些只有 symbol 没有 exchange 的脚本用。

    顶层 akshare 脚本（futures_30min_signals 等）原先只有 sina symbol（如 'RB0'），
    迁移到 tqsdk 后端需要 exchange 才能解析合约。本函数从 DB 一次性取出映射。

    例：{'IM0': 'cffex', 'RB0': 'shfe', 'AU0': 'shfe', ...}
    """
    return {sym: ex for sym, _name, ex in read_symbols(db_path=db_path, table=table)}


def read_symbols_df(
    db_path: Path | str | None = None,
    table: str = DEFAULT_TABLE,
    limit: int | None = None,
) -> pd.DataFrame:
    """同 read_symbols，但返回 DataFrame（含排名等全部列，便于展示）。"""
    db = Path(db_path) if db_path else DEFAULT_DB_PATH
    conn = sqlite3.connect(str(db))
    try:
        sql = f"SELECT * FROM {table}"
        if limit:
            sql += f" ORDER BY 排名 LIMIT {int(limit)}"
        elif "排名" in pd.read_sql_query(
            f"PRAGMA table_info({table})", conn
        ).get("name", pd.Series(dtype=str)).values:
            sql += " ORDER BY 排名"
        return pd.read_sql_query(sql, conn)
    finally:
        conn.close()
