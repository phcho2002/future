"""Offline smoke tests (no network)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from future_signal.config import SignalConfig
from future_signal.daily_filter import DailyFilterResult, _build_ranks, apply_daily_filter
from future_signal.second_breakout import SecondBreakoutEngine, evaluate_second_breakout


def test_daily_ban_rules():
    cfg = SignalConfig(ban_long_bottom_n=10, ban_short_top_n=10)
    rows = [
        {"symbol": f"S{i:02d}0", "name": f"n{i}", "总分": 100 - i}
        for i in range(40)
    ]
    df = pd.DataFrame(rows)
    ranks = _build_ranks(df, cfg)
    daily = DailyFilterResult(ranks=ranks, source="test", asof="t", n=40)

    d, _, reason = apply_daily_filter(1, "S000", daily)
    assert d == 1, reason
    d, _, reason = apply_daily_filter(-1, "S000", daily)
    assert d == 0 and "banned_top" in reason, reason

    weak = "S390"
    d, _, reason = apply_daily_filter(1, weak, daily)
    assert d == 0 and "banned_bottom" in reason, reason
    d, _, reason = apply_daily_filter(-1, weak, daily)
    assert d == -1, reason
    print("test_daily_ban_rules OK")


def _make_second_breakout_long(n: int = 400) -> pd.DataFrame:
    rng = np.random.default_rng(7)
    base = np.linspace(100.0, 130.0, n)
    close = base.copy()
    box_lo, box_hi = 250, 300
    box_mid = 118.0
    close[box_lo:box_hi] = box_mid + rng.normal(0, 0.15, box_hi - box_lo)
    close[box_hi] = box_mid + 1.2
    close[box_hi + 1] = box_mid + 0.9
    close[box_hi + 2] = box_mid - 0.2
    close[box_hi + 3] = box_mid - 0.3
    close[box_hi + 4] = box_mid + 1.5
    close[box_hi + 5 :] = np.linspace(box_mid + 1.6, box_mid + 8.0, n - (box_hi + 5))

    high = close + 0.25
    low = close - 0.25
    high[box_lo:box_hi] = np.maximum(high[box_lo:box_hi], box_mid + 0.35)
    low[box_lo:box_hi] = np.minimum(low[box_lo:box_hi], box_mid - 0.35)
    high[box_hi] = box_mid + 1.4
    low[box_hi + 2] = box_mid - 0.5

    vol = np.full(n, 1000.0)
    vol[box_hi] = 1800
    vol[box_hi + 4] = 2200

    return pd.DataFrame(
        {
            "datetime": pd.date_range("2025-01-01", periods=n, freq="h"),
            "open": close.copy(),
            "high": high,
            "low": low,
            "close": close,
            "volume": vol,
        }
    )


def test_second_and_first_events():
    cfg = SignalConfig(
        pattern_lookback=20,
        box_range_max=0.05,
        box_touch_min=2,
        fail_confirm_bars=2,
        second_break_min_gap=2,
        second_break_max_gap=30,
        ema_trend_period=50,
        breakout_threshold_2nd=0.002,
        allow_first_breakout_probe=True,
        first_probe_strength_scale=0.20,
    )
    df = _make_second_breakout_long()
    eng = SecondBreakoutEngine(df, cfg)
    eng.run()
    kinds = [e[4] for e in eng.events]
    assert "first" in kinds, eng.events
    assert "second" in kinds, eng.events
    assert "fail_exit" in kinds, eng.events  # 假突破失败应平试探
    assert len(eng.signals) >= 1
    print(f"test_second_and_first_events OK  events={eng.events}")


def test_probe_strength_smaller_than_full():
    cfg = SignalConfig(
        pattern_lookback=20,
        box_range_max=0.05,
        box_touch_min=2,
        fail_confirm_bars=2,
        second_break_min_gap=2,
        second_break_max_gap=30,
        ema_trend_period=50,
        breakout_threshold_2nd=0.002,
        allow_first_breakout_probe=True,
        first_probe_strength_scale=0.20,
        initial_stop_atr=10.0,  # 放宽，便于 hold 到序列末
        trail_atr_mult=10.0,
    )
    df = _make_second_breakout_long()
    # 截到首次突破当根
    eng = SecondBreakoutEngine(df, cfg)
    eng.run()
    first_idx = next(e[0] for e in eng.events if e[4] == "first")
    snap_first = evaluate_second_breakout("RB0", df.iloc[: first_idx + 1], cfg)
    assert snap_first.entry_tier == "first_probe", snap_first
    assert snap_first.order_now, snap_first
    assert snap_first.strength < 0.35, snap_first.strength

    second_idx = next(e[0] for e in eng.events if e[4] == "second")
    snap_sec = evaluate_second_breakout("RB0", df.iloc[: second_idx + 1], cfg)
    assert snap_sec.entry_tier == "second_full", snap_sec
    assert snap_sec.order_now, snap_sec
    assert snap_sec.strength > snap_first.strength, (snap_first.strength, snap_sec.strength)
    print(
        f"test_probe_strength OK  probe={snap_first.strength:.3f} "
        f"full={snap_sec.strength:.3f}"
    )


def test_no_probe_mode():
    cfg = SignalConfig(
        pattern_lookback=20,
        box_range_max=0.05,
        box_touch_min=2,
        fail_confirm_bars=2,
        second_break_min_gap=2,
        second_break_max_gap=30,
        ema_trend_period=50,
        breakout_threshold_2nd=0.002,
        allow_first_breakout_probe=False,
    )
    df = _make_second_breakout_long()
    eng = SecondBreakoutEngine(df, cfg)
    eng.run()
    kinds = [e[4] for e in eng.events]
    assert "first" not in kinds
    assert "second" in kinds
    print("test_no_probe_mode OK")


if __name__ == "__main__":
    test_daily_ban_rules()
    test_second_and_first_events()
    test_probe_strength_smaller_than_full()
    test_no_probe_mode()
    print("all smoke tests passed")
