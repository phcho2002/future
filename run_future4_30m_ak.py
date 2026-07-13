#!/usr/bin/env python3
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
        
        # EMA cross detection
        df['ema8'] = df['close'].ewm(span=8).mean()
        df['ema21'] = df['close'].ewm(span=21).mean()
        df['twist'] = ((df['ema8'] > df['ema21']) != (df['ema8'].shift(1) > df['ema21'].shift(1))).astype(int)
        twist_cross = df['twist'].tail(60).sum()
        
        if twist_cross >= 3:
            last_dir = 1 if df['ema8'].iloc[-1] > df['ema21'].iloc[-1] else 0
            rows.append(f"{sym}|{name}|{'做多' if last_dir == 1 else '做空'}|{twist_cross}|{df['close'].iloc[-1]:.2f}")
        time.sleep(0.5)
    except:
        continue

print('\n'.join(rows))
