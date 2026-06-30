import pandas as pd

from future_quant.config import QuantConfig
from future_quant.engine import QuantEngine
from future_quant.core.types import MarketRegime


def test_detect_trading_range() -> None:
    df = pd.DataFrame(
        {
            "open": [10, 10.2, 10.1, 10.3, 10.2, 10.4, 10.1, 10.2, 10.3, 10.1],
            "high": [11] * 10,
            "low": [9] * 10,
            "close": [10.1, 10.3, 10.2, 10.4, 10.1, 10.2, 10.3, 10.1, 10.2, 10.3],
            "volume": [100] * 10,
        }
    )
    result = QuantEngine(QuantConfig()).analyze_df(df)
    assert result.market_state.regime == MarketRegime.TRADING_RANGE
    assert result.market_state.allow_wedge_reversal is True


def test_engine_handles_small_df() -> None:
    df = pd.DataFrame(
        {
            "open": [1, 2, 3],
            "high": [2, 3, 4],
            "low": [0, 1, 2],
            "close": [1.5, 2.5, 3.5],
            "volume": [10, 10, 10],
        }
    )
    result = QuantEngine().analyze_df(df)
    assert result.data["atr"].notna().all()
