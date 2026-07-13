"""
signals.py
==========
Renko 信号识别。覆盖需求 3/4/5：

  (3) 密集成交区 —— 水平价位阻力/支撑
      - 在砖块序列上把"砖的 open/close 价位"按桶聚类，得到水平密集带
      - 识别两类信号：
          · 遇阻回落 / 遇阻回升（触及密集带后反转）
          · 突破阻力 / 跌破支撑（突破密集带后延续）
      信号在"确认砖"（连续 N 块同向砖）出现时落定，无未来函数

  (4) 趋势回调顺势
      - 大段同向砖确认"趋势"
      - 随后回调（反向砖段），幅度 <= 前期涨幅/跌幅的 1/2
      - 回调后再出现 N 块同向砖 → 顺势信号（多/空）

  (5) RSI 超买超卖风险提示
      - Renko-RSI >= 极端超买阈值 → 严重超买风险（顶部反转预警）
      - Renko-RSI <= 极端超卖阈值 → 严重超卖风险（底部反转预警）

所有信号统一输出 DataFrame，含 direction / type / entry_idx(砖) /
entry_time(砖收盘时间) / entry_price / 强度评分等。
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from renko import Renko, renko_rsi


# =====================================================================
# 3) 密集成交区（水平价位阻力/支撑）
# =====================================================================
def find_zones(renko: Renko, cfg_zones: dict) -> pd.DataFrame:
    """
    识别水平密集成交区：把所有砖的 open 价位按桶宽 bin 聚类，
    桶内砖数 >= zone_touch_min 即为密集带。

    Returns DataFrame:
      - zone_id, price_lo, price_hi, n_touch, n_up, n_down,
        first_dt, last_dt (该桶涉及砖的时间范围)
    """
    b = renko.bricks
    if len(b) == 0:
        return pd.DataFrame(columns=[
            "zone_id", "price_lo", "price_hi", "n_touch",
            "n_up", "n_down", "first_dt", "last_dt"])
    bs = renko.brick_size
    bin_w = bs * cfg_zones.get("price_bin_ratio", 1.0)
    tol = bs * cfg_zones.get("reentry_within_bricks", 1)
    lookback = int(cfg_zones.get("zone_lookback_bricks", 300))

    # 仅在最近 lookback 块砖内统计（自适应滚动）
    recent = b.tail(lookback).reset_index(drop=True)
    prices = recent["open"].to_numpy(dtype=float)
    dirs = recent["dir"].to_numpy(dtype=int)
    dts_open = recent["open_dt"].to_numpy()
    dts_close = recent["close_dt"].to_numpy()

    # 用"价位的最近网格"分桶
    base = prices.min()
    bins = np.floor((prices - base) / bin_w).astype(int)
    order = np.argsort(bins, kind="stable")
    sorted_bins = bins[order]

    rows = []
    zid = 0
    i = 0
    m = len(sorted_bins)
    while i < m:
        j = i
        while j + 1 < m and sorted_bins[j + 1] == sorted_bins[i]:
            j += 1
        grp_idx = order[i:j + 1]
        n_touch = len(grp_idx)
        if n_touch >= cfg_zones.get("zone_touch_min", 3):
            grp_prices = prices[grp_idx]
            grp_dirs = dirs[grp_idx]
            grp_open_dts = dts_open[grp_idx]
            grp_close_dts = dts_close[grp_idx]
            rows.append({
                "zone_id": zid,
                "price_lo": float(grp_prices.min()) - tol,
                "price_hi": float(grp_prices.max()) + tol,
                "n_touch": int(n_touch),
                "n_up": int((grp_dirs == 1).sum()),
                "n_down": int((grp_dirs == -1).sum()),
                "first_dt": grp_open_dts.min(),
                "last_dt": grp_close_dts.max(),
            })
            zid += 1
        i = j + 1

    return pd.DataFrame(rows)


def zone_signals(renko: Renko, cfg_zones: dict, zones: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    """
    遍历砖块，识别"触及密集成交区后的反转/突破"信号。

    遇阻回落(空)：价格上探进入上方阻力带 → 连续 N 块下跌砖确认
    遇阻回升(多)：价格下探进入下方支撑带 → 连续 N 块上涨砖确认
    突破阻力(多)：突破阻力带上沿 → 连续 N 块上涨砖确认
    跌破支撑(空)：跌破支撑带下沿 → 连续 N 块下跌砖确认

    Returns: 信号行（每行一个信号），列含 brick_idx/dir/type/zone_id/entry...
    """
    b = renko.bricks
    if len(b) == 0:
        return pd.DataFrame()
    if zones is None:
        zones = find_zones(renko, cfg_zones)
    if len(zones) == 0:
        return pd.DataFrame()

    rev_n = int(cfg_zones.get("reversal_confirm_bricks", 2))
    brk_n = int(cfg_zones.get("breakout_confirm_bricks", 2))

    d = b["dir"].to_numpy(dtype=int)
    op = b["open"].to_numpy(dtype=float)
    cl = b["close"].to_numpy(dtype=float)
    bidx = b["brick_idx"].to_numpy(dtype=int)
    cdt = b["close_dt"].to_numpy()
    nb = len(b)

    def _consec_same_dir_from(start: int, want_dir: int, k: int) -> bool:
        """从 start 开始连续 k 块方向==want_dir"""
        if start + k > nb:
            return False
        return bool(np.all(d[start:start + k] == want_dir))

    sig_rows = []
    for zi, z in zones.iterrows():
        plo, phi = float(z["price_lo"]), float(z["price_hi"])
        zid = int(z["zone_id"])
        for i in range(nb):
            o_i = op[i]
            c_i = cl[i]
            touched_hi = (o_i >= plo) and (o_i <= phi)   # 砖的 open 落在带内
            crossed_up = (o_i <= phi) and (c_i > phi)    # 砖从带内/下穿越上沿向上
            crossed_dn = (o_i >= plo) and (c_i < plo)    # 砖从带内/上穿越下沿向下

            # 遇阻回落(空)：触及阻力带，紧接 rev_n 块下跌
            if touched_hi and i + 1 + rev_n <= nb and _consec_same_dir_from(i + 1, -1, rev_n):
                conf = i + rev_n
                sig_rows.append(_mk_zone_sig(
                    "zone_reversal_short", zid, phi, plo, conf, -1,
                    bidx, cl, cdt, z))
            # 遇阻回升(多)：触及支撑带，紧接 rev_n 块上涨
            if touched_hi and i + 1 + rev_n <= nb and _consec_same_dir_from(i + 1, 1, rev_n):
                conf = i + rev_n
                sig_rows.append(_mk_zone_sig(
                    "zone_reversal_long", zid, plo, phi, conf, 1,
                    bidx, cl, cdt, z))
            # 突破阻力(多)：穿越上沿，紧接 brk_n 块上涨
            if crossed_up and i + 1 + brk_n <= nb and _consec_same_dir_from(i + 1, 1, brk_n):
                conf = i + brk_n
                sig_rows.append(_mk_zone_sig(
                    "zone_breakout_long", zid, phi, plo, conf, 1,
                    bidx, cl, cdt, z))
            # 跌破支撑(空)：穿越下沿，紧接 brk_n 块下跌
            if crossed_dn and i + 1 + brk_n <= nb and _consec_same_dir_from(i + 1, -1, brk_n):
                conf = i + brk_n
                sig_rows.append(_mk_zone_sig(
                    "zone_breakout_short", zid, plo, phi, conf, -1,
                    bidx, cl, cdt, z))

    if not sig_rows:
        return pd.DataFrame()
    out = pd.DataFrame(sig_rows).drop_duplicates(
        subset=["type", "zone_id", "entry_brick_idx"])
    out = out.sort_values("entry_brick_idx").reset_index(drop=True)
    return out


def _mk_zone_sig(stype: str, zid: int, ref_price: float, other: float,
                 conf_brick_pos: int, direction: int,
                 bidx: np.ndarray, cl: np.ndarray, cdt: np.ndarray,
                 zone_row: pd.Series) -> dict:
    """构造一条密集成交区信号记录"""
    return {
        "type": stype,
        "zone_id": zid,
        "trigger_price": ref_price,
        "zone_other_price": other,
        "entry_brick_idx": int(bidx[conf_brick_pos]),
        "entry_time": cdt[conf_brick_pos],
        "entry_price": float(cl[conf_brick_pos]),
        "direction": direction,
        "zone_n_touch": int(zone_row["n_touch"]),
        "zone_first_dt": zone_row["first_dt"],
        "zone_last_dt": zone_row["last_dt"],
    }


# =====================================================================
# 4) 趋势回调顺势
# =====================================================================
def trend_signals(renko: Renko, cfg_trend: dict, runs: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    """
    趋势 → 回调(<=1/2) → 恢复同向 → 顺势信号。

    在 run 段序列上识别三元组 (trend_run, pullback_run, resume_run)：
      - trend_run.n_bricks >= trend_run_min  (大段确认趋势)
      - pullback_run.dir 与 trend_run.dir 相反
      - pullback_run.extent 的绝对值 <= trend_run.extent 的绝对值 * pullback_max_ratio
      - pullback_run.n_bricks >= pullback_min_bricks
      - resume_run.dir == trend_run.dir, n_bricks >= resume_confirm_bricks
        (resume 的首块即是确认砖；resume_confirm_bricks 颗粒=2 对应"出现 2 块同向砖")

    信号落在 resume_run 起点处（确认砖），entry 取该处砖收盘价/时间。
    """
    from renko import brick_runs
    if runs is None:
        runs = brick_runs(renko)
    if len(runs) < 3:
        return pd.DataFrame()

    rmin = int(cfg_trend.get("trend_run_min", 6))
    pmax = float(cfg_trend.get("pullback_max_ratio", 0.5))
    pmin = int(cfg_trend.get("pullback_min_bricks", 1))
    rconf = int(cfg_trend.get("resume_confirm_bricks", 2))

    b = renko.bricks
    bidx_arr = b["brick_idx"].to_numpy()
    cl_arr = b["close"].to_numpy()
    cdt_arr = b["close_dt"].to_numpy()
    dirs_arr = b["dir"].to_numpy()

    rows = []
    for k in range(1, len(runs) - 1):
        tr = runs.iloc[k - 1]
        pb = runs.iloc[k]
        rs = runs.iloc[k + 1]
        if tr["n_bricks"] < rmin:
            continue
        if pb["dir"] == tr["dir"]:
            continue  # 回调段必须反向
        trend_extent_abs = abs(tr["extent"])
        if trend_extent_abs <= 0:
            continue
        pullback_abs = abs(pb["extent"])
        if pullback_abs > trend_extent_abs * pmax:
            continue  # 回调过大，破坏趋势结构
        if pb["n_bricks"] < pmin:
            continue
        if rs["dir"] != tr["dir"]:
            continue  # 恢复段必须与趋势同向
        if rs["n_bricks"] < rconf:
            continue

        # 确认砖 = resume 段第 rconf-1 块（第 rconf 块收尾）
        resume_start = rs["start_idx"]
        # 在 bidx_arr 中定位 resume_start 的位置
        pos = int(np.searchsorted(bidx_arr, resume_start))
        if pos + rconf - 1 >= len(b):
            continue
        conf_pos = pos + rconf - 1
        direction = int(tr["dir"])
        rows.append({
            "type": "trend_pullback_long" if direction == 1 else "trend_pullback_short",
            "direction": direction,
            "trend_bricks": int(tr["n_bricks"]),
            "trend_extent": float(tr["extent"]),
            "pullback_bricks": int(pb["n_bricks"]),
            "pullback_extent": float(pb["extent"]),
            "pullback_ratio": float(pullback_abs / trend_extent_abs),
            "resume_bricks": int(rs["n_bricks"]),
            "entry_brick_idx": int(bidx_arr[conf_pos]),
            "entry_time": cdt_arr[conf_pos],
            "entry_price": float(cl_arr[conf_pos]),
        })

    if not rows:
        return pd.DataFrame()
    out = pd.DataFrame(rows)
    # 同一砖多信号去重：同方向同 type 仅保留最强（pullback_ratio 最小=回调最浅）
    out = (out.sort_values(["entry_brick_idx", "pullback_ratio"], ascending=[True, True])
              .drop_duplicates(subset=["type", "entry_brick_idx"])
              .reset_index(drop=True))
    return out


# =====================================================================
# 5) RSI 超买超卖风险
# =====================================================================
def rsi_extreme_signals(renko: Renko, cfg_rsi: dict, rsi: Optional[pd.Series] = None) -> pd.DataFrame:
    """
    识别 RSI 严重超买/超卖风险点（顶部/底部反转预警）。

    严重超买：RSI >= extreme_overbought
    严重超卖：RSI <= extreme_oversold

    注意：这是"风险提示"而非直接交易信号；通常与 zone_reversal 同向共振时更强。
    """
    if rsi is None:
        rsi = renko_rsi(renko, period=int(cfg_rsi.get("period", 14)))
    if len(rsi) == 0:
        return pd.DataFrame()

    b = renko.bricks.reset_index(drop=True)
    if len(b) != len(rsi):
        # rsi 第一项为 NaN(diff)，对齐
        rsi = rsi.reset_index(drop=True)

    eb = float(cfg_rsi.get("extreme_overbought", 80))
    es = float(cfg_rsi.get("extreme_oversold", 20))

    rows = []
    rsi_arr = rsi.to_numpy(dtype=float)
    bidx = b["brick_idx"].to_numpy()
    cl = b["close"].to_numpy()
    cdt = b["close_dt"].to_numpy()
    for i, v in enumerate(rsi_arr):
        if np.isnan(v):
            continue
        if v >= eb:
            rows.append({
                "type": "rsi_overbought_risk",
                "direction": -1,  # 风险方向=潜在反转下行
                "rsi": float(v),
                "entry_brick_idx": int(bidx[i]),
                "entry_time": cdt[i],
                "entry_price": float(cl[i]),
            })
        elif v <= es:
            rows.append({
                "type": "rsi_oversold_risk",
                "direction": 1,
                "rsi": float(v),
                "entry_brick_idx": int(bidx[i]),
                "entry_time": cdt[i],
                "entry_price": float(cl[i]),
            })
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows)


# =====================================================================
# 6) M头 / W底（双顶/双底）颈线突破
# =====================================================================
def m_w_signals(renko: Renko, cfg_patterns: dict, runs: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    """
    识别 M头(双顶) / W底(双底),颈线突破后发信号。

    在 Renko "运行段(run)" 上做:run 的端点天然就是局部峰/谷
    (一段上涨砖的末尾=局部高点, 一段下跌砖的末尾=局部低点)。
    所以直接用 run 序列的拐点来构造顶/底,无需额外找极值。

    ── M头(看空) ─────────────────────────────
        run k-1 段向上结束 → 峰1 P1
        run k   段向下结束 → 谷(颈线) N   ← 颈线 = 两个峰之间的最低谷
        run k+1 段向上结束 → 峰2 P2
        要求: P1≈P2(双顶), 两峰间隔在 [neck_min, neck_max] 砖内,
              峰所在 run >= min_peak_run 砖
        突破: 颈线 N 之下出现 breakout_confirm 块连续下跌砖 → 做空信号

    ── W底(看多) ─────────────────────────────
        镜像:谷1≈谷2, 颈线=两谷之间最高峰, 突破颈线之上连续上涨 → 做多信号

    信号落在"突破确认砖"处(连续 N 块同向砖的末块),无未来函数。

    Returns: 每行一个 M/W 信号,列含 type/direction/neck_price/p1/p2/
             entry_brick_idx/entry_time/entry_price 等
    """
    from renko import brick_runs
    if runs is None:
        runs = brick_runs(renko)
    if len(runs) < 3:
        return pd.DataFrame()

    ptol = float(cfg_patterns.get("peak_tolerance", 0.010))
    vtol = float(cfg_patterns.get("valley_tolerance", 0.010))
    gmax = int(cfg_patterns.get("neck_max_gap_bricks", 60))
    gmin = int(cfg_patterns.get("neck_min_gap_bricks", 4))
    minrun = int(cfg_patterns.get("min_peak_run", 3))
    bconf = int(cfg_patterns.get("breakout_confirm_bricks", 2))
    lookback = int(cfg_patterns.get("lookback_bricks", 400))

    b = renko.bricks
    bidx_arr = b["brick_idx"].to_numpy()
    cl_arr = b["close"].to_numpy()
    cdt_arr = b["close_dt"].to_numpy()
    dirs_arr = b["dir"].to_numpy()
    nb = len(b)

    # 仅在最近 lookback 砖内寻找形态:取末 brick_idx >= last - lookback 的 run
    last_bidx = int(bidx_arr[-1]) if nb else 0
    r = runs.copy()
    r = r[r["start_idx"] >= last_bidx - lookback].reset_index(drop=True)
    if len(r) < 3:
        return pd.DataFrame()

    rows = []
    # 滑动三元组 (k-1, k, k+1) = (第一段, 颈线段, 第二段)
    for k in range(1, len(r) - 1):
        seg_a = r.iloc[k - 1]   # 第一段(到峰1/谷1)
        seg_neck = r.iloc[k]    # 颈线段
        seg_b = r.iloc[k + 1]   # 第二段(到峰2/谷2)

        gap_bricks = int(seg_b["end_idx"]) - int(seg_a["end_idx"])
        if not (gmin <= gap_bricks <= gmax):
            continue
        if int(seg_a["n_bricks"]) < minrun or int(seg_b["n_bricks"]) < minrun:
            continue

        # ── M头: a 上行→峰1, neck 下行→谷, b 上行→峰2 ──
        if int(seg_a["dir"]) == 1 and int(seg_neck["dir"]) == -1 and int(seg_b["dir"]) == 1:
            p1 = float(seg_a["end_price"])   # 峰1(上行段收价=段最高)
            p2 = float(seg_b["end_price"])   # 峰2
            neck = float(seg_neck["end_price"])  # 颈线(下行段收价=段最低)
            avg = (p1 + p2) / 2.0
            if avg <= 0 or abs(p1 - p2) / avg > ptol:
                continue  # 两顶不平齐,不算双顶
            if min(p1, p2) <= neck:
                continue  # 颈线必须低于两顶
            sig = _pattern_breakout(
                "m_head_short", direction=-1, neck_price=neck,
                p1=p1, p2=p2, seg_end_idx=int(seg_b["end_idx"]),
                bidx_arr=bidx_arr, dirs_arr=dirs_arr, cl_arr=cl_arr,
                cdt_arr=cdt_arr, nb=nb, bconf=bconf)
            if sig:
                sig["gap_bricks"] = gap_bricks
                rows.append(sig)

        # ── W底: a 下行→谷1, neck 上行→峰, b 下行→谷2 ──
        if int(seg_a["dir"]) == -1 and int(seg_neck["dir"]) == 1 and int(seg_b["dir"]) == -1:
            g1 = float(seg_a["end_price"])   # 谷1
            g2 = float(seg_b["end_price"])   # 谷2
            neck = float(seg_neck["end_price"])  # 颈线(上行段收价=段最高)
            avg = (g1 + g2) / 2.0
            if avg <= 0 or abs(g1 - g2) / avg > vtol:
                continue
            if max(g1, g2) >= neck:
                continue  # 颈线必须高于两谷
            sig = _pattern_breakout(
                "w_bottom_long", direction=1, neck_price=neck,
                p1=g1, p2=g2, seg_end_idx=int(seg_b["end_idx"]),
                bidx_arr=bidx_arr, dirs_arr=dirs_arr, cl_arr=cl_arr,
                cdt_arr=cdt_arr, nb=nb, bconf=bconf)
            if sig:
                sig["gap_bricks"] = gap_bricks
                rows.append(sig)

    if not rows:
        return pd.DataFrame()
    out = pd.DataFrame(rows)
    # 同一颈线 + 同方向去重(可能被多个 run 三元组命中),保留突破最近的一次
    out = (out.sort_values(["entry_brick_idx", "neck_price"])
              .drop_duplicates(subset=["type", "neck_price", "direction"],
                               keep="last")
              .reset_index(drop=True))
    return out


def _pattern_breakout(stype: str, direction: int, neck_price: float,
                      p1: float, p2: float, seg_end_idx: int,
                      bidx_arr: np.ndarray, dirs_arr: np.ndarray,
                      cl_arr: np.ndarray, cdt_arr: np.ndarray,
                      nb: int, bconf: int) -> dict:
    """
    在第二段(seg_b)结束之后,寻找颈线突破的连续同向砖确认。
    M头(空): neck 之下连续 bconf 块下跌砖
    W底(多): neck 之上连续 bconf 块上涨砖

    返回信号 dict(未找到确认则返回 None)。
    """
    # 定位 seg_end_idx(峰2/谷2) 在 bidx_arr 中的位置
    pos = int(np.searchsorted(bidx_arr, seg_end_idx))
    if pos >= nb:
        return None
    # 从 pos+1 开始往后找"第一块穿越颈线"的砖
    start = pos + 1
    for i in range(start, nb):
        crossed = False
        if direction == -1 and cl_arr[i] < neck_price:
            crossed = True
        elif direction == 1 and cl_arr[i] > neck_price:
            crossed = True
        if not crossed:
            continue
        # 从这块起连续 bconf 块同向?
        if i + bconf > nb:
            return None
        seg_dirs = dirs_arr[i:i + bconf]
        want = 1 if direction == 1 else -1
        if not np.all(seg_dirs == want):
            continue
        conf_pos = i + bconf - 1
        return {
            "type": stype,
            "direction": direction,
            "neck_price": float(neck_price),
            "p1": float(p1),
            "p2": float(p2),
            "entry_brick_idx": int(bidx_arr[conf_pos]),
            "entry_time": cdt_arr[conf_pos],
            "entry_price": float(cl_arr[conf_pos]),
            "breakout_brick_idx": int(bidx_arr[i]),
        }
    return None


# =====================================================================
# 统一装配：把三类信号合并为一张表
# =====================================================================
def find_all_signals(renko: Renko, cfg: dict) -> dict:
    """
    汇总三类信号 + 密集成交区 + RSI 序列，返回结构化字典。

    Returns
    -------
    {
      'zones'    : pd.DataFrame   密集成交区
      'zone_sig' : pd.DataFrame   密集区反转/突破信号
      'trend_sig': pd.DataFrame   趋势回调顺势信号
      'rsi_risk' : pd.DataFrame   RSI 超买超卖风险
      'rsi'      : pd.Series      Renko-RSI 全序列（绘图用）
      'all'      : pd.DataFrame   以上三类信号合并（标准化列）
    }
    """
    zones = find_zones(renko, cfg["zones"])
    zone_sig = zone_signals(renko, cfg["zones"], zones=zones)
    from renko import brick_runs
    runs = brick_runs(renko)
    trend_sig = trend_signals(renko, cfg["trend"], runs=runs)
    rsi = renko_rsi(renko, period=int(cfg["renko_rsi"]["period"]))
    rsi_risk = rsi_extreme_signals(renko, cfg["renko_rsi"], rsi=rsi)

    # 标准化合并：统一列 type/direction/entry_brick_idx/entry_time/entry_price
    keep = ["type", "direction", "entry_brick_idx", "entry_time", "entry_price"]
    parts = []
    for df in (zone_sig, trend_sig, rsi_risk):
        if len(df):
            parts.append(df[[c for c in keep if c in df.columns]].copy())
    if parts:
        merged = pd.concat(parts, ignore_index=True)
        merged = merged.sort_values(
            ["entry_brick_idx", "type"]).reset_index(drop=True)
    else:
        merged = pd.DataFrame(columns=keep)

    return {
        "zones": zones,
        "zone_sig": zone_sig,
        "trend_sig": trend_sig,
        "rsi_risk": rsi_risk,
        "rsi": rsi,
        "all": merged,
        "runs": runs,
    }


if __name__ == "__main__":
    from data_loader import load_config, load_klines
    from renko import compute_brick_size, build_renko

    cfg = load_config()
    df = load_klines("AU0", cfg)
    bs = compute_brick_size(df, cfg["brick"], symbol="AU0")
    rk = build_renko(df, bs)
    print(f"AU0 bricks={len(rk.bricks)} bs={bs}")
    res = find_all_signals(rk, cfg)
    print("zones:", len(res["zones"]),
          "zone_sig:", len(res["zone_sig"]),
          "trend_sig:", len(res["trend_sig"]),
          "rsi_risk:", len(res["rsi_risk"]))
    if len(res["trend_sig"]):
        print("\n-- 最近趋势回调信号 --")
        print(res["trend_sig"].tail(4).to_string(index=False))
    if len(res["zone_sig"]):
        print("\n-- 最近密集区信号 --")
        print(res["zone_sig"].tail(4).to_string(index=False))
    if len(res["rsi_risk"]):
        print("\n-- 最近 RSI 风险点 --")
        print(res["rsi_risk"].tail(4).to_string(index=False))
