#!/usr/bin/env python3
"""
综合交易监控卡片 v5
DB + future_2(wyckoff@2H) + future_4(dual_ma@30m) + future_6(renko@60m) + akshare实价
全系统走 akshare 数据源
"""
import os, sys, time, sqlite3, json
from datetime import datetime
from pathlib import Path
from glob import glob

import akshare as ak
import pandas as pd
import numpy as np

BASE = Path("D:/work_ai")
OUT_DIR = BASE / "singal_h"
DB = BASE / "futures_data.db"
F2 = BASE / "future_2"
F4 = BASE / "future_4"
F6 = BASE / "future_6"

sys.path.insert(0, str(F2))
sys.path.insert(0, str(F4))
sys.path.insert(0, str(F6))

SECTORS = {
    'IM0':'股指','IC0':'股指','IF0':'股指','IH0':'股指',
    'TF0':'国债','TS0':'国债','T0':'国债','TL0':'国债',
    'AU0':'贵金属','AG0':'贵金属',
    'CU0':'有色','AL0':'有色','ZN0':'有色','PB0':'有色','NI0':'有色','SN0':'有色',
    'RB0':'黑色','HC0':'黑色','JM0':'黑色','J0':'黑色','I0':'黑色',
    'SC0':'原油','FU0':'原油','LU0':'原油','PG0':'原油','BU0':'原油',
    'BR0':'化工','TA0':'化工','MA0':'化工','RU0':'化工','V0':'化工','PP0':'化工',
    'L0':'化工','EG0':'化工','EB0':'化工','PF0':'化工','SH0':'化工','PX0':'化工',
    'LC0':'新能源','SI0':'新能源','AO0':'新能源',
    'M0':'农产品','Y0':'农产品','OI0':'农产品','P0':'农产品','RM0':'农产品',
    'C0':'农产品','A0':'农产品','CS0':'农产品','B0':'农产品','JD0':'农产品','LH0':'农产品',
    'SR0':'软商品','CF0':'软商品','AP0':'软商品','CJ0':'软商品','CY0':'软商品','PK0':'软商品','SP0':'软商品',
}

# 五维评分 → 0-100
def score_to_pct(db_total):
    return min(db_total / 5 * 20, 100) if db_total else 50

def code_to_sector(code):
    return SECTORS.get(code[:2] + '0', SECTORS.get(code[:3], '其他'))

# ─── 数据库 ───
def load_db_scores():
    conn = sqlite3.connect(DB)
    df = pd.read_sql("SELECT * FROM hourly_analysis_all", conn)
    conn.close()
    return df

# ─── akshare 实价 ───
def load_akshare_prices():
    print("[1] akshare 实时价格 ...")
    marks_df = ak.futures_symbol_mark()
    price_map = {}
    code_lookup = {
        '螺纹钢':'RB0','热轧':'HC0','不锈钢':'SS0','焦煤':'JM0','焦炭':'J0','铁矿石':'I0',
        '黄金':'AU0','白银':'AG0','铜':'CU0','铝':'AL0','锌':'ZN0','铅':'PB0','镍':'NI0','锡':'SN0',
        'PTA':'TA0','甲醇':'MA0','燃油':'FU0','原油':'SC0','低硫燃料油':'LU0','液化石油气':'PG0','石油沥青':'BU0',
        '天然橡胶':'RU0','纸浆':'SP0','短纤':'PF0','烧碱':'SH0',
        '棕榈油':'P0','豆油':'Y0','菜油':'OI0','豆一':'A0','豆二':'B0','豆粕':'M0','菜粕':'RM0',
        '玉米':'C0','淀粉':'CS0','鸡蛋':'JD0','生猪':'LH0','花生':'PK0',
        '棉花':'CF0','白糖':'SR0','苹果':'AP0','红枣':'CJ0','棉纱':'CY0',
        '纯碱':'SA0','尿素':'UR0','玻璃':'FG0','硅铁':'SF0','锰硅':'SM0',
        '乙二醇':'EG0','聚丙烯':'PP0','苯乙烯':'EB0','对二甲苯':'PX0','聚乙烯':'L0','PVC':'V0',
        '沪深300':'IF0','中证500':'IC0','上证50':'IH0','中证1000':'IM0',
        '二年期国债':'TS0','五年期国债':'TF0','十年期国债':'T0',
        '碳酸锂':'LC0','工业硅':'SI0','氧化铝':'AO0','丁二烯橡胶':'BR0',
    }
    for _, row in marks_df.iterrows():
        name = row['symbol']
        try:
            df = ak.futures_zh_realtime(symbol=name)
            if df is not None and len(df) > 0:
                df['volume'] = pd.to_numeric(df['volume'], errors='coerce').fillna(0)
                best = df.loc[df['volume'].idxmax()]
                code = None
                for key, c in code_lookup.items():
                    if key in name:
                        code = c
                        break
                if code:
                    price_map[code] = {
                        'price': float(best.get('trade', best.get('close', 0)) or 0),
                        'change_pct': float(best.get('changepercent', 0) or 0),
                        'high': float(best.get('high', 0) or 0),
                        'low': float(best.get('low', 0) or 0),
                    }
        except:
            continue
    print(f"  {len(price_map)} 个实价")
    return price_map

