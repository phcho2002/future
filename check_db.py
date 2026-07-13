import sqlite3
conn = sqlite3.connect(r'd:\work_ai\futures_data.db')
cursor = conn.cursor()
cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = cursor.fetchall()
print('Tables:', tables)
for t in tables:
    cursor.execute(f'SELECT COUNT(*) FROM "{t[0]}"')
    cnt = cursor.fetchone()[0]
    print(f'  {t[0]}: {cnt} rows')
    cursor.execute(f'SELECT * FROM "{t[0]}" LIMIT 3')
    cols = [d[0] for d in cursor.description]
    print(f'  Columns: {cols}')
    rows = cursor.fetchall()
    for r in rows:
        print(f'    {r}')
conn.close()
