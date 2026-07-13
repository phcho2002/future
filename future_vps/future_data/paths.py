"""future_vps 路径中心 —— 所有运行时路径的相对解析入口。

设计目标
========
原系统把路径硬编码成 ``D:/work_ai/...``（Win 本地），无法在 Linux VPS 运行。
本模块把所有运行时路径收口为「相对 future_vps 根目录推导 + 环境变量覆盖」：

    future_vps/
    ├── futures_top40.json   ← 品种表（FUTURES_TOP40_JSON 可覆盖）
    ├── xt_token.py          ← 迅投 token（XT_TOKEN_PATH 可覆盖）
    └── quote_cache/         ← 缓存目录（QUOTE_CACHE_DIR 可覆盖）

环境变量优先级最高，方便 VPS 上把缓存/凭证放到任意位置（如挂载的数据盘）。
所有路径都用 ``pathlib``，跨平台（Win/Linux 均可）。

约定：future_vps 根 = 本文件所在 future_data 包的上一级目录（即 ``parents[1]``）。
"""

from __future__ import annotations

import os
from pathlib import Path

# future_vps 根目录 = future_data/paths.py 的上两级（future_data 的父目录）
_VPS_ROOT = Path(__file__).resolve().parents[1]


def vps_root() -> Path:
    """future_vps 根目录（绝对路径）。"""
    return _VPS_ROOT


def cache_dir() -> Path:
    """行情缓存目录。

    默认 ``<future_vps>/quote_cache``；环境变量 ``QUOTE_CACHE_DIR`` 可覆盖。
    """
    env = os.environ.get("QUOTE_CACHE_DIR")
    return Path(env).expanduser().resolve() if env else _VPS_ROOT / "quote_cache"


def token_path() -> Path:
    """迅投 token 文件路径。

    默认 ``<future_vps>/xt_token.py``；环境变量 ``XT_TOKEN_PATH`` 可覆盖。
    文件内容形如 ``XT_TOKEN = "your_token"``。
    """
    env = os.environ.get("XT_TOKEN_PATH")
    return Path(env).expanduser().resolve() if env else _VPS_ROOT / "xt_token.py"


def auth_path() -> Path:
    """[已废弃] 旧 tqsdk TqAuth 凭证路径的兼容别名。

    原指向 ``<future_vps>/tq_auth.py``（环境变量 ``TQ_AUTH_PATH`` 可覆盖）。
    数据层已切换到 xtquant，新代码请用 :func:`token_path`。保留本函数仅为
    避免旧引用直接报错。
    """
    env = os.environ.get("TQ_AUTH_PATH")
    return Path(env).expanduser().resolve() if env else _VPS_ROOT / "tq_auth.py"


def symbols_json() -> Path:
    """TOP40 品种表 JSON 路径。

    默认 ``<future_vps>/futures_top40.json``；环境变量 ``FUTURES_TOP40_JSON`` 可覆盖。
    """
    env = os.environ.get("FUTURES_TOP40_JSON")
    return Path(env).expanduser().resolve() if env else _VPS_ROOT / "futures_top40.json"