# ─── future_2 wyckoff @ 120min ───
def run_future2_120m():
    print("[2] future_2 (威科夫) @ 120min ...")
    try:
        from wyckoff_quant.config import WyckoffConfig
        from wyckoff_quant.engine import WyckoffEngine
        
        engine = WyckoffEngine()
        syms = _get_symbols()
        signals = []
        
        for sym, name, ex in syms:
            try:
                # 获取60min数据合成120min
                raw = ak.futures_zh_minute_sina(symbol=sym, period="60")
                if raw is None or len(raw) < 40:
                    continue
                df = _norm_60m(raw)
                df120 = _synthesize_120m(df)
                if df120 is None or len(df120) < 30:
                    continue
                
                result = engine.analyze_df(df120)
                sig = result.signal
                if sig.is_valid and sig.side in ('long', 'short'):
                    lv = sig.levels
                    signals.append({
                        'system': 'wyckoff',
                        'symbol': sym,
                        'direction': '做多' if sig.side == 'long' else '做空',
                        'entry': lv.entry,
                        'stop': lv.stop,
                        'target': lv.target,
                        'rsi': sig.rsi_value,
                        'phase': sig.phase.phase if sig.phase else '',
                        'reason': sig.entry_reason[:50],
                        'weight': 10,
                    })
            except:
                continue
            time.sleep(0.4)
        
        print(f"  {len(signals)} 条威科夫信号")
        return signals
    except Exception as e:
        print(f"  future_2 加载失败: {e}")
        return []

def _get_symbols():
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    cur.execute("SELECT symbol, name, exchange FROM futures_top40 ORDER BY 排名 LIMIT 40")
    rows = cur.fetchall()
    conn.close()
    return rows

def _norm_60m(raw):
    df = raw.copy()
    col_map = {}
    cn = {'日期':'date','开盘':'open','最高':'high','最低':'low','收盘':'close','成交量':'volume','持仓量':'hold'}
    for c in df.columns:
        c2 = str(c).strip().lower()
        if c2 in cn:
            col_map[c] = cn[c2]
        elif c2 in ('datetime','open','high','low','close','volume'):
            col_map[c] = c2
    df = df.rename(columns=col_map)
    if 'datetime' not in df.columns and 'date' in df.columns:
        df['datetime'] = pd.to_datetime(df['date'], errors='coerce')
    else:
        df['datetime'] = pd.to_datetime(df.get('datetime', df.iloc[:, 0]), errors='coerce')
    need = ['open','high','low','close','volume']
    for c in need:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors='coerce')
    df = df.dropna(subset=['datetime','open','high','low','close']).sort_values('datetime').reset_index(drop=True)
    return df

