"""
renko.py
========
Renko Chart 核心引擎，全部向量化、无未来函数。

覆盖需求 1/2：
  (1) ATR 砖块大小自适应 —— 每个品种按其小时线 ATR 计算合适的价格梯度
  (2) Renko Chart 生成 + 以"砖块数"为单位的 RSI（而非时间周期）

────────────────────────────────────────────────────────────────────
核心概念
────────────────────────────────────────────────────────────────────
- 砖 (brick)：固定价格梯度 brick_size 的一块。上涨砖收在上一块 +brick_size，
  下跌砖收在上一块 -brick_size。
- 传统 Renko 仅在价格穿越阈值时产新砖；一块砖可以"吸收"多根 K 线，
  也可以一根 K 线产生多块砖（大涨大跌）。
- 砖的方向序列 dir ∈ {+1, -1} 是本系统所有信号的输入，因为 Renko 已天然
  去除了时间噪声，仅保留"有效价格运动"。

────────────────────────────────────────────────────────────────────
为何用 ATR 定砖
────────────────────────────────────────────────────────────────────
固定比例（如 close*0.5%）对低波动品种过粗、对高波动品种过细。用 1 倍小时
ATR 作为砖块，能让每块砖代表"一个正常小时的价格运动量"——既不过细（噪声
频繁翻砖）也不过粗（漏掉有效运动）。再按品种最小变动价位 round。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd


# =====================================================================
# 1) ATR（Wilder）—— 用于砖块大小自适应
# =====================================================================
def atr(df: pd.DataFrame, period: int = 60) -> pd.Series:
    """Wilder ATR（与 future_4 indicators.atr 一致，独立复制以解耦）"""
    high, low = df["high"], df["low"]
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [(high - low).abs(),
         (high - prev_close).abs(),
         (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1.0 / period, adjust=False).mean()


# =====================================================================
# 2) 砖块大小（ATR 自适应 + 品种合规 round）
# =====================================================================
def compute_brick_size(
    df: pd.DataFrame,
    cfg_brick: dict,
    symbol: Optional[str] = None,
) -> float:
    """
    计算单品种的 Renko 砖块价格梯度。

    规则：
      - 若 per_symbol_override[symbol] 存在则直接用
      - 否则 brick_size = atr_mult * ATR(period)
      - 夹在 [close * min_size_ratio, close * max_size_ratio] 之间
      - 按 close 量级 round 到"干净"步长（近似品种最小变动价位）

    Returns
    -------
    float : 砖块价格梯度（>0）
    """
    override = (cfg_brick.get("per_symbol_override") or {}).get(symbol)
    if override:
        return float(override)

    close = float(df["close"].iloc[-1])
    a = atr(df, period=int(cfg_brick.get("atr_period", 60)))
    a_val = float(a.iloc[-1]) if (len(a) and not np.isnan(a.iloc[-1])) else close * 0.005

    size = cfg_brick.get("atr_mult", 1.0) * a_val
    lo = close * cfg_brick.get("min_size_ratio", 0.0015)
    hi = close * cfg_brick.get("max_size_ratio", 0.015)
    size = float(np.clip(size, lo, hi))

    if cfg_brick.get("use_close_round", True):
        size = _round_to_tick(size, close)
    # 保底：非零
    return max(size, _round_to_tick(close * cfg_brick.get("min_size_ratio", 0.0015), close))


def _round_to_tick(size: float, price: float) -> float:
    """把 size round 到"干净"步长（近似最小变动价位）。

    无品种 tick 表时的通用做法：按价格量级选步长（1, 2, 5 × 10^k），
    让砖块大小落在合规、可读的网格上。这对绝大多数期货品种足够准确。
    """
    if size <= 0 or price <= 0:
        return size
    # 按 size 量级取 1/2/5 网格
    mag = 10 ** math.floor(math.log10(size))
    candidates = [c * mag for c in (1.0, 2.0, 5.0, 10.0)]
    best = min(candidates, key=lambda c: abs(c - size))
    # 微小价位品种（< 1）保留更多小数位
    if best < 1:
        best = round(best, 3)
    elif best < 10:
        best = round(best, 2)
    else:
        best = round(best, 1)
    return best


# =====================================================================
# 3) Renko Chart 生成（向量化核心）
# =====================================================================
@dataclass
class Renko:
    """Renko 序列容器。

    Attributes
    ----------
    bricks : DataFrame
        每块砖一行，列：
        - brick_idx : 整数序号（0-based）
        - dir       : +1 上涨砖 / -1 下跌砖
        - open, close : 该砖的开收价（close = open ± brick_size）
        - open_dt, close_dt : 触发该砖的首末小时 K 线时间
        - n_bars   : 该砖吸收了多少根小时 K 线
        - source_idx: 触发该砖最后一根的小时 K 线原始索引（可空）
    brick_size : float
    source_close : pd.Series   源小时线 close（调试/绘图用）
    """
    bricks: pd.DataFrame
    brick_size: float


def build_renko(
    df: pd.DataFrame,
    brick_size: float,
) -> Renko:
    """
    由小时 K 线 df 生成 Renko 砖块序列。

    算法（经典 reversal 规则，无未来函数，保证终止）：
      对每根 K 线：
        1) 先在当前方向 last_dir 上"耗尽"：只要该方向极值还够 1 块，就补延续砖。
        2) 再判断反转：若反向累计幅度 >= 2*brick_size（经典 Renko 反转阈值），
           翻向并产出首块反向砖，然后在新方向上继续耗尽该 K 线的剩余幅度。
        3) 每根 K 线最多反转一次（避免在宽幅 K 线内来回 ping-pong 死循环）。
      一根 K 线可产多块砖（暴涨暴跌），但 last_close 单调推进、保证终止。

    返回的 bricks 已按时间顺序排列。
    """
    bs = float(brick_size)
    if bs <= 0:
        raise ValueError("brick_size must be > 0")

    o = df["open"].to_numpy(dtype=float)
    h = df["high"].to_numpy(dtype=float)
    l = df["low"].to_numpy(dtype=float)
    dt = df["datetime"].to_numpy()
    n = len(df)
    if n == 0:
        return Renko(pd.DataFrame(columns=[
            "brick_idx", "dir", "open", "close", "open_dt", "close_dt",
            "n_bars", "source_idx"]), bs)

    rows = []
    # 起点：第一块砖的 open 取第一根 K 线的 open
    last_close = float(o[0])
    last_dir = 0  # 0=中性(尚未确立方向)，1=多，-1=空
    bidx = 0
    brick_start_bar = 0  # 当前(尚未收盘)砖吸收的第一根 K 线

    def emit(d: int, i: int):
        """产出一块方向为 d 的砖；闭包更新 last_close/last_dir/bidx。"""
        nonlocal last_close, last_dir, bidx, brick_start_bar
        new_close = last_close + d * bs
        rows.append({
            "brick_idx": bidx, "dir": d,
            "open": last_close, "close": new_close,
            "open_dt": dt[brick_start_bar], "close_dt": dt[i],
            "n_bars": i - brick_start_bar + 1,
            "source_idx": i,
        })
        last_close = new_close
        last_dir = d
        bidx += 1
        brick_start_bar = i

    for i in range(n):
        hi = h[i]
        lo = l[i]

        # ---- 中性起步：首根可单向确立方向（只需 1 块阈值）----
        if last_dir == 0:
            up_room = hi - last_close
            dn_room = last_close - lo
            if up_room >= bs and up_room >= dn_room:
                emit(1, i)
                while hi - last_close >= bs:   # 耗尽向上
                    emit(1, i)
            elif dn_room >= bs:
                emit(-1, i)
                while last_close - lo >= bs:   # 耗尽向下
                    emit(-1, i)
            continue  # 确立方向的这根不再反转

        # ---- 1) 在当前方向上耗尽延续砖 ----
        if last_dir == 1:
            while hi - last_close >= bs:
                emit(1, i)
        else:  # last_dir == -1
            while last_close - lo >= bs:
                emit(-1, i)

        # ---- 2) 至多一次反转 ----
        if last_dir == 1 and (last_close - lo) >= 2 * bs:
            emit(-1, i)            # 首块反向砖（消耗 2*bs 中的第一块）
            while last_close - lo >= bs:   # 在新方向上耗尽剩余幅度
                emit(-1, i)
        elif last_dir == -1 and (hi - last_close) >= 2 * bs:
            emit(1, i)
            while hi - last_close >= bs:
                emit(1, i)

    bricks = pd.DataFrame(rows)
    if len(bricks) == 0:
        bricks = pd.DataFrame(columns=[
            "brick_idx", "dir", "open", "close", "open_dt", "close_dt",
            "n_bars", "source_idx"])
    return Renko(bricks=bricks, brick_size=bs)


# =====================================================================
# 4) Renko-RSI：以砖块数为单位（非时间周期）
# =====================================================================
def renko_rsi(renko: Renko, period: int = 14) -> pd.Series:
    """
    在 Renko 砖块序列上计算 RSI，period 是"砖块数"。

    Renko 的妙处：每块砖要么 +1（涨）要么 -1（跌），所以"涨跌"天然就是砖的
    方向。这里用经典 Wilder RSI 公式，但输入序列是砖的 close 序列而非 K 线：
      gain  = max(close - prev_close, 0)
      loss  = max(prev_close - close, 0)
      RS    = EMA(gain) / EMA(loss)   (alpha = 1/period)
      RSI   = 100 - 100/(1+RS)

    因为砖的 close 是离散的 ±brick_size，RSI 在持续单边时会快速逼近
    100/0，这正是"砖块驱动的超买超卖"——比时间 RSI 更早暴露极端。
    """
    b = renko.bricks
    if len(b) < 2:
        return pd.Series(dtype=float)
    close = b["close"].astype(float)
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False).mean()
    # Wilder：avg_loss==0 → RSI=100；avg_gain==0 → RSI=0；其余用 RS 公式
    rsi = pd.Series(np.nan, index=close.index)
    both_pos = (avg_gain > 0) & (avg_loss > 0)
    rs = avg_gain[both_pos] / avg_loss[both_pos]
    rsi.loc[both_pos] = 100.0 - 100.0 / (1.0 + rs)
    rsi.loc[avg_loss == 0] = 100.0
    rsi.loc[avg_gain == 0] = 0.0
    rsi.index = b["brick_idx"].values
    return rsi


# =====================================================================
# 5) 砖块"运行段" (run) —— 趋势/回调信号的基础结构
# =====================================================================
def brick_runs(renko: Renko) -> pd.DataFrame:
    """
    把连续同向砖合并为"运行段"。

    Returns DataFrame:
      - run_id, dir, start_idx, end_idx, n_bricks,
      - start_price (首砖 open), end_price (末砖 close), extent (end-start)
      - start_dt, end_dt

    趋势回调信号在此基础上识别："大段趋势 → 小段回调 → 再起同向段"。
    """
    b = renko.bricks.reset_index(drop=True)
    if len(b) == 0:
        return pd.DataFrame(columns=[
            "run_id", "dir", "start_idx", "end_idx", "n_bricks",
            "start_price", "end_price", "extent", "start_dt", "end_dt"])
    d = b["dir"].to_numpy()
    # 段边界：方向变化处
    change = np.where(np.diff(d) != 0)[0] + 1
    starts = np.concatenate(([0], change))
    ends = np.concatenate((change, [len(d)])) - 1
    rows = []
    for k, (s, e) in enumerate(zip(starts, ends)):
        rows.append({
            "run_id": k,
            "dir": int(d[s]),
            "start_idx": int(b["brick_idx"].iloc[s]),
            "end_idx": int(b["brick_idx"].iloc[e]),
            "n_bricks": int(e - s + 1),
            "start_price": float(b["open"].iloc[s]),
            "end_price": float(b["close"].iloc[e]),
            "extent": float(b["close"].iloc[e] - b["open"].iloc[s]),
            "start_dt": b["open_dt"].iloc[s],
            "end_dt": b["close_dt"].iloc[e],
        })
    return pd.DataFrame(rows)


# =====================================================================
# 自检
# =====================================================================
if __name__ == "__main__":
    from data_loader import load_klines, load_config

    cfg = load_config()
    df = load_klines("AU0", cfg)
    print(f"AU0: {len(df)} bars")
    bs = compute_brick_size(df, cfg["brick"], symbol="AU0")
    print(f"brick_size = {bs}")
    rk = build_renko(df, bs)
    print(f"bricks = {len(rk.bricks)}")
    print(rk.bricks.tail(6).to_string(index=False))
    rsi = renko_rsi(rk, period=cfg["renko_rsi"]["period"])
    print(f"last RSI = {rsi.iloc[-1]:.1f}")
    runs = brick_runs(rk)
    print(f"runs = {len(runs)}")
    print(runs.tail(4).to_string(index=False))
