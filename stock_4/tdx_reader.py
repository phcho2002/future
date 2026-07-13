"""通达信本地 .day 二进制数据读取 + 沪深主板股票池发现。

文件格式（已实测验证）：
  - 每条记录 32 字节，小端
  - struct 格式 <IIIIIfII>
  - 字段: date(uint32 YYYYMMDD), open, high, low, close (均 ×100 存为 uint32),
          amount(float32 元), volume(uint32 股), pad(uint32)
  - OHLC 读取后 ÷100 还原

路径规则:
  - 6 开头(沪市) → vipdoc/sh/lday/sh6xxxxx.day
  - 其余(深市)   → vipdoc/sz/lday/sz0xxxxx.day
"""
from __future__ import annotations

import struct
from datetime import datetime
from pathlib import Path

import pandas as pd

_DAY_STRUCT = struct.Struct("<IIIIIfII")  # date,o,h,l,c,amount(f32),vol,pad = 32B
_RECORD_BYTES = 32


def read_tdx_day_file(path: Path) -> pd.DataFrame:
    """读取单个 .day 文件，返回 date,open,high,low,close,volume,amount（升序）。"""
    raw = path.read_bytes()
    n = len(raw) // _RECORD_BYTES
    if n == 0:
        return pd.DataFrame(
            columns=["date", "open", "high", "low", "close", "volume", "amount"]
        )
    records = []
    for i in range(n):
        chunk = raw[i * _RECORD_BYTES : (i + 1) * _RECORD_BYTES]
        date_int, o, h, l, c, amount, vol, _pad = _DAY_STRUCT.unpack(chunk)
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
    df = pd.DataFrame(records).sort_values("date").reset_index(drop=True)
    return df


def is_a_share(symbol: str) -> bool:
    """判断 6 位代码是否为 A 股（含主板/创业板/科创板）。

    包含:
      - 60xxxx          沪市主板
      - 688xxx          科创板
      - 000/001/002/003 深市主板/中小板
      - 300/301         创业板
    排除: 指数、ETF、可转债、B股、北交所(8/4开头)
    """
    sym = symbol.strip().zfill(6)
    if len(sym) != 6 or not sym.isdigit():
        return False
    if sym.startswith("60"):                        # 沪市主板
        return True
    if sym.startswith("688"):                       # 科创板
        return True
    if sym.startswith(("000", "001", "002", "003")):  # 深市主板/中小板
        return True
    if sym.startswith(("300", "301")):              # 创业板
        return True
    return False


# 兼容旧名称
is_main_board = is_a_share


def limit_pct_of(symbol: str, base_pct: float = 0.10) -> float:
    """按板块返回涨跌停幅度。

    主板 10%，创业板(300/301)和科创板(688) 20%。
    base_pct 为主板幅度（从 config 读取），特殊板块在此基础上放大。
    """
    sym = symbol.strip().zfill(6)
    if sym.startswith("688") or sym.startswith(("300", "301")):
        return base_pct * 2.0   # 创业板/科创板 20%
    return base_pct             # 主板 10%


def exchange_of(symbol: str) -> str:
    """代码 → 交易所前缀: 6 开头→sh, 否则→sz。"""
    return "sh" if symbol.strip().zfill(6).startswith("6") else "sz"


class TDXLocalProvider:
    """通达信本地 .day 离线数据提供者。

    用法:
        provider = TDXLocalProvider()
        df = provider.history("000001")        # 平安银行日线
        symbols = provider.all_main_board_symbols()
    """

    def __init__(self, vipdoc_dir: str | Path = "D:/new_tdx/vipdoc"):
        self.vipdoc_dir = Path(vipdoc_dir)
        if not self.vipdoc_dir.exists():
            raise FileNotFoundError(f"通达信 vipdoc 目录不存在: {self.vipdoc_dir}")

    def day_path(self, symbol: str) -> Path | None:
        """返回该股票的 .day 文件路径，不存在则 None。"""
        sym = symbol.strip().zfill(6)
        exch = exchange_of(sym)
        p = self.vipdoc_dir / exch / "lday" / f"{exch}{sym}.day"
        return p if p.exists() else None

    def history(
        self,
        symbol: str,
        lookback: int = 500,
        start_date: str | None = None,
    ) -> pd.DataFrame:
        """读取日线，返回最近 lookback 根（且晚于 start_date）。

        Args:
            symbol: 6 位股票代码
            lookback: 返回最近 N 根 K 线（0=全部）
            start_date: 起始日期 YYYYMMDD（可选）

        Returns:
            DataFrame[date,open,high,low,close,volume,amount]，升序
        """
        path = self.day_path(symbol)
        if path is None:
            raise FileNotFoundError(f"无 .day 文件: {symbol} (vipdoc={self.vipdoc_dir})")
        df = read_tdx_day_file(path)
        if start_date:
            start = datetime.strptime(start_date, "%Y%m%d")
            df = df[df["date"] >= start].reset_index(drop=True)
        if lookback and len(df) > lookback:
            df = df.iloc[-lookback:].reset_index(drop=True)
        return df

    def all_symbols(self) -> list[str]:
        """扫描 vipdoc 目录，返回全部 A 股代码（主板+创业板+科创板，升序）。"""
        symbols: list[str] = []
        for exch in ("sh", "sz"):
            day_dir = self.vipdoc_dir / exch / "lday"
            if not day_dir.exists():
                continue
            for f in sorted(day_dir.iterdir()):
                if f.suffix.lower() != ".day":
                    continue
                sym = f.stem[2:]          # 去掉 sh/sz 前缀
                if is_a_share(sym):
                    symbols.append(sym)
        return symbols

    # 兼容旧名称
    all_main_board_symbols = all_symbols
