"""
N型主升浪交易系统 — N型结构识别引擎
=========================================
核心算法：检测 L1(底部) → H1(首波顶部) → L2(回调底部) 的完整N型结构。
"""

import numpy as np
import pandas as pd
from typing import Optional, Dict, Tuple
from dataclasses import dataclass, field

from config import Config


@dataclass
class NPatternResult:
    """N型结构检测结果"""
    found: bool = False
    l1_price: float = np.nan
    l1_idx: int = -1
    h1_price: float = np.nan
    h1_idx: int = -1
    l2_price: float = np.nan
    l2_idx: int = -1
    leg_pct: float = np.nan         # 首波涨幅%
    retrace_pct: float = np.nan     # 回调深度%
    ready: bool = False              # 结构完成，等待突破
    breakout: bool = False           # 已突破前高
    phase: str = 'SCANNING'          # 当前阶段
    # 止盈止损
    stop_loss: float = np.nan
    tp1: float = np.nan
    tp2: float = np.nan


@dataclass
class SignalInfo:
    """综合信号信息"""
    date: object = None
    score: int = 0
    grade: str = 'D'
    n_pattern: NPatternResult = field(default_factory=NPatternResult)
    # 各项通过状态
    ma_bullish: bool = False
    rsi_healthy: bool = False
    vol_burst: bool = False
    macd_bullish: bool = False
    adx_trend: bool = False
    # 交易决策
    buy_signal: bool = False
    entry_price: float = np.nan
    stop_loss: float = np.nan
    tp1: float = np.nan
    tp2: float = np.nan


