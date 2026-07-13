#!/usr/bin/env python3
"""
综合交易监控卡片生成器 (future_2~6)
====================================
扫描威科夫/缠绕突破/Renko/Renko Chart 系统最新输出，
综合分析 TOP4 品种，输出 JSON 到 d:/work_ai/singal_h/

入口: 直接运行，non_agent=True 模式
"""
import os
import sys
import json
import time
import subprocess
import traceback
from datetime import datetime
from pathlib import Path

# 基础路径配置
BASE = Path("D:/work_ai")
OUT_DIR = BASE / "singal_h"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# ── 系统配置: 小时线强弱分析 (future_2/4/6) ──────────
SYSTEMS = {
    "wyckoff": {                          # future_2: 威科夫 Spring/Upthrust
        "dir": BASE / "future_2",
        "script": "tqsdk_wyckoff_scan.py --period 60",  # 1小时周期
        "out": BASE / "future_2" / "wyckoff_signals_*.csv",
        "timeout": 180,
    },
    "dual_ma": {                          # future_4: 双均线缠绕突破
        "dir": BASE / "future_4",
        "script": "run_scan.py --no-plot",
        "out": BASE / "future_4" / "output" / "alerts_latest.csv",
        "timeout": 180,
    },
    "renko_chart": {                      # future_6: Renko Chart
        "dir": BASE / "future_6",
        "script": "scanner.py --no-plot",
        "out_signals": BASE / "future_6" / "output",
        "timeout": 300,  # future_6 较慢，给更长时间
    },
}

# 执行一个系统的扫描，超时返回 False
def run_system(name, cfg, timeout=180):
    """运行单个系统扫描脚本，返回是否成功"""
    script = cfg["script"].split()[0]
    args = cfg["script"].split()[1:] if " " in cfg["script"] else []
    workdir = str(cfg["dir"])
    cmd = [sys.executable, script] + args
    
    print(f"[{name}] 启动扫描: {workdir}")
    try:
        r = subprocess.run(
            cmd, cwd=workdir, capture_output=True, text=True, timeout=timeout
        )
        print(f"[{name}] 完成: returncode={r.returncode}, stdout[-200:]={r.stdout[-200:]}")
        if r.returncode != 0:
            print(f"[{name}] STDERR: {r.stderr[-300:]}")
        return r.returncode == 0
    except subprocess.TimeoutExpired:
        print(f"[ERROR] {name} 超时 ({timeout}s)")
        return False
    except Exception as e:
        print(f"[ERROR] {name} 异常: {e}")
        return False

# ── 解析 future_2 威科夫信号 (wyckoff_signals_*.csv) ─
def parse_wyckoff_csv(csv_path):
    """从威科夫最新 CSV 解析 (symbol,name,exchange,side,entry,stop,target,rsi,phase,reason)"""
    import pandas as pd, glob
    res = []
    
    if isinstance(csv_path, Path) and "*" in str(csv_path):
        files = sorted(glob.glob(str(csv_path)))
    elif Path(str(csv_path)).exists():
        files = [str(csv_path)]
    else:
        files = []
    
    if not files:
        return res
    
    df = pd.read_csv(files[-1])
    for _, r in df.iterrows():
        side = str(r.get("side", "long"))
        res.append({
            "symbol": r.get("symbol", ""),
            "name": r.get("name", ""),
            "direction": "做多" if side == "long" else "做空",
            "entry_price": float(r.get("entry", 0)),
            "stop_loss": float(r.get("stop", 0)),
            "target_price": float(r.get("target", 0)),
            "rsi": float(r.get("rsi", 0)) if pd.notna(r.get("rsi", None)) else None,
            "phase": r.get("phase", ""),
            "reason": r.get("reason", ""),
            "trigger": f"{r.get('phase','')} — {str(r.get('reason',''))[:60]}",
        })
    return res

