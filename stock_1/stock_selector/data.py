from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


REQUIRED_COLUMNS = ["date", "open", "high", "low", "close", "volume"]


def normalize_daily_ohlcv(raw: pd.DataFrame) -> pd.DataFrame:
    """Normalize AkShare Chinese daily stock columns to date/open/high/low/close/volume."""
    rename_map = {
        "日期": "date",
        "开盘": "open",
        "最高": "high",
        "最低": "low",
        "收盘": "close",
        "成交量": "volume",
        "datetime": "date",
        "time": "date",
    }
    df = raw.rename(columns=rename_map).copy()
    missing = [col for col in REQUIRED_COLUMNS if col not in df.columns]
    if missing:
        raise ValueError(f"missing required columns: {missing}")

    df = df[REQUIRED_COLUMNS].copy()
    df["date"] = pd.to_datetime(df["date"])
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(subset=REQUIRED_COLUMNS).sort_values("date").reset_index(drop=True)
    return df


@dataclass(frozen=True)
class AkShareStockProvider:
    period: str = "daily"
    adjust: str = "qfq"

    def history(self, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        try:
            import akshare as ak
        except ImportError as exc:
            raise RuntimeError("akshare is not installed. Run: pip install -r requirements.txt") from exc

        raw = ak.stock_zh_a_hist(
            symbol=symbol,
            period=self.period,
            start_date=start_date,
            end_date=end_date,
            adjust=self.adjust,
        )
        return normalize_daily_ohlcv(raw)

    def all_a_share_symbols(self) -> list[str]:
        try:
            import akshare as ak
        except ImportError as exc:
            raise RuntimeError("akshare is not installed. Run: pip install -r requirements.txt") from exc

        raw = ak.stock_info_a_code_name()
        code_col = "code" if "code" in raw.columns else "代码"
        return raw[code_col].astype(str).str.zfill(6).tolist()
