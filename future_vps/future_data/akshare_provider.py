"""akshare 行情后端 —— 备份数据源（与 xtquant provider 返回同一 schema）。

xtquant 是主数据源，akshare 作为无需账号的备份后端：
    FUTURE_DATA_BACKEND=akshare  # 切换到新浪备份

接口：
    fetch_kline(symbol, exchange, period, length) -> DataFrame
    fetch_many(symbols, period, length)           -> dict[str, DataFrame]

akshare：
    分钟：futures_zh_minute_sina(symbol, period)  period∈{1,5,15,30,60}
    日线：futures_main_sina(symbol, start_date, end_date)

默认限速 REQUEST_SLEEP 秒/次，避免新浪频控。
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta

import pandas as pd

# 与 xtquant 路径同一契约
OHLC_COLUMNS = ("datetime", "open", "high", "low", "close", "volume")
REQUEST_SLEEP = 0.35  # 秒；批量拉取时每品种间隔

# 新浪分钟周期白名单
_MINUTE_PERIODS = {"1", "5", "15", "30", "60"}


def _normalize_akshare(df: pd.DataFrame | None) -> pd.DataFrame:
    """把 akshare 原始表规范为 [datetime, open, high, low, close, volume]。"""
    if df is None or getattr(df, "empty", True):
        return pd.DataFrame(columns=list(OHLC_COLUMNS))

    out = df.copy()
    out.columns = [str(c).strip().lower() for c in out.columns]
    cn_map = {
        "日期": "date",
        "时间": "date",
        "开盘": "open",
        "开盘价": "open",
        "最高": "high",
        "最高价": "high",
        "最低": "low",
        "最低价": "low",
        "收盘": "close",
        "收盘价": "close",
        "成交量": "volume",
        "持仓量": "hold",
        "动态结算价": "settle",
    }
    # 中文列名可能在 lower 之后仍是中文
    rename = {}
    for c in out.columns:
        if c in cn_map:
            rename[c] = cn_map[c]
    if rename:
        out = out.rename(columns=rename)

    if "datetime" not in out.columns:
        if "date" in out.columns:
            out["datetime"] = pd.to_datetime(out["date"], errors="coerce")
        elif "day" in out.columns:
            out["datetime"] = pd.to_datetime(out["day"], errors="coerce")
        else:
            # 有的版本第一列就是时间
            first = out.columns[0]
            out["datetime"] = pd.to_datetime(out[first], errors="coerce")
    else:
        out["datetime"] = pd.to_datetime(out["datetime"], errors="coerce")

    if "close" not in out.columns and "settle" in out.columns:
        out["close"] = out["settle"]

    for col in ("open", "high", "low", "close", "volume"):
        if col not in out.columns:
            if col == "volume":
                out["volume"] = 0.0
            else:
                raise ValueError(f"akshare 缺少列: {col}, columns={list(out.columns)}")
        out[col] = pd.to_numeric(out[col], errors="coerce")

    out = (
        out.dropna(subset=["datetime", "open", "high", "low", "close"])
        .sort_values("datetime")
        .reset_index(drop=True)
    )
    out["volume"] = out["volume"].fillna(0.0)
    return out[list(OHLC_COLUMNS)].copy()


def _sina_symbol(symbol: str) -> str:
    """akshare/sina 主力占位：RB0 / AU0；具体月合约原样。"""
    return str(symbol).strip()


def fetch_kline_akshare(
    symbol: str,
    exchange: str = "",
    period: str = "15",
    length: int = 200,
    sleep_s: float = REQUEST_SLEEP,
) -> pd.DataFrame:
    """拉取单品种 K 线（akshare）。

    Parameters
    ----------
    symbol : 如 RB0 / IF0 / RB2610
    exchange : 保留参数，akshare 新浪接口不需要交易所
    period : 分钟 "1"/"5"/"15"/"30"/"60"，日线用 "1440" 或 "D"/"1d"
    length : 返回最近 length 根
    """
    import akshare as ak

    sym = _sina_symbol(symbol)
    p = str(period).lower().strip()
    if p in ("d", "day", "1d", "daily", "1440"):
        end = datetime.now().strftime("%Y%m%d")
        # 按 length 估天数，至少 400 日历日
        days = max(400, int(length) * 2 + 30)
        start = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
        raw = ak.futures_main_sina(symbol=sym, start_date=start, end_date=end)
        df = _normalize_akshare(raw)
    else:
        # 分钟
        if p not in _MINUTE_PERIODS:
            # 尝试映射常见别名
            if p in ("min", "m"):
                p = "1"
            else:
                raise ValueError(
                    f"akshare 不支持 period={period!r}，分钟请用 {_MINUTE_PERIODS}，日线 1440"
                )
        raw = ak.futures_zh_minute_sina(symbol=sym, period=p)
        df = _normalize_akshare(raw)

    if sleep_s and sleep_s > 0:
        time.sleep(float(sleep_s))

    if df is None or df.empty:
        raise ValueError(f"akshare 无数据: {sym} period={period}")

    if length and len(df) > int(length):
        df = df.tail(int(length)).reset_index(drop=True)
    return df


def fetch_many_akshare(
    symbols: list[tuple[str, str, str]],
    period: str = "15",
    length: int = 200,
    sleep_s: float = REQUEST_SLEEP,
    verbose: bool = False,
) -> dict[str, pd.DataFrame]:
    """批量拉取。symbols: [(symbol, name, exchange), ...]。失败品种跳过。"""
    out: dict[str, pd.DataFrame] = {}
    n = len(symbols)
    for i, (symbol, _name, exchange) in enumerate(symbols, 1):
        try:
            if verbose:
                print(f"  [akshare {i}/{n}] {symbol} ...", flush=True)
            df = fetch_kline_akshare(
                symbol, exchange, period=period, length=length, sleep_s=sleep_s
            )
            if df is not None and not df.empty:
                out[symbol] = df
        except Exception as e:  # noqa: BLE001
            if verbose:
                print(f"  [akshare {i}/{n}] {symbol} FAIL: {e}", flush=True)
            continue
    return out
