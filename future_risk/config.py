"""Risk / sizing defaults for 500万 top40 book."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

WORK_AI = Path(__file__).resolve().parents[1]
TOP40_JSON = WORK_AI / "futures_top40.json"
DB_PATH = WORK_AI / "futures_data.db"


# 板块映射：用于组合风险预算（相关品种共享上限）
SECTOR_MAP: dict[str, str] = {
    # 股指
    "IM0": "equity_index",
    "IC0": "equity_index",
    "IF0": "equity_index",
    "IH0": "equity_index",
    # 国债
    "TF0": "bond",
    "TS0": "bond",
    # 贵金属
    "AU0": "precious",
    "AG0": "precious",
    # 有色
    "CU0": "base_metal",
    "AL0": "base_metal",
    "NI0": "base_metal",
    "SN0": "base_metal",
    "ZN0": "base_metal",
    "AO0": "base_metal",
    # 黑色
    "RB0": "black",
    "HC0": "black",
    "I0": "black",
    "JM0": "black",
    # 建材化工（玻璃纯碱锰硅）
    "FG0": "building_chem",
    "SA0": "building_chem",
    "SM0": "building_chem",
    # 能源
    "SC0": "energy",
    # 化工
    "TA0": "chem",
    "MA0": "chem",
    "PP0": "chem",
    "L0": "chem",
    "V0": "chem",
    "EB0": "chem",
    "RU0": "chem",
    "SP0": "chem",
    # 油脂油料农产品
    "Y0": "agri",
    "P0": "agri",
    "OI0": "agri",
    "M0": "agri",
    "RM0": "agri",
    "C0": "agri",
    "CF0": "agri",
    "SR0": "agri",
    # 生猪
    "LH0": "livestock",
    # 新能源金属
    "LC0": "lithium",
}

# 近似保证金比例（占合约价值）。实盘应以期货公司为准覆盖。
DEFAULT_MARGIN_RATE: dict[str, float] = {
    "equity_index": 0.12,
    "bond": 0.02,
    "precious": 0.10,
    "base_metal": 0.10,
    "black": 0.10,
    "building_chem": 0.12,
    "energy": 0.12,
    "chem": 0.10,
    "agri": 0.09,
    "livestock": 0.12,
    "lithium": 0.14,
}

# 板块风险预算上限（占组合目标风险的比例）
DEFAULT_SECTOR_CAP: dict[str, float] = {
    "equity_index": 0.30,
    "bond": 0.20,
    "precious": 0.20,
    "base_metal": 0.25,
    "black": 0.30,
    "building_chem": 0.20,
    "energy": 0.15,
    "chem": 0.25,
    "agri": 0.25,
    "livestock": 0.10,
    "lithium": 0.10,
}


@dataclass
class RiskConfig:
    """500万账户的波动率目标 + 动态风险预算参数。"""

    capital: float = 5_000_000.0
    # 组合年化波动目标（按 252 交易日）
    target_vol_annual: float = 0.15
    # 单品种最大风险权重（占组合目标风险）
    max_symbol_risk_weight: float = 0.10
    # 单品种最大保证金占用 / 权益
    max_symbol_margin_frac: float = 0.08
    # 组合最大保证金占用 / 权益
    max_portfolio_margin_frac: float = 0.40
    # 单品种最大亏损 / 总资金：触及则无条件止损（硬规则）
    # 0.95% → 400 万账本约 3.8 万；开仓时也按此上限钳制「止损风险」手数
    max_symbol_loss_frac: float = 0.0095
    # 无显式止损价时，用 N 倍日波动估算单手最坏风险以做手数上限（0=关闭该估算）
    loss_cap_vol_mult: float = 2.0
    # 同时持仓品种数上限
    max_positions: int = 12
    # 波动率估计
    vol_lookback: int = 20
    ewma_lambda: float = 0.94
    # 日波动 floor/cap，防止杠杆爆炸或几乎空仓
    vol_floor_daily: float = 0.005   # 0.5%/日
    vol_cap_daily: float = 0.06      # 6%/日
    # 调仓阈值：目标手数与当前手数相对差超过该比例才交易
    rebalance_threshold: float = 0.20
    min_lot_change: int = 1
    # 换月：平旧+开新视为同一风险意图，默认不改变目标手数
    roll_simultaneous: bool = True
    # 路径
    top40_json: Path = field(default_factory=lambda: TOP40_JSON)
    db_path: Path = field(default_factory=lambda: DB_PATH)
    sector_map: dict[str, str] = field(default_factory=lambda: dict(SECTOR_MAP))
    margin_rate_by_sector: dict[str, float] = field(
        default_factory=lambda: dict(DEFAULT_MARGIN_RATE)
    )
    sector_cap: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_SECTOR_CAP))
    # 日历风险乘数（节前等由外部注入，默认 1.0）
    calendar_risk_mult: float = 1.0

    @property
    def target_vol_daily(self) -> float:
        return self.target_vol_annual / (252.0 ** 0.5)

    @property
    def max_symbol_loss_cny(self) -> float:
        """单品种允许的最大亏损金额（元）。"""
        return float(self.capital) * float(self.max_symbol_loss_frac)

    def margin_rate(self, symbol: str) -> float:
        sector = self.sector_map.get(symbol, "chem")
        return self.margin_rate_by_sector.get(sector, 0.10)

    def sector_of(self, symbol: str) -> str:
        return self.sector_map.get(symbol, "other")
