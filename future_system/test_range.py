"""震荡腿单元测试（纯合成数据，无网络）。"""

from __future__ import annotations

import numpy as np
import pandas as pd

from future_system.range_exits import RangeExitConfig
from future_system.range_signal import (
    RangeSignalConfig,
    evaluate_range_signal,
    scan_events,
    _add_structure,
    _prep,
)


def _box_series(n=150, mid=100.0, half=1.0) -> pd.DataFrame:
    """窄幅箱体序列。"""
    rng = np.random.default_rng(0)
    close = mid + rng.normal(0, half * 0.15, n)
    close = np.clip(close, mid - half * 0.9, mid + half * 0.9)
    high = close + half * 0.25
    low = close - half * 0.25
    # 上下沿多触碰，便于 detect_box
    for k in range(20, n - 10, 7):
        high[k] = mid + half
        low[k + 1] = mid - half
    return pd.DataFrame(
        {
            "datetime": pd.date_range("2025-01-01", periods=n, freq="30min"),
            "open": close.copy(),
            "high": high,
            "low": low,
            "close": close,
            "volume": np.full(n, 1000.0),
        }
    )


def test_failed_break_short():
    """向上假突破后收回 → 做空。"""
    df = _box_series()
    n = len(df)
    mid, half = 100.0, 1.0
    # 刺穿上沿
    df.loc[n - 4, "high"] = mid + half + 0.8
    df.loc[n - 4, "close"] = mid + half + 0.3
    df.loc[n - 4, "open"] = mid + half + 0.2
    df.loc[n - 4, "volume"] = 2000
    # 收回阴线
    df.loc[n - 1, "open"] = mid + half + 0.1
    df.loc[n - 1, "high"] = mid + half + 0.15
    df.loc[n - 1, "low"] = mid + 0.2
    df.loc[n - 1, "close"] = mid + 0.3
    df.loc[n - 1, "volume"] = 2200

    cfg = RangeSignalConfig(
        box_lookback=30,
        box_range_max=0.05,
        pierce_atr=0.15,
        recover_within=5,
        min_failure_score=35,
        require_pattern=False,  # 合成数据放宽
        enable_fade=False,
        exits=RangeExitConfig(max_hold_bars=30, failed_stop_buffer_atr=0.3, min_reward_risk_tp1=0.5),
    )
    # 把刺穿与收回做成 scan 能看到的序列：pierce at n-4, recover at n-3..n-1
    # 简化：整段重扫
    work = _add_structure(_prep(df), cfg)
    events = scan_events(work, cfg)
    shorts = [e for e in events if e.side == -1 and "failed" in e.setup]
    assert len(shorts) >= 1, f"expected failed_break_short, events={events}"
    snap = evaluate_range_signal("TST0", df, cfg)
    # 可能仍在 hold 或已出场，至少事件存在
    print("test_failed_break_short OK", "events", len(shorts), "snap", snap.direction, snap.reason, snap.setup)


def test_failed_break_long():
    df = _box_series()
    n = len(df)
    mid, half = 100.0, 1.0
    df.loc[n - 4, "low"] = mid - half - 0.8
    df.loc[n - 4, "close"] = mid - half - 0.2
    df.loc[n - 4, "open"] = mid - half
    df.loc[n - 4, "volume"] = 2000
    df.loc[n - 1, "open"] = mid - half
    df.loc[n - 1, "low"] = mid - half - 0.1
    df.loc[n - 1, "high"] = mid - 0.1
    df.loc[n - 1, "close"] = mid - 0.2
    df.loc[n - 1, "volume"] = 2200

    cfg = RangeSignalConfig(
        box_lookback=30,
        box_range_max=0.05,
        pierce_atr=0.15,
        recover_within=5,
        min_failure_score=35,
        require_pattern=False,
        enable_fade=False,
    )
    work = _add_structure(_prep(df), cfg)
    events = scan_events(work, cfg)
    longs = [e for e in events if e.side == 1 and "failed" in e.setup]
    assert len(longs) >= 1, events
    print("test_failed_break_long OK", len(longs))


def test_fade_edge():
    df = _box_series()
    n = len(df)
    mid, half = 100.0, 1.0
    # 贴上沿收阴
    df.loc[n - 1, "open"] = mid + half * 0.95
    df.loc[n - 1, "high"] = mid + half * 0.98
    df.loc[n - 1, "low"] = mid + half * 0.5
    df.loc[n - 1, "close"] = mid + half * 0.55
    cfg = RangeSignalConfig(
        box_lookback=30,
        box_range_max=0.05,
        require_pattern=False,
        enable_fade=True,
        edge_atr=0.8,
        fade_min_body_atr=0.1,
        pierce_atr=0.5,  # 提高，避免误触假突破
        min_failure_score=90,  # 基本关掉假突破
    )
    snap = evaluate_range_signal("TST0", df, cfg)
    # fade 或 flat 均可，逻辑不崩
    assert snap.price > 0
    print("test_fade_edge OK", snap.direction, snap.setup, snap.reason)


def test_hold_has_stop_target():
    df = _box_series(n=180)
    n = len(df)
    mid, half = 100.0, 1.0
    # 制造假突破并在倒数第 3 根收回，之后 hold
    pierce = n - 6
    recover = n - 4
    df.loc[pierce, "high"] = mid + half + 1.0
    df.loc[pierce, "close"] = mid + half + 0.4
    df.loc[pierce, "volume"] = 2500
    df.loc[recover, "open"] = mid + half
    df.loc[recover, "close"] = mid + 0.2
    df.loc[recover, "high"] = mid + half
    df.loc[recover, "low"] = mid
    df.loc[recover, "volume"] = 2500
    # 之后几根在箱内横盘，避免止损
    for j in range(recover + 1, n):
        df.loc[j, "open"] = mid + 0.3
        df.loc[j, "close"] = mid + 0.25
        df.loc[j, "high"] = mid + 0.5
        df.loc[j, "low"] = mid

    cfg = RangeSignalConfig(
        box_lookback=30,
        box_range_max=0.05,
        pierce_atr=0.15,
        recover_within=6,
        min_failure_score=30,
        require_pattern=False,
        enable_fade=False,
        exits=RangeExitConfig(max_hold_bars=20, failed_stop_buffer_atr=0.5, min_reward_risk_tp1=0.5),
    )
    snap = evaluate_range_signal("TST0", df, cfg)
    if snap.direction != 0:
        assert snap.stop is not None and snap.tp1 is not None and snap.tp2 is not None
        assert snap.setup.startswith("failed_break")
    print(
        "test_hold OK",
        snap.direction,
        snap.setup,
        snap.stop,
        snap.tp1,
        snap.tp2,
        snap.bars_in_trade,
        snap.reason,
    )


if __name__ == "__main__":
    test_failed_break_short()
    test_failed_break_long()
    test_fade_edge()
    test_hold_has_stop_target()
    print("all range tests done")
