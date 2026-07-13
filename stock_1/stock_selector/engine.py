from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from stock_selector.data import AkShareStockProvider
from stock_selector.strategy import AnalysisRow, Signal, analyze_daily_data


@dataclass(frozen=True)
class SelectionResult:
    analyses: list[AnalysisRow]
    signals: list[Signal]
    errors: dict[str, str]


class StockSelector:
    def __init__(self, provider: AkShareStockProvider | None = None) -> None:
        self.provider = provider or AkShareStockProvider()

    def analyze_symbols(self, symbols: list[str], start_date: str, end_date: str) -> SelectionResult:
        analyses: list[AnalysisRow] = []
        signals: list[Signal] = []
        errors: dict[str, str] = {}

        for symbol in symbols:
            try:
                daily = self.provider.history(symbol, start_date, end_date)
                symbol_analyses, symbol_signals = analyze_daily_data(symbol, daily)
                analyses.extend(symbol_analyses)
                signals.extend(symbol_signals)
            except Exception as exc:  # Keep batch selection running when a single symbol fails.
                errors[symbol] = str(exc)

        return SelectionResult(analyses=analyses, signals=signals, errors=errors)

    @staticmethod
    def write_outputs(result: SelectionResult, output_dir: Path) -> None:
        output_dir.mkdir(parents=True, exist_ok=True)

        analysis_df = pd.DataFrame([row.__dict__ for row in result.analyses])
        signal_df = pd.DataFrame([row.__dict__ for row in result.signals])
        error_df = pd.DataFrame(
            [{"symbol": symbol, "error": error} for symbol, error in result.errors.items()]
        )

        analysis_df.to_csv(output_dir / "analysis.csv", index=False, encoding="utf-8-sig")
        signal_df.to_csv(output_dir / "signals.csv", index=False, encoding="utf-8-sig")
        error_df.to_csv(output_dir / "errors.csv", index=False, encoding="utf-8-sig")

        with (output_dir / "report.md").open("w", encoding="utf-8") as fh:
            fh.write("# 股票量化选股信号报告\n\n")
            if result.analyses:
                fh.write("## 逐段分析\n\n")
                for row in result.analyses:
                    ratio_prev = "NA" if row.volume_ratio_prev is None else f"{row.volume_ratio_prev:.2f}"
                    ratio_ma20 = "NA" if row.volume_ratio_ma20 is None else f"{row.volume_ratio_ma20:.2f}"
                    fh.write(
                        f"- {row.symbol} {row.date}: 交叉次数={row.cross_count}, "
                        f"金叉日收盘价={row.golden_close:.2f}, 倍量成交量={row.volume_value:.0f}, "
                        f"较前日={ratio_prev}, 较20日均量={ratio_ma20}, {row.note}\n"
                    )
                fh.write("\n")

            fh.write("## 信号表\n\n")
            if not result.signals:
                fh.write("当前数据无信号\n")
            else:
                fh.write("| 股票代码 | 信号类型 | 日期 | 触发价格 | 置信度 | 理由 |\n")
                fh.write("| --- | --- | --- | ---: | --- | --- |\n")
                for signal in result.signals:
                    fh.write(
                        f"| {signal.symbol} | {signal.signal_type} | {signal.date} | "
                        f"{signal.trigger_price:.2f} | {signal.confidence} | {signal.reason} |\n"
                    )

            if result.errors:
                fh.write("\n## 数据错误\n\n")
                for symbol, error in result.errors.items():
                    fh.write(f"- {symbol}: {error}\n")
