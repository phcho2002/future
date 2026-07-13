"""Wyckoff Engine — main orchestrator that combines all analysis modules."""

import pandas as pd

from wyckoff_quant.config import WyckoffConfig
from wyckoff_quant.core.types import AnalysisResult
from wyckoff_quant.indicators import add_indicators
from wyckoff_quant.phase_detector import detect_phase
from wyckoff_quant.range_analyzer import analyze_range
from wyckoff_quant.volume_analyzer import analyze_volume
from wyckoff_quant.stop_behavior import detect_stop_behavior
from wyckoff_quant.signal_generator import generate_signal


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
