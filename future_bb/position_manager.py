"""
仓位管理模块
实现金字塔加仓和动态止损止盈
"""

import pandas as pd
import numpy as np
from typing import Dict, List, Optional
import config


class Position:
    """持仓类（支持多空双向）"""

    def __init__(
        self,
        symbol: str,
        entry_date: str,
        entry_price: float,
        initial_size: int,
        stop_loss: float,
        atr: float,
        direction: int = 1  # 1=做多, -1=做空
    ):
        self.symbol = symbol
        self.entry_date = entry_date
        self.entry_price = entry_price
        self.initial_size = initial_size
        self.current_size = initial_size
        self.stop_loss = stop_loss
        self.atr = atr
        self.direction = direction  # 1 多 / -1 空

        # 加仓记录
        self.add_positions = []  # [(date, price, size)]

        # 止盈记录
        self.take_profits = []  # [(date, price, size)]

        # 状态
        self.is_closed = False
        self.close_date = None
        self.close_price = None
        self.pnl = 0.0

    def get_average_price(self) -> float:
        """计算平均持仓成本"""
        total_cost = self.entry_price * self.initial_size

        for date, price, size in self.add_positions:
            total_cost += price * size

        total_size = self.initial_size + sum(size for _, _, size in self.add_positions)

        return total_cost / total_size if total_size > 0 else self.entry_price

    def get_total_size(self) -> int:
        """获取当前总持仓"""
        if self.is_closed:
            return 0

        total = self.initial_size + sum(size for _, _, size in self.add_positions)
        total -= sum(size for _, _, size in self.take_profits)

        return total

    def add_position(self, date: str, price: float, size: int):
        """加仓"""
        self.add_positions.append((date, price, size))

    def take_profit(self, date: str, price: float, size: int):
        """止盈"""
        self.take_profits.append((date, price, size))

    def close(self, date: str, price: float, reason: str = "stop_loss"):
        """平仓（按方向计算盈亏）

        盈亏 = (平仓价 - 开仓均价) × direction × 持仓量 + 已止盈部分的盈亏
        做多 direction=1：涨则盈；做空 direction=-1：跌则盈。
        """
        # 先在 is_closed 仍为 False 时计算 size（get_total_size 在 closed 时返回 0）
        total_size = self.get_total_size()
        avg_price = self.get_average_price()

        # 剩余持仓在平仓价的盈亏
        remaining_pnl = (price - avg_price) * self.direction * total_size

        # 已止盈部分的盈亏（止盈价相对平均成本）
        tp_pnl = 0.0
        for _, tp_price, tp_size in self.take_profits:
            tp_pnl += (tp_price - avg_price) * self.direction * tp_size

        self.pnl = remaining_pnl + tp_pnl

        # 最后再标记为已平仓
        self.is_closed = True
        self.close_date = date
        self.close_price = price

    def __repr__(self):
        status = "CLOSED" if self.is_closed else "OPEN"
        side = "LONG" if self.direction == 1 else "SHORT"
        return f"Position({self.symbol} {side}, {status}, size={self.get_total_size()}, pnl={self.pnl:.2f})"


