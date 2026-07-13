"""xtquant (迅投行情) 数据后端 —— token 模式直连。

与 akshare provider 返回同一 schema：
    DataFrame[datetime, open, high, low, close, volume]

接口：
    fetch_kline(symbol, exchange, period, length) -> DataFrame
    fetch_many(symbols, period, length)           -> dict[str, DataFrame]
    normalize_xtquant(raw)                        -> DataFrame

xtquant token 模式：
    - 无需 MiniQMT 客户端后台运行，通过 token 直连迅投行情服务器
    - 初始化需 ~10s（加载合约表），之后可反复查询
    - 获取数据前需 download_history_data2 缓存到本地，再 get_market_data_ex 读取

迅投代码规则（实测/官方文档确认，2026-07）：
    - 交易所后缀：SF(上期) / DF(大商) / ZF(郑商) / IF(中金) / INE(能源) / GF(广期)
    - 主力连续：品种00.后缀，如 rb00.SF / IF00.IF / MA00.ZF / sc00.INE
    - 具体月份：品种YYMM.后缀，如 rb2610.SF / IF2412.IF
    - 大小写：CFFEX/CZCE 品种大写，其余交易所小写（与 tqsdk 规则一致）
    - period：1m/5m/15m/30m/1h/1d
    - get_market_data_ex 返回 {symbol: DataFrame}，DataFrame 含
      time(毫秒int)/open/high/low/close/volume/amount/...
"""
from __future__ import annotations

import os
import time
from functools import lru_cache
from pathlib import Path

import pandas as pd

from future_data.symbols import resolve_xt_symbol

# 与全系统统一的数据契约
OHLC_COLUMNS = ("datetime", "open", "high", "low", "close", "volume")

# 系统内部分钟数 → 迅投 period 字符串
_PERIOD_MAP = {
    "1": "1m",
    "5": "5m",
    "15": "15m",
    "30": "30m",
    "60": "1h",
    "120": "2h",
}

# 日线别名 → 迅投 1d
_DAY_ALIASES = {"1440", "d", "day", "1d", "daily"}


def _to_xt_period(period: str) -> str:
    """系统内部分钟周期字符串 → 迅投 period。

    >>> _to_xt_period("15")
    '15m'
    >>> _to_xt_period("60")
    '1h'
    >>> _to_xt_period("1440")
    '1d'
    """
    p = str(period).strip().lower()
    if p in _DAY_ALIASES:
        return "1d"
    if p in _PERIOD_MAP:
        return _PERIOD_MAP[p]
    # 纯数字 → 分钟
    try:
        mins = int(p)
        if mins >= 1440:
            return "1d"
        if mins >= 60 and mins % 60 == 0:
            h = mins // 60
            return f"{h}h" if h > 1 else "1h"
        return f"{mins}m"
    except (TypeError, ValueError):
        # 已经是 15m/1h 这样的格式
        return p


def _load_token() -> str:
    """读取迅投 token：环境变量 XT_TOKEN 优先，否则读 D:/work_ai/xt_token.py。"""
    tok = os.environ.get("XT_TOKEN", "").strip()
    if tok:
        return tok

    # 尝试几个可能的路径
    candidates = [
        Path("D:/work_ai/xt_token.py"),
        Path(__file__).resolve().parent.parent / "xt_token.py",
    ]
    for path in candidates:
        if path.exists():
            text = path.read_text(encoding="utf-8")
            # 匹配 XT_TOKEN = "xxx" 或 XT_TOKEN = 'xxx'
            import re

            m = re.search(r'''XT_TOKEN\s*=\s*["']([^"']+)["']''', text)
            if m:
                return m.group(1)

    raise FileNotFoundError(
        "未找到迅投 token。请设置环境变量 XT_TOKEN，"
        "或在 D:/work_ai/xt_token.py 中写 XT_TOKEN = \"your_token\""
    )


# VIP 行情服务器地址池（token 模式下 set_allow_optmize_address 使用第一个地址做全推连接，
# 其余做负载均衡/故障切换）。
_VIP_ADDR_LIST = [
    "115.231.218.73:55310",
    "115.231.218.79:55310",
    "42.228.16.211:55300",
    "42.228.16.210:55300",
    "36.99.48.20:55300",
    "36.99.48.21:55300",
]


@lru_cache(maxsize=1)
def _ensure_init() -> bool:
    """幂等初始化 xtquant 行情模块（进程级一次）。

    token 模式初始化流程：
        1. xtdatacenter.set_token(token)
        2. xtdatacenter.set_allow_optmize_address(addr_list)  # VIP 地址池
        3. xtdatacenter.init()  # ~10s，加载合约表

    返回 True 表示成功。失败抛异常（调用方自行 catch）。
    """
    from xtquant import xtdatacenter as xtdc

    token = _load_token()
    xtdc.set_token(token)

    # VIP 服务器连接池：第一个地址作为全推连接，其余做故障切换/负载均衡
    try:
        xtdc.set_allow_optmize_address(_VIP_ADDR_LIST)
    except Exception:
        # 某些 xtquant 版本可能不支持此方法，忽略错误继续
        pass

    xtdc.init()  # ~10s，加载合约表
    return True


