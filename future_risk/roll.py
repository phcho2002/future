"""Contract roll: simultaneous close + open (平开同时).

换月原则
--------
1. 换月不改变策略对「品种」的风险意图（方向与目标手数保持不变）。
2. 旧合约平仓 + 新合约开仓在同一决策时刻生成，成对下发。
3. 实盘执行顺序建议：先挂平仓、再挂开仓（或交易接口支持的组合单）；
   本模块在意图层把两者绑定为同一 RollPlan，避免只平不开或只开不平。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


Side = Literal["BUY", "SELL"]
Offset = Literal["OPEN", "CLOSE", "CLOSETODAY", "CLOSEYESTERDAY"]


@dataclass(frozen=True)
class LegOrder:
    """单腿委托意图。"""

    contract: str          # 具体合约，如 SHFE.rb2510 / rb2510
    side: Side
    offset: Offset
    lots: int
    symbol: str            # 品种占位，如 RB0
    role: Literal["roll_close", "roll_open", "trade"]
    ref_price: float | None = None
    comment: str = ""


@dataclass
class RollPlan:
    """同一品种的一次换月：平旧 + 开新，手数一致、方向继承。"""

    symbol: str
    from_contract: str
    to_contract: str
    lots: int
    direction: int                 # 持仓方向：+1 多 / -1 空
    simultaneous: bool = True
    legs: list[LegOrder] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.legs:
            self.legs = build_roll_legs(
                symbol=self.symbol,
                from_contract=self.from_contract,
                to_contract=self.to_contract,
                lots=self.lots,
                direction=self.direction,
            )


def build_roll_legs(
    symbol: str,
    from_contract: str,
    to_contract: str,
    lots: int,
    direction: int,
) -> list[LegOrder]:
    """生成换月双腿。

    多头换月：SELL CLOSE 旧 + BUY OPEN 新
    空头换月：BUY  CLOSE 旧 + SELL OPEN 新
    """
    if lots <= 0:
        return []
    if direction not in (1, -1):
        raise ValueError("direction must be +1 or -1")

    if direction == 1:
        close_side: Side = "SELL"
        open_side: Side = "BUY"
    else:
        close_side = "BUY"
        open_side = "SELL"

    return [
        LegOrder(
            contract=from_contract,
            side=close_side,
            offset="CLOSE",
            lots=lots,
            symbol=symbol,
            role="roll_close",
            comment=f"roll close {from_contract} -> {to_contract}",
        ),
        LegOrder(
            contract=to_contract,
            side=open_side,
            offset="OPEN",
            lots=lots,
            symbol=symbol,
            role="roll_open",
            comment=f"roll open {from_contract} -> {to_contract}",
        ),
    ]


def build_roll_orders(
    symbol: str,
    from_contract: str,
    to_contract: str,
    lots: int,
    direction: int,
    simultaneous: bool = True,
) -> RollPlan:
    """对外入口：构造换月计划（平开同时）。"""
    if from_contract == to_contract:
        raise ValueError("from_contract and to_contract must differ")
    return RollPlan(
        symbol=symbol,
        from_contract=from_contract,
        to_contract=to_contract,
        lots=int(lots),
        direction=int(direction),
        simultaneous=simultaneous,
    )


def merge_roll_with_rebalance(
    roll: RollPlan,
    new_target_lots: int,
    new_direction: int,
) -> list[LegOrder]:
    """换月同时发生加减仓时的合并逻辑。

    - 先按旧手数完整换月到新合约（保持风险不断档）
    - 再在新合约上补做目标手数差（加仓 OPEN / 减仓 CLOSE）

    这样「换月」与「调仓」解耦，避免在旧合约上调完再滚。
    """
    legs = list(roll.legs)
    if new_direction != roll.direction:
        # 方向反转：换月后在新合约平掉全部，再反手 —— 由上层拆成 flatten + open
        return legs

    delta = int(new_target_lots) - int(roll.lots)
    if delta == 0:
        return legs

    if delta > 0:
        side: Side = "BUY" if roll.direction == 1 else "SELL"
        legs.append(
            LegOrder(
                contract=roll.to_contract,
                side=side,
                offset="OPEN",
                lots=delta,
                symbol=roll.symbol,
                role="trade",
                comment="post-roll add",
            )
        )
    else:
        side = "SELL" if roll.direction == 1 else "BUY"
        legs.append(
            LegOrder(
                contract=roll.to_contract,
                side=side,
                offset="CLOSE",
                lots=abs(delta),
                symbol=roll.symbol,
                role="trade",
                comment="post-roll reduce",
            )
        )
    return legs