def _synthesize_120m(df):
    if len(df) < 4:
        return None
    bars = []
    for i in range(0, len(df)-1, 2):
        k1, k2 = df.iloc[i], df.iloc[i+1]
        bars.append({
            'datetime': k1['datetime'],
            'open': k1['open'], 'high': max(k1['high'], k2['high']),
            'low': min(k1['low'], k2['low']), 'close': k2['close'],
            'volume': k1['volume'] + k2['volume'],
        })
    ret = pd.DataFrame(bars)
    if len(ret) > 200:
        ret = ret.tail(200).reset_index(drop=True)
    return ret

# ─── future_4 dual_ma @ 30min ───
def run_future4_30m():
    print("[3] future_4 (双均线缠绕) @ 30min ...")
    
    import subprocess
    future4_script = BASE / "run_future4_30m_ak.py"
    if not future4_script.exists():
        helper_code = '''#!/usr/bin/env python3
import akshare as ak
import pandas as pd
import numpy as np
import time, sys

DB_PATH = "D:/work_ai/futures_data.db"
import sqlite3
conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()
cur.execute("SELECT symbol, name, exchange FROM futures_top40 ORDER BY 排名 LIMIT 40")
syms = cur.fetchall()
conn.close()

rows = []
for i, (sym, name, ex) in enumerate(syms, 1):
    try:
        raw = ak.futures_zh_minute_sina(symbol=sym, period="30")
        if raw is None or len(raw) < 100:
            continue
        df = raw.copy()
        col_map = {}
        cn = {'日期':'date','开盘':'open','最高':'high','最低':'low','收盘':'close','成交量':'volume'}
        for c in df.columns:
            c2 = str(c).strip().lower()
            if c2 in cn:
                col_map[c] = cn[c2]
        df = df.rename(columns=col_map)
        if 'datetime' not in df.columns and 'date' in df.columns:
            df['datetime'] = pd.to_datetime(df['date'])
        else:
            df['datetime'] = pd.to_datetime(df.get('datetime', df.iloc[:,0]), errors='coerce')
        for c in ('open','high','low','close','volume'):
            df[c] = pd.to_numeric(df[c], errors='coerce')
        df = df.dropna(subset=['datetime','open','high','low','close']).sort_values('datetime').reset_index(drop=True)
        df['ema8'] = df['close'].ewm(span=8).mean()
        df['ema21'] = df['close'].ewm(span=21).mean()
        df['twist'] = ((df['ema8'] > df['ema21']) != (df['ema8'].shift(1) > df['ema21'].shift(1))).astype(int)
        twist_cross = df['twist'].tail(60).sum()
        if twist_cross >= 3:
            last_dir = 1 if df['ema8'].iloc[-1] > df['ema21'].iloc[-1] else 0
            direction = '做多' if last_dir == 1 else '做空'
            price = df['close'].iloc[-1]
            print(f"{sym}|{name}|{direction}|{twist_cross}|{price}")
        time.sleep(0.5)
    except:
        continue
'''
        future4_script.write_text(helper_code, encoding='utf-8')
    
    try:
        result = subprocess.run(
            [sys.executable, str(future4_script)],
            capture_output=True, text=True, timeout=300
        )
        signals = []
        for line in result.stdout.strip().split('\n'):
            line = line.strip()
            if not line:
                continue
            parts = line.split('|')
            if len(parts) >= 4:
                signals.append({
                    'system': 'dual_ma',
                    'symbol': parts[0],
                    'name': parts[1],
                    'direction': parts[2],
                    'weight': 5,
                    'entry': float(parts[4]) if len(parts) > 4 else 0,
                    'stop': float(parts[4]) * 0.99 if len(parts) > 4 else 0,
                    'target': float(parts[4]) * 1.01 if len(parts) > 4 else 0,
                    'rsi': None,
                    'phase': '',
                    'reason': f"缠绕{int(parts[3])}次",
                })
        print(f"  {len(signals)} 条缠绕信号")
        return signals
    except Exception as e:
        print(f"  future_4 执行失败: {e}")
        return []

