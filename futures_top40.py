"""
期货主力合约沉淀资金计算 - 最近20个交易日Top 40
沉淀资金 = 持仓量 x 合约乘数 x 收盘价
写入 SQLite 数据库
"""
import akshare as ak
import pandas as pd
import sqlite3
import os
import time
from datetime import datetime, timedelta

# --- 合约乘数映射表 ---
MULTIPLIER_MAP = {
    "V": 5, "P": 10, "B": 10, "M": 10, "I": 100,
    "JD": 5, "L": 5, "PP": 5, "FB": 10, "Y": 10,
    "C": 10, "A": 10, "J": 100, "JM": 60, "CS": 10,
    "EG": 10, "RR": 10, "EB": 5, "PG": 20, "LH": 16,
    "LG": 90, "BZ": 5,
    "TA": 5, "OI": 10, "RS": 10, "RM": 10, "WH": 20,
    "JR": 20, "SR": 10, "CF": 5, "RI": 20, "MA": 10,
    "FG": 20, "LR": 20, "SF": 5, "SM": 5, "CY": 5,
    "AP": 10, "CJ": 5, "UR": 20, "SA": 20, "PF": 5,
    "PK": 5, "SH": 5, "PX": 5, "PR": 5, "PL": 5,
    "FU": 10, "AL": 5, "RU": 10, "ZN": 5, "CU": 5,
    "AU": 1000, "RB": 10, "PB": 5, "AG": 15, "BU": 10,
    "HC": 10, "SN": 1, "NI": 1, "SP": 10, "SS": 5,
    "AO": 20, "BR": 5, "AD": 25, "OP": 10,
    "SC": 1000, "NR": 10, "LU": 10, "BC": 5, "EC": 50,
    "IF": 300, "IH": 300, "IC": 200, "IM": 200,
    "TS": 20000, "TF": 10000, "T": 10000, "TL": 10000,
    "SI": 5, "LC": 1, "PS": 3, "PT": 1, "PD": 1,
}


def get_symbol_prefix(sym):
    if len(sym) >= 2 and sym[:2] in MULTIPLIER_MAP:
        return sym[:2]
    if sym[:1] in MULTIPLIER_MAP:
        return sym[:1]
    return None


def get_multiplier(sym):
    prefix = get_symbol_prefix(sym)
    return MULTIPLIER_MAP.get(prefix)


