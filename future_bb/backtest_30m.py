#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
30 分钟突破量化系统回测
======================

在 30 分钟周期上,对指定品种的最近 N 根 K 线运行突破策略回测,
统计:信号数量、胜率、收益率、年化收益率。

策略来源
--------
复用本目录(future_bb)已有的突破策略:
  - breakout_detector.BreakoutDetector  (价格/量能/时间三维突破)
  - filter_conditions.SignalFilter       (必要条件 + 评分 >= 65)
  - indicators.Indicators                (ATR/ADX/SMA/实体等)
  - config                               (LOOKBACK=20, 阈值0.3%, ADX>20 ...)

方向
----
做多 : 完全复用原系统(向上突破 + 多头过滤)。
做空 : 对称镜像(向下突破 + 空头过滤),参数与做多完全一致。

回测口径
--------
每品种独立、逐 K 线事件驱动、固定 1 手、同品种同时仅 1 仓。
出场: ATR止损 -> 2×风险止盈 -> 跟踪止损 -> 趋势反转出场。

运行
----
    python backtest_30m.py
首次联网拉数据(~30s),之后走 quote_cache 纯读盘。
"""

import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

# ---- 复用本目录的策略模块(裸名 import)----
THIS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(THIS_DIR))

import config  # noqa: E402
from breakout_detector import BreakoutDetector  # noqa: E402
from filter_conditions import FilterConditions  # noqa: E402
from indicators import Indicators  # noqa: E402

# ---- 接入全系统统一行情入口 future_data(xtquant 后端 + TTL 缓存)----
WORK_AI = r"d:\work_ai"
sys.path.insert(0, WORK_AI)
from future_data import get_klines  # noqa: E402


# ============================================================
# 从 strategy_config.yaml 加载品种池与参数(向后兼容:无yaml则用代码内默认值)
# ============================================================

def _load_strategy_config():
    """读取 strategy_config.yaml,返回 (targets, params)。

    targets: [(symbol, name, exchange, multiplier), ...]
    params : dict,含 backtest/money_management 配置(键值扁平化)。

    若 yaml 不存在或解析失败,返回 None(由调用方使用代码内默认值)。
    """
    cfg_path = THIS_DIR / "strategy_config.yaml"
    try:
        import yaml
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
    except Exception as e:
        print(f"[配置] 未加载 strategy_config.yaml ({e}),使用代码内默认值")
        return None

    # 品种池:支持 monthly_overrides 按月覆盖
    univ = cfg.get("universe", [])
    overrides = cfg.get("monthly_overrides", {}) or {}
    today = pd.Timestamp.now().strftime("%Y-%m")
    if today in overrides:
        univ = overrides[today]
        print(f"[配置] 命中月份覆盖 {today},品种池 {len(univ)} 个")

    targets = [
        (u["symbol"], u["name"], u["exchange"], u["u_multiplier"] if "u_multiplier" in u else u["multiplier"])
        for u in univ
    ]

    # 参数扁平化
    bt = cfg.get("backtest", {})
    mm = cfg.get("money_management", {})
    params = {**bt, **mm}
    return targets, params


_CFG = _load_strategy_config()


# ============================================================
# 品种与合约参数
# ============================================================

# (symbol, name, exchange, multiplier)
# 默认品种池(代码内兜底);若 strategy_config.yaml 存在则优先用 yaml 的品种池
_DEFAULT_TARGETS = [
    ("SN0", "锡",       "shfe", 1),
    ("LC0", "碳酸锂",   "gfex", 1),
    ("P0",  "棕榈油",   "dce",  10),
    ("AO0", "氧化铝",   "shfe", 20),
    ("CF0", "棉花",     "czce", 5),
    ("AU0", "黄金",     "shfe", 1000),
]

# yaml 优先:品种池
TARGETS = _CFG[0] if _CFG else _DEFAULT_TARGETS

# yaml 优先:周期/根数/资金/手续费/资金管理
_P = _CFG[1] if _CFG else {}
KLINE_LENGTH = _P.get("kline_length", 8000)        # 拉取根数
KLINE_PERIOD = _P.get("kline_period", "30")        # K线周期 "30"/"60"

# ============ 资金与手续费 ============
INITIAL_CAPITAL = _P.get("initial_capital", 5_000_000)  # 初始资金(每品种独立账户)
COMMISSION_RATE = _P.get("commission_rate", 0.0003)     # 手续费率(单边)
SLIPPAGE_TICKS = _P.get("slippage_ticks", 1)            # 滑点(跳数)
TICK_SIZE = 1.0              # 简化:1 跳 = 1 个价格单位
MARGIN_RATE = _P.get("margin_rate", 0.12)               # 保证金率(用于开仓手数计算)

# ============ 资金管理:按资金比例开仓 ============
RISK_CAPITAL_RATIO = _P.get("risk_capital_ratio", 0.08)    # 开仓金额 = 当前资金 × 8%
ADD_LOTS_RATIO = _P.get("add_lots_ratio", 1.0 / 3.0)       # 加仓手数 = 初次开仓手数 × 1/3
HARD_STOP_LOSS = _P.get("hard_stop_loss", 0.009)           # 硬止损:单笔亏损达总资金千分之9

EMA_PERIOD = 200             # EMA 趋势过滤周期(当前已关闭,保留计算)
# 出场:移动止损为主(让利润奔跑)
EXIT_STOP_ATR = config.BREAK_EXIT_BO_ATR       # 初始止损 = 突破点 ∓ 1.0×ATR
EXIT_TRAIL_ATR = config.TRAIL_ATR_MULT_2ND     # 移动止损:跟踪极值 ∓ 1.0×ATR
EXIT_BREAKEVEN_R = config.BREAKEVEN_TRIGGER_R  # 浮盈达1R后止损上移至盈亏平衡
ADD_ON_R = config.ADD_ON_R                     # 浮盈达1R时触发加仓

OUTPUT_DIR = THIS_DIR / "output"


# ============================================================
# 数据获取
# ============================================================

def load_klines(symbol: str, exchange: str) -> pd.DataFrame:
    """获取 30 分钟 K 线(TTL 缓存优先)。

    返回 DataFrame[datetime, open, high, low, close, volume] 升序。
    """
    df = get_klines(
        symbol, exchange, period=KLINE_PERIOD,
        length=KLINE_LENGTH, ttl_hours=9999,  # 回测快照:缓存新鲜期内纯读盘
    )
    if df is None or df.empty:
        raise RuntimeError(f"{symbol} 无数据")
    # 确保数值类型 & 升序
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["open", "high", "low", "close"]).sort_values("datetime").reset_index(drop=True)
    return df.tail(KLINE_LENGTH).reset_index(drop=True)


# ============================================================
# 二次突破状态机引擎
# 蓄势形态(箱体/收敛楔形) → 首次假突破 → 失败回踩 → 二次真突破
# ============================================================

class SecondBreakoutEngine:
    """逐K线状态机:跟踪"蓄势→首次突破→失败→二次突破"四阶段,产出多空入场信号。

    状态流转(做多为例,做空镜像):
        IDLE        : 无蓄势形态,等待
        PRIMED_LONG : 检测到蓄势形态,记录阻力位,等首次向上突破
        BROKEN_LONG : 已首次向上突破,等失败(收盘跌回阻力位下方)
        FAILED_LONG : 已失败(跌回形态内≥FAIL_CONFIRM_BARS根),等二次向上突破 → 入场
        (做空对称:PRIMED_SHORT / BROKEN_SHORT / FAILED_SHORT)

    关键改进 vs 旧 detect_second_breakout:
        - 必须验证首次突破真的"失败"(收盘回到形态内持续N根)
        - 蓄势形态必须是箱体或收敛楔形(有明确边界),不是任意震荡
        - 首次→二次有时效(SECOND_BREAK_MAX_GAP),超时作废
    """

    # 状态常量
    IDLE = "IDLE"
    PRIMED_LONG = "PRIMED_LONG"
    BROKEN_LONG = "BROKEN_LONG"
    FAILED_LONG = "FAILED_LONG"
    PRIMED_SHORT = "PRIMED_SHORT"
    BROKEN_SHORT = "BROKEN_SHORT"
    FAILED_SHORT = "FAILED_SHORT"

    def __init__(self, df: pd.DataFrame):
        self.df = df.reset_index(drop=True)
        self.n = len(df)
        self.state = self.IDLE
        self.pattern_level = None    # 形态边界(阻力/支撑位)
        self.breakout_idx = None     # 首次突破的K线序号
        self.fail_count = 0          # 失败确认计数(回到形态内的连续根数)
        self.signals = []            # [(idx, side, level, atr), ...] 入场信号

        # 预计算形态列(向量化)
        self._compute_patterns()

    def _compute_patterns(self):
        """预计算蓄势形态、ATR、形态边界(阻力/支撑)。"""
        df = self.df
        high, low, close = df["high"], df["low"], df["close"]
        N = config.PATTERN_LOOKBACK

        # 蓄势形态:箱体 OR 收敛楔形
        is_box = Indicators.detect_box(
            high, low, period=N,
            range_max=config.BOX_RANGE_MAX, touch_min=config.BOX_TOUCH_MIN,
            touch_tol=config.BOX_TOUCH_TOL,
        )
        is_wedge = Indicators.detect_wedge(
            high, low, close, period=N,
            atr_shrink=config.WEDGE_ATR_SHRINK, range_shrink=config.WEDGE_RANGE_SHRINK,
        )
        df["setup_pattern"] = (is_box | is_wedge).fillna(False)

        # 形态边界:蓄势窗口内的最高(阻力)/最低(支撑)
        df["resistance_level"] = high.rolling(N).max().shift(1)   # 不含当日
        df["support_level"] = low.rolling(N).min().shift(1)

        # ATR
        df["atr"] = Indicators.ATR(high, low, close, config.ATR_PERIOD)

        # EMA200 趋势过滤:价格在 EMA200 之上才做多,之下才做空(顺势)
        df["ema200"] = Indicators.EMA(close, EMA_PERIOD)

        # ADX/DI(趋势反转出场用)
        adx, plus_di, minus_di = Indicators.ADX(high, low, close, config.ADX_PERIOD)
        df["adx"] = adx
        df["plus_di"] = plus_di
        df["minus_di"] = minus_di

        self.df = df

    def run(self):
        """逐K线推进状态机,填充 self.signals。"""
        for i in range(self.n):
            self._step(i)
        return self.signals

    def _step(self, i):
        df = self.df
        if i < config.PATTERN_LOOKBACK:
            return
        row = df.iloc[i]
        close = float(row["close"])
        high = float(row["high"])
        low = float(row["low"])
        atr = float(row["atr"]) if np.isfinite(row["atr"]) else 0.0
        has_pattern = bool(row["setup_pattern"])
        resistance = float(row["resistance_level"]) if np.isfinite(row["resistance_level"]) else 0.0
        support = float(row["support_level"]) if np.isfinite(row["support_level"]) else 0.0
        ema200 = float(row["ema200"]) if np.isfinite(row.get("ema200", np.nan)) else 0.0
        gap = i - self.breakout_idx if self.breakout_idx is not None else 0

        # ---- 超时回退(首次突破后太久没二次突破) ----
        if self.state in (self.BROKEN_LONG, self.FAILED_LONG, self.BROKEN_SHORT, self.FAILED_SHORT):
            if gap > config.SECOND_BREAK_MAX_GAP:
                self._reset()

        if self.state == self.IDLE:
            # 检测蓄势形态,进入 PRIMED
            if has_pattern and resistance > 0 and support > 0:
                # 同时评估多空潜在形态(后续由首次突破方向决定)
                self.pattern_level = {"resistance": resistance, "support": support}
                # 进入待突破状态(多空都待命,谁先突破跟谁)
                self.state = self.PRIMED_LONG  # 复用:实际多空在突破时判定
                self._primed_short = True      # 标记空头也待命

        elif self.state == self.PRIMED_LONG:
            # 等待首次突破(向上或向下)
            if not has_pattern and self._pattern_expired(i):
                self._reset()
                return
            broke_up = close > resistance * (1 + config.BREAKOUT_THRESHOLD_2ND)
            broke_dn = close < support * (1 - config.BREAKOUT_THRESHOLD_2ND)
            if broke_up:
                self.state = self.BROKEN_LONG
                self.breakout_idx = i
                self.fail_count = 0
                self._primed_short = False
            elif broke_dn and getattr(self, "_primed_short", True):
                self.state = self.BROKEN_SHORT
                self.breakout_idx = i
                self.fail_count = 0
                self.pattern_level = {"resistance": resistance, "support": support}

        elif self.state == self.BROKEN_LONG:
            # 等待失败:收盘跌回阻力位下方,连续 FAIL_CONFIRM_BARS 根
            if close < self.pattern_level["resistance"]:
                self.fail_count += 1
                if self.fail_count >= config.FAIL_CONFIRM_BARS:
                    self.state = self.FAILED_LONG
            else:
                self.fail_count = 0  # 中途又站上,重新计数

        elif self.state == self.BROKEN_SHORT:
            if close > self.pattern_level["support"]:
                self.fail_count += 1
                if self.fail_count >= config.FAIL_CONFIRM_BARS:
                    self.state = self.FAILED_SHORT
            else:
                self.fail_count = 0

        elif self.state == self.FAILED_LONG:
            # 等二次向上突破 → 入场做多(需 EMA200 向上:close > ema200)
            if i - self.breakout_idx < config.SECOND_BREAK_MIN_GAP:
                return
            if close > self.pattern_level["resistance"] * (1 + config.BREAKOUT_THRESHOLD_2ND):
                if ema200 > 0 and close > ema200:
                    self.signals.append((i, 1, self.pattern_level["resistance"], atr))
                self._reset()

        elif self.state == self.FAILED_SHORT:
            # 等二次向下突破 → 入场做空(需 EMA200 向下:close < ema200)
            if i - self.breakout_idx < config.SECOND_BREAK_MIN_GAP:
                return
            if close < self.pattern_level["support"] * (1 - config.BREAKOUT_THRESHOLD_2ND):
                if ema200 > 0 and close < ema200:
                    self.signals.append((i, -1, self.pattern_level["support"], atr))
                self._reset()

    def _pattern_expired(self, i):
        """蓄势形态是否过期(超过2倍窗口未突破)。"""
        return i - (self.breakout_idx or i) > config.PATTERN_LOOKBACK * 2

    def _reset(self):
        self.state = self.IDLE
        self.pattern_level = None
        self.breakout_idx = None
        self.fail_count = 0
        self._primed_short = True


def backtest_symbol(df: pd.DataFrame, multiplier: float) -> dict:
    """二次突破状态机驱动的回测(资金比例开仓 + 硬止损,每品种独立500万账户)。

    资金管理:
    - 开仓金额 = 当前资金 × 8%,手数 = 开仓金额 / (入场价 × 保证金率)
    - 加仓手数 = 初次开仓手数 × 1/3(浮盈达1R时,仅一次)
    - 硬止损:单笔浮亏达开仓时总资金 × 千分之9(0.9%)即平仓
    - 移动止损:盈利后跟踪极值 ∓ ATR(让利润奔跑)
    """
    n = len(df)
    if n < config.PATTERN_LOOKBACK + config.ATR_PERIOD + 10:
        return {"error": "data too short"}

    # 每品种独立账户,初始500万
    capital_state = {"capital": float(INITIAL_CAPITAL)}

    engine = SecondBreakoutEngine(df)
    signals = engine.run()
    df = engine.df

    trades = []
    position = None
    sig_queue = list(signals)
    last_sig_idx = -1

    def _open(idx, side, level, atr_val):
        if idx + 1 >= n or atr_val <= 0:
            return None
        entry_bar = df.iloc[idx + 1]
        raw = float(entry_bar["open"])
        slip = SLIPPAGE_TICKS * TICK_SIZE
        entry_price = raw + slip * side
        # 开仓手数 = 当前资金×8%(作为保证金) / (入场价 × 合约乘数 × 保证金率)
        position_value = capital_state["capital"] * RISK_CAPITAL_RATIO
        lots = int(position_value / (entry_price * multiplier * MARGIN_RATE))
        if lots < 1:
            return None
        stop = level - side * atr_val * EXIT_STOP_ATR
        R = abs(entry_price - stop)
        if R <= 0:
            return None
        return {
            "side": side,
            "entry_dt": entry_bar["datetime"],
            "entry_idx": idx + 1,
            "level": level,
            "initial_entry": entry_price,
            "initial_stop": stop,
            "R": R,
            "lots": [{"entry_price": entry_price, "size": lots, "entry_idx": idx + 1}],
            "initial_lots": lots,
            "stop": stop,
            "added": False,
            "breakeven_done": False,
            "max_lots": lots,
            "capital_at_open": capital_state["capital"],
        }

    def _close_all(pos, j, price, reason):
        bar = df.iloc[j]
        pnl_money = 0.0
        pnl_price = 0.0
        total_size = 0
        for lot in pos["lots"]:
            pp = (price - lot["entry_price"]) * pos["side"] * lot["size"]
            cc = (lot["entry_price"] + price) * lot["size"] * COMMISSION_RATE
            pnl_money += (pp - cc) * multiplier
            pnl_price += pp - cc
            total_size += lot["size"]
        capital_state["capital"] += pnl_money
        trades.append({
            "side": "LONG" if pos["side"] == 1 else "SHORT",
            "entry_dt": pos["entry_dt"],
            "exit_dt": bar["datetime"],
            "hold_bars": j - pos["entry_idx"],
            "size": total_size,
            "max_lots": pos["max_lots"],
            "added": pos["added"],
            "pnl_price": pnl_price,
            "pnl_money": pnl_money,
            "reason": reason,
        })

    def _trail_stop(pos, i, bar):
        side = pos["side"]
        close = float(bar["close"])
        atr_now = float(bar["atr"]) if np.isfinite(bar.get("atr", np.nan)) else 0
        unreal = (close - pos["initial_entry"]) * side
        R_mult = unreal / pos["R"]
        if not pos["breakeven_done"] and R_mult >= EXIT_BREAKEVEN_R:
            avg = sum(l["entry_price"] * l["size"] for l in pos["lots"]) / sum(l["size"] for l in pos["lots"])
            if side == 1:
                pos["stop"] = max(pos["stop"], avg)
            else:
                pos["stop"] = min(pos["stop"], avg)
            pos["breakeven_done"] = True
            if not pos["added"] and i + 1 < n:
                add_lots = max(1, int(pos["initial_lots"] * ADD_LOTS_RATIO))
                add_price = float(df.iloc[i + 1]["open"]) + SLIPPAGE_TICKS * TICK_SIZE * side
                pos["lots"].append({"entry_price": add_price, "size": add_lots, "entry_idx": i + 1})
                pos["added"] = True
                pos["max_lots"] = max(pos["max_lots"], sum(l["size"] for l in pos["lots"]))
        if pos["breakeven_done"] and atr_now > 0:
            if side == 1:
                new_stop = float(bar["low"]) - atr_now * EXIT_TRAIL_ATR
                pos["stop"] = max(pos["stop"], new_stop)
            else:
                new_stop = float(bar["high"]) + atr_now * EXIT_TRAIL_ATR
                pos["stop"] = min(pos["stop"], new_stop)

    def _hard_stop_price(pos):
        """硬止损价:单笔浮亏达开仓时总资金×千分之9 的价格。"""
        max_loss = pos["capital_at_open"] * HARD_STOP_LOSS
        total_size = sum(l["size"] for l in pos["lots"])
        if total_size <= 0:
            return None
        avg_cost = sum(l["entry_price"] * l["size"] for l in pos["lots"]) / total_size
        side = pos["side"]
        price_at_loss = avg_cost - side * max_loss / (total_size * multiplier)
        return price_at_loss

    for i in range(n):
        bar = df.iloc[i]
        if position is not None:
            side = position["side"]
            high = float(bar["high"]); low = float(bar["low"]); close = float(bar["close"])
            hard_stop = _hard_stop_price(position)
            hit_hard = (side == 1 and low <= hard_stop) or (side == -1 and high >= hard_stop)
            hit_tech = (side == 1 and low <= position["stop"]) or (side == -1 and high >= position["stop"])
            if hit_hard:
                _close_all(position, i, hard_stop, "hard_stop")
                position = None
            elif hit_tech:
                _close_all(position, i, position["stop"], "stop_loss")
                position = None
            else:
                _trail_stop(position, i, bar)
                if position is not None and _check_trend_reversal(df, i, side):
                    _close_all(position, i, close, "trend_reversal")
                    position = None
        if position is None and sig_queue:
            sig_idx, sig_side, sig_level, sig_atr = sig_queue[0]
            if sig_idx == i and sig_idx > last_sig_idx:
                last_sig_idx = sig_idx
                sig_queue.pop(0)
                position = _open(sig_idx, sig_side, sig_level, sig_atr)
            elif sig_idx < i:
                sig_queue.pop(0)

    if position is not None:
        last = df.iloc[-1]
        _close_all(position, n - 1, float(last["close"]), "eod_close")
        position = None

    summary = _summarize(df, trades, multiplier, n_signals=len(signals))
    summary["_trades"] = trades
    summary["final_capital"] = capital_state["capital"]
    summary["capital_return"] = (capital_state["capital"] - INITIAL_CAPITAL) / INITIAL_CAPITAL
    return summary


def _check_trend_reversal(df: pd.DataFrame, i: int, side: int) -> bool:
    """趋势反转出场判断(沿用 signal_generator._generate_exit_signals 逻辑)。

    多头出场: ADX<20 或 +DI/-DI 死叉 或 收盘破前5根最低。
    空头出场: ADX<20 或 -DI/+DI 金叉 或 收盘破前5根最高。
    """
    row = df.iloc[i]
    if "adx" not in df.columns:
        return False
    adx = row.get("adx", np.nan)
    plus_di = row.get("plus_di", np.nan)
    minus_di = row.get("minus_di", np.nan)
    if not np.isfinite(adx):
        return False

    adx_weak = adx < 20
    close = float(row["close"])

    if side == 1:
        if adx_weak:
            return True
        # +DI 下穿 -DI
        if i >= 1:
            prev = df.iloc[i - 1]
            if (plus_di < minus_di) and (prev.get("plus_di", np.nan) >= prev.get("minus_di", np.nan)):
                return True
        # 破前5根最低
        prev_low = df["low"].iloc[max(0, i - 5):i].min() if i >= 1 else np.nan
        if np.isfinite(prev_low) and close < prev_low:
            return True
    else:
        if adx_weak:
            return True
        if i >= 1:
            prev = df.iloc[i - 1]
            if (minus_di < plus_di) and (prev.get("minus_di", np.nan) >= prev.get("plus_di", np.nan)):
                return True
        prev_high = df["high"].iloc[max(0, i - 5):i].max() if i >= 1 else np.nan
        if np.isfinite(prev_high) and close > prev_high:
            return True
    return False


# ============================================================
# 指标统计
# ============================================================

def _summarize(df: pd.DataFrame, trades: list, multiplier: float, n_signals: int = 0) -> dict:
    # 多空信号数:从交易记录按方向统计(状态机产出的信号未必都开仓,这里统计实际交易)
    n_long_trades = sum(1 for t in trades if t["side"] == "LONG")
    n_short_trades = sum(1 for t in trades if t["side"] == "SHORT")

    n_trades = len(trades)
    wins = [t for t in trades if t["pnl_money"] > 0]
    losses = [t for t in trades if t["pnl_money"] <= 0]
    win_rate = len(wins) / n_trades if n_trades > 0 else 0.0

    total_pnl_money = sum(t["pnl_money"] for t in trades)
    total_pnl_price = sum(t["pnl_price"] for t in trades)

    avg_price = float(df["close"].mean())
    # 口径A: 价格点数收益率(Σ净盈亏点数 / 品种均价,不依赖资金/杠杆)
    return_price = total_pnl_price / avg_price if avg_price > 0 else 0.0

    # 口径B: 账户资金收益率(总盈亏 / 初始资金 400 万)
    return_capital = total_pnl_money / INITIAL_CAPITAL if INITIAL_CAPITAL > 0 else 0.0

    # 年化(按实际时间跨度)
    span_days = (df["datetime"].iloc[-1] - df["datetime"].iloc[0]).total_seconds() / 86400.0
    years = span_days / 365.0 if span_days > 0 else 0.0
    if years > 0 and return_capital > -1:
        annual = (1 + return_capital) ** (1 / years) - 1
    else:
        annual = 0.0

    # 盈亏比
    avg_win = np.mean([t["pnl_money"] for t in wins]) if wins else 0.0
    avg_loss = abs(np.mean([t["pnl_money"] for t in losses])) if losses else 0.0
    profit_factor = avg_win / avg_loss if avg_loss > 0 else (float("inf") if avg_win > 0 else 0.0)

    # 平均持仓 K 线数 & 平均峰值手数 & 加仓比例
    avg_hold = np.mean([t["hold_bars"] for t in trades]) if trades else 0.0
    avg_max_lots = np.mean([t["max_lots"] for t in trades]) if trades else 0.0
    add_ratio = (sum(1 for t in trades if t.get("added")) / n_trades) if n_trades > 0 else 0.0

    # 出场原因分布
    reason_dist = {}
    for t in trades:
        reason_dist[t["reason"]] = reason_dist.get(t["reason"], 0) + 1

    return {
        "bars": len(df),
        "span_days": span_days,
        "start": df["datetime"].iloc[0],
        "end": df["datetime"].iloc[-1],
        "avg_price": avg_price,
        "n_signals": n_signals,             # 状态机产出的总信号数(含未开仓)
        "long_trades": n_long_trades,
        "short_trades": n_short_trades,
        "trades": n_trades,
        "win_rate": win_rate,
        "total_pnl_money": total_pnl_money,
        "total_pnl_price": total_pnl_price,
        "return_price": return_price,      # 口径A
        "return_capital": return_capital,  # 口径B(账户收益率,分母400万)
        "annual_return": annual,
        "profit_factor": profit_factor,
        "avg_hold_bars": avg_hold,
        "avg_max_lots": avg_max_lots,      # 平均峰值手数
        "add_ratio": add_ratio,            # 加仓交易占比
        "reason_dist": reason_dist,
    }


# ============================================================
# 输出
# ============================================================

def print_summary(results: dict):
    """打印中文汇总表。"""
    print()
    print("=" * 118)
    period_label = "小时级别" if KLINE_PERIOD == "60" else f"{KLINE_PERIOD}分钟"
    print(f"  {period_label} 二次突破系统回测 — 蓄势形态(箱体/楔形) + 假突破 + 二次突破 + EMA200顺势 + 移动止损")
    print("=" * 118)
    print(f"  入场: 蓄势{config.PATTERN_LOOKBACK}根(箱体≤{config.BOX_RANGE_MAX*100:.0f}%/楔形收敛) → 首次突破 → "
          f"失败回踩≥{config.FAIL_CONFIRM_BARS}根 → 二次突破 + EMA{EMA_PERIOD}顺势")
    print(f"  出场: 硬止损千分之{int(HARD_STOP_LOSS*1000)}(总资金) / 初始{EXIT_STOP_ATR}×ATR / 浮盈1R保本+加仓 / 移动止损{EXIT_TRAIL_ATR}×ATR / 趋势反转")
    print(f"  仓位: 资金×{RISK_CAPITAL_RATIO*100:.0f}%开仓 + 盈利1R加仓初次手数×{ADD_LOTS_RATIO:.2f} | "
          f"账户{INITIAL_CAPITAL/10000:.0f}万/品种 | 手续费{COMMISSION_RATE*10000:.1f}‱ 滑点{SLIPPAGE_TICKS}跳")
    print("-" * 118)
    header = (f"  {'品种':<8}{'K线':>6}{'天数':>6}{'信号':>6}{'做多':>6}{'做空':>6}"
              f"{'胜率':>7}{'收益率(价格)':>13}{'账户收益率':>12}{'年化':>9}{'盈亏比':>8}"
              f"{'均峰值手':>9}{'加仓率':>7}{'均持仓':>7}")
    print(header)
    print("  " + "-" * 114)

    agg = {k: 0 for k in ["n_signals", "long_trades", "short_trades", "trades", "wins",
                          "total_pnl_money", "total_pnl_price"]}
    for sym, r in results.items():
        if "error" in r:
            print(f"  {sym:<8}  [错误] {r['error']}")
            continue
        wins = int(round(r["win_rate"] * r["trades"]))
        agg["n_signals"] += r.get("n_signals", 0)
        agg["long_trades"] += r["long_trades"]
        agg["short_trades"] += r["short_trades"]
        agg["trades"] += r["trades"]
        agg["wins"] += wins
        agg["total_pnl_money"] += r["total_pnl_money"]
        agg["total_pnl_price"] += r["total_pnl_price"]

        print(f"  {sym:<8}{r['bars']:>6}{r['span_days']:>6.0f}"
              f"{r.get('n_signals',0):>6}{r['long_trades']:>6}{r['short_trades']:>6}"
              f"{r['win_rate']*100:>6.1f}%"
              f"{r['return_price']*100:>12.2f}%"
              f"{r['return_capital']*100:>11.2f}%"
              f"{r['annual_return']*100:>8.2f}%"
              f"{r['profit_factor']:>8.2f}"
              f"{r['avg_max_lots']:>9.1f}"
              f"{r['add_ratio']*100:>6.0f}%{r['avg_hold_bars']:>7.1f}")

    # 合计行
    print("  " + "-" * 114)
    n = agg["trades"]
    wr = agg["wins"] / n if n > 0 else 0
    n_acc = sum(1 for r in results.values() if "error" not in r)
    total_capital = INITIAL_CAPITAL * n_acc
    cap_ret_total = agg["total_pnl_money"] / total_capital if total_capital > 0 else 0.0
    print(f"  {'合计':<8}{'':>6}{'':>6}"
          f"{agg['n_signals']:>6}{agg['long_trades']:>6}{agg['short_trades']:>6}"
          f"{wr*100:>6.1f}%"
          f"{agg['total_pnl_price']:>12.0f}点"
          f"{cap_ret_total*100:>11.2f}%"
          f"{'':>9}{'':>8}{'':>9}{'':>7}{'':>7}")
    print(f"  ({n_acc}个独立账户合计: 总盈亏 {agg['total_pnl_money']:+,.0f} 元 / 总本金 {total_capital/10000:.0f}万 → 账户收益率 {cap_ret_total*100:+.2f}%)")
    print("=" * 118)

    print("""
  指标说明:
    信号     = 状态机产出的二次突破信号数(含未开仓的重叠信号)
    做多/做空= 实际开仓的多/空交易数
    胜率     = 盈利交易数 / 总交易数
    收益率(价格)= Σ净盈亏点数 / 品种均价   (口径A:不依赖资金,反映策略本身)
    账户收益率 = Σ净盈亏金额 / 400万本金   (口径B:含加仓放大)
    年化     = (1+账户收益率)^(365/实际天数) - 1
    加仓率   = 触发加仓的交易占比(浮盈达1R)