# ─── future_6 renko @ 60min ───
def run_future6_60m():
    print("[4] future_6 (Renko Chart) @ 60min ...")
    try:
        from data_loader import load_config
        from indicators import atr, round_brick_size
        from renko import build_renko
        from signals import generate_signals
        
        cfg = load_config(str(F6 / "config.yaml"))
        cfg['data']['backend'] = 'akshare'
        
        syms = _get_symbols()
        signals = []
        
        for sym, name, ex in syms:
            try:
                raw = ak.futures_zh_minute_sina(symbol=sym, period="60")
                if raw is None or len(raw) < 60:
                    continue
                df = _norm_60m(raw)
                if df is None or len(df) < 60:
                    continue
                
                brick_size = round_brick_size(float(atr(df, 14).iloc[-1]) * 1.5, float(df['close'].iloc[-1]))
                renko = build_renko(df, brick_size=brick_size, reversal_mult=2.0, max_bricks_per_bar=3)
                if renko is None or renko.empty:
                    continue
                
                sigs = generate_signals(sym, name, df, renko, brick_size, cfg)
                for s in sigs:
                    direction = '做多' if s.direction > 0 else '做空'
                    signals.append({
                        'system': 'renko_chart',
                        'symbol': sym,
                        'direction': direction,
                        'entry': s.close,
                        'stop': s.close * 0.99,
                        'target': s.close * 1.01,
                        'rsi': s.rsi_brick,
                        'phase': s.signal_type,
                        'reason': s.detail[:30],
                        'weight': 5,
                    })
            except:
                continue
            time.sleep(0.5)
        
        print(f"  {len(signals)} 条Renko信号")
        return signals
    except Exception as e:
        print(f"  future_6 加载失败: {e}")
        import traceback; traceback.print_exc()
        return []

# ─── 综合评分 ───
def compute_card(db_df, f2_sigs, f4_sigs, f6_sigs, prices):
    print("[5] 综合评分 ...")
    
    # 系统信号索引
    sys_map = {}
    for s in f2_sigs + f4_sigs + f6_sigs:
        sys_map.setdefault(s['symbol'], []).append(s)
    
    rows = []
    for _, r in db_df.iterrows():
        sym = r['symbol']
        db_total = int(r['总分'])
        judgment = r['综合判定']
        name = r['name']
        
        rt = prices.get(sym, {})
        price = rt.get('price', 0)
        chg = rt.get('change_pct', 0)
        
        # Direction from judgment
        if '强势' in judgment or '偏强' in judgment:
            direction = '做多'
        elif '弱势' in judgment or '偏弱' in judgment:
            direction = '做空'
        else:
            direction = '做多' if chg >= 0 else '做空'
        
        # System boost
        sys_list = sys_map.get(sym, [])
        sys_count = len(sys_list)
        boost = sum(s.get('weight', 0) for s in sys_list)
        
        # Multi-system bonus
        systems_hit = set(s['system'] for s in sys_list)
        if len(systems_hit) >= 2:
            boost += 8 * (len(systems_hit) - 1)
        
        # Direction alignment bonus
        dir_match = sum(1 for s in sys_list if s['direction'] == direction)
        if dir_match > 0:
            boost += 3 * dir_match
        
        # Final score: DB(0-100) + boost
        # DB score: total/5 * 20 maps to 0-100
        db_score = db_total / 5 * 20
        score = db_score + boost
        
        if chg != 0 and price > 0:
            score += min(max(chg * 100, -15), 15)
        
        sector = code_to_sector(sym)
        
        rows.append({
            'symbol': sym,
            'name': name,
            'sector': sector,
            'price': price,
            'change_pct': chg,
            'db_total': db_total,
            'judgment': judgment,
            'score': round(score, 1),
            'direction': direction,
            'sys_count': sys_count,
            'sys_details': [f"{s['system']}:{s['direction']}" for s in sys_list],
        })
    
    rows.sort(key=lambda x: x['score'], reverse=True)
    return rows

