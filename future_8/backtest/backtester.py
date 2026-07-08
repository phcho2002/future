"""向量化滚动回测 —— 带加仓 + 跟踪止损 + 资金管理。

交易逻辑（以空头为例，多头镜像）：
1. 假突破信号触发 → 开空，名义价值 = 总资金 × position_pct(8%)
2. 持仓 add_bars 根内：
   - 价格跌破前低(最近swing_low) → 加仓 1/2 → 立即转跟踪止损(近期高点 - trail_atr×ATR)
   - 未跌破前低 → 全部止盈平仓
3. 未加仓时 → 硬止损 = 总资金 × hard_stop_pct(0.9%)
4. 加仓后 → 跟踪止损让利润奔跑

成本：commission(手续费) + slippage(滑点，默认0)。按品种真实合约乘数。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from fakebreak.config import FakeBreakConfig
from fakebreak.signal import generate_signal
from fakebreak.swing import find_swing_lows, find_swing_highs
from fakebreak.types import SignalSide

# 品种合约乘数（元/点）。借自 future_1，补充 LC0 等。
DEFAULT_MULTIPLIERS: dict[str, float] = {
    "IF0": 300.0, "IC0": 200.0, "IH0": 300.0, "IM0": 200.0,
    "RB0": 10.0, "HC0": 10.0, "AU0": 1000.0, "AG0": 15.0,
    "CU0": 5.0, "AL0": 5.0, "ZN0": 5.0, "NI0": 1.0, "SN0": 1.0,
    "M0": 10.0, "Y0": 10.0, "P0": 10.0, "A0": 10.0, "C0": 10.0,
    "SR0": 10.0, "CF0": 5.0, "TA0": 5.0, "MA0": 10.0, "PP0": 5.0,
    "I0": 100.0, "J0": 100.0, "JM0": 60.0,
    "FU0": 10.0, "BU0": 10.0, "RU0": 10.0,
    "FG0": 20.0, "SA0": 20.0, "SF0": 5.0, "SM0": 5.0,
    "AP0": 10.0, "CJ0": 5.0, "UR0": 20.0,
    "LC0": 1.0, "SI0": 5.0,
    "TF0": 10000.0, "T0": 10000.0, "TS0": 20000.0,
    "SC0": 1000.0, "LH0": 16.0, "AO0": 20.0, "EB0": 5.0,
    "EG0": 10.0, "V0": 5.0, "L0": 5.0, "PG0": 20.0, "SP0": 10.0,
    "OI0": 10.0, "RM0": 10.0, "PF0": 5.0, "PX0": 5.0, "NR0": 10.0,
    "BR0": 5.0, "SH0": 30.0, "EC0": 50.0, "LU0": 10.0,
}


@dataclass(frozen=True)
class BacktestConfig:
    signal_valid_bars: int = 5      # 信号触发后 N 根内有效
    commission_rate: float = 0.00005  # 单边手续费率
    slippage_points: float = 0.0    # 单边滑点(价格点)，默认0
    multiplier: float = 10.0       # 合约乘数(元/点)
    warmup: int = 120              # 预热根数
    initial_capital: float = 5_000_000.0  # 初始资金500万
    # ── 仓位管理 ──
    position_pct: float = 0.08     # 每次开仓名义价值占总资金比例
    hard_stop_pct: float = 0.009   # 未加仓时硬止损(总资金的0.9%)
    add_bars: int = 5              # 加仓/止盈判定窗口N根
    add_ratio: float = 0.5         # 加仓比例(初始仓位的)
    trail_atr_mult: float = 1.2    # 跟踪止损 ATR 倍数


@dataclass
class Trade:
    symbol: str
    side: str
    entry_idx: int
    exit_idx: int
    entry_price: float
    exit_price: float
    pnl: float
    is_win: bool
    bars_held: int
    lots: float = 1.0       # 总手数（含加仓）
    added: bool = False     # 是否加过仓
    reason: str = ""


@dataclass
class BacktestResult:
    symbol: str
    trades: list[Trade] = field(default_factory=list)
    net_pnl: float = 0.0
    win_rate: float = 0.0
    max_drawdown: float = 0.0
    sharpe: float = 0.0
    profit_factor: float = 0.0
    equity_curve: list[float] = field(default_factory=list)

    @property
    def n_trades(self) -> int:
        return len(self.trades)

    def summary(self) -> dict:
        return {
            "symbol": self.symbol,
            "trades": self.n_trades,
            "net_pnl": round(self.net_pnl, 2),
            "win_rate": round(self.win_rate, 4),
            "max_drawdown": round(self.max_drawdown, 4),
            "sharpe": round(self.sharpe, 4),
            "profit_factor": round(self.profit_factor, 4),
        }


class Backtester:
    def __init__(
        self,
        strategy_config: FakeBreakConfig | None = None,
        backtest_config: BacktestConfig | None = None,
    ) -> None:
        self.strategy_config = strategy_config or FakeBreakConfig()
        self.backtest_config = backtest_config or BacktestConfig()

    def run(self, df: pd.DataFrame, symbol: str = "SYMBOL") -> BacktestResult:
        """回测单个品种（带加仓+跟踪止损+资金管理）。"""
        cfg = self.backtest_config
        sc = self.strategy_config
        result = BacktestResult(symbol=symbol)

        df = df.reset_index(drop=True)
        n = len(df)
        if n < cfg.warmup + 5:
            return result

        highs = df["high"].to_numpy(dtype=float)
        lows = df["low"].to_numpy(dtype=float)
        opens = df["open"].to_numpy(dtype=float)
        closes = df["close"].to_numpy(dtype=float)
        mult = cfg.multiplier

        equity = cfg.initial_capital
        result.equity_curve = [equity]

        # 仓位状态
        in_pos = False
        pos_side = ""
        entry_price = 0.0          # 初始开仓价
        add_price = 0.0            # 加仓价（0=未加仓）
        initial_lots = 0.0         # 初始手数
        add_lots = 0.0             # 加仓手数
        entry_idx = 0
        pos_start_equity = 0.0     # 开仓时的资金（算硬止损用）
        stop_price = 0.0           # 当前止损价
        using_trail = False        # 是否在用跟踪止损
        ref_low = 0.0              # 前低（开仓时的 swing_low，加仓触发基准）

        # 待入场信号
        pending_side = ""
        pending_expire = 0

        for i in range(cfg.warmup, n - 1):
            # ── 持仓管理 ──
            if in_pos:
                total_lots = initial_lots + add_lots

                # 更新跟踪止损（加仓后启用）
                if using_trail:
                    window_atr = self._atr_at(df, i, sc.atr_period)
                    if window_atr and window_atr > 0:
                        if pos_side == "short":
                            recent_high = float(np.max(highs[max(entry_idx, i - 5):i + 1]))
                            new_stop = recent_high + cfg.trail_atr_mult * window_atr
                            stop_price = min(stop_price, new_stop) if stop_price > 0 else new_stop
                        else:
                            recent_low = float(np.min(lows[max(entry_idx, i - 5):i + 1]))
                            new_stop = recent_low - cfg.trail_atr_mult * window_atr
                            stop_price = max(stop_price, new_stop) if stop_price > 0 else new_stop

                # 检查止损
                exit_price = self._check_stop(pos_side, highs[i], lows[i], stop_price)
                if exit_price is None and not using_trail:
                    # 未加仓时检查止盈（N根内未突破前低）
                    bars_since = i - entry_idx
                    if bars_since >= cfg.add_bars:
                        exit_price = closes[i]  # 市价止盈

                if exit_price is not None:
                    # 计算总盈亏（初始仓+加仓仓）
                    pnl = self._position_pnl(pos_side, entry_price, add_price, exit_price,
                                             initial_lots, add_lots, mult)
                    cost = self._total_cost(entry_price, add_price, exit_price,
                                            initial_lots, add_lots, mult, cfg)
                    net = pnl - cost
                    equity += net
                    result.trades.append(Trade(
                        symbol=symbol, side=pos_side,
                        entry_idx=entry_idx, exit_idx=i,
                        entry_price=entry_price, exit_price=exit_price,
                        pnl=net, is_win=net > 0, bars_held=i - entry_idx,
                        lots=total_lots, added=add_lots > 0,
                    ))
                    in_pos = False
                    result.equity_curve.append(equity)
                    continue

                # 检查加仓（未加仓时，N根内突破前低）
                if add_lots == 0 and pos_side == "short":
                    if lows[i] <= ref_low:
                        add_price = self._slipped_entry("short", opens[i + 1]) if i + 1 < n else closes[i]
                        add_lots = initial_lots * cfg.add_ratio
                        using_trail = True
                        # 初始化跟踪止损
                        window_atr = self._atr_at(df, i, sc.atr_period)
                        if window_atr and window_atr > 0:
                            stop_price = float(highs[entry_idx:i + 1].max()) + cfg.trail_atr_mult * window_atr
                        continue
                elif add_lots == 0 and pos_side == "long":
                    if highs[i] >= ref_low:
                        add_price = self._slipped_entry("long", opens[i + 1]) if i + 1 < n else closes[i]
                        add_lots = initial_lots * cfg.add_ratio
                        using_trail = True
                        window_atr = self._atr_at(df, i, sc.atr_period)
                        if window_atr and window_atr > 0:
                            stop_price = float(lows[entry_idx:i + 1].min()) - cfg.trail_atr_mult * window_atr
                        continue

                continue  # 持仓中，跳过开新仓

            # ── 开仓 ──
            # 1) 待入场信号
            entered = False
            if pending_side and i <= pending_expire:
                side = pending_side
                ep = self._slipped_entry(side, opens[i + 1])
                # 算手数：名义价值 = equity × position_pct
                notional = equity * cfg.position_pct
                initial_lots = notional / (ep * mult) if ep * mult > 0 else 1.0
                entry_price = ep
                pos_side = side
                in_pos = True
                entry_idx = i + 1
                pos_start_equity = equity
                add_lots = 0
                add_price = 0.0
                using_trail = False
                # 硬止损价（总资金的 hard_stop_pct 换算成价格）
                stop_money = pos_start_equity * cfg.hard_stop_pct
                price_stop_dist = stop_money / (initial_lots * mult) if initial_lots * mult > 0 else ep * cfg.hard_stop_pct
                stop_price = ep - price_stop_dist if side == "long" else ep + price_stop_dist
                # 前低（最近 swing_low/Low）：做空用最近 swing_low，做多用最近 swing_high
                ref_low = self._recent_swing(df, i, side, sc)
                entered = True
                pending_side = ""
            if pending_side and i >= pending_expire:
                pending_side = ""
            if entered:
                continue

            # 2) 跑信号
            window = df.iloc[:i + 1]
            try:
                sig = generate_signal(window, sc)
            except Exception:
                sig = None
            if sig is None or not sig.is_valid or sig.side == SignalSide.NONE:
                continue
            pending_side = sig.side.value
            pending_expire = i + cfg.signal_valid_bars

        # 收盘强平
        if in_pos:
            last_close = float(closes[-1])
            total_lots = initial_lots + add_lots
            pnl = self._position_pnl(pos_side, entry_price, add_price, last_close,
                                     initial_lots, add_lots, mult)
            cost = self._total_cost(entry_price, add_price, last_close,
                                    initial_lots, add_lots, mult, cfg)
            net = pnl - cost
            equity += net
            result.trades.append(Trade(
                symbol=symbol, side=pos_side,
                entry_idx=entry_idx, exit_idx=n - 1,
                entry_price=entry_price, exit_price=last_close,
                pnl=net, is_win=net > 0, bars_held=n - 1 - entry_idx,
                lots=total_lots, added=add_lots > 0,
            ))
            result.equity_curve.append(equity)

        self._finalize(result)
        return result

    # ── 辅助方法 ──
    def _atr_at(self, df: pd.DataFrame, idx: int, period: int) -> float:
        """取 idx 处的 ATR 值（简单近似：最近 period 根 TR 均值）。"""
        if idx < period:
            return 0.0
        h = df["high"].to_numpy(dtype=float)[max(0, idx - period):idx + 1]
        l = df["low"].to_numpy(dtype=float)[max(0, idx - period):idx + 1]
        c = df["close"].to_numpy(dtype=float)
        prev_c = c[max(0, idx - period):idx]
        trs = []
        for k in range(len(prev_c)):
            tr = max(h[k] - l[k], abs(h[k] - prev_c[k]), abs(l[k] - prev_c[k]))
            trs.append(tr)
        return float(np.mean(trs)) if trs else 0.0

    def _recent_swing(self, df: pd.DataFrame, idx: int, side: str, sc: FakeBreakConfig) -> float:
        """取开仓前最近的 swing 点：做空用 swing_low，做多用 swing_high。"""
        window = df.iloc[max(0, idx - sc.swing_window):idx + 1]
        if side == "short":
            lows = window["low"].to_numpy(dtype=float)
            pts = find_swing_lows(lows, sc.swing_lookback)
        else:
            highs = window["high"].to_numpy(dtype=float)
            pts = find_swing_highs(highs, sc.swing_lookback)
        if pts:
            return float(window.iloc[pts[-1]][("low" if side == "short" else "high")])
        # 兜底：窗口极值
        return float(window["low"].min() if side == "short" else window["high"].max())

    def _check_stop(self, side: str, bar_high: float, bar_low: float, stop: float) -> float | None:
        if side == "long":
            if bar_low <= stop:
                return stop
        else:
            if bar_high >= stop:
                return stop
        return None

    def _position_pnl(self, side: str, entry: float, add_entry: float, exit_price: float,
                      init_lots: float, add_l: float, mult: float) -> float:
        direction = 1.0 if side == "long" else -1.0
        pnl1 = direction * (exit_price - entry) * init_lots * mult
        pnl2 = direction * (exit_price - add_entry) * add_l * mult if add_l > 0 else 0.0
        return pnl1 + pnl2

    def _total_cost(self, entry: float, add_entry: float, exit_price: float,
                    init_lots: float, add_l: float, mult: float, cfg: BacktestConfig) -> float:
        rate = cfg.commission_rate
        slip = cfg.slippage_points
        # 手续费：每次开/平的名义价值 × rate
        c1 = abs(entry) * init_lots * mult * rate * 2  # 开+平
        c2 = abs(add_entry) * add_l * mult * rate * 2 if add_l > 0 else 0.0
        # 滑点
        s1 = slip * init_lots * mult * 2
        s2 = slip * add_l * mult * 2 if add_l > 0 else 0.0
        return c1 + c2 + s1 + s2

    def _slipped_entry(self, side: str, open_price: float) -> float:
        slip = self.backtest_config.slippage_points
        return open_price + slip if side == "long" else open_price - slip

    def _finalize(self, result: BacktestResult) -> None:
        trades = result.trades
        result.net_pnl = sum(t.pnl for t in trades)
        if trades:
            wins = [t.pnl for t in trades if t.pnl > 0]
            losses = [abs(t.pnl) for t in trades if t.pnl <= 0]
            result.win_rate = len(wins) / len(trades)
            result.profit_factor = (sum(wins) / sum(losses)) if losses else float("inf")

        curve = np.asarray(result.equity_curve, dtype=float)
        if len(curve) > 1:
            peak = np.maximum.accumulate(curve)
            dd = (peak - curve) / np.where(peak > 0, peak, 1.0)
            result.max_drawdown = float(dd.max()) if dd.size else 0.0
            rets = np.diff(curve) / np.where(curve[:-1] > 0, curve[:-1], 1.0)
            if rets.std() > 0:
                result.sharpe = float(rets.mean() / rets.std() * np.sqrt(len(rets)))