""")


def _save_csv(df: pd.DataFrame, path: Path, label: str):
    """保存 CSV，文件被占用时自动带时间戳另存，避免 PermissionError 中断。"""
    try:
        df.to_csv(path, index=False, encoding="utf-8-sig")
        print(f"  {label}已保存: {path}")
    except PermissionError:
        from datetime import datetime
        ts = datetime.now().strftime("%H%M%S")
        alt = path.with_name(path.stem + f"_{ts}" + path.suffix)
        df.to_csv(alt, index=False, encoding="utf-8-sig")
        print(f"  {label}已保存(原文件被占用,另存): {alt}")


def save_results(results: dict, all_trades: list):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # 汇总 CSV
    rows = []
    for sym, r in results.items():
        if "error" in r:
            continue
        rows.append({
            "品种": sym,
            "K线数": r["bars"],
            "回测天数": round(r["span_days"], 1),
            "起始时间": str(r["start"]),
            "结束时间": str(r["end"]),
            "均价": round(r["avg_price"], 2),
            "信号数": r.get("n_signals", 0),
            "做多交易数": r["long_trades"],
            "做空交易数": r["short_trades"],
            "实际交易数": r["trades"],
            "胜率": round(r["win_rate"] * 100, 2),
            "总盈亏金额": round(r["total_pnl_money"], 0),
            "收益率_价格口径%": round(r["return_price"] * 100, 2),
            "账户收益率%": round(r["return_capital"] * 100, 2),
            "年化收益率%": round(r["annual_return"] * 100, 2),
            "盈亏比": round(r["profit_factor"], 2),
            "平均峰值手数": round(r["avg_max_lots"], 2),
            "加仓率%": round(r["add_ratio"] * 100, 1),
            "平均持仓K线数": round(r["avg_hold_bars"], 1),
            "出场原因": str(r["reason_dist"]),
        })
    summary_df = pd.DataFrame(rows)
    spath = OUTPUT_DIR / "backtest_30m_summary.csv"
    _save_csv(summary_df, spath, "汇总")

    # 逐笔 CSV
    if all_trades:
        tdf = pd.DataFrame(all_trades)
        tpath = OUTPUT_DIR / "backtest_30m_trades.csv"
        _save_csv(tdf, tpath, f"逐笔({len(tdf)}笔)")


# ============================================================
# 主入口
# ============================================================

def main():
    print("=" * 110)
    print(f"  小时级别({KLINE_PERIOD}分钟) 二次突破回测  —  数据加载")
    print("=" * 110)

    # 1) 加载全部数据
    data = {}
    for sym, name, ex, mult in TARGETS:
        print(f"  [{sym}] {name} ({ex}) 乘数={mult} ...", end=" ", flush=True)
        try:
            df = load_klines(sym, ex)
            data[sym] = (df, mult, name)
            print(f"{len(df)} 根  {df['datetime'].iloc[0]} ~ {df['datetime'].iloc[-1]}")
        except Exception as e:
            print(f"失败: {e}")

    if not data:
        print("  [错误] 无可用数据,退出。")
        return

    # 2) 逐品种生成信号 + 回测
    print()
    print("=" * 110)
    print("  逐品种回测")
    print("=" * 110)

    results = {}
    all_trades = []
    for sym, (df, mult, name) in data.items():
        print(f"  [{sym}] {name} 二次突破状态机 + 回测 ...", end=" ", flush=True)
        try:
            r = backtest_symbol(df, mult)
            results[sym] = r
            for t in r.get("_trades", []):
                t["symbol"] = sym
                all_trades.append(t)
            if "error" in r:
                print(f"错误: {r['error']}")
            else:
                print(f"信号={r.get('n_signals',0)} 交易={r['trades']}  胜率={r['win_rate']*100:.1f}%  "
                      f"账户收益率={r['return_capital']*100:.2f}%  年化={r['annual_return']*100:.2f}%")
        except Exception as e:
            import traceback
            print(f"异常: {e}")
            traceback.print_exc()
            results[sym] = {"error": str(e)}

    # 3) 输出
    print_summary(results)
    save_results(results, all_trades)


def _iter_trades(r: dict):
    """从回测结果里取 trades 列表(回测引擎内部存了,这里透传)。"""
    # backtest_symbol 没有把 trades 放进返回 dict,这里需要一个间接通道。
    # 改为:backtest_symbol 直接返回 trades。
    return r.get("_trades", [])


if __name__ == "__main__":
    main()
