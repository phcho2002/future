"""回测薄封装 —— 复用 future_zigzag.backtest 通用引擎。

future_zigzag.backtest.run_backtest 是品种无关、信号类型无关的通用引擎：
它接受 list[(symbol, signal, df, atr)]，signal 只要有
.trigger_idx/.side/.entry/.stop/.signal_type 即可（duck-typing）。
我们的 BreakoutSignal 完全满足，故直接复用，省去重写。

唯一适配点：run_backtest 用 future_zigzag.config.BacktestConfig 构建 per-symbol
配置并读 cfg.target_rr_by_type。本模块把 future_twohigh.BreakoutConfig 的字段
映射成 future_zigzag.config.BacktestConfig，并把 target_rr 设到 "breakout" 键。

anti-repaint：信号 trigger_idx 已收盘，入场用次根开盘（引擎内置），全程无未来信息。
"""
from __future__ import annotations

import pandas as pd

from future_zigzag.config import BacktestConfig as ZZBacktestConfig
from future_zigzag.backtest import run_backtest as _zz_run_backtest, BacktestResult, Trade

from .config import BacktestConfig
from .signals import BreakoutSignal


def _to_zz_cfg(cfg: BacktestConfig, target_rr: float) -> ZZBacktestConfig:
    """把 future_twohigh.BreakoutConfig 映射成 future_zigzag.BacktestConfig。"""
    return ZZBacktestConfig(
        commission_rate=cfg.commission_rate,
        slippage_points=cfg.slippage_points,
        multiplier=cfg.multiplier,
        max_hold_bars=cfg.max_hold_bars,
        initial_capital=cfg.initial_capital,
        stop_mode=cfg.stop_mode,
        risk_pct=cfg.risk_pct,
        target_rr_by_type={"breakout": target_rr},
    )


def run_backtest(signals: list[tuple[str, BreakoutSignal, pd.DataFrame, pd.Series]],
                 cfg: BacktestConfig | None = None) -> BacktestResult:
    """对一批突破信号跑独立结算回测（复用 future_zigzag 引擎）。

    Parameters
    ----------
    signals : list of (symbol, BreakoutSignal, df, atr)
    cfg : future_twohigh.BacktestConfig

    Returns
    -------
    future_zigzag.backtest.BacktestResult
    """
    cfg = cfg or BacktestConfig()
    zz_cfg = _to_zz_cfg(cfg, cfg.target_rr_by_type.get("breakout", 2.0))
    return _zz_run_backtest(signals, zz_cfg)


__all__ = ["run_backtest", "BacktestResult", "Trade"]
