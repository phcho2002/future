"""三类反转信号触发器 —— 流程图的最后一块。

future_1 这一块基本是空的：楔形突破/反转K/二次入场三个检测器塌缩成一个
body>=0.3×ATR 的弱布尔。本模块把三类写成独立检测器，各有硬触发条件，
且只用已确认 Pivot 和已收盘 K 线（anti-repaint 安全）。

三类信号（基于已确认的 Three Push 模式 + 收缩状态）：
    ① 楔形趋势线突破：用三个推极值(P1,P3,P5)拟合趋势线，收盘突破触发。
    ② 强反转 K：大实体 + 收盘极端 + 放量 + 位置在 P5 极值附近。
    ③ 二次入场：首次突破 → 回踩不破前极值 → 二次突破（跨 K 线状态机）。

每类信号返回 ReversalSignal，包含触发 K 线、入场价、止损价。
止损统一放在 P5 极值外侧 + ATR buffer（二次入场例外，放回踩极值外侧）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd

from .config import SignalConfig
from .three_push import ThreePushPattern
from .trendline import fit_trendline, TrendLine

SignalType = Literal["wedge_breakout", "reversal_bar", "second_entry"]


@dataclass(frozen=True)
class ReversalSignal:
    """一个触发了的反转信号。

    Attributes
    ----------
    signal_type : 三类之一
    trigger_idx : int
        触发 K 线索引（已收盘，anti-repaint 安全）。
    side : 'long' | 'short'
        交易方向。顶部三推 → short；底部三推 → long。
    entry : float
        入场价（触发 K 线收盘价）。
    stop : float
        止损价。
    detail : str
        人类可读的触发原因。
    """

    signal_type: SignalType
    trigger_idx: int
    side: Literal["long", "short"]
    entry: float
    stop: float
    detail: str

    def reward_risk_to(self, target: float) -> float:
        """对给定目标价的盈亏比。"""
        risk = abs(self.entry - self.stop)
        reward = abs(target - self.entry)
        return reward / risk if risk > 1e-9 else float("nan")


# ─────────────────────────────────────────────────────────────
# 共用工具
# ─────────────────────────────────────────────────────────────

def _atr_val(atr: pd.Series, idx: int) -> float:
    arr = atr.to_numpy(dtype=float)
    if 0 <= idx < len(arr):
        v = arr[idx]
        return float(v) if np.isfinite(v) and v > 0 else float("nan")
    return float("nan")


def _avg_volume(df: pd.DataFrame, end_idx: int, lookback: int) -> float:
    if "volume" not in df.columns or end_idx < 1:
        return float("nan")
    lo = max(0, end_idx - lookback)
    seg = df["volume"].iloc[lo:end_idx]
    return float(seg.mean()) if not seg.empty else float("nan")


def _side_and_extreme(pattern: ThreePushPattern, p5_price: float):
    """返回 (side, p5_price, is_top) 和止损方向。"""
    is_top = pattern.reversal_kind == "high"   # 顶部三推 → 做空
    side = "short" if is_top else "long"
    return side, is_top


def _compute_stop(pattern: ThreePushPattern, df: pd.DataFrame, atr_val: float,
                  cfg: SignalConfig) -> float:
    """按 cfg.stop_base + cfg.stop_atr_buffer 计算止损价。

    stop_base='p5'：P5 极值外侧（传统，较松）。
    stop_base='p4'：P4 回撤极值外侧（结构止损，更紧——若价格回到最后回撤点，
      说明三推结构已失效，无需等到 P5 被突破）。

    顶部三推(做空)：止损在上方（基准 + buffer×ATR）。
    底部三推(做多)：止损在下方（基准 - buffer×ATR）。
    """
    is_top = pattern.reversal_kind == "high"
    piv = pattern.pivot_indices  # [P0,P1,P2,P3,P4,P5]
    if cfg.stop_base == "p4":
        base_idx = piv[4]
        base_price = float(df["low"].iloc[base_idx]) if is_top else float(df["high"].iloc[base_idx])
    else:  # 'p5'
        base_idx = piv[5]
        base_price = float(df["high"].iloc[base_idx]) if is_top else float(df["low"].iloc[base_idx])
    if is_top:
        return base_price + cfg.stop_atr_buffer * atr_val
    else:
        return base_price - cfg.stop_atr_buffer * atr_val


# ─────────────────────────────────────────────────────────────
# ① 楔形趋势线突破
# ─────────────────────────────────────────────────────────────

def detect_wedge_breakout(pattern: ThreePushPattern, df: pd.DataFrame,
                          atr: pd.Series, cfg: SignalConfig) -> ReversalSignal | None:
    """检测楔形趋势线收盘突破。

    用三个推极值 P1,P3,P5 拟合趋势线（顶部=阻力线，底部=支撑线）。
    P5 确认后 cfg.trendline_max_lookback 根内，若收盘突破趋势线则触发。
    """
    piv_idx = pattern.pivot_indices   # [P0,P1,P2,P3,P4,P5]
    # 三个推极值的索引：P1,P3,P5
    push_idx = [piv_idx[1], piv_idx[3], piv_idx[5]]
    push_prices = [df["high"].iloc[piv_idx[1]], df["high"].iloc[piv_idx[3]],
                   df["high"].iloc[piv_idx[5]]] if pattern.reversal_kind == "high" \
        else [df["low"].iloc[piv_idx[1]], df["low"].iloc[piv_idx[3]], df["low"].iloc[piv_idx[5]]]

    line = fit_trendline(push_idx, push_prices)
    if line is None:
        return None

    side, is_top = _side_and_extreme(pattern, push_prices[-1])
    p5_idx = piv_idx[5]
    p5_price = push_prices[-1]
    lo = p5_idx + 1
    hi = min(p5_idx + 1 + cfg.trendline_max_lookback, len(df))
    buf = cfg.trendline_break_buffer_atr

    for i in range(lo, hi):
        a = _atr_val(atr, i)
        if not np.isfinite(a):
            continue
        close = float(df["close"].iloc[i])
        lp = line.price_at(i)
        if is_top:
            # 阻力线被收盘跌破 → 做空触发
            if close < lp - buf * a:
                stop = _compute_stop(pattern, df, a, cfg)
                return ReversalSignal("wedge_breakout", i, "short", close, stop,
                                      f"收盘{close:.1f}跌破阻力线{lp:.1f}(-{buf}ATR)")
        else:
            # 支撑线被收盘涨破 → 做多触发
            if close > lp + buf * a:
                stop = _compute_stop(pattern, df, a, cfg)
                return ReversalSignal("wedge_breakout", i, "long", close, stop,
                                      f"收盘{close:.1f}涨破支撑线{lp:.1f}(+{buf}ATR)")
    return None


# ─────────────────────────────────────────────────────────────
# ② 强反转 K 线
# ─────────────────────────────────────────────────────────────

def detect_reversal_bar(pattern: ThreePushPattern, df: pd.DataFrame,
                        atr: pd.Series, cfg: SignalConfig) -> ReversalSignal | None:
    """检测强反转 K 线。

    硬定义（替代 future_1 的 body>=0.3×ATR 弱条件）：
        - 实体 >= reversal_body_atr × ATR（大实体）
        - 收盘在 K 线极端区间（反转方向那一端 reversal_close_extreme_ratio 内）
        - 量 >= 近 N 根均值 × reversal_volume_ratio（放量）
        - 位置：必须在 P5 极值 ± reversal_position_atr×ATR 内
    """
    side, is_top = _side_and_extreme(pattern, 0.0)
    piv_idx = pattern.pivot_indices
    p5_idx = piv_idx[5]
    p5_price = float(df["high"].iloc[p5_idx]) if is_top else float(df["low"].iloc[p5_idx])
    lo = p5_idx + 1
    hi = min(p5_idx + 1 + cfg.reversal_max_lookback, len(df))

    for i in range(lo, hi):
        a = _atr_val(atr, i)
        if not np.isfinite(a):
            continue
        op = float(df["open"].iloc[i])
        cl = float(df["close"].iloc[i])
        hi_ = float(df["high"].iloc[i])
        lo_ = float(df["low"].iloc[i])
        rng = hi_ - lo_
        if rng < 1e-9:
            continue
        body = abs(cl - op)

        # 实体够大
        if body < cfg.reversal_body_atr * a:
            continue
        # 收盘极端：顶部反转(做空)→收盘在 K 线下部；底部反转(做多)→收盘在上部
        if is_top:
            close_pos = (cl - lo_) / rng   # 0=最低, 1=最高
            if close_pos > cfg.reversal_close_extreme_ratio:   # 收盘要在下部
                continue
            bar_dir_ok = cl < op           # 阴线
        else:
            close_pos = (hi_ - cl) / rng
            if close_pos > cfg.reversal_close_extreme_ratio:
                continue
            bar_dir_ok = cl > op           # 阳线
        if not bar_dir_ok:
            continue
        # 放量
        avg_vol = _avg_volume(df, i, cfg.reversal_volume_lookback)
        vol = float(df["volume"].iloc[i]) if "volume" in df.columns else float("nan")
        if np.isfinite(avg_vol) and avg_vol > 0:
            if vol < cfg.reversal_volume_ratio * avg_vol:
                continue
        # 位置约束：反转 K 的高低点要触及 P5 极值附近
        if is_top:
            if hi_ < p5_price - cfg.reversal_position_atr * a:
                continue
        else:
            if lo_ > p5_price + cfg.reversal_position_atr * a:
                continue
        stop = _compute_stop(pattern, df, a, cfg)
        return ReversalSignal("reversal_bar", i, side, cl, stop,
                              f"强反转K body={body/a:.2f}ATR 量比={vol/avg_vol:.2f}"
                              if np.isfinite(avg_vol) and avg_vol > 0
                              else f"强反转K body={body/a:.2f}ATR")
    return None


# ─────────────────────────────────────────────────────────────
# ③ 二次入场（状态机：首次突破 → 回踩 → 二次突破）
# ─────────────────────────────────────────────────────────────

def detect_second_entry(pattern: ThreePushPattern, df: pd.DataFrame,
                        atr: pd.Series, cfg: SignalConfig) -> ReversalSignal | None:
    """检测二次入场信号（Al Brooks 体系胜率最高的一类）。

    状态机（跨 K 线时序，这是 future_1 架构根本缺失的）：
        状态1 WAIT_FIRST_BREAK: 等首次收盘突破 P5 极值线
        状态2 WAIT_PULLBACK:    首次突破后，等回踩（反向幅度 >= pullback_atr×ATR）
        状态3 WAIT_SECOND_BREAK: 回踩后，等二次收盘突破 → 触发

    二次入场的价值：过滤假突破。首次突破可能是假突破（突破后立即反转），
    二次突破发生在回踩确认后，胜率显著更高。
    止损放在回踩极值外侧（比 P5 外侧更紧，R:R 更好）。
    """
    side, is_top = _side_and_extreme(pattern, 0.0)
    piv_idx = pattern.pivot_indices
    p5_idx = piv_idx[5]
    p5_price = float(df["high"].iloc[p5_idx]) if is_top else float(df["low"].iloc[p5_idx])
    lo = p5_idx + 1
    hi = min(p5_idx + 1 + cfg.second_entry_max_lookback, len(df))

    # 极值线：顶部=跌破 P5 低点；底部=涨破 P5 高点。用 P5 那根的 high/low 做突破参照。
    # 这里简化：首次突破 = 收盘越过 P5 极值线的反向
    #   顶部(做空): 首次突破 = 收盘跌破 p5_price；回踩 = 反弹；二次 = 再跌破回踩后低点
    #   底部(做多): 首次突破 = 收盘涨破 p5_price；回踩 = 回落；二次 = 再涨破回踩后高点
    state = "WAIT_FIRST_BREAK"
    first_break_idx = -1
    pullback_extreme = p5_price   # 回踩期间的反向极值
    pullback_done = False

    for i in range(lo, hi):
        a = _atr_val(atr, i)
        if not np.isfinite(a):
            continue
        cl = float(df["close"].iloc[i])
        hi_ = float(df["high"].iloc[i])
        lo_ = float(df["low"].iloc[i])

        if state == "WAIT_FIRST_BREAK":
            # 首次收盘突破 P5 极值（反转方向）
            if is_top and cl < p5_price - cfg.second_entry_break_atr * a:
                state = "WAIT_PULLBACK"
                first_break_idx = i
                pullback_extreme = hi_   # 回踩看反弹高点
            elif not is_top and cl > p5_price + cfg.second_entry_break_atr * a:
                state = "WAIT_PULLBACK"
                first_break_idx = i
                pullback_extreme = lo_
        elif state == "WAIT_PULLBACK":
            # 追踪回踩极值
            if is_top:
                pullback_extreme = max(pullback_extreme, hi_)
                # 回踩幅度达标 → 进入等二次突破
                if pullback_extreme - cl >= cfg.second_entry_pullback_atr * a:
                    state = "WAIT_SECOND_BREAK"
            else:
                pullback_extreme = min(pullback_extreme, lo_)
                if cl - pullback_extreme >= cfg.second_entry_pullback_atr * a:
                    state = "WAIT_SECOND_BREAK"
        elif state == "WAIT_SECOND_BREAK":
            # 二次收盘突破回踩极值线 → 触发
            if is_top and cl < pullback_extreme - cfg.second_entry_break_atr * a:
                stop = pullback_extreme + cfg.stop_atr_buffer * a   # 回踩极值外侧
                return ReversalSignal("second_entry", i, "short", cl, stop,
                                      f"二次入场: 首破@{first_break_idx} 回踩极值{pullback_extreme:.1f}")
            elif not is_top and cl > pullback_extreme + cfg.second_entry_break_atr * a:
                stop = pullback_extreme - cfg.stop_atr_buffer * a
                return ReversalSignal("second_entry", i, "long", cl, stop,
                                      f"二次入场: 首破@{first_break_idx} 回踩极值{pullback_extreme:.1f}")
    return None


# ─────────────────────────────────────────────────────────────
# 汇总：对单个模式跑三类检测，返回最早触发的（或全部）
# ─────────────────────────────────────────────────────────────

def detect_all_signals(pattern: ThreePushPattern, df: pd.DataFrame,
                       atr: pd.Series, cfg: SignalConfig | None = None
                       ) -> list[ReversalSignal]:
    """对一个 Three Push 模式跑三类触发器，返回所有触发的信号（按 trigger_idx 升序）。"""
    cfg = cfg or SignalConfig()
    sigs: list[ReversalSignal] = []
    for fn in (detect_wedge_breakout, detect_reversal_bar, detect_second_entry):
        try:
            s = fn(pattern, df, atr, cfg)
            if s is not None:
                sigs.append(s)
        except Exception:
            continue
    sigs.sort(key=lambda s: s.trigger_idx)
    return sigs


# 信号优先级（基于前瞻验证结论）：
#   reversal_bar 胜率最高(63%)、止损率最低(44%) > wedge_breakout 风险调整最优(MFE/MAE 1.54)
#   > second_entry 最弱(53.9%/1.28)。同 pattern 多信号时按此优先级取一个。
_SIGNAL_PRIORITY = {"reversal_bar": 0, "wedge_breakout": 1, "second_entry": 2}


def dedupe_signals(sigs: list[ReversalSignal]) -> ReversalSignal | None:
    """对一个 pattern 的多信号去重，返回最优的一个。

    策略：按信号类型优先级取最高的；同级取 trigger_idx 最早（更快入场）。
    前瞻验证发现 wedge_breakout 和 second_entry 高度共线（中位间隔仅 2 根 K 线，
    67.5% 重复间隔≤3 根），本质是同一突破事件的重复记录。去重后每个 pattern
    恰好一个信号，样本干净且无重复。
    """
    if not sigs:
        return None
    return min(sigs, key=lambda s: (_SIGNAL_PRIORITY.get(s.signal_type, 99), s.trigger_idx))


def earliest_signal(pattern: ThreePushPattern, df: pd.DataFrame,
                    atr: pd.Series, cfg: SignalConfig | None = None
                    ) -> ReversalSignal | None:
    """返回最早触发的信号（若有多个取 trigger_idx 最小的）。"""
    sigs = detect_all_signals(pattern, df, atr, cfg)
    return sigs[0] if sigs else None


def best_signal(pattern: ThreePushPattern, df: pd.DataFrame,
                atr: pd.Series, cfg: SignalConfig | None = None
                ) -> ReversalSignal | None:
    """返回去重后的最优信号（推荐入口，替代 earliest_signal 用于实盘/回测）。"""
    sigs = detect_all_signals(pattern, df, atr, cfg)
    return dedupe_signals(sigs)