class NPatternDetector:
    """
    N型结构检测器

    在K线数据上逐根推进，追踪最新的 L1 → H1 → L2 结构。
    模仿 Pine Script 中 var 变量的持久化行为。
    """

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.reset()

    def reset(self):
        """重置状态机"""
        self.l1_price = np.nan
        self.l1_idx = -1
        self.h1_price = np.nan
        self.h1_idx = -1
        self.l2_price = np.nan
        self.l2_idx = -1
        self.ready = False
        self.phase = 'SCANNING'
        self._entered = False  # 当前结构是否已触发过入场

    def _check_consolidation(self, row: pd.Series) -> bool:
        """检测盘整（低波动）环境"""
        atr_pct = row.get('atr_pct', np.nan)
        if np.isnan(atr_pct):
            return False
        return atr_pct < 4.0  # ATR/价格 < 4%

    def process_bar(self, idx: int, row: pd.Series) -> NPatternResult:
        """
        处理一根新K线，更新N型结构状态。

        参数
        ----
        idx : DataFrame中的位置索引
        row : 当前K线数据（包含所有技术指标列）

        返回
        ----
        NPatternResult : 当前最新的N型结构状态
        """
        pl_val = row.get('pl', np.nan)
        ph_val = row.get('ph', np.nan)
        is_consolidating = self._check_consolidation(row)

        # ================================================================
        # 处理枢轴低点 — 可能是 L1 或 L2
        # ================================================================
        if not np.isnan(pl_val):
            if np.isnan(self.l1_price):
                # 情况1：尚无 L1 → 初始化
                if is_consolidating or True:  # 放宽盘整要求
                    self.l1_price = pl_val
                    self.l1_idx = idx
                    self.h1_price = np.nan
                    self.h1_idx = -1
                    self.l2_price = np.nan
                    self.l2_idx = -1
                    self.ready = False
                    self._entered = False
                    self.phase = 'BASE'

            elif not np.isnan(self.h1_price) and idx > self.h1_idx:
                # 情况2：已有 L1 + H1，检查是否为有效回调低点
                leg_pct = (self.h1_price - self.l1_price) / self.l1_price * 100
                retrace = (self.h1_price - pl_val) / (self.h1_price - self.l1_price)
                leg_bars = self.h1_idx - self.l1_idx

                if (leg_pct >= self.cfg.n_pattern.min_leg_pct
                        and leg_bars >= self.cfg.n_pattern.min_leg_bars
                        and self.cfg.n_pattern.fib_min <= retrace <= self.cfg.n_pattern.fib_max
                        and pl_val > self.l1_price):
                    # ✅ 有效 L2！N型结构确认
                    self.l2_price = pl_val
                    self.l2_idx = idx
                    self.ready = True
                    self._entered = False
                    self.phase = 'READY'

                elif pl_val < self.l1_price:
                    # ❌ 跌破 L1，重置为新周期的 L1
                    self.l1_price = pl_val
                    self.l1_idx = idx
                    self.h1_price = np.nan
                    self.h1_idx = -1
                    self.l2_price = np.nan
                    self.l2_idx = -1
                    self.ready = False
                    self._entered = False
                    self.phase = 'BASE'

            elif np.isnan(self.h1_price):
                # 情况3：仅有 L1，检查是否有更低的低点
                if pl_val < self.l1_price:
                    self.l1_price = pl_val
                    self.l1_idx = idx
                    self.phase = 'BASE'

        # ================================================================
        # 处理枢轴高点 — 可能是 H1
        # ================================================================
        if not np.isnan(ph_val) and not np.isnan(self.l1_price) and idx > self.l1_idx:
            if np.isnan(self.h1_price) or idx > self.h1_idx:
                if np.isnan(self.l2_price) or idx > self.l2_idx:
                    leg_pct = (ph_val - self.l1_price) / self.l1_price * 100
                    leg_bars = idx - self.l1_idx
                    if (leg_pct >= self.cfg.n_pattern.min_leg_pct
                            and leg_bars >= self.cfg.n_pattern.min_leg_bars):
                        self.h1_price = ph_val
                        self.h1_idx = idx
                        self.phase = 'LEG1'

        # ================================================================
        # 检测突破
        # ================================================================
        breakout = False
        if self.ready and not np.isnan(self.h1_price):
            if row['close'] > self.h1_price:
                breakout = True
                if not self._entered:
                    self._entered = True
                    self.phase = 'BREAKOUT'

        # ================================================================
        # 结构过期检查
        # ================================================================
        if not np.isnan(self.l1_price) and idx - self.l1_idx > self.cfg.n_pattern.reset_bars:
            self.reset()

        # ================================================================
        # 计算止盈止损
        # ================================================================
        stop_loss = np.nan
        tp1 = np.nan
        tp2 = np.nan

        if self.ready and not np.isnan(self.l2_price) and not np.isnan(self.h1_price):
            risk_amount = self.h1_price - self.l2_price
            stop_loss = self.l2_price  # 止损在 L2 下方
            tp1 = self.h1_price + risk_amount * self.cfg.risk.tp_rr_1
            tp2 = self.h1_price + risk_amount * self.cfg.risk.tp_rr_2

        # ================================================================
        # 返回结果
        # ================================================================
        return NPatternResult(
            found=not np.isnan(self.l1_price) and not np.isnan(self.h1_price),
            l1_price=self.l1_price if not np.isnan(self.l1_price) else np.nan,
            l1_idx=self.l1_idx if self.l1_idx >= 0 else -1,
            h1_price=self.h1_price if not np.isnan(self.h1_price) else np.nan,
            h1_idx=self.h1_idx if self.h1_idx >= 0 else -1,
            l2_price=self.l2_price if not np.isnan(self.l2_price) else np.nan,
            l2_idx=self.l2_idx if self.l2_idx >= 0 else -1,
            leg_pct=((self.h1_price - self.l1_price) / self.l1_price * 100)
                     if not np.isnan(self.l1_price) and not np.isnan(self.h1_price) else np.nan,
            retrace_pct=((self.h1_price - self.l2_price) / (self.h1_price - self.l1_price) * 100)
                        if self.ready else np.nan,
            ready=self.ready,
            breakout=breakout,
            phase=self.phase,
            stop_loss=stop_loss,
            tp1=tp1,
            tp2=tp2
        )


