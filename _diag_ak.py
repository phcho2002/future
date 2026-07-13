import akshare as ak
df = ak.futures_symbol_mark()
print(df.columns.tolist())
print(df.head(20))
print(f"\n总行数: {len(df)}")
