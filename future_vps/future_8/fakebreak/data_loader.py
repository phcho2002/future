"""品种表加载器（轻量，直接读 futures_top40.json）。

复用 future_1/future_quant/data/universe.py 的逻辑，但不依赖 future_1 包，
保持 future_8 独立。
"""
from __future__ import annotations

import json
from pathlib import Path

# future_8 自包含：优先用本目录下的 futures_top40.json（future_vps/future_8/ 场景），
# 否则回退到仓库根（本地 work_ai/ 场景）。
_FUTURE_8 = Path(__file__).resolve().parents[1]  # future_8/
_REPO_ROOT = _FUTURE_8 if (_FUTURE_8 / "futures_top40.json").exists() else _FUTURE_8.parent
DEFAULT_JSON_PATH = _REPO_ROOT / "futures_top40.json"


def load_top40(path: str | Path | None = None) -> list[dict]:
    """读取 TOP40 品种，返回按 ``排名`` 升序的字典列表。

    每个元素含 ``排名`` / ``symbol`` / ``name`` / ``exchange``。
    兼容 list[dict] 和 [[symbol, name, exchange], ...] 两种格式。
    """
    p = Path(path) if path else DEFAULT_JSON_PATH
    with open(p, encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, dict):
        data = data.get("symbols", [])

    rows: list[dict] = []
    for i, r in enumerate(data, 1):
        if isinstance(r, dict):
            rows.append(r)
        elif isinstance(r, (list, tuple)) and len(r) >= 3:
            rows.append({"排名": i, "symbol": r[0], "name": r[1], "exchange": r[2]})
        else:
            raise ValueError(f"不支持的品种表格式: {r!r}")

    rows.sort(key=lambda r: r.get("排名", 0))
    return rows


def load_top40_tuples(path: str | Path | None = None, limit: int | None = None):
    """返回 (symbol, name, exchange) 元组列表。"""
    rows = load_top40(path)
    if limit is not None:
        rows = rows[:limit]
    return [(r["symbol"], r["name"], r["exchange"]) for r in rows]
