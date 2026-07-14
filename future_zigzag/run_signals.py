"""CLI: 三类反转信号检测 + 前瞻验证。

对 9 品种：ZigZag → Three Push(有效) → 收缩 → 三类信号触发 → 前瞻验证。
核心输出：三类信号各自的胜率/MFE/MAE，判断哪类值得用、哪类该砍。

用法:
    python -m future_zigzag.run_signals
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

_SCRIPT_DIR = Path(__file__).resolve().parent
_WORK_AI = _SCRIPT_DIR.parent
sys.path.insert(0, str(_WORK_AI))

from future_data import get_klines  # noqa: E402

from future_zigzag.config import (  # noqa: E402
    SYMBOLS, ZigZagConfig, ThreePushConfig, ContractionConfig, SignalConfig,
)
from future_zigzag.zigzag import detect_zigzag  # noqa: E402
from future_zigzag.three_push import detect_three_push  # noqa: E402
from future_zigzag.contraction import evaluate_contraction  # noqa: E402
from future_zigzag.signals import detect_all_signals, dedupe_signals  # noqa: E402

OUTPUT_DIR = _SCRIPT_DIR / "output"
MIN_BARS = 3000
HORIZONS = (10, 20, 40, 80)


def _atr_at(atr, idx):
    arr = atr.to_numpy(dtype=float)
    if 0 <= idx < len(arr):
        v = arr[idx]
        return float(v) if np.isfinite(v) and v > 0 else float("nan")
    return float("nan")


def signal_lookahead(sig, df, atr, horizons=HORIZONS):
    """从信号触发 K 线起算前瞻 MFE/MAE/ret（ATR 归一化）。
    止损用信号自带 stop；统计是否被止损打掉。"""
    n = len(df)
    i = sig.trigger_idx
    close = df["close"].to_numpy(float)
    high = df["high"].to_numpy(float)
    low = df["low"].to_numpy(float)
    if i >= n - 1:
        return []
    entry = sig.entry
    a = _atr_at(atr, i)
    if not np.isfinite(a) or a <= 0:
        return []
    is_long = sig.side == "long"
    sign = 1.0 if is_long else -1.0
    out = []
    for h in horizons:
        end = min(i + h, n - 1)
        seg_c = close[i + 1:end + 1]
        seg_h = high[i + 1:end + 1]
        seg_l = low[i + 1:end + 1]
        if len(seg_c) == 0:
            continue
        if is_long:
            fav = seg_h - entry; adv = entry - seg_l
        else:
            fav = entry - seg_l; adv = seg_h - entry
        mfe = float(np.max(fav) / a) if len(fav) else float("nan")
        mae = float(np.max(adv) / a) if len(adv) else float("nan")
        ret = float((seg_c[-1] - entry) * sign / a)
        # 是否触及止损（mae 方向达到 stop 距离）
        stop_dist = abs(entry - sig.stop) / a if a > 0 else float("inf")
        stopped = bool(mae >= stop_dist) if np.isfinite(mae) else False
        out.append({"horizon": h, "mfe": mfe, "mae": mae, "ret": ret,
                    "stopped": stopped, "stop_dist_atr": round(stop_dist, 2)})
    return out


def run(depth=1.5, min_score=0.45, period="15", length=MIN_BARS):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    zcfg = ZigZagConfig(depth_atr_multiple=depth)
    tcfg = ThreePushConfig(zigzag=zcfg, valid_score_threshold=min_score)
    ccfg = ContractionConfig()
    scfg = SignalConfig()

    print("=" * 88)
    print(f"  三类反转信号检测 + 前瞻验证  depth={depth}×ATR  period={period}")
    print("=" * 88)

    # 双轨收集：raw（去重前，全部信号）+ deduped（去重后，每 pattern 一个）
    by_type_raw: dict[str, list] = {"wedge_breakout": [], "reversal_bar": [], "second_entry": []}
    by_type_dedup: dict[str, list] = {"wedge_breakout": [], "reversal_bar": [], "second_entry": []}
    all_rows = []
    n_raw = 0
    n_dedup = 0

    for i, (sym, name, exch) in enumerate(SYMBOLS):
        print(f"\n[{i+1}/{len(SYMBOLS)}] {sym} {name} ({exch})...", flush=True)
        try:
            df = get_klines(sym, exch, period=period, length=length, force=True)
            if df is None or df.empty:
                continue
        except Exception as e:
            print(f"  ❌ {e}"); continue
        if len(df) > MIN_BARS:
            df = df.tail(MIN_BARS).reset_index(drop=True)

        zz = detect_zigzag(df, zcfg)
        patterns = detect_three_push(zz, tcfg)
        valid = [p for p in patterns if p.score.hard_gate_passed and p.score.total >= min_score]
        atr = zz.atr

        n_contracted = 0
        for p in valid:
            cr = evaluate_contraction(p, atr, len(df), ccfg)
            if not cr.passed:
                continue
            n_contracted += 1
            sigs = detect_all_signals(p, df, atr, scfg)
            # raw: 全部
            for s in sigs:
                outs = signal_lookahead(s, df, atr)
                by_type_raw[s.signal_type].append(outs)
                n_raw += 1
            # deduped: 每 pattern 取最优一个
            best = dedupe_signals(sigs)
            if best is not None:
                outs = signal_lookahead(best, df, atr)
                by_type_dedup[best.signal_type].append(outs)
                n_dedup += 1
                all_rows.append({
                    "symbol": sym, "type": best.signal_type, "side": best.side,
                    "trigger": best.trigger_idx,
                    "tp_score": round(p.score.total, 2),
                    "entry": round(best.entry, 2), "stop": round(best.stop, 2),
                })
        print(f"  有效={len(valid)} 收缩={n_contracted}")

    # ── 双轨前瞻统计 ──
    def summarize(by_type, label):
        rows = []
        for stype, outs_list in by_type.items():
            by_h = {}
            for outs in outs_list:
                for o in outs:
                    by_h.setdefault(o["horizon"], []).append(o)
            for h in sorted(by_h):
                os_ = by_h[h]
                rets = np.array([o["ret"] for o in os_ if np.isfinite(o["ret"])])
                mfes = np.array([o["mfe"] for o in os_ if np.isfinite(o["mfe"])])
                maes = np.array([o["mae"] for o in os_ if np.isfinite(o["mae"])])
                stopped = np.array([o["stopped"] for o in os_])
                if len(rets) == 0:
                    continue
                rows.append({
                    "label": label, "signal_type": stype, "horizon": h, "count": len(rets),
                    "win_rate": round(float((rets > 0).mean()), 3),
                    "mfe_mean": round(float(mfes.mean()), 3) if len(mfes) else None,
                    "mae_mean": round(float(maes.mean()), 3) if len(maes) else None,
                    "ret_mean": round(float(rets.mean()), 3),
                    "stop_rate": round(float(stopped.mean()), 3),
                    "mfe_mae": round(float(mfes.mean()/maes.mean()), 3) if len(maes) and maes.mean()>0 else None,
                })
        return rows

    raw_rows = summarize(by_type_raw, "raw")
    dedup_rows = summarize(by_type_dedup, "dedup")

    print("\n" + "=" * 92)
    print(f"  信号去重对比  (raw={n_raw} → dedup={n_dedup}, body_atr={scfg.reversal_body_atr})")
    print("=" * 92)

    # 合并总览：raw 全部 vs dedup 全部（不分类型）
    def overall(by_type, label):
        allouts = [o for v in by_type.values() for o in v]
        by_h = {}
        for outs in allouts:
            for o in outs:
                by_h.setdefault(o["horizon"], []).append(o)
        res = {}
        for h in sorted(by_h):
            os_ = by_h[h]
            rets = np.array([o["ret"] for o in os_ if np.isfinite(o["ret"])])
            mfes = np.array([o["mfe"] for o in os_ if np.isfinite(o["mfe"])])
            maes = np.array([o["mae"] for o in os_ if np.isfinite(o["mae"])])
            stopped = np.array([o["stopped"] for o in os_])
            res[h] = {"n": len(rets), "win": float((rets>0).mean()),
                      "mfe": float(mfes.mean()), "mae": float(maes.mean()),
                      "stop": float(stopped.mean()),
                      "mfe_mae": float(mfes.mean()/maes.mean()) if maes.mean()>0 else float("nan")}
        return label, res

    lbl_r, res_r = overall(by_type_raw, "去重前(raw)")
    lbl_d, res_d = overall(by_type_dedup, "去重后(dedup)")
    print(f"\n  {'':12} {'n':>5} {'胜率':>7} {'MFE':>6} {'MAE':>6} {'MFE/MAE':>8} {'止损率':>7}")
    for h in sorted(res_r):
        r, d = res_r[h], res_d[h]
        print(f"  h={h:<3} {lbl_r}")
        print(f"  {'':>6} raw  {r['n']:>5} {r['win']:>6.1%} {r['mfe']:>6.2f} {r['mae']:>6.2f} {r['mfe_mae']:>8.2f} {r['stop']:>6.1%}")
        print(f"  {'':>6} dedup{d['n']:>5} {d['win']:>6.1%} {d['mfe']:>6.2f} {d['mae']:>6.2f} {d['mfe_mae']:>8.2f} {d['stop']:>6.1%}")

    # 去重后分类型（验证 body_atr 放松后 reversal_bar 样本变化）
    print(f"\n  ── 去重后分类型（body_atr={scfg.reversal_body_atr}）h=40 ──")
    for stype in ["wedge_breakout", "reversal_bar", "second_entry"]:
        sub = [r for r in dedup_rows if r["signal_type"] == stype and r["horizon"] == 40]
        if sub:
            r = sub[0]
            print(f"  {stype:<16} n={int(r['count']):<4} 胜率={r['win_rate']:.1%} "
                  f"MFE={r['mfe_mean']:.2f} MAE={r['mae_mean']:.2f} "
                  f"MFE/MAE={r['mfe_mae']:.2f} 止损率={r['stop_rate']:.1%}")

    # 存 CSV
    pd.DataFrame(raw_rows + dedup_rows).to_csv(
        OUTPUT_DIR / "signals_lookahead.csv", index=False, encoding="utf-8-sig")
    if all_rows:
        pd.DataFrame(all_rows).to_csv(OUTPUT_DIR / "signals_detail.csv",
                                      index=False, encoding="utf-8-sig")
    print(f"\n  去重前 {n_raw} → 去重后 {n_dedup} 个信号")

    return 0


if __name__ == "__main__":
    sys.exit(run())