# ── 解析 future_4 预警信号 ────────────────────────
def parse_alert_row(row):
    """从 future_4 alerts_latest.csv 解析"""
    return {
        "symbol": row.get("symbol", ""),
        "name": row.get("name", ""),
        "direction": "做多" if int(row.get("direction", 0)) == 1 else "做空",
        "entry_price": float(row.get("entry_price", 0)),
        "last_close": float(row.get("last_close", 0)),
        "bars_since": int(row.get("bars_since", 0)),
        "dist_fast": float(row.get("dist_fast", 0)),
        "dist_slow": float(row.get("dist_slow", 0)),
        "twist_cross": int(row.get("twist_cross", 0)),
    }

# ── 解析 future_6 Renko Chart 信号 ────────────────
def parse_renko_chart(output_dir):
    """从 future_6 最新输出解析"""
    import pandas as pd, glob
    res = {"signals": [], "summary": {}}
    
    sig_files = sorted(glob.glob(str(output_dir / "renko_signals_*.csv")))
    if sig_files:
        df = pd.read_csv(sig_files[-1])
        if len(df):
            for sym, g in df.groupby("symbol"):
                row = g.iloc[-1]
                res["signals"].append({
                    "symbol": sym,
                    "name": row.get("name", ""),
                    "signal_type": row.get("signal_type", ""),
                    "direction": "做多" if "long" in str(row.get("signal_type", ""))
                                         or row.get("direction", 0) == 1 else "做空",
                    "strength": float(row.get("strength_score", 0)),
                    "rsi": float(row.get("rsi_brick", 0)),
                    "entry_price": float(row.get("close", 0)),
                    "nearest_support": row.get("nearest_support"),
                    "nearest_resistance": row.get("nearest_resistance"),
                    "detail": row.get("detail", ""),
                })
    
    summ_files = sorted(glob.glob(str(output_dir / "renko_summary_*.csv")))
    if summ_files:
        df = pd.read_csv(summ_files[-1])
        if len(df):
            for _, r in df.iterrows():
                res["summary"][r["symbol"]] = {
                    "last_close": float(r["last_close"]),
                    "bricks": int(r["bricks"]),
                }
    
    return res

# ── 评分函数 ────────────────────────────────────────
MAX_BARS_SINCE = 20  # 信号超过20根K线则过滤

def score_wyckoff(rsi, direction):
    """威科夫信号评分: RSI极端位置40 + 结构完整性30 + 逻辑清晰度30"""
    rsi_score = 0
    if rsi is not None:
        # RSI 极端位置=高分 (做多看低位, 做空看高位)
        if direction == "做多" and rsi < 35:
            rsi_score = 40 - (rsi / 35) * 10  # RSI越低分越高
        elif direction == "做空" and rsi > 65:
            rsi_score = 40 - ((100 - rsi) / 35) * 10
        else:
            rsi_score = max(0, 30 - abs(rsi - 50) / 5)
    else:
        rsi_score = 20
    struct_score = 30  # Wyckoff 完整结构自带可信度
    return rsi_score + struct_score + 30

def score_dual_ma(bars_since, twist_cross, dist_fast, dist_slow):
    """future_4 缠绕突破评分: EA距离20 + 时效性30 + 缠绕密度25 + 方向一致性25"""
    dist = abs(dist_fast) + abs(dist_slow)
    dist_score = min(dist * 1000, 20)  # 距离越大突破越有力度
    time_score = max(0, 30 - bars_since * 1.5)
    twist_score = min(twist_cross * 5, 25)
    return dist_score + time_score + twist_score + 25

def score_renko_chart(signal_type, strength, bars_since=0):
    """Renko Chart 评分: strength_score 0.5 + 信号类型加权"""
    base = strength * 50  # 0-50
    type_bonus = {
        "w_bottom_breakout": 20,
        "m_top_breakout": 20,
        "trend_pullback_long": 15,
        "trend_pullback_short": 15,
        "zone_breakout_up": 12,
        "zone_breakout_down": 12,
        "zone_reversal": 10,
    }.get(signal_type, 5)
    fresh = max(0, 10 - bars_since / 5)
    return base + type_bonus + fresh

