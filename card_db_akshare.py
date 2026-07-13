#!/usr/bin/env python3
"""
交易监控卡片 v3 — 综合 DB评分 + 系统信号 + akshare实价
"""
import json, sqlite3, sys
from datetime import datetime
from pathlib import Path
import pandas as pd
import numpy as np
import akshare as ak

BASE = Path("D:/work_ai")
OUT_DIR = BASE / "singal_h"
DB = BASE / "futures_data.db"

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

# DB name -> akshare symbol_name mapping
AKSHARE_NAME_MAP = {
    '甲醇': '郑醇', '聚丙烯': 'PP', '棕榈油': '棕榈', '镍': '沪镍',
    '上海原油': '原油', '铝': '沪铝', '锡': '沪锡', '天然橡胶': '橡胶',
    '铜': '沪铜', '中证指数期货': '中证1000股指期货',
    '2年期国债期货': '2年期国债', '5年期国债期货': '5年期国债',
    '10年期国债期货': '10年期国债', '30年期国债期货': '30年期国债',
    '沪深300指数期货': '沪深300', '上证50指数期货': '上证50',
    '中证500指数期货': '中证500',
}

def code_to_sector(code):
    k = code[:2] + '0'
    return SECTORS.get(k, SECTORS.get(code[:3], '其他'))

def fetch_db():
    conn = sqlite3.connect(DB)
    df = pd.read_sql("SELECT * FROM hourly_analysis_all", conn)
    conn.close()
    return df

def fetch_akshare(db_df):
    """Get realtime prices for all symbols in db_df"""
    # Build lookup: symbol -> akshare_name
    price_map = {}
    
    for _, row in db_df.iterrows():
        sym = row['symbol']
        name = row['name']
        
        # Get akshare name (use map if needed)
        ak_name = AKSHARE_NAME_MAP.get(name, name)
        try:
            rt = ak.futures_zh_realtime(symbol=ak_name)
            if rt is not None and len(rt) > 0:
                rt['volume'] = pd.to_numeric(rt['volume'], errors='coerce').fillna(0)
                best = rt.loc[rt['volume'].idxmax()]
                price_map[sym] = {
                    'price': float(best.get('trade', 0) or 0),
                    'change_pct': float(best.get('changepercent', 0) or 0),
                    'high': float(best.get('high', 0) or 0),
                    'low': float(best.get('low', 0) or 0),
                }
        except:
            # Fallback: try original name
            try:
                rt = ak.futures_zh_realtime(symbol=name)
                if rt is not None and len(rt) > 0:
                    rt['volume'] = pd.to_numeric(rt['volume'], errors='coerce').fillna(0)
                    best = rt.loc[rt['volume'].idxmax()]
                    price_map[sym] = {
                        'price': float(best.get('trade', 0) or 0),
                        'change_pct': float(best.get('changepercent', 0) or 0),
                        'high': float(best.get('high', 0) or 0),
                        'low': float(best.get('low', 0) or 0),
                    }
            except:
                pass
    
    return price_map

def load_signals():
    """Load future_2/4/6 signals"""
    sigs = []
    
    # future_2 wyckoff
    wyckoff = sorted((BASE / "future_2").glob("wyckoff_signals_*.csv"))
    if wyckoff:
        df = pd.read_csv(wyckoff[-1])
        for _, r in df.iterrows():
            sigs.append({
                'symbol': r.get('symbol',''),
                'direction': '做多' if str(r.get('side','long'))=='long' else '做空',
                'weight': 10,
                'system': 'wyckoff',
                'trigger': f"{r.get('phase','')} {str(r.get('reason',''))[:40]}"
            })
    
    # future_4 dual_ma
    f4 = BASE / "future_4/output/alerts_latest.csv"
    if f4.exists():
        df = pd.read_csv(f4)
        for _, r in df.iterrows():
            if int(r.get('bars_since',0)) <= 20:
                sigs.append({
                    'symbol': r.get('symbol',''),
                    'direction': '做多' if int(r.get('direction',0))==1 else '做空',
                    'weight': 5,
                    'system': 'dual_ma',
                    'trigger': f"缠绕{int(r.get('twist_cross',0))}次"
                })
    
    # future_6 renko
    renko = sorted((BASE / "future_6/output").glob("renko_signals_*.csv"))
    if renko:
        df = pd.read_csv(renko[-1])
        if len(df):
            for sym, g in df.groupby('symbol'):
                row = g.iloc[-1]
                sigs.append({
                    'symbol': sym,
                    'direction': '做多' if 'long' in str(row.get('signal_type','')) else '做空',
                    'weight': 5,
                    'system': 'renko_chart',
                    'trigger': str(row.get('signal_type',''))[:30]
                })
    
    return sigs

