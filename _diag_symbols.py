import akshare as ak
import pandas as pd
import sqlite3

conn = sqlite3.connect("D:/work_ai/futures_data.db")
db = pd.read_sql("SELECT symbol, name FROM hourly_analysis_all LIMIT 40", conn)
conn.close()

failing = []
for _, row in db.iterrows():
    sym, name = row['symbol'], row['name']
    try:
        rt = ak.futures_zh_realtime(symbol=name)
        if rt is not None and len(rt) > 0:
            rt['volume'] = pd.to_numeric(rt['volume'], errors='coerce').fillna(0)
            best = rt.loc[rt['volume'].idxmax()]
            price = float(best.get('trade', 0) or 0)
            print(f"{sym:6s} {name:12s} -> OK price={price:.2f}")
        else:
            failing.append((sym, name, 'empty'))
    except Exception as e:
        failing.append((sym, name, str(e)[:30]))

print(f"\n--- FAILING ({len(failing)}) ---")
for sym, name, err in failing:
    print(f"  {sym:6s} {name:12s} -> {err}")
