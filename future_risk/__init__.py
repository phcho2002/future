"""Volatility targeting + dynamic risk budget for CN commodity futures.

Universe: futures_top40.json
Default capital: 5_000_000 CNY
Roll: simultaneous close + open (平开同时)
"""

from .config import RiskConfig
from .engine import (
    RiskEngine,
    TargetPosition,
    OrderIntent,
    SignalInput,
    position_pnl_cny,
    loss_per_lot_to_stop,
)
from .roll import RollPlan, build_roll_orders
from .universe import load_universe, ProductSpec

__all__ = [
    "RiskConfig",
    "RiskEngine",
    "TargetPosition",
    "OrderIntent",
    "SignalInput",
    "position_pnl_cny",
    "loss_per_lot_to_stop",
    "RollPlan",
    "build_roll_orders",
    "load_universe",
    "ProductSpec",
]