def compute(db_df, prices, sigs):
    rows = []
    sig_map = {}
    for s in sigs:
        sig_map.setdefault(s['symbol'], []).append(s)
    
    for _, r in db_df.iterrows():
        sym = r['symbol']
        db_total = int(r['总分'])
        judgment = r['综合判定']
        name = r['name']
        
        rt = prices.get(sym, {})
        price = rt.get('price', 0)
        chg = rt.get('change_pct', 0)
        
        # Direction
        if '强势' in judgment or '偏强' in judgment:
            direction = '做多'
        elif '弱势' in judgment or '偏弱' in judgment:
            direction = '做空'
        else:
            direction = '做多' if chg >= 0 else '做空'
        
        # System boost
        sysList = sig_map.get(sym, [])
        sys_count = len(sysList)
        boost = sum(s['weight'] for s in sysList)
        if sys_count >= 2:
            boost += 5 * (sys_count - 1)
        
        # Score: DB(0-5)*20 + boost, max around 100 + boost
        score = db_total / 5 * 10 + boost
        
        # Price factor: if we have a live price, adjust slightly
        if price > 0:
            score += min(max(chg * 100, -10), 10)  # -10..+10 based on daily change
        
        sector = code_to_sector(sym)
        
        rows.append({
            'symbol': sym,
            'name': name,
            'sector': sector,
            'price': price,
            'db_total': db_total,
            'judgment': judgment,
            'change_pct': chg,
            'high': rt.get('high', 0),
            'low': rt.get('low', 0),
            'score': round(score, 1),
            'direction': direction,
            'sys_count': sys_count,
            'sys_details': [f"{s['system']}:{s['direction']}" for s in sysList],
        })
    
    rows.sort(key=lambda x: x['score'], reverse=True)
    return rows

def print_report(rows):
    print("\n" + "="*75)
    print("  综合交易监控卡片 — DB(五维) + 系统信号 + akshare实价")
    print("="*75)
    
    print("\n>>> 强势 TOP10 <<<")
    for i, r in enumerate(rows[:10], 1):
        sys = f"[{r['sys_count']}系统]" if r['sys_count'] else ""
        chg = f"{r['change_pct']:+.2f}%" if r['price'] else "N/A"
        print(f"  {i:2d}. {r['score']:6.1f} {r['symbol']:6s} {r['name']:12s} "
              f"DB={r['db_total']:>3d} {r['judgment']:8s} "
              f"价={r['price']:>10.2f} 涨跌={chg:>8s} {sys}")
        if r['sys_details']:
            print(f"      信号: {', '.join(r['sys_details'])}")
    
    print("\n>>> 弱势 BOTTOM10 <<<")
    for i, r in enumerate(rows[-10:], 1):
        sys = f"[{r['sys_count']}系统]" if r['sys_count'] else ""
        chg = f"{r['change_pct']:+.2f}%" if r['price'] else "N/A"
        print(f"  {i:2d}. {r['score']:6.1f} {r['symbol']:6s} {r['name']:12s} "
              f"DB={r['db_total']:>3d} {r['judgment']:8s} "
              f"价={r['price']:>10.2f} 涨跌={chg:>8s} {sys}")
        if r['sys_details']:
            print(f"      信号: {', '.join(r['sys_details'])}")
    
    # Summary stats
    up = sum(1 for r in rows if r['direction'] == '做多')
    down = len(rows) - up
    print(f"\n统计: 做多{up}个, 做空{down}个 | 有系统共振: {sum(1 for r in rows if r['sys_count']>0)}个")

def build_json(rows):
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    cards = []
    for r in rows[:4]:
        cards.append({
            'rank': 0,  # filled below
            'symbol': r['symbol'],
            'name': r['name'],
            'sector': r['sector'],
            'direction': r['direction'],
            'score': r['score'],
            'current_price': r['price'],
            'db_judgment': r['judgment'],
            'db_total': r['db_total'],
            'change_pct': r['change_pct'],
            'high': r['high'],
            'low': r['low'],
            'sys_count': r['sys_count'],
            'sys_details': r['sys_details'],
            'risk': ['暂无显著风险'] if r['sys_count'] >= 2 else ['无系统共振确认'],
        })
    for i, c in enumerate(cards):
        c['rank'] = i + 1
    
    out = {
        'timestamp': datetime.now().isoformat(),
        'data_source': 'futures_data.db + future_2/4/6 signals + akshare realtime',
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
    print(f"=== 综合交易监控卡片 v3 {datetime.now():%Y-%m-%d %H:%M} ===\n")
    
    db_df = fetch_db()
    print(f"[1] DB 评分: {len(db_df)} 个品种")
    
    prices = fetch_akshare(db_df)
    print(f"[2] akshare 实价: {len(prices)} 个品种")
    
    sigs = load_signals()
    print(f"[3] 系统信号: {len(sigs)} 条")
    
    rows = compute(db_df, prices, sigs)
    
    print_report(rows)
    
    out_file = build_json(rows)
    print(f"\n输出: {out_file}")
    print(f"最新: {OUT_DIR / 'cards_latest.json'}")
    
    print("\n" + "="*75)
    print("  <<< TOP4 交易监控卡片 >>>")
    print("="*75)
    for r in rows[:4]:
        print(f"[{r['symbol']}] {r['name']:12s} {r['direction']} "
              f"| DB={r['db_total']}星 {r['judgment']} "
              f"| 价={r['price']:.2f} 涨跌={r['change_pct']:+.2f}% "
              f"| 评分={r['score']:.1f} | 共振={r['sys_count']}")

if __name__ == "__main__":
    sys.exit(main())
