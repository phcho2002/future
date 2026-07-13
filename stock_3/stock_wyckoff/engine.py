"""Wyckoff Engine — main orchestrator that combines all analysis modules."""

import pandas as pd

from stock_wyckoff.config import WyckoffConfig
from stock_wyckoff.core.types import AnalysisResult
from stock_wyckoff.indicators import add_indicators
from stock_wyckoff.phase_detector import detect_phase
from stock_wyckoff.range_analyzer import analyze_range
from stock_wyckoff.volume_analyzer import analyze_volume
from stock_wyckoff.stop_behavior import detect_stop_behavior
from stock_wyckoff.signal_generator import generate_signal


class WyckoffEngine:
    """Main engine for Wyckoff Method quantitative analysis."""

    def __init__(self, config: WyckoffConfig | None = None) -> None:
        self.config = config or WyckoffConfig()

    def analyze_df(self, df: pd.DataFrame) -> AnalysisResult:
        """Run full Wyckoff analysis on a DataFrame.

        Parameters
        ----------
        df : pd.DataFrame
            OHLCV DataFrame with columns: open, high, low, close, volume

        Returns
        -------
        AnalysisResult
            Complete analysis including phase, range, volume, stop behavior and signal.
        """
        # 1. Add base indicators (RSI, ATR, wicks, volume MA, etc.)
        data = add_indicators(
            df,
            atr_period=self.config.atr_period,
            rsi_period=self.config.rsi_period,
        )

        # 2. Detect Wyckoff phase
        phase = detect_phase(data, self.config)

        # 3. Analyze price range
        range_analysis = analyze_range(data, self.config)

        # 4. Analyze volume patterns
        volume_analysis = analyze_volume(data, self.config)

        # 5. Detect stop behavior
        stop_behavior = detect_stop_behavior(data, self.config)

        # 6. Generate trading signal
        signal = generate_signal(
            data,
            phase=phase,
            range_analysis=range_analysis,
            volume_analysis=volume_analysis,
            stop_behavior=stop_behavior,
            config=self.config,
        )

        return AnalysisResult(
            data=data,
            phase=phase,
            range_analysis=range_analysis,
            volume_analysis=volume_analysis,
            stop_behavior=stop_behavior,
            signal=signal,
        )
