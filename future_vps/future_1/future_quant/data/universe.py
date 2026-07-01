"""TOP40 期货品种集合加载器。

历史上从 ``futures_data.db`` 的 ``futures_top40`` 表读取，
现统一改为从 ``futures_top40.json``（仓库快照）读取，避免
依赖 sqlite，部署更简单。JSON 与原表字段一致（``排名``/``symbol``/
``name``/``exchange`` 等），因此下游调用方无需改动字段名。
"""

from __future__ import annotations

import json
from pathlib import Path

# 默认从 future_vps 根目录下的 futures_top40.json 读取；通过 path 参数可覆盖。
# future_quant/data -> future_1 -> future_vps（JSON 落在 future_vps 根）。
_REPO_ROOT = Path(__file__).resolve().parents[2]  # future_quant/data -> future_1
DEFAULT_JSON_PATH = _REPO_ROOT.parent / "futures_top40.json"  # future_1.parent = future_vps


def load_top40(path: str | Path | None = None) -> list[dict]:
    """读取 TOP40 品种，返回按 ``排名`` 升序的字典列表。

    每个元素包含 ``排名`` / ``symbol`` / ``name`` / ``exchange`` 等字段，
    与原 ``pd.read_sql("SELECT ... FROM futures_top40 ORDER BY 排名").to_dict('records')``
    的输出完全等价。
    """
    p = Path(path) if path else DEFAULT_JSON_PATH
    with open(p, encoding="utf-8") as f:
        data = json.load(f)
    rows = sorted(data, key=lambda r: r.get("排名", 0))
    return rows


def load_top40_tuples(
    path: str | Path | None = None, limit: int | None = None
) -> list[tuple[str, str, str]]:
    """读取 TOP40，返回 ``(symbol, name, exchange)`` 元组列表（按 ``排名`` 升序）。

    用于替代原 ``SELECT symbol, name, exchange FROM futures_top40 ORDER BY 排名``
    的结果。可选 ``limit`` 等价于 SQL 的 ``LIMIT N``。
    """
    rows = load_top40(path)
    if limit is not None:
        rows = rows[: int(limit)]
    return [(r["symbol"], r["name"], r["exchange"]) for r in rows]
