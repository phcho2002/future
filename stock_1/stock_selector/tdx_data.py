"""
Tongdaxin (.day) data provider for daily stock data.
Reads the standard 32-byte-per-record format from 通达信 v6/v7.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

REQUIRED_COLUMNS = ["date", "open", "high", "low", "close", "volume"]

# Tongdaxin .day file: 32 bytes per record
# int32 date (YYYYMMDD), int32 open*100, int32 high*100,
# int32 low*100, int32 close*100, int32 amount, int32 volume, int32 reserved
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


@dataclass(frozen=True)
class TDXStockProvider:
    """Reads daily A-share data from Tongdaxin .day files.

    Parameters
    ----------
    tdx_dir : str or Path
        Root of Tongdaxin vipdoc directory, e.g. D:/new_tdx/vipdoc.
    """
    tdx_dir: Path = field(default=Path("D:/new_tdx/vipdoc"))

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
        """Fetch daily history for a single symbol from Tongdaxin .day files.

        Parameters
        ----------
        symbol : str
            6-digit stock code (will be zero-padded).
        start_date : str
            Start date YYYYMMDD.
        end_date : str
            End date YYYYMMDD.

        Returns
        -------
        pd.DataFrame with columns: date, open, high, low, close, volume
        """
        path = self._day_path(symbol)
        if path is None:
            raise FileNotFoundError(f"No .day file for symbol {symbol} in {self.tdx_dir}")

        df = read_tdx_day_file(path)

        # Date filter
        start = datetime.strptime(start_date, "%Y%m%d") if start_date else datetime.min
        end = datetime.strptime(end_date, "%Y%m%d") if end_date else datetime.max
        df = df[(df["date"] >= start) & (df["date"] <= end)].reset_index(drop=True)

        return df[["date", "open", "high", "low", "close", "volume"]]

    def all_a_share_symbols(self) -> list[str]:
        """Return all stock symbols available in the Tongdaxin data directory."""
        symbols: list[str] = []
        for exch in ("sh", "sz"):
            day_dir = self.tdx_dir / exch / "lday"
            if not day_dir.exists():
                continue
            for f in sorted(day_dir.iterdir()):
                if f.suffix.lower() == ".day":
                    # filename format: sh000001.day → symbol = 000001
                    sym = f.stem[2:]  # strip 'sh' or 'sz' prefix
                    symbols.append(sym)
        return symbols