# ==============================================================================
# 信号评分引擎
# ==============================================================================

def compute_signal_score(row: pd.Series, n_result: NPatternResult, cfg: Config) -> int:
    """
    综合信号评分（0-100分）

    评分维度:
        N型结构就绪:  40分
        均线多头排列:  15分
        RSI健康区间:   10分
        成交量配合:     15分
        MACD多头:       10分
        ADX趋势确认:     5分
        多周期共振:    +10分（额外）
    """
    score = 0

    # 1. N型结构（核心，40分）
    if n_result.ready:
        score += cfg.score.n_pattern

    # 2. 均线（15分）
    ema_s = row.get('ema_s', np.nan)
    ema_m = row.get('ema_m', np.nan)
    ema_l = row.get('ema_l', np.nan)

    if not np.isnan(ema_s) and not np.isnan(ema_m) and not np.isnan(ema_l):
        if ema_s > ema_m > ema_l:
            score += cfg.score.ma  # 完全多头排列
        elif ema_s > ema_m:
            score += cfg.score.ma // 2  # 短期多头

    # 3. RSI（10分）
    rsi_val = row.get('rsi', np.nan)
    if not np.isnan(rsi_val):
        if cfg.rsi.low <= rsi_val <= cfg.rsi.high:
            score += cfg.score.rsi

    # 4. 成交量（15分）
    vol_ratio = row.get('vol_ratio', np.nan)
    if not np.isnan(vol_ratio):
        if vol_ratio >= cfg.volume.burst_mult:
            score += cfg.score.volume
        elif vol_ratio >= 1.0:
            score += cfg.score.volume // 2

    # 5. MACD（10分）
    macd_l = row.get('macd_line', np.nan)
    macd_s = row.get('macd_sig', np.nan)
    if not np.isnan(macd_l) and not np.isnan(macd_s):
        if macd_l > macd_s:
            score += 5
        if macd_l > 0:
            score += 5

    # 6. ADX（5分）
    adx_val = row.get('adx', np.nan)
    di_p = row.get('di_plus', np.nan)
    di_m = row.get('di_minus', np.nan)
    if not np.isnan(adx_val) and adx_val > cfg.adx.threshold:
        if not np.isnan(di_p) and not np.isnan(di_m) and di_p > di_m:
            score += cfg.score.adx

    return min(score, 100)


def get_signal_grade(score: int) -> str:
    """评分 → 等级"""
    if score >= 80:
        return 'S'
    elif score >= 65:
        return 'A'
    elif score >= 50:
        return 'B'
    elif score >= 30:
        return 'C'
    return 'D'


