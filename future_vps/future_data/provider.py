"""tqsdk 数据 provider —— 所有联网拉取 K 线的唯一通道。

本模块是原 future_1/future_quant/data/tqsdk_provider.py 的增强版：
    - 复用经过验证的 _load_auth / 批量 fetch_many / _is_filled 逻辑
    - 用新的 resolve_symbol 替代只能处理主力的 build_tq_symbol（支持具体合约）
    - normalize 后的 schema 与原两个 provider 完全一致，下游零改动
    - DEFAULT_CACHE_DIR 改为全系统共享目录（不再写死 future_1/data_cache）

公开 API：
    fetch_kline(symbol, exchange, period, length)   -> DataFrame（单品种，单连接）
    fetch_many(symbols, period, length)             -> dict[str, DataFrame]（批量，单连接）
    normalize_kline(raw_tqsdk_df)                   -> 规范化 DataFrame
    TqSdkProvider                                    有状态封装（缓存场景用）
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from future_data.paths import auth_path, cache_dir
from future_data.symbols import resolve_symbol

# ---------------------------------------------------------------- defaults
# 全系统共享缓存目录（相对 future_vps 根推导，环境变量 QUOTE_CACHE_DIR 可覆盖）。
DEFAULT_CACHE_DIR = cache_dir()
DEFAULT_PERIOD = "15"
DEFAULT_LENGTH = 200
DEFAULT_TTL_HOURS = 2
DEFAULT_WAIT_TIMEOUT = 20.0

# 规范化后的列契约（全系统统一）
OHLC_COLUMNS = ("open", "high", "low", "close")

_AUTH_CACHE = None


def _load_auth():
    """从 tq_auth.py 读取 TqAuth（进程内缓存）。

    路径默认 ``<future_vps>/tq_auth.py``，可用环境变量 ``TQ_AUTH_PATH`` 覆盖。
    原始文件形如：  TqAuth("user", "password")
    """
    global _AUTH_CACHE
    if _AUTH_CACHE is not None:
        return _AUTH_CACHE

    auth_file = auth_path()
    if not auth_file.exists():
        raise FileNotFoundError(
            f"tq_auth.py 未找到: {auth_file}"
            "（需要 TqAuth 账号才能拉取 tqsdk 行情；"
            "请在 future_vps 根目录创建 tq_auth.py，或设置环境变量 TQ_AUTH_PATH）"
        )

    text = auth_file.read_text(encoding="utf-8")
    m = re.search(r'TqAuth\("([^"]+)",\s*"([^"]+)"\)', text)
    if not m:
        raise ValueError(f"无法从 {auth_file} 解析 TqAuth(user, password)")

    from tqsdk import TqAuth

    _AUTH_CACHE = TqAuth(m.group(1), m.group(2))
    return _AUTH_CACHE


def normalize_kline(raw: pd.DataFrame) -> pd.DataFrame:
    """把 tqsdk 原始 kline DataFrame 规范化为 [datetime, open, high, low, close, volume]。

    与原 future_1/future_2 provider 的输出完全一致：
        - datetime：pd.Timestamp（从 tqsdk 纳秒索引转秒）
        - open/high/low/close/volume：float64
        - 升序、整数 RangeIndex
    """
    result = pd.DataFrame()
    # tqsdk kline：真实时间戳在 datetime 列里（int64 纳秒，如 1.78e18）；
    # raw.index 只是行号（0,1,2,...），不是时间戳。
    ts_ns = pd.to_numeric(raw["datetime"], errors="coerce")
    result["datetime"] = pd.to_datetime(ts_ns, unit="ns")
    for col in OHLC_COLUMNS:
        result[col] = pd.to_numeric(raw[col], errors="coerce")
    # volume 走 Series 路径才能 fillna（对 ndarray 调 fillna 会 AttributeError）
    result["volume"] = pd.to_numeric(raw["volume"], errors="coerce").fillna(0.0)
    return (
        result.dropna(subset=list(OHLC_COLUMNS))
        .sort_values("datetime")
        .reset_index(drop=True)
    )


def _is_filled(raw: pd.DataFrame | None) -> bool:
    """tqsdk kline 序列最后一根有了真实（非 epoch）时间戳，即认为数据就绪。"""
    if raw is None or getattr(raw, "empty", True):
        return False
    try:
        return raw.iloc[-1].get("datetime", 0) != 0
    except Exception:
        return False


def _period_to_seconds(period: str) -> int:
    """分钟周期字符串 -> 秒。"""
    return int(period) * 60


def _subscribe_one(api, tq_symbol: str, period: str, length: int, timeout: float):
    """订阅一个合约并等待填充。返回原始 kline DataFrame 或 None。"""
    duration = _period_to_seconds(period)
    try:
        raw = api.get_kline_serial(
            tq_symbol, duration_seconds=duration, data_length=length
        )
    except Exception:
        return None
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            api.wait_update(deadline=deadline)
        except Exception:
            break
        if _is_filled(raw):
            return raw
    return raw if _is_filled(raw) else None


def fetch_kline(
    symbol: str,
    exchange: str,
    period: str = DEFAULT_PERIOD,
    length: int = DEFAULT_LENGTH,
    auth=None,
    wait_timeout: float = DEFAULT_WAIT_TIMEOUT,
) -> pd.DataFrame:
    """拉取单品种 K 线（开一个新连接）。

    Parameters
    ----------
    symbol : str
        "RB0"（主力）或 "RB2610"（具体合约）。
    exchange : str
        cffex / shfe / dce / czce / gfex / ine。
    period : str
        分钟周期，如 "15" / "30" / "60"。
    length : int
        拉取根数（tqsdk 单次最多约 8964）。

    Returns
    -------
    DataFrame[datetime, open, high, low, close, volume]
    """
    from tqsdk import TqApi

    tq_symbol, kind = resolve_symbol(symbol, exchange)
    auth = auth or _load_auth()
    api = TqApi(auth=auth)
    try:
        # 先试解析出的 instrument；若失败且不是连续合约，再退回主力连续兜底
        raw = _subscribe_one(api, tq_symbol, period, length, wait_timeout)
        if raw is None and kind == "specific":
            # 具体合约拿不到时，退回主力连续
            cont, _ = resolve_symbol(symbol[:-1] + "0" if not symbol.endswith("0") else symbol, exchange)
            if cont != tq_symbol:
                raw = _subscribe_one(api, cont, period, length, wait_timeout)
        if raw is None:
            raise ValueError(f"tqsdk 无数据返回: {tq_symbol}")
        return normalize_kline(raw)
    finally:
        try:
            api.close()
        except Exception:
            pass


def fetch_many(
    symbols: list[tuple[str, str, str]],
    period: str = DEFAULT_PERIOD,
    length: int = DEFAULT_LENGTH,
    auth=None,
    wait_timeout: float = DEFAULT_WAIT_TIMEOUT,
) -> dict[str, pd.DataFrame]:
    """批量拉取多个品种（**只用一个 tqsdk 连接**）。

    这是相对原 future_2 的核心优化点：future_2 原来每个品种 new 一个 TqApi，
    40 个品种要重连 40 次；本函数一次连接订阅全部。

    Parameters
    ----------
    symbols : list[(symbol, name, exchange)]
        name 仅用于透传，不参与拉取。
    period, length : 同 fetch_kline。

    Returns
    -------
    dict[symbol -> DataFrame]；失败的品种被静默跳过（调用方可与输入比对找缺漏）。
    """
    from tqsdk import TqApi

    auth = auth or _load_auth()
    api = TqApi(auth=auth)
    out: dict[str, pd.DataFrame] = {}
    try:
        # 一次性订阅全部
        subs: dict[str, pd.DataFrame] = {}
        for symbol, _name, exchange in symbols:
            tq_symbol, _kind = resolve_symbol(symbol, exchange)
            try:
                raw = api.get_kline_serial(
                    tq_symbol,
                    duration_seconds=_period_to_seconds(period),
                    data_length=length,
                )
                subs[symbol] = raw
            except Exception:
                continue

        # 持续 wait_update，填充好一个就收一个，直到超时或全部就绪
        deadline = time.time() + wait_timeout
        while subs and time.time() < deadline:
            try:
                api.wait_update(deadline=deadline)
            except Exception:
                break
            filled = {s for s, r in subs.items() if _is_filled(r)}
            for symbol in list(filled):
                out[symbol] = normalize_kline(subs.pop(symbol))
            if not subs:
                break

        # 收集剩余已填充但循环退出的
        for symbol, raw in list(subs.items()):
            if _is_filled(raw):
                out[symbol] = normalize_kline(raw)
                subs.pop(symbol, None)
    finally:
        try:
            api.close()
        except Exception:
            pass
    return out


@dataclass
class TqSdkProvider:
    """有状态的封装：缓存/回测场景用，普通扫描直接用 get_klines/fetch_many 即可。

    字段语义沿用原 future_1 的 TqSdkProvider，便于平滑替换。
    """

    period: str = DEFAULT_PERIOD
    data_length: int = DEFAULT_LENGTH
    cache_dir: Path = field(default_factory=lambda: DEFAULT_CACHE_DIR)
    auth: object | None = None
    wait_timeout: float = DEFAULT_WAIT_TIMEOUT

    def fetch_kline(self, symbol, exchange, period=None, length=None):
        period = period or self.period
        length = length or self.data_length
        return fetch_kline(
            symbol, exchange, period, length, auth=self.auth, wait_timeout=self.wait_timeout
        )

    def fetch_many(self, symbols, period=None, length=None):
        period = period or self.period
        length = length or self.data_length
        return fetch_many(
            symbols, period, length, auth=self.auth, wait_timeout=self.wait_timeout
        )

    # ----- 缓存层（"回测快照"语义：skip_if_cached 时不再联网）-----
    def cache_path(self, symbol: str, period: str | None = None) -> Path:
        period = period or self.period
        safe = symbol.replace("/", "_")
        return self.cache_dir / f"{safe}_{period}m.parquet"

    def fetch_and_cache(
        self,
        symbols: list[tuple[str, str, str]],
        period: str | None = None,
        length: int | None = None,
        skip_if_cached: bool = True,
    ) -> dict[str, pd.DataFrame]:
        """批量拉取并落盘。skip_if_cached=True 时已有缓存文件就只读盘。"""
        period = period or self.period
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        out: dict[str, pd.DataFrame] = {}
        to_fetch: list[tuple[str, str, str]] = []
        for sym, name, ex in symbols:
            path = self.cache_path(sym, period)
            if skip_if_cached and path.exists():
                out[sym] = pd.read_parquet(path)
            else:
                to_fetch.append((sym, name, ex))

        if to_fetch:
            fetched = self.fetch_many(to_fetch, period=period, length=length)
            for sym, df in fetched.items():
                df.to_parquet(self.cache_path(sym, period))
                out[sym] = df
        return out

    def load_cache(self, symbol: str, period: str | None = None) -> pd.DataFrame | None:
        path = self.cache_path(symbol, period)
        return pd.read_parquet(path) if path.exists() else None