def print_report(rows):
    print("\n" + "=" * 80)
    print("  综合交易监控卡片 — DB + future_2(2H) + future_4(30m) + future_6(60m)")
    print("=" * 80)
    
    print("\n>>> 强势 TOP10 <<<")
    for i, r in enumerate(rows[:10], 1):
        sys = f"[{r['sys_count']}系统]" if r['sys_count'] else ""
        chg = f"{r['change_pct']:+.2f}%" if r['price'] else "N/A"
        print(f"  {i:2d}. {r['score']:6.1f}  {r['symbol']:6s} {r['name']:12s} "
              f"DB={r['db_total']:>3d}  {r['judgment']:8s}  "
              f"价={r['price']:>10.2f} 涨跌={chg:>8s} {sys}")
        if r['sys_details']:
            print(f"       信号: {', '.join(r['sys_details'])}")
    
    print("\n>>> 弱势 BOTTOM10 <<<")
    n = len(rows)
    for i, r in enumerate(rows[-10:], n - 9):
        sys = f"[{r['sys_count']}系统]" if r['sys_count'] else ""
        chg = f"{r['change_pct']:+.2f}%" if r['price'] else "N/A"
        print(f"  {i:2d}. {r['score']:6.1f}  {r['symbol']:6s} {r['name']:12s} "
              f"DB={r['db_total']:>3d}  {r['judgment']:8s}  "
              f"价={r['price']:>10.2f} 涨跌={chg:>8s} {sys}")
    
    up = sum(1 for r in rows if r['direction'] == '做多')
    down = len(rows) - up
    has_sys = sum(1 for r in rows if r['sys_count'] > 0)
    multi_sys = sum(1 for r in rows if len(set(d.split(':')[0] for d in r['sys_details'])) >= 2)
    print(f"\n统计: 做多{up} / 做空{down} | 有系统共振: {has_sys} | 多系统共振: {multi_sys}")

def build_json(rows, ts):
    # TOP4
    cards = []
    for i, r in enumerate(rows[:4], 1):
        cards.append({
            'rank': i,
            'symbol': r['symbol'],
            'name': r['name'],
            'sector': r['sector'],
            'direction': r['direction'],
            'score': r['score'],
            'current_price': r['price'],
            'db_judgment': r['judgment'],
            'db_total': r['db_total'],
            'change_pct': r['change_pct'],
            'sys_count': r['sys_count'],
            'sys_details': r['sys_details'],
            'risk': ['暂无显著风险'] if r['sys_count'] >= 2 else ['无多系统共振确认'],
        })
    
    out = {
        'timestamp': datetime.now().isoformat(),
        'data_source': 'DB(hourly_5dim) + future_2(120min) + future_4(30min) + future_6(60min) + akshare',
        'total_scanned': len(rows),
        'cards': cards,
    }
    
    out_file = OUT_DIR / f"cards_{ts}.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    with open(OUT_DIR / "cards_latest.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    
    return out_file

def main():
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    print(f"  综合交易监控卡片 v5 {datetime.now():%Y-%m-%d %H:%M}")
    
    db_df = load_db_scores()
    print(f"  DB: {len(db_df)} 个品种")
    
    prices = load_akshare_prices()
    f2_sigs = []  # future_2: 跳过
    f4_sigs = run_future4_30m()
    f6_sigs = run_future6_60m()
    
    rows = compute_card(db_df, f2_sigs, f4_sigs, f6_sigs, prices)
    
    print_report(rows)
    
    out_file = build_json(rows, ts)
    print(f"\n输出: {out_file}")
    
    print("\n" + "=" * 80)
    print("  TOP4 交易监控卡片")
    print("=" * 80)
    for i, r in enumerate(rows[:4], 1):
        systems = ', '.join(r['sys_details']) if r['sys_details'] else "无"
        print(f"[{i}] {r['symbol']} {r['name']:12s} {r['direction']} "
              f"| DB={r['db_total']}星 {r['judgment']} "
              f"| 价={r['price']:.2f} 涨跌={r['change_pct']:+.2f}% "
              f"| 评分={r['score']:.1f}")
        print(f"    系统: {systems}")

if __name__ == "__main__":
    main()
