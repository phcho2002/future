"""
每日 top40 强势/弱势报告脚本
被 cron 调用，运行分析并输出结果
"""
import subprocess, sys, sqlite3, pandas as pd
from datetime import datetime

DB_PATH = "d:/work_ai/futures_data.db"
SCRIPT_PATH = "d:/work_ai/hourly_fix.py"

print("=" * 70)
print("  TOP40 期货强势/弱势分析报告")
print("  " + datetime.now().strftime("%Y-%m-%d %H:%M"))
print("=" * 70)

# Step 1: Run the analysis
print("")
print("[Step 1/2] Running hourly_fix.py...")
r = subprocess.run([sys.executable, SCRIPT_PATH], capture_output=True, text=True, timeout=600)
if r.returncode != 0:
    print("Error: script returned " + str(r.returncode))
    print(r.stderr[-500:])
    sys.exit(1)
print("  Analysis complete")

# Step 2: Read and display results
print("")
print("[Step 2/2] Reading results...")
conn = sqlite3.connect(DB_PATH)
df = pd.read_sql("SELECT * FROM hourly_analysis_all ORDER BY 强弱势排名", conn)

# ---- 保存到小时线追踪表 ----
strong = df.head(3)
weak = df.tail(3)
scores = df['总分']
try:
    cur = conn.cursor()
    hour = datetime.now().hour
    if hour < 12: label = 'am'
    elif hour < 18: label = 'pm'
    else: label = 'night'
    
    s1, s2, s3r = strong.iloc[0], strong.iloc[1], strong.iloc[2]
    w1, w2, w3r = weak.iloc[2], weak.iloc[1], weak.iloc[0]
    
    cur.execute("""
        INSERT INTO hourly_top3_tracking
        (run_time, run_label,
         s1_symbol, s1_contract, s1_name, s1_score,
         s2_symbol, s2_contract, s2_name, s2_score,
         s3_symbol, s3_contract, s3_name, s3_score,
         w1_symbol, w1_contract, w1_name, w1_score,
         w2_symbol, w2_contract, w2_name, w2_score,
         w3_symbol, w3_contract, w3_name, w3_score,
         max_score, max_name, min_score, min_name, avg_score, total_count)
        VALUES (?, ?,
         ?,?,?,?, ?,?,?,?, ?,?,?,?,
         ?,?,?,?, ?,?,?,?, ?,?,?,?,
         ?,?,?,?,?,?)
    """, (
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"), label,
        s1['symbol'], s1['contract'], s1['name'], int(s1['总分']),
        s2['symbol'], s2['contract'], s2['name'], int(s2['总分']),
        s3r['symbol'], s3r['contract'], s3r['name'], int(s3r['总分']),
        w1['symbol'], w1['contract'], w1['name'], int(w1['总分']),
        w2['symbol'], w2['contract'], w2['name'], int(w2['总分']),
        w3r['symbol'], w3r['contract'], w3r['name'], int(w3r['总分']),
        int(scores.max()), str(df.loc[scores.idxmax(), 'name']),
        int(scores.min()), str(df.loc[scores.idxmin(), 'name']),
        round(float(scores.mean()), 1), len(df),
    ))
    conn.commit()
    print(f"  hourly_top3_tracking saved [{label}]")
except Exception as e:
    print(f"  ⚠ tracking save failed: {e}")

print("")

strong = df.head(3)
print("")
print("=== STRONG TOP 3 ===")
for _, row in strong.iterrows():
    print(f"  [{int(row['强弱势排名'])}] {row['symbol']} ({row['contract']} {row['name']}) Score={int(row['总分'])} {row['综合判定']}")
    print(f"    Breakout: {row['突破说明']}")
    print(f"    Retrace: {row['回调说明']}")
    print(f"    AnomalyK: {row['异常K说明']}")
    print(f"    Resonance: {row['共振说明']}")
    print(f"    Intraday: {row['日内说明']}")
    print("")

weak = df.tail(3)
print("=== WEAK BOTTOM 3 ===")
for _, row in weak.iterrows():
    print(f"  [{int(row['强弱势排名'])}] {row['symbol']} ({row['contract']} {row['name']}) Score={int(row['总分'])} {row['综合判定']}")
    print(f"    Breakout: {row['突破说明']}")
    print(f"    Retrace: {row['回调说明']}")
    print(f"    Resonance: {row['共振说明']}")
    print(f"    Intraday: {row['日内说明']}")
    print("")

print("--- Summary ---")
col = '\u603b\u5206'
print(f"  Strong (80+): {(df[col]>=80).sum()}")
print(f"  Moderate strong (65-79): {((df[col]>=65)&(df[col]<80)).sum()}")
print(f"  Neutral (45-64): {((df[col]>=45)&(df[col]<65)).sum()}")
print(f"  Weak (<45): {(df[col]<45).sum()}")
max_idx = df[col].idxmax()
min_idx = df[col].idxmin()
print(f"  Max score: {df[col].max()} ({df.loc[max_idx, 'name']})")
print(f"  Min score: {df[col].min()} ({df.loc[min_idx, 'name']})")
conn.close()
print("")
print("=" * 70)
print("  Report complete")
print("=" * 70)
