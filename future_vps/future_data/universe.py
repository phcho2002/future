"""品种表读取 —— 统一从 futures_top40.json 读 TOP40。

历史上从 ``futures_data.db`` 的 ``futures_top40`` 表读取（依赖 sqlite），
现改为从 ``futures_top40.json``（future_vps 根目录快照）读取，部署更简单。
JSON 与原表字段一致（``排名`` / ``symbol`` / ``name`` / ``exchange``），
因此下游调用方（scanner / cache / 各顶层脚本）无需改动字段名。

默认 JSON 路径由 :mod:`future_data.paths` 提供：
    ``<future_vps>/futures_top40.json``，环境变量 ``FUTURES_TOP40_JSON`` 可覆盖。
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from future_data.paths import symbols_json

# 默认 JSON 路径（相对 future_vps 根推导，环境变量 FUTURES_TOP40_JSON 可覆盖）。
DEFAULT_JSON_PATH = symbols_json()


def _load_rows(path: Path) -> list[dict]:
    """读取 JSON，返回原始字典列表（不做排序/裁剪）。"""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    # 兼容两种结构：直接 list[dict]，或 {"symbols": list[...]}。
    if isinstance(data, dict) and "symbols" in data:
        data = data["symbols"]
    return list(data)


def read_symbols(
    json_path: Path | str | None = None,
    limit: int | None = None,
    order_by_rank: bool = True,
) -> list[tuple[str, str, str]]:
    """读品种表，返回 [(symbol, name, exchange)]（按 ``排名`` 升序）。

    Parameters
    ----------
    json_path : 默认 ``<future_vps>/futures_top40.json``（环境变量可覆盖）。
    limit : 仅取前 N（按排名）。
    order_by_rank : True 则按 ``排名`` 升序。

    Returns
    -------
    list[(symbol, name, exchange)]
        symbol 形如 "RB0"/"AU0"/"IM0"（主力占位符），可直接喂给 get_klines。
    """
    p = Path(json_path) if json_path else DEFAULT_JSON_PATH
    rows = _load_rows(p)
    if order_by_rank:
        rows = sorted(rows, key=lambda r: r.get("排名", 0))
    if limit:
        rows = rows[: int(limit)]
    return [(r["symbol"], r["name"], r["exchange"]) for r in rows]


def build_exchange_map(json_path: Path | str | None = None) -> dict[str, str]:
    """返回 {symbol: exchange}，给那些只有 symbol 没有 exchange 的脚本用。

    迁移到 tqsdk 后端需要 exchange 才能解析合约，本函数从 JSON 一次性取出映射。

    例：{'IM0': 'cffex', 'RB0': 'shfe', 'AU0': 'shfe', ...}
    """
    return {sym: ex for sym, _name, ex in read_symbols(json_path=json_path)}


def read_symbols_df(
    json_path: Path | str | None = None,
    limit: int | None = None,
) -> pd.DataFrame:
    """同 read_symbols，但返回 DataFrame（含排名等全部列，便于展示）。"""
    p = Path(json_path) if json_path else DEFAULT_JSON_PATH
    rows = _load_rows(p)
    if "排名" in (rows[0] if rows else {}):
        rows = sorted(rows, key=lambda r: r.get("排名", 0))
    if limit:
        rows = rows[: int(limit)]
    return pd.DataFrame(rows)