def normalize_xtquant(raw: pd.DataFrame) -> pd.DataFrame:
    """把 xtquant 原始 DataFrame 规范化为标准 schema。

    迅投返回的 DataFrame:
        - time: 毫秒级 UTC 时间戳 (int64, 如 1692288000000)
        - open/high/low/close/volume: float
        - 可能还有 amount/settelementPrice/openInterest 等

    注意：迅投 time 字段是 UTC 时间戳，本函数自动 +8h 转为北京时间（Asia/Shanghai），
    与 akshare/tqsdk 返回的本地时间保持一致。

    输出: [datetime, open, high, low, close, volume]，升序，整数 RangeIndex。
    """
    if raw is None or getattr(raw, "empty", True):
        return pd.DataFrame(columns=list(OHLC_COLUMNS))

    out = pd.DataFrame()
    # time 列：毫秒 UTC 时间戳 → pd.Timestamp → +8h 北京时间
    ts_ms = pd.to_numeric(raw["time"], errors="coerce")
    out["datetime"] = pd.to_datetime(ts_ms, unit="ms") + pd.Timedelta(hours=8)

    for col in ("open", "high", "low", "close"):
        if col in raw.columns:
            out[col] = pd.to_numeric(raw[col], errors="coerce")
        else:
            out[col] = float("nan")

    # volume 列名兼容
    if "volume" in raw.columns:
        out["volume"] = pd.to_numeric(raw["volume"], errors="coerce").fillna(0.0)
    else:
        out["volume"] = 0.0

    return (
        out.dropna(subset=["open", "high", "low", "close"])
        .drop_duplicates(subset=["datetime"])
        .sort_values("datetime")
        .reset_index(drop=True)
    )


