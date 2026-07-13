"""Load top40 universe from JSON + contract multipliers from DB."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .config import RiskConfig, TOP40_JSON, DB_PATH


@dataclass(frozen=True)
class ProductSpec:
    symbol: str          # e.g. RB0
    name: str
    exchange: str
    multiplier: float    # 合约乘数
    last_price: float | None = None
    sector: str = "other"

    @property
    def product_code(self) -> str:
        """去掉主力占位 0 后的品种代码，如 RB / AU / IM。"""
        return self.symbol[:-1] if self.symbol.endswith("0") else self.symbol

    def notional_per_lot(self, price: float) -> float:
        return abs(price) * self.multiplier

    def margin_per_lot(self, price: float, margin_rate: float) -> float:
        return self.notional_per_lot(price) * margin_rate


def load_multipliers(db_path: Path | str = DB_PATH) -> dict[str, float]:
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            'SELECT symbol, "合约乘数" FROM futures_top40'
        ).fetchall()
    except Exception:
        rows = conn.execute(
            "SELECT symbol, multiplier FROM futures_all"
        ).fetchall()
    finally:
        conn.close()
    out: dict[str, float] = {}
    for sym, mult in rows:
        try:
            out[str(sym).strip()] = float(mult)
        except (TypeError, ValueError):
            continue
    return out


def load_last_prices(db_path: Path | str = DB_PATH) -> dict[str, float]:
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            'SELECT symbol, "最新价格" FROM futures_top40'
        ).fetchall()
    except Exception:
        return {}
    finally:
        conn.close()
    out: dict[str, float] = {}
    for sym, px in rows:
        try:
            out[str(sym).strip()] = float(px)
        except (TypeError, ValueError):
            continue
    return out


def load_universe(cfg: RiskConfig | None = None) -> list[ProductSpec]:
    """从 futures_top40.json 读品种，乘数/最新价从 DB 补齐。"""
    cfg = cfg or RiskConfig()
    path = Path(cfg.top40_json)
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    mults = load_multipliers(cfg.db_path)
    prices = load_last_prices(cfg.db_path)

    products: list[ProductSpec] = []
    for item in raw["symbols"]:
        symbol, name, exchange = item[0], item[1], item[2]
        mult = mults.get(symbol)
        if mult is None:
            # 无法定价仓位则跳过
            continue
        products.append(
            ProductSpec(
                symbol=symbol,
                name=name,
                exchange=exchange,
                multiplier=mult,
                last_price=prices.get(symbol),
                sector=cfg.sector_of(symbol),
            )
        )
    return products


def product_map(cfg: RiskConfig | None = None) -> dict[str, ProductSpec]:
    return {p.symbol: p for p in load_universe(cfg)}
