import pandas as pd

from future_quant.channels import analyze_channel
from future_quant.config import QuantConfig
from future_quant.core.types import AnalysisResult
from future_quant.data.tqsdk_provider import TqSdkProvider
from future_quant.indicators import add_indicators
from future_quant.market_state import detect_market_state
from future_quant.pushes import detect_pushes
from future_quant.signals import generate_signal


class QuantEngine:
    def __init__(self, config: QuantConfig | None = None) -> None:
        self.config = config or QuantConfig()
        self._provider: TqSdkProvider | None = None

    @property
    def provider(self) -> TqSdkProvider:
        if self._provider is None:
            self._provider = TqSdkProvider()
        return self._provider

    def with_provider(self, provider: TqSdkProvider) -> "QuantEngine":
        """Inject a pre-configured provider (e.g. for batch scans / backtests)."""
        self._provider = provider
        return self

    def analyze_df(self, df: pd.DataFrame, account_equity: float | None = None) -> AnalysisResult:
        data = add_indicators(df, atr_period=self.config.atr_period)
        market_state = detect_market_state(data, self.config)
        push_set = detect_pushes(data, self.config)
        channel = analyze_channel(data, push_set, self.config)
        signal = generate_signal(
            data,
            market_state=market_state,
            channel=channel,
            push_set=push_set,
            config=self.config,
            account_equity=account_equity,
        )
        return AnalysisResult(
            data=data,
            market_state=market_state,
            channel=channel,
            push_set=push_set,
            signal=signal,
        )

    def analyze_futures_minute(
        self,
        symbol: str,
        exchange: str,
        period: str = "15",
        account_equity: float | None = None,
        provider: TqSdkProvider | None = None,
    ) -> AnalysisResult:
        """Fetch a single symbol's K-line from TqSdk and analyze it.

        ``exchange`` is required because TqSdk symbols are exchange-qualified
        (e.g. CFFEX.IF vs SHFE.rb); the previous AkShare sina symbol is no
        longer used.
        """
        provider = provider or self.provider
        if provider is None:
            raise RuntimeError(
                "TqSdkProvider not available. "
                "Please install tqsdk: pip install tqsdk, "
                "or ensure future_data is importable."
            )
        df = provider.fetch_kline(symbol=symbol, exchange=exchange, period=period)
        return self.analyze_df(df, account_equity=account_equity)