def check_buy_signal(row: pd.Series, n_result: NPatternResult, cfg: Config) -> Tuple[bool, SignalInfo]:
    """
    综合判断买入信号

    条件:
        1. N型结构就绪（L1→H1→L2完整）
        2. 价格突破 H1（收盘确认）
        3. 成交量放大（可选）
        4. 均线多头排列（可选）
        5. RSI健康（可选）

    返回: (是否为买入信号, SignalInfo)
    """
    score = compute_signal_score(row, n_result, cfg)
    grade = get_signal_grade(score)

    # 突破确认
    breakout = n_result.ready and row['close'] > n_result.h1_price

    # 各项条件
    ema_s = row.get('ema_s', np.nan)
    ema_m = row.get('ema_m', np.nan)
    ema_l = row.get('ema_l', np.nan)
    rsi_val = row.get('rsi', np.nan)
    vol_ratio = row.get('vol_ratio', np.nan)
    macd_l = row.get('macd_line', np.nan)
    macd_s = row.get('macd_sig', np.nan)
    adx_val = row.get('adx', np.nan)

    ma_bullish = (ema_s > ema_m > ema_l) if not np.isnan(ema_s) else False
    rsi_healthy = (cfg.rsi.low <= rsi_val <= cfg.rsi.high) if not np.isnan(rsi_val) else False
    vol_burst = (vol_ratio >= cfg.volume.burst_mult) if not np.isnan(vol_ratio) else False
    macd_bullish = (macd_l > macd_s) if not np.isnan(macd_l) else False
    adx_trend = (adx_val > cfg.adx.threshold and row.get('di_plus', 0) > row.get('di_minus', 0)) \
        if not np.isnan(adx_val) else False

    # 综合过滤（默认全部启用）
    ma_pass = not cfg.ma.enabled or ma_bullish
    rsi_pass = not cfg.rsi.enabled or rsi_healthy
    vol_pass = not cfg.volume.enabled or vol_burst

    buy_signal = breakout and ma_pass and rsi_pass and vol_pass

    # 止盈止损
    stop_loss = n_result.stop_loss
    tp1 = n_result.tp1
    tp2 = n_result.tp2
    entry_price = row['close'] if buy_signal else np.nan

    info = SignalInfo(
        date=row.name,
        score=score,
        grade=grade,
        n_pattern=n_result,
        ma_bullish=ma_bullish,
        rsi_healthy=rsi_healthy,
        vol_burst=vol_burst,
        macd_bullish=macd_bullish,
        adx_trend=adx_trend,
        buy_signal=buy_signal,
        entry_price=entry_price,
        stop_loss=stop_loss,
        tp1=tp1,
        tp2=tp2,
    )

    return buy_signal, info


# ==============================================================================
# 全量检测（遍历整个DataFrame）
# ==============================================================================

def detect_all_signals(df: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    """
    对整个DataFrame进行N型结构检测，标记所有信号。

    返回添加了以下列的 DataFrame:
        n_l1, n_h1, n_l2, n_ready, n_phase, n_breakout
        signal_score, signal_grade, buy_signal
        stop_loss, tp1, tp2
    """
    df = df.copy()
    detector = NPatternDetector(cfg)

    results = {
        'n_l1': [], 'n_h1': [], 'n_l2': [],
        'n_ready': [], 'n_phase': [], 'n_breakout': [],
        'signal_score': [], 'signal_grade': [], 'buy_signal': [],
        'stop_loss': [], 'tp1': [], 'tp2': [],
        'ma_bullish': [], 'rsi_healthy': [], 'vol_burst': [],
    }

    for idx, (_, row) in enumerate(df.iterrows()):
        n_result = detector.process_bar(idx, row)
        _, signal = check_buy_signal(row, n_result, cfg)

        results['n_l1'].append(n_result.l1_price)
        results['n_h1'].append(n_result.h1_price)
        results['n_l2'].append(n_result.l2_price)
        results['n_ready'].append(n_result.ready)
        results['n_phase'].append(n_result.phase)
        results['n_breakout'].append(n_result.breakout)
        results['signal_score'].append(signal.score)
        results['signal_grade'].append(signal.grade)
        results['buy_signal'].append(signal.buy_signal)
        results['stop_loss'].append(signal.stop_loss)
        results['tp1'].append(signal.tp1)
        results['tp2'].append(signal.tp2)
        results['ma_bullish'].append(signal.ma_bullish)
        results['rsi_healthy'].append(signal.rsi_healthy)
        results['vol_burst'].append(signal.vol_burst)

    for key, values in results.items():
        df[key] = values

    return df


def get_latest_signal(df: pd.DataFrame, cfg: Config) -> SignalInfo:
    """获取最新一根K线的信号"""
    if df.empty:
        return SignalInfo()

    detector = NPatternDetector(cfg)
    last_idx = len(df) - 1

    for idx, (_, row) in enumerate(df.iterrows()):
        if idx == last_idx:
            n_result = detector.process_bar(idx, row)
            _, signal = check_buy_signal(row, n_result, cfg)
            return signal
        detector.process_bar(idx, row)

    return SignalInfo()
