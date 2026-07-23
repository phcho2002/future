"""顺势放量突破信号触发器。

形态（两高两低）+ 整理质量合格之后，等"顺势放量突破"触发入场。

突破定义（多头为例，空头镜像）：
    形态最后一个拐点(L2 多头 / H2 空头)确认后的 breakout_max_lookback 根内，
    首根满足以下四个硬条件(AND)的 K 线即为突破触发：
        ① 价格：收盘越过 H2（多头）/ 跌破 L2（空头）+ buffer×ATR
        ② 量能：成交量 ≥ volume_multiple × 近 N 根均量（放量）
        ③ 实体：K 线实体 ≥ body_multiple × 近 N 根平均实体（强势突破K）
        ④ 幅度：突破幅度（|收盘−突破位|）> magnitude_atr × ATR

为什么四个条件 AND（参考 future_bb 已验证的三维突破）：
    单看收盘越过阻力，假突破太多（贴边、无量、小实体）。加上放量+大实体+幅度，
    才是"真突破"——有资金推动、有方向决心、有足够位移。future_bb 在 40 品种
    回测验证过这套阈值（1.5×均量、1.5×实体、0.5×ATR）。

anti-repaint：触发 K 线必须是已收盘的（trigger_idx 那根 close 已定型）。
    入场用次根开盘（回测里由 backtest 引擎处理）。

止损：整理区间对侧（多头=L2 下沿 ∓ buffer×ATR；空头=H2 上沿 ± buffer×ATR）。
    这是结构止损——跌破/涨破整理区间说明形态失效，无需等更大止损。

接口对齐 ReversalSignal（duck-typing）：trigger_idx/side/entry/stop/signal_type，
    可直接喂给 future_zigzag.backtest.run_backtest，无需重写回测引擎。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd

from .config import BreakoutConfig
from .pattern import TwoHighPattern


@dataclass(frozen=True)
class BreakoutSignal:
    """一个触发了的顺势突破信号。

    字段与 future_zigzag.signals.ReversalSignal 对齐（duck-typing），
    可直接喂给 future_zigzag.backtest.run_backtest。

    Attributes
    ----------
    signal_type : 固定 'breakout'
    trigger_idx : int
        触发 K 线索引（已收盘，anti-repaint 安全）。
    side : 'long' | 'short'
    entry : float
        入场价（触发 K 线收盘价；回测实际用次根开盘）。
    stop : float
        止损价（整理区间对侧 ± buffer×ATR）。
    detail : str
        人类可读的触发原因。
    """

    signal_type: str
    trigger_idx: int
    side: Literal["long", "short"]
    entry: float
    stop: float
    detail: str


# ─────────────────────────────────────────────────────────────
# 工具
# ─────────────────────────────────────────────────────────────

def _atr_val(atr: pd.Series, idx: int) -> float:
    arr = atr.to_numpy(dtype=float)
    if 0 <= idx < len(arr):
        v = arr[idx]
        return float(v) if np.isfinite(v) and v > 0 else float("nan")
    return float("nan")


def _rolling_mean(series: pd.Series, end_idx: int, lookback: int) -> float:
    """[end_idx-lookback, end_idx] 区间均值。"""
    if end_idx < 1:
        return float("nan")
    lo = max(0, end_idx - lookback)
    seg = series.iloc[lo:end_idx]
    if seg.empty:
        return float("nan")
    return float(seg.mean())


# ─────────────────────────────────────────────────────────────
# 突破检测
# ─────────────────────────────────────────────────────────────

def detect_breakout(pattern: TwoHighPattern, df: pd.DataFrame,
                    atr: pd.Series, cfg: BreakoutConfig | None = None,
                    as_of: int | None = None) -> BreakoutSignal | None:
    """检测顺势放量突破信号。

    Parameters
    ----------
    pattern : TwoHighPattern
    df : pd.DataFrame
        OHLCV K 线。
    atr : pd.Series
        ATR 序列（ZigZagResult.atr）。
    cfg : BreakoutConfig
    as_of : int, 可选
        回测时当前 K 线索引；只检测 trigger_idx <= as_of 的突破（anti-repaint）。

    Returns
    -------
    BreakoutSignal | None
        首个触发的突破信号（最早入场）。
    """
    cfg = cfg or BreakoutConfig()
    is_long = pattern.direction == "long"
    level = pattern.breakout_level            # 多头=H2，空头=L2
    opposite = pattern.opposite_level         # 多头=L2，空头=H2

    # 突破检测起点 = 形态最后一个拐点(L2/H2)的 confirmed_at 之后第一根
    last_pivot_idx = pattern.pivot_indices[-1]
    lo = last_pivot_idx + 1
    hi = min(last_pivot_idx + 1 + cfg.breakout_max_lookback, len(df))
    if as_of is not None:
        hi = min(hi, as_of + 1)

    open_ = df["open"]
    high_ = df["high"]
    low_ = df["low"]
    close_ = df["close"]
    vol_ = df["volume"] if "volume" in df.columns else None

    for i in range(lo, hi):
        a = _atr_val(atr, i)
        if not np.isfinite(a):
            continue
        op = float(open_.iloc[i])
        cl = float(close_.iloc[i])
        hi_p = float(high_.iloc[i])
        lo_p = float(low_.iloc[i])
        body = abs(cl - op)

        # ① 价格：收盘顺势越过突破位 + buffer×ATR
        if is_long:
            if cl <= level + cfg.breakout_buffer_atr * a:
                continue
            magnitude = cl - level
        else:
            if cl >= level - cfg.breakout_buffer_atr * a:
                continue
            magnitude = level - cl

        # ④ 幅度：突破位移 > magnitude_atr × ATR
        if magnitude <= cfg.magnitude_atr * a:
            continue

        # ③ 实体：K 线实体 ≥ body_multiple × 近 N 根平均实体
        body_ma = _rolling_mean((close_ - open_).abs(), i, cfg.body_lookback)
        if np.isfinite(body_ma) and body_ma > 0:
            if body < cfg.body_multiple * body_ma:
                continue

        # ② 量能：成交量 ≥ volume_multiple × 近 N 根均量
        if vol_ is not None:
            vol = float(vol_.iloc[i])
            vol_ma = _rolling_mean(vol_, i, cfg.volume_lookback)
            if np.isfinite(vol_ma) and vol_ma > 0:
                if vol < cfg.volume_multiple * vol_ma:
                    continue
        # 无 volume 列时量能条件降级放行（数据 schema 通常有 volume）

        # 触发：止损放整理区间对侧 ± buffer×ATR
        if is_long:
            stop = opposite - cfg.stop_atr_buffer * a
        else:
            stop = opposite + cfg.stop_atr_buffer * a

        vol_tag = ""
        if vol_ is not None:
            vol_ma = _rolling_mean(vol_, i, cfg.volume_lookback)
            if np.isfinite(vol_ma) and vol_ma > 0:
                vol_tag = f" 量比={float(vol_.iloc[i])/vol_ma:.2f}"

        return BreakoutSignal(
            signal_type="breakout",
            trigger_idx=i,
            side=pattern.direction,
            entry=cl,
            stop=float(stop),
            detail=f"顺势突破{pattern.kind} 收盘{cl:.1f}越过{level:.1f}"
                   f"(+{magnitude/a:.2f}ATR) 实体{body/a:.2f}ATR{vol_tag}",
        )
    return None
