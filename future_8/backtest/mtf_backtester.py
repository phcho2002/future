"""多周期回测器 —— 15m 为主时间轴，60m 定关键位。

与 backtester.py（单周期假突破）并行的回测路径，仓位管理逻辑复用其模式
（开仓 → 加仓/止盈 → 跟踪止损 → 硬止损），信号调用换成 generate_mtf_signal。

核心设计：
    - 输入 15m DataFrame（主序列），内部按 datetime.floor('h') 聚合出 60m
    - 逐 15m 推进：当前 15m 落入新 60m 桶时刷新 60m zone/趋势缓存
    - 信号用 60m 缓存（截至上一根已收盘 60m）+ 15m 截至当前根 判定
    - 入场在信号触发后下一根 15m 开盘（signal_valid_bars 内有效）
    - 出场（止损/止盈/跟踪）在 15m 上判定

午休/夜盘：floor('h') 聚合天然处理——同一交易小时内多根 15m 归一组，
跨日的小时不会混淆（datetime 带日期）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from fakebreak.config import FakeBreakConfig
from fakebreak.mtf_signal import generate_mtf_signal
from fakebreak.swing import find_swing_lows, find_swing_highs
from fakebreak.types import SignalSide
from backtest.backtester import BacktestConfig, Trade, BacktestResult, DEFAULT_MULTIPLIERS


def aggregate_60m(df_15m: pd.DataFrame) -> pd.DataFrame:
    """从 15m DataFrame 聚合出 60m OHLCV。

    按 datetime.floor('h') 分组：open=first, high=max, low=min, close=last, volume=sum。
    天然处理午休/夜盘——同一交易小时归一组，跨日小时不混淆。
    """
    out = df_15m.copy()
    out["_h"] = out["datetime"].dt.floor("h")
    agg = out.groupby("_h").agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
    ).reset_index().rename(columns={"_h": "datetime"})
    return agg


class MTFBacktester:
    """多周期回测器（60m 关键位 + 15m 形态入场）。"""

    def __init__(
        self,
        strategy_config: FakeBreakConfig | None = None,
        backtest_config: BacktestConfig | None = None,
    ) -> None:
        self.strategy_config = strategy_config or FakeBreakConfig()
        self.backtest_config = backtest_config or BacktestConfig()

    def run(self, df_15m: pd.DataFrame, symbol: str = "SYMBOL") -> BacktestResult:
        """回测单个品种。df_15m 是升序 15m OHLCV DataFrame。"""
        cfg = self.backtest_config
        sc = self.strategy_config
        result = BacktestResult(symbol=symbol)

        df_15m = df_15m.reset_index(drop=True).copy()
        df_15m["datetime"] = pd.to_datetime(df_15m["datetime"])
        n = len(df_15m)
        if n < cfg.warmup + 5:
            return result

        # ── 聚合 60m + 建立 15m→60m 索引映射 ──
        df_60m = aggregate_60m(df_15m)
        # 每根 15m 对应的 60m 在 df_60m 中的位置
        hour_to_60m_idx = {ts: i for i, ts in enumerate(df_60m["datetime"])}
        bar_60m_idx = df_15m["datetime"].dt.floor("h").map(hour_to_60m_idx).to_numpy()

        highs = df_15m["high"].to_numpy(dtype=float)
        lows = df_15m["low"].to_numpy(dtype=float)
        opens = df_15m["open"].to_numpy(dtype=float)
        closes = df_15m["close"].to_numpy(dtype=float)
        mult = cfg.multiplier

        equity = cfg.initial_capital
        result.equity_curve = [equity]

        # 仓位状态（与 backtester.py 一致）
        in_pos = False
        pos_side = ""
        entry_price = 0.0
        add_price = 0.0
        initial_lots = 0.0
        add_lots = 0.0
        entry_idx = 0
        pos_start_equity = 0.0
        stop_price = 0.0
        using_trail = False
        ref_low = 0.0

        pending_side = ""
        pending_expire = 0
        last_60m_used = -1  # 上次喂给信号的 60m 截止索引（避免重复计算）

        for i in range(cfg.warmup, n - 1):
            # ── 持仓管理（与单周期回测器一致，只是时间轴换成 15m）──
            if in_pos:
                total_lots = initial_lots + add_lots

                if using_trail:
                    window_atr = self._atr_at(df_15m, i, sc.atr_period)
                    if window_atr and window_atr > 0:
                        if pos_side == "short":
                            recent_high = float(np.max(highs[max(entry_idx, i - 5):i + 1]))
                            new_stop = recent_high + cfg.trail_atr_mult * window_atr
                            stop_price = min(stop_price, new_stop) if stop_price > 0 else new_stop
                        else:
                            recent_low = float(np.min(lows[max(entry_idx, i - 5):i + 1]))
                            new_stop = recent_low - cfg.trail_atr_mult * window_atr
                            stop_price = max(stop_price, new_stop) if stop_price > 0 else new_stop

                exit_price = self._check_stop(pos_side, highs[i], lows[i], stop_price)
                if exit_price is None and not using_trail:
                    bars_since = i - entry_idx
                    if bars_since >= cfg.add_bars:
                        exit_price = closes[i]

                if exit_price is not None:
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

                # 加仓判定（未加仓时，N根内突破前低/前高）
                if add_lots == 0 and pos_side == "short":
                    if lows[i] <= ref_low:
                        add_price = self._slipped_entry("short", opens[i + 1]) if i + 1 < n else closes[i]
                        add_lots = initial_lots * cfg.add_ratio
                        using_trail = True
                        window_atr = self._atr_at(df_15m, i, sc.atr_period)
                        if window_atr and window_atr > 0:
                            stop_price = float(highs[entry_idx:i + 1].max()) + cfg.trail_atr_mult * window_atr
                        continue
                elif add_lots == 0 and pos_side == "long":
                    if highs[i] >= ref_low:
                        add_price = self._slipped_entry("long", opens[i + 1]) if i + 1 < n else closes[i]
                        add_lots = initial_lots * cfg.add_ratio
                        using_trail = True
                        window_atr = self._atr_at(df_15m, i, sc.atr_period)
                        if window_atr and window_atr > 0:
                            stop_price = float(lows[entry_idx:i + 1].min()) - cfg.trail_atr_mult * window_atr
                        continue

                continue

            # ── 开仓 ──
            entered = False
            if pending_side and i <= pending_expire:
                side = pending_side
                ep = self._slipped_entry(side, opens[i + 1])
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
                stop_money = pos_start_equity * cfg.hard_stop_pct
                price_stop_dist = stop_money / (initial_lots * mult) if initial_lots * mult > 0 else ep * cfg.hard_stop_pct
                stop_price = ep - price_stop_dist if side == "long" else ep + price_stop_dist
                ref_low = self._recent_swing(df_15m, i, side, sc)
                entered = True
                pending_side = ""
            if pending_side and i >= pending_expire:
                pending_side = ""
            if entered:
                continue

            # ── 跑 MTF 信号 ──
            # 60m 截止到当前 15m 所属小时的前一根已收盘 60m（不含当前未收盘小时）
            cur_60m = bar_60m_idx[i]
            if cur_60m is None or pd.isna(cur_60m) or cur_60m < 1:
                continue
            htf_end = int(cur_60m)  # 60m 截止索引（含当前小时）
            # 只在 60m 有更新时才重算信号（减少计算量）
            if htf_end == last_60m_used and not self._at_15m_bar_close(df_15m, i):
                continue

            window_60m = df_60m.iloc[:htf_end + 1]
            window_15m = df_15m.iloc[:i + 1]

            try:
                sig = generate_mtf_signal(window_60m, window_15m, sc)
            except Exception:  # noqa: BLE001
                sig = None
            if sig is None or not sig.is_valid or sig.side == SignalSide.NONE:
                last_60m_used = htf_end
                continue

            pending_side = sig.side.value
            pending_expire = i + cfg.signal_valid_bars
            last_60m_used = htf_end

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

    # ── 辅助方法（与 backtester.py 一致，时间轴换成 15m）──
    def _at_15m_bar_close(self, df: pd.DataFrame, idx: int) -> bool:
        """总是 True——逐 15m 检查形态是否更新。简化为每根都查。"""
        return True

    def _atr_at(self, df: pd.DataFrame, idx: int, period: int) -> float:
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
        window = df.iloc[max(0, idx - sc.swing_window * 4):idx + 1]  # 15m 上窗口放大4倍≈60m的swing_window
        if side == "short":
            lows = window["low"].to_numpy(dtype=float)
            pts = find_swing_lows(lows, sc.swing_lookback)
        else:
            highs = window["high"].to_numpy(dtype=float)
            pts = find_swing_highs(highs, sc.swing_lookback)
        if pts:
            return float(window.iloc[pts[-1]][("low" if side == "short" else "high")])
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
        c1 = abs(entry) * init_lots * mult * rate * 2
        c2 = abs(add_entry) * add_l * mult * rate * 2 if add_l > 0 else 0.0
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
