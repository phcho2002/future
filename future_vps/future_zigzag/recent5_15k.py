from __future__ import annotations
import argparse
import csv, json, sys
from pathlib import Path

import pandas as pd

ROOT = Path(r"D:/work_ai")
ZIG = ROOT / "future_zigzag"
sys.path.insert(0, str(ROOT))

from future_data import get_klines, get_backend
from future_zigzag.config import ZigZagConfig, ThreePushConfig, ContractionConfig, SignalConfig
from future_zigzag.zigzag import detect_zigzag
from future_zigzag.three_push import detect_three_push
from future_zigzag.contraction import evaluate_contraction
from future_zigzag.signals import detect_all_signals, dedupe_signals

with open(ROOT / "futures_top40.json", encoding="utf-8") as f:
    symbols = [(x[0], x[1], x[2]) for x in json.load(f)["symbols"]]

parser = argparse.ArgumentParser()
parser.add_argument("--period", default="15")
parser.add_argument("--length", type=int, default=3000)
parser.add_argument("--recent", type=int, default=5)
parser.add_argument("--output", default="recent5_15k_signals.csv")
args = parser.parse_args()
period = args.period
length = args.length
recent_n = args.recent
zcfg = ZigZagConfig(depth_atr_multiple=1.5)
tcfg = ThreePushConfig(zigzag=zcfg, valid_score_threshold=0.45)
ccfg = ContractionConfig()
scfg = SignalConfig()
rows = []
errors = []

print(f"backend={get_backend()} period={period} recent_bars={recent_n} symbols={len(symbols)}")
for num, (sym, name, exch) in enumerate(symbols, 1):
    try:
        df = get_klines(sym, exch, period=period, length=length, force=True)
        if df is None or df.empty:
            errors.append((sym, "empty")); continue
        df = df.reset_index(drop=True)
        zz = detect_zigzag(df, zcfg)
        patterns = detect_three_push(zz, tcfg)
        valid = [p for p in patterns if p.score.hard_gate_passed and p.score.total >= 0.45]
        found = 0
        for pat in valid:
            if not evaluate_contraction(pat, zz.atr, len(df), ccfg).passed:
                continue
            sigs = detect_all_signals(pat, df, zz.atr, scfg)
            # 保留该 pattern 的全部触发类型；回答最近信号时不能用单一最优信号覆盖较新的触发。
            for sig in sigs:
                if sig.trigger_idx < len(df) - recent_n:
                    continue
                t = df.iloc[sig.trigger_idx]
                rows.append({
                    "symbol": sym, "name": name, "exchange": exch,
                    "signal_type": sig.signal_type, "direction": "做多" if sig.side == "long" else "做空",
                    "trigger_time": str(t["datetime"]), "trigger_idx": int(sig.trigger_idx),
                    "bars_ago": int(len(df) - 1 - sig.trigger_idx),
                    "entry": float(sig.entry), "stop": float(sig.stop),
                    "latest_time": str(df.iloc[-1]["datetime"]), "latest_close": float(df.iloc[-1]["close"]),
                    "pattern_score": round(float(pat.score.total), 3), "detail": sig.detail,
                })
                found += 1
        print(f"[{num:02d}/40] {sym} latest={df.iloc[-1]['datetime']} recent_signals={found}", flush=True)
    except Exception as e:
        errors.append((sym, repr(e)))
        print(f"[{num:02d}/40] {sym} ERROR {e}", flush=True)

out = ZIG / "output" / args.output
out.parent.mkdir(parents=True, exist_ok=True)
fields = ["symbol","name","exchange","signal_type","direction","trigger_time","trigger_idx","bars_ago","entry","stop","latest_time","latest_close","pattern_score","detail"]
with out.open("w", encoding="utf-8-sig", newline="") as f:
    w = csv.DictWriter(f, fieldnames=fields)
    w.writeheader(); w.writerows(rows)

print("\nRESULTS")
print(f"signals={len(rows)} errors={len(errors)} output={out}")
for r in sorted(rows, key=lambda x: (x["bars_ago"], x["symbol"])):
    print(f"{r['symbol']} {r['name']} {r['direction']} {r['signal_type']} trigger={r['trigger_time']} bars_ago={r['bars_ago']} entry={r['entry']} stop={r['stop']} latest={r['latest_close']}")
if errors:
    print("ERRORS", errors)