class PositionManager:
    """仓位管理器（支持多空双向）"""

    def __init__(self, initial_capital: float):
        self.initial_capital = initial_capital
        self.current_capital = initial_capital
        # key 用 (symbol, direction) 以便同品种可同时持多空；direction: 'LONG'/'SHORT'
        self.positions: Dict[tuple, Position] = {}
        self.closed_positions: List[Position] = []
        self.config = config

    def _pos_key(self, symbol: str, direction: int) -> tuple:
        return (symbol, 'LONG' if direction == 1 else 'SHORT')

    def can_open_position(self, symbol: str, direction: int = 1) -> bool:
        """
        判断是否可以开仓

        条件：
        1. 该 (品种, 方向) 没有同向持仓（允许同品种反向持仓）
        2. 持仓品种数未超过上限
        """
        if self._pos_key(symbol, direction) in self.positions:
            return False

        if len(self.positions) >= self.config.MAX_POSITIONS:
            return False

        return True

    def open_position(
        self,
        symbol: str,
        date: str,
        entry_price: float,
        stop_loss: float,
        atr: float,
        direction: int = 1  # 1=做多, -1=做空
    ) -> Optional[Position]:
        """
        开仓（支持多空双向）

        Args:
            symbol: 品种代码
            date: 日期
            entry_price: 入场价格
            stop_loss: 止损价格（做多在下方，做空在上方）
            atr: ATR值
            direction: 1=做多, -1=做空

        Returns:
            Position对象或None
        """
        if not self.can_open_position(symbol, direction):
            return None

        # 计算初始仓位大小（按单笔风险，多空对称）
        risk_amount = self.current_capital * self.config.MAX_SINGLE_LOSS
        risk_per_contract = abs(entry_price - stop_loss)
        position_size = int(risk_amount / risk_per_contract * self.config.INITIAL_POSITION)
        position_size = max(1, position_size)

        # 创建持仓
        position = Position(symbol, date, entry_price, position_size, stop_loss, atr, direction=direction)
        self.positions[self._pos_key(symbol, direction)] = position

        side = "做多" if direction == 1 else "做空"
        print(f"[开仓{side}] {date} {symbol} @ {entry_price:.2f}, 手数={position_size}, 止损={stop_loss:.2f}")

        return position

    def update_positions(self, date: str, prices: Dict[str, Dict]) -> List[str]:
        """
        更新所有持仓状态

        Args:
            date: 当前日期
            prices: {symbol: {'open': x, 'high': x, 'low': x, 'close': x, 'atr': x}}

        Returns:
            触发动作的品种列表
        """
        actions = []

        for key, position in list(self.positions.items()):
            symbol = key[0]
            if symbol not in prices:
                continue

            price_data = prices[symbol]
            close = price_data['close']
            low = price_data['low']
            high = price_data['high']
            atr = price_data.get('atr', position.atr)

            # 检查止损（做多看 low 跌破；做空看 high 突破）
            if position.direction == 1 and low <= position.stop_loss:
                self._close_position(key, date, position.stop_loss, reason="stop_loss")
                actions.append(f"{symbol}_LONG_stop_loss")
                continue
            if position.direction == -1 and high >= position.stop_loss:
                self._close_position(key, date, position.stop_loss, reason="stop_loss")
                actions.append(f"{symbol}_SHORT_stop_loss")
                continue

            # 检查加仓条件
            if self._should_add_position(position, close, atr):
                self._add_position(position, date, close)
                actions.append(f"{symbol}_{'LONG' if position.direction==1 else 'SHORT'}_add")

            # 检查止盈条件
            if self._should_take_profit(position, close, atr):
                self._take_profit(position, date, close)
                actions.append(f"{symbol}_{'LONG' if position.direction==1 else 'SHORT'}_take_profit")

            # 更新移动止损（做多上移；做空下移）
            new_stop = self._calculate_trailing_stop(position, close, low, high, atr)
            if position.direction == 1 and new_stop > position.stop_loss:
                position.stop_loss = new_stop
                actions.append(f"{symbol}_LONG_trail_stop")
            elif position.direction == -1 and new_stop < position.stop_loss:
                position.stop_loss = new_stop
                actions.append(f"{symbol}_SHORT_trail_stop")

        return actions

    def _should_add_position(self, position: Position, current_price: float, atr: float) -> bool:
        """
        判断是否应该加仓（多空通用）

        加仓条件：
        1. 当前盈利（按方向）
        2. 加仓次数未超过限制
        3. 总仓位不超过上限
        """
        if position.is_closed:
            return False

        # 当前盈利（按方向）
        unrealized_pnl = (current_price - position.entry_price) * position.direction * position.initial_size
        if unrealized_pnl <= 0:
            return False

        # 加仓次数限制
        num_adds = len(position.add_positions)
        if num_adds >= 3:  # 最多3次加仓
            return False

        # 总仓位限制
        total_size = position.get_total_size()
        max_size = int(position.initial_size / self.config.INITIAL_POSITION * self.config.MAX_POSITION)
        if total_size >= max_size:
            return False

        # 加仓条件：盈利足够（按方向计算盈利率）
        # 做多：profit_ratio = (price-entry)/entry；做空：profit_ratio = (entry-price)/entry
        profit_ratio = (current_price - position.entry_price) * position.direction / position.entry_price

        if num_adds == 0 and profit_ratio >= 0.02:  # 第一次加仓：盈利2%
            return True
        elif num_adds == 1 and profit_ratio >= 0.05:  # 第二次加仓：盈利5%
            return True
        elif num_adds == 2 and profit_ratio >= 0.10:  # 第三次加仓：盈利10%
            return True

        return False

    def _add_position(self, position: Position, date: str, price: float):
        """执行加仓"""
        num_adds = len(position.add_positions)

        if num_adds == 0:
            add_size = int(position.initial_size * self.config.ADD_POSITION_1 / self.config.INITIAL_POSITION)
        elif num_adds == 1:
            add_size = int(position.initial_size * self.config.ADD_POSITION_2 / self.config.INITIAL_POSITION)
        else:
            add_size = int(position.initial_size * self.config.ADD_POSITION_3 / self.config.INITIAL_POSITION)

        position.add_position(date, price, add_size)
        side = "LONG" if position.direction == 1 else "SHORT"
        print(f"[加仓{side}] {date} {position.symbol} @ {price:.2f}, 手数={add_size}, 总手数={position.get_total_size()}")

    def _should_take_profit(self, position: Position, current_price: float, atr: float) -> bool:
        """判断是否应该止盈（多空通用）"""
        if position.is_closed:
            return False

        # 已止盈次数
        num_tps = len(position.take_profits)
        if num_tps >= 2:  # 最多2次分批止盈
            return False

        # 计算盈利空间（以止损空间为单位，按方向）
        risk = abs(position.entry_price - position.stop_loss)
        profit = (current_price - position.entry_price) * position.direction
        profit_ratio = profit / risk if risk > 0 else 0

        # 第一次止盈：2倍止损空间
        if num_tps == 0 and profit_ratio >= self.config.PROFIT_TAKE_1_RATIO:
            return True

        # 第二次止盈：5倍止损空间
        if num_tps == 1 and profit_ratio >= self.config.PROFIT_TAKE_2_RATIO:
            return True

        return False

    def _take_profit(self, position: Position, date: str, price: float):
        """执行止盈"""
        num_tps = len(position.take_profits)
        current_size = position.get_total_size()

        if num_tps == 0:
            # 第一次止盈50%
            tp_size = int(current_size * 0.5)
        else:
            # 第二次止盈30%
            tp_size = int(current_size * 0.3)

        position.take_profit(date, price, tp_size)
        side = "LONG" if position.direction == 1 else "SHORT"
        print(f"[止盈{side}] {date} {position.symbol} @ {price:.2f}, 手数={tp_size}, 剩余={position.get_total_size()}")

    def _calculate_trailing_stop(
        self,
        position: Position,
        current_price: float,
        current_low: float,
        current_high: float,
        atr: float
    ) -> float:
        """
        计算移动止损（多空对称）

        做多盈利后：止损 = 当前低点 - 0.5×ATR（只上移）
        做空盈利后：止损 = 当前高点 + 0.5×ATR（只下移）
        """
        # 当前盈利（按方向）
        unrealized_pnl = (current_price - position.entry_price) * position.direction * position.get_total_size()

        if unrealized_pnl > 0:
            if position.direction == 1:
                # 做多盈利：前低 - 0.5 ATR，只上移
                trailing_stop = current_low - atr * 0.5
                return max(trailing_stop, position.stop_loss)
            else:
                # 做空盈利：前高 + 0.5 ATR，只下移
                trailing_stop = current_high + atr * 0.5
                return min(trailing_stop, position.stop_loss)
        else:
            # 亏损状态：保持初始止损
            return position.stop_loss

    def close_position(self, symbol: str, date: str, price: float, reason: str = "signal", direction: int = 1):
        """手动平仓（指定方向）"""
        self._close_position(self._pos_key(symbol, direction), date, price, reason)

    def _close_position(self, key: tuple, date: str, price: float, reason: str):
        """内部平仓方法"""
        if key not in self.positions:
            return

        position = self.positions[key]
        position.close(date, price, reason)

        # 更新资金
        self.current_capital += position.pnl

        # 移动到已平仓列表
        self.closed_positions.append(position)
        del self.positions[key]

        side = "做多" if position.direction == 1 else "做空"
        print(f"[平仓{side}] {date} {position.symbol} @ {price:.2f}, 原因={reason}, 盈亏={position.pnl:.2f}, 资金={self.current_capital:.2f}")

    def get_total_exposure(self) -> float:
        """获取总持仓敞口（市值）"""
        # 简化计算：这里需要当前价格，实际应用中需要传入
        return sum(pos.get_total_size() * pos.entry_price for pos in self.positions.values())

    def get_statistics(self) -> Dict:
        """获取交易统计"""
        if not self.closed_positions:
            return {
                'total_trades': 0,
                'win_rate': 0.0,
                'avg_profit': 0.0,
                'total_pnl': 0.0,
                'return_rate': 0.0,
            }

        total_trades = len(self.closed_positions)
        winning_trades = sum(1 for pos in self.closed_positions if pos.pnl > 0)
        win_rate = winning_trades / total_trades

        total_pnl = sum(pos.pnl for pos in self.closed_positions)
        avg_profit = total_pnl / total_trades

        return_rate = (self.current_capital - self.initial_capital) / self.initial_capital

        return {
            'total_trades': total_trades,
            'winning_trades': winning_trades,
            'losing_trades': total_trades - winning_trades,
            'win_rate': win_rate,
            'avg_profit': avg_profit,
            'total_pnl': total_pnl,
            'return_rate': return_rate,
            'final_capital': self.current_capital,
        }