def main():
    db_path = r"d:\work_ai\futures_data.db"
    os.makedirs(r"d:\work_ai", exist_ok=True)

    print("获取主力合约列表...")
    symbols_df = ak.futures_display_main_sina()
    print(f"共获取到 {len(symbols_df)} 个主力合约")

    results = []
    errors = []
    today = datetime.now()

    for idx, row in symbols_df.iterrows():
        symbol = row["symbol"]
        name = row["name"]
        exchange = row["exchange"]
        mult = get_multiplier(symbol)

        if mult is None:
            errors.append((symbol, name, "未找到合约乘数"))
            print(f"  [{idx+1}/{len(symbols_df)}] {symbol} ({name}) - 跳过: 未知合约乘数")
            continue

        try:
            start = (today - timedelta(days=400)).strftime("%Y%m%d")
            end = today.strftime("%Y%m%d")
            df = ak.futures_main_sina(symbol=symbol, start_date=start, end_date=end)

            if df is None or df.empty:
                errors.append((symbol, name, "无数据"))
                print(f"  [{idx+1}/{len(symbols_df)}] {symbol} ({name}) - 无数据")
                continue

            df_20 = df.tail(20).copy()
            if len(df_20) < 5:
                errors.append((symbol, name, f"数据不足20日(仅{len(df_20)}日)"))
                print(f"  [{idx+1}/{len(symbols_df)}] {symbol} ({name}) - 数据不足")
                continue

            df_20["沉淀资金"] = df_20.apply(
                lambda r: r["持仓量"] * mult * (r["动态结算价"] if r["动态结算价"] > 0 else r["收盘价"]),
                axis=1
            )

            latest = df_20.iloc[-1]
            latest_date = latest["日期"]
            latest_fund = latest["沉淀资金"]
            avg_fund = df_20["沉淀资金"].mean()
            latest_oi = int(latest["持仓量"])
            latest_price = latest["动态结算价"] if latest["动态结算价"] > 0 else latest["收盘价"]

            results.append({
                "symbol": symbol,
                "name": name.replace("连续", ""),
                "exchange": exchange,
                "multiplier": mult,
                "latest_date": latest_date,
                "latest_price": float(latest_price),
                "latest_oi": latest_oi,
                "沉淀资金_最新": float(latest_fund),
                "沉淀资金_20日均": float(avg_fund),
                "data_days": len(df_20),
            })

            print(f"  [{idx+1}/{len(symbols_df)}] {symbol} {name}: "
                  f"最新={latest_fund/1e8:.2f}亿  20日均={avg_fund/1e8:.2f}亿")
            time.sleep(0.3)

        except Exception as e:
            errors.append((symbol, name, str(e)))
            print(f"  [{idx+1}/{len(symbols_df)}] {symbol} ({name}) - 错误: {e}")
            time.sleep(0.5)

    results_df = pd.DataFrame(results)
    results_df.sort_values("沉淀资金_最新", ascending=False, inplace=True)
    results_df.reset_index(drop=True, inplace=True)

    top40 = results_df.head(40).copy()
    top40["排名"] = range(1, 41)

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS futures_top40 (
            排名 INTEGER,
            symbol TEXT,
            name TEXT,
            exchange TEXT,
            合约乘数 INTEGER,
            最新日期 TEXT,
            最新价格 REAL,
            最新持仓量 INTEGER,
            沉淀资金_最新 REAL,
            沉淀资金_20日均 REAL,
            数据天数 INTEGER,
            更新时间 TEXT,
            PRIMARY KEY (symbol)
        )
    """)

    cursor.execute("DELETE FROM futures_top40")

    update_time = today.strftime("%Y-%m-%d %H:%M:%S")
    for _, r in top40.iterrows():
        cursor.execute("""
            INSERT INTO futures_top40 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            int(r["排名"]), r["symbol"], r["name"], r["exchange"],
            int(r["multiplier"]), r["latest_date"], r["latest_price"],
            int(r["latest_oi"]), r["沉淀资金_最新"], r["沉淀资金_20日均"],
            int(r["data_days"]), update_time
        ))

    conn.commit()

    cursor.execute("DROP TABLE IF EXISTS futures_all")
    results_df["更新时间"] = update_time
    results_df.to_sql("futures_all", conn, if_exists="replace", index=False)
    conn.close()

    print()
    print("=" * 110)
    print(f"期货主力合约沉淀资金 TOP 40 (更新于 {update_time})")
    print(f"数据库路径: {db_path}")
    print(f"成功: {len(results)}  |  失败: {len(errors)}")
    print("=" * 110)
    hdr = f"{'排名':>4} {'合约':>6} {'名称':<10} {'交易所':<6} {'乘数':>5} {'最新持仓量':>12} {'最新价格':>10} {'沉淀资金(亿)':>12} {'20日均(亿)':>12}"
    print(hdr)
    print("-" * 110)
    for _, r in top40.iterrows():
        print(f"{int(r['排名']):>4} {r['symbol']:>6} {r['name']:<10} {r['exchange']:<6} "
              f"{int(r['multiplier']):>5} {int(r['latest_oi']):>12,} {r['latest_price']:>10.1f} "
              f"{r['沉淀资金_最新']/1e8:>12.2f} {r['沉淀资金_20日均']/1e8:>12.2f}")

    if errors:
        print(f"\n警告: {len(errors)} 个合约处理失败:")
        for sym, nm, err in errors:
            print(f"  {sym} ({nm}): {err}")
    print("\n完成!")


if __name__ == "__main__":
    main()
