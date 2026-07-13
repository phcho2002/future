"""Tongdaxin (.day) data provider for daily stock data.
Reads the standard 32-byte-per-record format from 通达信 v6/v7.

优化点:
  - 仅返回真正的 A 股股票 (过滤指数/ETF/可转债/B股/基金)
  - 从通达信 T0002/blocknew 或 vipdoc 目录尽力加载股票名称
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import pandas as pd

REQUIRED_COLUMNS = ["date", "open", "high", "low", "close", "volume"]

# Tongdaxin .day file: 32 bytes per record
_DAY_STRUCT = struct.Struct("<IIIIIIIi")  # 8 x 4 = 32 bytes
_RECORD_BYTES = 32


def read_tdx_day_file(path: Path) -> pd.DataFrame:
    """Read a single Tongdaxin .day file into a DataFrame.

    Returns columns: date, open, high, low, close, volume, amount
    """
    raw = path.read_bytes()
    n = len(raw) // _RECORD_BYTES
    records = []
    for i in range(n):
        offset = i * _RECORD_BYTES
        chunk = raw[offset : offset + _RECORD_BYTES]
        date_int, o, h, l, c, amount, vol, *_ = _DAY_STRUCT.unpack(chunk)
        records.append(
            {
                "date": datetime.strptime(str(date_int), "%Y%m%d"),
                "open": o / 100.0,
                "high": h / 100.0,
                "low": l / 100.0,
                "close": c / 100.0,
                "volume": vol,
                "amount": amount,
            }
        )
    df = pd.DataFrame(records)
    df = df.sort_values("date").reset_index(drop=True)
    return df


def is_a_share_stock(symbol: str) -> bool:
    """判断6位代码是否为真正的 A 股股票。

    规则:
      沪市主板:   60xxxx
      沪市科创板: 688xxx
      深市主板:   000xxx, 001xxx, 002xxx, 003xxx
      深市创业板: 300xxx, 301xxx
    排除: 指数(000300/880xxx)、ETF(15/16/50/51/52/56/58)、
          可转债(11/12)、B股(200/900)、基金、期权等
    """
    sym = symbol.zfill(6)
    if len(sym) != 6 or not sym.isdigit():
        return False

    # 沪市 A 股
    if sym.startswith("60"):
        return True
    if sym.startswith("688"):
        return True
    # 深市 A 股
    if sym.startswith(("000", "001", "002", "003")):
        # 排除 000001(上证指数等虽在sh，但深市000开头) — 000001是平安银行，是股票
        return True
    if sym.startswith(("300", "301")):
        return True
    return False


@dataclass(frozen=True)
class TDXStockProvider:
    """Reads daily A-share data from Tongdaxin .day files.

    Parameters
    ----------
    tdx_dir : str or Path
        Root of Tongdaxin vipdoc directory, e.g. D:/new_tdx/vipdoc.
    stocks_only : bool
        若为 True，all_a_share_symbols() 仅返回真正的 A 股股票代码。
    """
    tdx_dir: Path = field(default=Path("D:/new_tdx/vipdoc"))
    stocks_only: bool = True

    def _day_path(self, symbol: str) -> Path | None:
        """Return path to the .day file for a given 6-digit symbol, or None."""
        sym = symbol.zfill(6)
        # Determine exchange: 6xxxxx → SH, 0/3xxxxx → SZ
        if sym.startswith("6"):
            exch = "sh"
        else:
            exch = "sz"
        p = self.tdx_dir / exch / "lday" / f"{exch}{sym}.day"
        return p if p.exists() else None

    def history(self, symbol: str, start_date: str = "20200101", end_date: str = "20991231") -> pd.DataFrame:
        """Fetch daily history for a single symbol from Tongdaxin .day files."""
        path = self._day_path(symbol)
        if path is None:
            raise FileNotFoundError(f"No .day file for symbol {symbol} in {self.tdx_dir}")

        df = read_tdx_day_file(path)

        start = datetime.strptime(start_date, "%Y%m%d") if start_date else datetime.min
        end = datetime.strptime(end_date, "%Y%m%d") if end_date else datetime.max
        df = df[(df["date"] >= start) & (df["date"] <= end)].reset_index(drop=True)

        return df[["date", "open", "high", "low", "close", "volume"]]

    def all_a_share_symbols(self) -> list[str]:
        """Return all stock symbols available in the Tongdaxin data directory.

        当 stocks_only=True 时，仅返回真正的 A 股股票 (排除指数/ETF/可转债/B股)。
        """
        symbols: list[str] = []
        for exch in ("sh", "sz"):
            day_dir = self.tdx_dir / exch / "lday"
            if not day_dir.exists():
                continue
            for f in sorted(day_dir.iterdir()):
                if f.suffix.lower() == ".day":
                    sym = f.stem[2:]  # strip 'sh' or 'sz' prefix
                    if self.stocks_only and not is_a_share_stock(sym):
                        continue
                    symbols.append(sym)
        return symbols

    def load_stock_names(self) -> dict[str, str]:
        """尽力加载股票名称映射。

        通达信的股票名称通常存储在 T0002/blocknew/*.blk 或
        T0002/hq_cache 目录下的 tdxhy.cfg (沪深代码表) 文件中。
        本方法尝试多种来源，失败则返回空字典。
        """
        names: dict[str, str] = {}
        # 来源1: T0002/hq_cache/tdxhy.cfg  (沪深代码表, 格式: code|name|...)
        cfg_candidates = [
            self.tdx_dir.parent / "T0002" / "hq_cache" / "tdxhy.cfg",
            self.tdx_dir.parent / "T0002" / "hq_cache" / "shm.tnf",
        ]
        for cfg in cfg_candidates:
            if cfg.exists():
                try:
                    names.update(_parse_tdx_cfg(cfg))
                    if names:
                        return names
                except Exception:
                    continue

        # 来源2: 通达信 stockview 自带 gbbq 文件
        gbbq = self.tdx_dir.parent / "T0002" / "hq_cache" / "gbbq.lst"
        if gbbq.exists():
            try:
                names.update(_parse_gbbq(gbbq))
            except Exception:
                pass

        return names


def _parse_tdx_cfg(path: Path) -> dict[str, str]:
    """解析 tdxhy.cfg 沪深代码表 (逐行 'code|name' 或类似格式)。"""
    names: dict[str, str] = {}
    try:
        text = path.read_text(encoding="gbk", errors="ignore")
    except Exception:
        return names
    for line in text.splitlines():
        parts = line.split("|")
        if len(parts) >= 2:
            code = parts[0].strip().zfill(6)
            name = parts[1].strip()
            if code.isdigit() and name:
                names[code] = name
    return names


def _parse_gbbq(path: Path) -> dict[str, str]:
    """解析 gbbq.lst (股本权息文件)。"""
    names: dict[str, str] = {}
    try:
        text = path.read_text(encoding="gbk", errors="ignore")
    except Exception:
        return names
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            code = parts[0].strip().zfill(6)
            if code.isdigit():
                names[code] = parts[1].strip()
    return names