def _calc_start_time(length: int, period: str) -> str:
    """按 length 和周期估算 start_time（download_history_data 用）。

    迅投 download 需要 start_time/end_time 指定范围。
    多算余量保证拿到足够根数。
    """
    xt_p = _to_xt_period(period)
    now = pd.Timestamp.now()

    if xt_p == "1d":
        days = max(length * 2 + 60, 400)
        start = now - pd.Timedelta(days=days)
    elif xt_p.endswith("h"):
        h = int(xt_p[:-1])
        # 每天约 6 小时期货交易，多算 50% 余量
        bars_per_day = max(6 // h, 1)
        days = max(length // bars_per_day * 2 + 30, 60)
        start = now - pd.Timedelta(days=days)
    else:
        # 分钟线
        try:
            mins = int(xt_p[:-1])
        except (TypeError, ValueError):
            mins = 15
        # 期货每天约 360 分钟交易时间（含夜盘），多算 50% 余量
        bars_per_day = max(360 // mins, 1)
        days = max(length // bars_per_day * 2 + 30, 30)
        start = now - pd.Timedelta(days=days)

    return start.strftime("%Y%m%d")


def _is_minute_period(xt_period: str) -> bool:
    """是否为分钟周期（需要走 1m 下载 + 重采样路径）。"""
    return xt_period.endswith("m") or (xt_period.endswith("h") and xt_period != "1d")


def _resample_1m(df_1m: pd.DataFrame, xt_period: str) -> pd.DataFrame:
    """把 1m K 线重采样为目标分钟周期。

    xt_period: '5m' / '15m' / '30m' / '60m' / '1h' / '2h' 等。
    """
    if df_1m is None or df_1m.empty:
        return pd.DataFrame(columns=list(OHLC_COLUMNS))

    # 按目标周期分钟数重采样
    if xt_period.endswith("h"):
        mins = int(xt_period[:-1]) * 60
    elif xt_period.endswith("m"):
        mins = int(xt_period[:-1])
    else:
        mins = 15

    df = df_1m.set_index("datetime")
    r = df.resample(f"{mins}min", label="left", closed="left").agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }).dropna(subset=["open"])
    r = r.reset_index()
    # 去掉全 0 volume 的空行（非交易时段产生的）
    r = r[r["volume"] > 0].reset_index(drop=True)
    return r[list(OHLC_COLUMNS)]


def _fetch_one(xtdata, xt_symbol: str, xt_period: str, start_time: str) -> pd.DataFrame:
    """下载 + 读取单个合约的 K 线。

    分钟周期（15m/30m/60m/1h）：token 模式只支持下载 1m 和 1d，
    所以分钟线走「下载 1m → 本地重采样」路径。
    日线（1d）：直接下载读取。
    """
    if _is_minute_period(xt_period):
        # 下载 1m 数据（多取余量，保证重采样后有足够根数）
        # 目标 length 根 N 分钟线 ≈ N 根 1m，按 2 倍余量取
        mins = int(xt_period[:-1]) if xt_period.endswith("m") else int(xt_period[:-1]) * 60
        # start_time 已经按 length 估算，1m 数据量会更大，足够覆盖
        try:
            xtdata.download_history_data(xt_symbol, "1m", start_time, "")
        except Exception:
            pass  # 已下载过或下载失败不致命

        data = xtdata.get_market_data_ex(
            field_list=[],
            stock_list=[xt_symbol],
            period="1m",
            start_time=start_time,
            end_time="",
            count=-1,
        )
        if not isinstance(data, dict) or xt_symbol not in data:
            raise ValueError(f"xtquant 无 1m 数据: {xt_symbol}")
        df_1m = normalize_xtquant(data[xt_symbol])
        if df_1m.empty:
            raise ValueError(f"xtquant 1m 数据为空: {xt_symbol}")
        return _resample_1m(df_1m, xt_period)

    else:
        # 日线：直接下载 1d
        try:
            xtdata.download_history_data(xt_symbol, "1d", start_time, "")
        except Exception:
            pass

        data = xtdata.get_market_data_ex(
            field_list=[],
            stock_list=[xt_symbol],
            period="1d",
            start_time=start_time,
            end_time="",
            count=-1,
        )
        if not isinstance(data, dict) or xt_symbol not in data:
            raise ValueError(f"xtquant 无日线数据: {xt_symbol}")
        return normalize_xtquant(data[xt_symbol])


def fetch_kline_xtquant(
    symbol: str,
    exchange: str,
    period: str = "15",
    length: int = 200,
) -> pd.DataFrame:
    """拉取单品种 K 线（xtquant token 模式）。

    Parameters
    ----------
    symbol : "RB0" / "IF0" / "rb2610"
    exchange : shfe/dce/czce/cffex/gfex/ine
    period : 系统内部分钟数字符串，如 "15"/"60"/"1440"
    length : 最近根数
    """
    from xtquant import xtdata

    _ensure_init()

    xt_symbol, _kind = resolve_xt_symbol(symbol, exchange)
    xt_period = _to_xt_period(period)
    start_time = _calc_start_time(length, period)

    df = _fetch_one(xtdata, xt_symbol, xt_period, start_time)
    if df.empty:
        raise ValueError(f"xtquant 数据为空: {xt_symbol} period={xt_period}")

    # 截取最近 length 根
    if length and len(df) > int(length):
        df = df.tail(int(length)).reset_index(drop=True)
    return df


def fetch_many_xtquant(
    symbols: list[tuple[str, str, str]],
    period: str = "15",
    length: int = 200,
    verbose: bool = False,
) -> dict[str, pd.DataFrame]:
    """批量拉取（xtquant token 模式，单次连接）。

    symbols: [(symbol, name, exchange), ...]
    返回 {symbol: DataFrame}。失败品种跳过。

    分钟线走「批量下载 1m → 逐品种重采样」路径。
    """
    from xtquant import xtdata

    _ensure_init()

    xt_period = _to_xt_period(period)
    start_time = _calc_start_time(length, period)

    # 映射 symbol → xt_symbol
    sym_map: dict[str, str] = {}  # {xt_symbol: original_symbol}
    xt_list: list[str] = []
    for symbol, _name, exchange in symbols:
        try:
            xt_sym, _kind = resolve_xt_symbol(symbol, exchange)
        except Exception:
            if verbose:
                print(f"  [xtquant] {symbol} 符号解析失败，跳过")
            continue
        if xt_sym not in sym_map:
            sym_map[xt_sym] = symbol
            xt_list.append(xt_sym)

    if not xt_list:
        return {}

    # 分钟线下载 1m，日线下载 1d
    dl_period = "1m" if _is_minute_period(xt_period) else "1d"
    rd_period = "1m" if _is_minute_period(xt_period) else "1d"

    try:
        xtdata.download_history_data2(xt_list, dl_period, start_time, "")
    except Exception as e:
        if verbose:
            print(f"  [xtquant] download_history_data2 警告: {str(e)[:80]}")

    try:
        data = xtdata.get_market_data_ex(
            field_list=[],
            stock_list=xt_list,
            period=rd_period,
            start_time=start_time,
            end_time="",
            count=-1,
        )
    except Exception as e:
        if verbose:
            print(f"  [xtquant] get_market_data_ex 异常: {str(e)[:80]}")
        return {}

    out: dict[str, pd.DataFrame] = {}
    if not isinstance(data, dict):
        return out

    need_resample = _is_minute_period(xt_period)
    for xt_sym, original_sym in sym_map.items():
        if xt_sym not in data:
            continue
        try:
            df_raw = normalize_xtquant(data[xt_sym])
            if df_raw.empty:
                continue
            if need_resample:
                df = _resample_1m(df_raw, xt_period)
            else:
                df = df_raw
            if df.empty:
                continue
            if length and len(df) > int(length):
                df = df.tail(int(length)).reset_index(drop=True)
            out[original_sym] = df
        except Exception as e:
            if verbose:
                print(f"  [xtquant] {original_sym} ({xt_sym}) 规范化失败: {str(e)[:60]}")
            continue

    return out
