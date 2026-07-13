import pandas as pd

from stock_selector.strategy import analyze_daily_data


def test_analyze_daily_data_finds_strict_breakout_signal():
    rows = []
    closes = (
        [10.00] * 21
        + [10.20, 10.15, 10.10, 10.05, 10.00, 9.95, 9.90, 9.85]
        + [9.80, 9.85, 9.90, 9.95, 10.00, 10.05, 10.10, 10.15]
        + [10.20, 10.15, 10.10, 10.05, 10.00, 9.95, 9.90, 9.85]
        + [9.80, 9.86, 9.94, 10.02, 10.10, 10.18, 10.26, 10.34, 10.80]
    )
    for i, close in enumerate(closes):
        volume = 1000
        if i == len(closes) - 1:
            volume = 2500
        rows.append(
            {
                "date": pd.Timestamp("2024-01-01") + pd.Timedelta(days=i),
                "open": close - 0.05,
                "high": close + 0.02,
                "low": close - 0.08,
                "close": close,
                "volume": volume,
            }
        )

    analyses, signals = analyze_daily_data("000001", pd.DataFrame(rows))

    assert analyses
    assert signals
    assert signals[-1].signal_type == "买入"
    assert signals[-1].confidence == "中"
    assert "补量确认" in signals[-1].reason


def test_analyze_daily_data_returns_no_signal_without_volume():
    rows = []
    closes = [10.0] * 80
    for i, close in enumerate(closes):
        rows.append(
            {
                "date": pd.Timestamp("2024-01-01") + pd.Timedelta(days=i),
                "open": close,
                "high": close,
                "low": close,
                "close": close,
                "volume": 1000,
            }
        )

    analyses, signals = analyze_daily_data("000001", pd.DataFrame(rows))

    assert analyses == []
    assert signals == []
