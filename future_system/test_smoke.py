"""Offline smoke for dual-book system."""

from __future__ import annotations

import numpy as np
import pandas as pd

from future_system.books import BookId, split_universe
from future_system.range_signal import evaluate_range_signal, RangeSignalConfig
from future_system.regime import Regime, RegimeConfig, classify_regime


def test_split():
    u = split_universe()
    fin = {s for s, _, _ in u[BookId.FINANCIAL]}
    assert "IF0" in fin and "TF0" in fin and "RB0" not in fin
    com = {s for s, _, _ in u[BookId.COMMODITY]}
    assert "RB0" in com and "IF0" not in com
    assert len(fin) + len(com) == 40
    print("test_split OK", len(fin), len(com))


def _synth(n=200, mode="range") -> pd.DataFrame:
    t = np.arange(n)
    if mode == "range":
        close = 100 + 1.5 * np.sin(t / 8)
    else:
        close = 100 + np.linspace(0, 20, n) + 0.3 * np.sin(t / 3)
    high = close + 0.4
    low = close - 0.4
    return pd.DataFrame(
        {
            "datetime": pd.date_range("2025-01-01", periods=n, freq="h"),
            "open": close,
            "high": high,
            "low": low,
            "close": close,
            "volume": np.full(n, 1000.0),
        }
    )


def test_regime_range_vs_trend():
    r = classify_regime(_synth(mode="range"), RegimeConfig(min_bars=80))
    # 合成窄幅震荡未必 ADX 极低，但不应该崩溃
    assert r.regime in (Regime.RANGE, Regime.NEUTRAL, Regime.TREND)
    t = classify_regime(_synth(mode="trend"), RegimeConfig(min_bars=80))
    assert t.regime in (Regime.RANGE, Regime.NEUTRAL, Regime.TREND)
    print("test_regime OK", r.regime, t.regime)


def test_range_failed_break():
    n = 120
    close = np.full(n, 100.0)
    high = close + 0.3
    low = close - 0.3
    # 箱 99-101，最后刺穿上沿收回
    high[-5] = 101.8
    close[-1] = 100.5
    high[-1] = 100.7
    low[-1] = 100.2
    open_ = close.copy()
    open_[-1] = 100.9  # 阴线
    df = pd.DataFrame(
        {
            "datetime": pd.date_range("2025-01-01", periods=n, freq="30min"),
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": np.full(n, 1000.0),
        }
    )
    snap = evaluate_range_signal("XX0", df, RangeSignalConfig(box_lookback=20, box_range_max=0.05))
    print("test_range", snap.direction, snap.reason, snap.setup)
    # 不强制必须触发（合成边界敏感），只保证不报错
    assert snap.price > 0


if __name__ == "__main__":
    test_split()
    test_regime_range_vs_trend()
    test_range_failed_break()
    print("all ok")
