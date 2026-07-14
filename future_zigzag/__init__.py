"""future_zigzag —— ATR 自适应 + anti-repaint 的 ZigZag 波段切分 + Three Push 评分。

后续反转信号系统的输入地基。

公开 API:
    detect_zigzag(df, config)   跑 ZigZag 切分
    detect_three_push(zz, cfg)  识别 Three Push 模式 + 四维评分
    ZigZagConfig / ThreePushConfig
    SYMBOLS                     回测/验证品种
"""
from .config import (
    SYMBOLS, ZigZagConfig, ThreePushConfig, ContractionConfig, LookaheadConfig,
    SignalConfig, BacktestConfig, DEFAULT_MULTIPLIERS,
)
from .indicators import wilder_atr, true_range, add_atr
from .zigzag import Pivot, ZigZagResult, detect_zigzag
from .three_push import (
    Leg, FourDimScore, ThreePushPattern,
    detect_three_push, latest_three_push,
)
from .contraction import ContractionResult, evaluate_contraction
from .lookahead import LookaheadOutcome, evaluate_pattern, summarize_groups
from .signals import ReversalSignal, detect_all_signals, earliest_signal, best_signal, dedupe_signals
from .backtest import Trade, BacktestResult, run_backtest, rr_sweep

__all__ = [
    "detect_zigzag",
    "ZigZagConfig",
    "ZigZagResult",
    "Pivot",
    "detect_three_push",
    "latest_three_push",
    "Leg",
    "FourDimScore",
    "ThreePushPattern",
    "ThreePushConfig",
    "evaluate_contraction",
    "ContractionResult",
    "ContractionConfig",
    "evaluate_pattern",
    "LookaheadOutcome",
    "LookaheadConfig",
    "summarize_groups",
    "detect_all_signals",
    "earliest_signal",
    "best_signal",
    "dedupe_signals",
    "ReversalSignal",
    "SignalConfig",
    "run_backtest",
    "rr_sweep",
    "Trade",
    "BacktestResult",
    "BacktestConfig",
    "DEFAULT_MULTIPLIERS",
    "SYMBOLS",
    "wilder_atr",
    "true_range",
    "add_atr",
]
