"""future_twohigh —— 两高两低顺势突破系统。

趋势 → 整理（两高两低）→ 顺势放量突破。

管线：
    detect_zigzag(df, cfg)       跑 ZigZag 切分（复用 future_zigzag）
    detect_two_high(zz, cfg)     识别两高两低形态（矩形/三角）
    evaluate_quality(pat, df)    整理质量软评分（量能/斐波那契/时长）
    detect_breakout(pat, df)     顺势放量突破信号触发
    run_backtest(signals, cfg)   回测（复用 future_zigzag.backtest 引擎）

区别于 future_zigzag（反转）/ future_8（假突破反转）：本系统是顺势突破——
在已确认的趋势中继整理末端，顺原趋势方向突破入场。
"""
from .config import (
    ZigZagConfig, TwoHighConfig, ConsolidationConfig,
    BreakoutConfig, BacktestConfig, DEFAULT_MULTIPLIERS, SYMBOLS,
)
from .pattern import (
    TwoHighPattern, detect_two_high, latest_two_high,
)
from .quality import ConsolidationResult, evaluate_quality
from .signals import BreakoutSignal, detect_breakout
from .backtest import run_backtest, BacktestResult, Trade
from .capital_backtest import simulate_capital, CapitalResult

__all__ = [
    # config
    "ZigZagConfig", "TwoHighConfig", "ConsolidationConfig",
    "BreakoutConfig", "BacktestConfig", "DEFAULT_MULTIPLIERS", "SYMBOLS",
    # pattern
    "TwoHighPattern", "detect_two_high", "latest_two_high",
    # quality
    "ConsolidationResult", "evaluate_quality",
    # signals
    "BreakoutSignal", "detect_breakout",
    # backtest
    "run_backtest", "BacktestResult", "Trade",
    # capital backtest
    "simulate_capital", "CapitalResult",
]

__version__ = "0.1.0"