# ── TOP4 选择 ────────────────────────────────────────
def select_top4(wyckoff_data, renko6_data, alert4_data):
    """从所有信号中综合评分选出 TOP4 (过滤 bars_since>20 的过时信号)"""
    
    # 时效性过滤: 超过20根K线的信号剔除 (future_4)
    alert4_list = [a for a in alert4_data if a["bars_since"] <= MAX_BARS_SINCE]
    
    candidates = []
    
    # 1. future_6 Renko Chart
    for sig in renko6_data.get("signals", []):
        score = score_renko_chart(sig["signal_type"], sig["strength"])
        candidates.append({
            "symbol": sig["symbol"],
            "name": sig["name"],
            "direction": sig["direction"],
            "score": score,
            "source": "future_6_renko_chart",
            "entry_price": sig["entry_price"],
            "last_close": sig["entry_price"],
            "bars_since": 0,
            "hist_pf": 0,
            "hist_winrate": 0,
            "trigger": sig.get("detail", sig["signal_type"]),
            "nearest_support": sig.get("nearest_support"),
            "nearest_resistance": sig.get("nearest_resistance"),
            "rsi": sig.get("rsi"),
        })
    
    # 2. future_4 缠绕突破 (过滤 bars_since)
    alert_map = {}
    for a in alert4_list:
        alert_map[a["symbol"]] = a
    
    for sym, a in alert_map.items():
        score = score_dual_ma(a["bars_since"], a["twist_cross"],
                              a["dist_fast"], a["dist_slow"])
        candidates.append({
            "symbol": a["symbol"],
            "name": a["name"],
            "direction": a["direction"],
            "score": score,
            "source": "future_4_dual_ma",
            "entry_price": a["entry_price"],
            "last_close": a["last_close"],
            "bars_since": a["bars_since"],
            "hist_pf": 0,
            "hist_winrate": 0,
            "trigger": f"缠绕突破 {a['twist_cross']}次",
        })
    
    # 3. future_2 威科夫 (如有时)
    for w in wyckoff_data:
        score = score_wyckoff(w.get("rsi"), w["direction"])
        candidates.append({
            "symbol": w["symbol"],
            "name": w["name"],
            "direction": w["direction"],
            "score": score,
            "source": "future_2_wyckoff",
            "entry_price": w["entry_price"],
            "last_close": w["entry_price"],
            "bars_since": 0,
            "hist_pf": 0,
            "hist_winrate": 0,
            "trigger": w["trigger"],
            "rsi": w.get("rsi"),
            "stop_loss": w.get("stop_loss"),
            "target_price": w.get("target_price"),
        })
    
    # 4. 去重: 同品种同方向取最高分
    best = {}
    for c in candidates:
        key = f"{c['symbol']}_{c['direction']}"
        if key not in best or c["score"] > best[key]["score"]:
            best[key] = c
    
    # 5. 排序取 TOP4 (做多做空各半)
    longs = sorted([v for v in best.values() if v["direction"] == "做多"],
                   key=lambda x: x["score"], reverse=True)
    shorts = sorted([v for v in best.values() if v["direction"] == "做空"],
                    key=lambda x: x["score"], reverse=True)
    
    top4 = []
    for i in range(2):
        if i < len(longs):
            top4.append(longs[i])
        if i < len(shorts):
            top4.append(shorts[i])
    
    # 补充到4个
    if len(top4) < 4:
        remaining = [v for v in best.values()
                     if v["symbol"] not in [t["symbol"] for t in top4]]
        remaining.sort(key=lambda x: x["score"], reverse=True)
        top4.extend(remaining[:4 - len(top4)])
    
    return top4[:4]