# 测试代码
if __name__ == "__main__":
    print("=== 测试仓位管理模块 ===")

    # 创建仓位管理器
    manager = PositionManager(initial_capital=100000)

    # 模拟开仓
    pos = manager.open_position(
        symbol="RB0",
        date="20240101",
        entry_price=3500,
        stop_loss=3450,
        atr=50
    )

    # 模拟价格变动
    dates = ['20240102', '20240103', '20240104', '20240105']
    prices_list = [
        {'close': 3580, 'low': 3560, 'high': 3590, 'atr': 50},  # 盈利，触发加仓
        {'close': 3650, 'low': 3630, 'high': 3660, 'atr': 52},  # 继续盈利
        {'close': 3750, 'low': 3730, 'high': 3760, 'atr': 55},  # 触发止盈
        {'close': 3700, 'low': 3680, 'high': 3720, 'atr': 53},  # 回调
    ]

    for date, prices in zip(dates, prices_list):
        print(f"\n=== {date} ===")
        actions = manager.update_positions(date, {'RB0': prices})
        print(f"动作: {actions}")

    # 手动平仓
    manager.close_position('RB0', '20240106', 3710, reason="manual")

    # 统计
    stats = manager.get_statistics()
    print(f"\n=== 交易统计 ===")
    for key, value in stats.items():
        print(f"  {key}: {value}")