# ── 构建监控卡片 ────────────────────────────────────
def build_card(top4, renko6_data):
    """构建 JSON 监控卡片"""
    cards = []
    
    for rank, item in enumerate(top4, 1):
        sym = item["symbol"]
        
        # 找支撑/阻力 (优先从 renko6 信号)
        support, resistance = None, None
        for sig in renko6_data.get("signals", []):
            if sig["symbol"] == sym:
                s = sig.get("nearest_support")
                r = sig.get("nearest_resistance")
                if s is not None:
                    try:
                        support = f"{float(s):.1f}"
                    except (ValueError, TypeError):
                        support = str(s)
                if r is not None:
                    try:
                        resistance = f"{float(r):.1f}"
                    except (ValueError, TypeError):
                        resistance = str(r)
                break
        
        # RSI
        rsi = item.get("rsi")
        
        # 风险点
        risks = []
        if rsi is not None:
            if rsi > 70:
                risks.append(f"RSI={rsi:.1f} 超买区，回调风险")
            elif rsi < 30:
                risks.append(f"RSI={rsi:.1f} 超卖区，反弹风险")
        if item.get("bars_since", 0) > 50:
            risks.append(f"信号已{item['bars_since']}根K线，时效性下降")
        if not risks:
            risks.append("暂无显著风险")
        
        card = {
            "rank": rank,
            "symbol": sym,
            "name": item["name"],
            "direction": item["direction"],
            "score": round(item["score"], 1),
            "current_price": item["last_close"],
            "entry_price": item["entry_price"],
            "support": support or "未识别",
            "resistance": resistance or "未识别",
            "trigger": item["trigger"],
            "rsi": round(rsi, 1) if rsi else None,
            "risk": risks,
            "source_system": item["source"],
            "hist_pf": item.get("hist_pf"),
            "hist_winrate": item.get("hist_winrate"),
        }
        cards.append(card)
    
    return cards

# ── 主流程 ────────────────────────────────────────────
def main():
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    print(f"=== 综合交易监控卡片生成 {ts} ===")
    
    # Step 1: 运行各系统扫描 (串行执行)
    results = {}
    for name, cfg in SYSTEMS.items():
        timeout = cfg.get("timeout", 180)
        ok = run_system(name, cfg, timeout=timeout)
        results[name] = ok
        print(f"  {name}: {'OK' if ok else 'FAIL'}")
    
    # Step 2: 解析各系统输出
    
    # future_2 (wyckoff)
    wyckoff_data = []
    wyckoff_csv = SYSTEMS["wyckoff"]["out"]
    wyckoff_data = parse_wyckoff_csv(wyckoff_csv)
    print(f"  future_2: {len(wyckoff_data)} 条威科夫信号")
    
    # future_4
    alert4_list = []
    f4_csv = SYSTEMS["dual_ma"]["out"]
    if f4_csv.exists():
        import pandas as pd
        df = pd.read_csv(f4_csv)
        for _, row in df.iterrows():
            alert4_list.append(parse_alert_row(row))
    print(f"  future_4: {len(alert4_list)} 条预警")
    
    # future_6
    renko6_data = parse_renko_chart(SYSTEMS["renko_chart"]["out_signals"])
    print(f"  future_6: {len(renko6_data['signals'])} 信号")
    
    # Step 3: 选 TOP4
    top4 = select_top4(wyckoff_data, renko6_data, alert4_list)
    print(f"\nTOP4 选中: {[t['symbol']+' '+t['direction'] for t in top4]}")
    
    # Step 4: 构建卡片
    cards = build_card(top4, renko6_data)
    
    # Step 5: 输出 JSON
    output = {
        "timestamp": datetime.now().isoformat(),
        "scan_results": {k: "OK" if v else "FAIL" for k, v in results.items()},
        "cards": cards,
    }
    
    out_file = OUT_DIR / f"cards_{ts}.json"
    latest_file = OUT_DIR / "cards_latest.json"
    
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    
    with open(latest_file, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    
    print(f"\n输出: {out_file}")
    print(f"最新: {latest_file}")
    
    # 打印摘要
    print("\n" + "="*60)
    for c in cards:
        print(f"[{c['rank']}] {c['symbol']} {c['name']} | {c['direction']} | "
              f"当前价={c['current_price']} | 评分={c['score']} | RSI={c['rsi']}")
        print(f"    触发: {c['trigger']}")
        print(f"    支撑: {c['support']} | 阻力: {c['resistance']}")
        print(f"    风险: {'; '.join(c['risk'])}")
    print("="*60)
    
    return 0

if __name__ == "__main__":
    sys.exit(main())
