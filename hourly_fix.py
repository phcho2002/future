"""
期货主力前40 - 小时线五维技术分析 (修复版)
使用 Sina API 自定义请求头避免被拦截
"""
import requests, json, pandas as pd, numpy as np, sqlite3, os, time, warnings
warnings.filterwarnings('ignore')
from datetime import datetime

DB_PATH = r"d:\work_ai\futures_data.db"

def get_60min_kline(symbol):
    """获取60分钟K线"""
    url = "https://stock2.finance.sina.com.cn/futures/api/jsonp.php/=/InnerFuturesNewService.getFewMinLine"
    params = {"symbol": symbol, "type": "60"}
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://vip.stock.finance.sina.com.cn/",
    }
    r = requests.get(url, params=params, headers=headers, timeout=15)
    if r.status_code != 200:
        return None
    text = r.text
    start = text.find("=(")
    if start == -1:
        start = text.find("([")
        if start == -1: return None
        end = text.rfind("]);")
        if end == -1:
            end = text.rfind("])")
            if end == -1: return None
            json_str = text[start+1:end+1]
        else:
            json_str = text[start+1:end]
    else:
        end = text.rfind(");")
        if end == -1: return None
        json_str = text[start+2:end]
    try:
        data = json.loads(json_str)
    except Exception as e:
        return None
    df = pd.DataFrame(data)
    df.columns = ['datetime', 'open', 'high', 'low', 'close', 'volume', 'hold']
    for col in ['open', 'high', 'low', 'close', 'volume', 'hold']:
        df[col] = pd.to_numeric(df[col], errors='coerce')
    df['datetime'] = pd.to_datetime(df['datetime'])
    df = df.sort_values('datetime').reset_index(drop=True)
    return df


def build_contract_mapping(top40_df):
    import akshare as ak
    exchange_contracts = {}
    for ex in ['czce', 'dce', 'shfe', 'cffex', 'gfex']:
        try:
            s = ak.match_main_contract(symbol=ex)
            exchange_contracts[ex] = [c.strip() for c in s.split(',') if c.strip()]
        except:
            exchange_contracts[ex] = []
    
    # 手动修正已知的映射问题
    manual_fixes = {
        'M0': 'M2609',    # 豆粕 -> dce
        'P0': 'P2609',    # 棕榈油 -> dce
        'C0': 'C2607',    # 玉米 -> dce
        'SC0': 'SC2607',  # 上海原油 -> shfe 下
    }
    
    # 交易所反查
    exchange_for_contract = {}
    for ex, contracts in exchange_contracts.items():
        for c in contracts:
            exchange_for_contract[c] = ex
    
    mapping = {}
    for _, r in top40_df.iterrows():
        sym = r['symbol']
        ex = r['exchange']
        
        if sym in manual_fixes:
            mapping[sym] = manual_fixes[sym]
            continue
        
        # 逐层查找: 先查2字母前缀，再查1字母前缀
        found = None
        for plen in [2, 1]:
            p = sym[:plen].upper()
            matching = [c for c in exchange_contracts.get(ex, []) if c.upper().startswith(p)]
            if not matching:
                matching = [c for c in exchange_contracts.get('shfe', []) if c.upper().startswith(p)]
            if matching:
                for c in matching:
                    ex_found = exchange_for_contract.get(c, '')
                    if ex_found == ex:
                        found = c
                        break
                if not found:
                    found = matching[0]
                break
        if found:
            mapping[sym] = found
    return mapping



def compute_breakthrough_quality(df_h):
    if len(df_h) < 20: return 10, "数据不足"
    recent = df_h.tail(10)
    recent_high = recent['high'].max()
    recent_low = recent['low'].min()
    last = df_h.iloc[-1]
    near_high = last['close'] >= recent_high * 0.98
    near_low = last['close'] <= recent_low * 1.02
    range_k = last['high'] - last['low']
    if range_k > 0:
        upper_shadow = (last['high'] - max(last['close'], last['open'])) / range_k
        lower_shadow = (min(last['close'], last['open']) - last['low']) / range_k
    else:
        upper_shadow = lower_shadow = 0
    if near_high and upper_shadow > 0.5:
        return 4, "突破质量差：遇阻回落/长上影/假突破"
    elif near_low and lower_shadow > 0.5:
        return 16, "突破质量好：支撑位获强支撑/下影线明显"
    elif near_high and last['close'] > last['open']:
        return 16, "突破质量好：放量突破阻力位"
    elif near_high:
        return 8, "突破质量一般：接近阻力位但无明确突破信号"
    elif near_low:
        return 8, "突破质量一般：接近支撑位"
    return 10, "突破质量一般"

def compute_pullback_quality(df_h):
    if len(df_h) < 20: return 10, "数据不足"
    mid = df_h.tail(20)
    mmax = mid['high'].max()
    mmin = mid['low'].min()
    if mmax == mmin: return 10, "回调一般"
    retrace = (mmax - mid['close'].iloc[-1]) / (mmax - mmin)
    last5 = mid.tail(5)
    bear = len(last5[last5['close'] < last5['open']])
    bull = len(last5[last5['close'] > last5['open']])
    if retrace < 0.2 and bull >= 3:
        return 18, "回调质量好：回调缩量，多头承接有力"
    elif retrace > 0.6 and bear >= 3:
        return 7, "回调质量差：下跌趋势中反弹无力"
    elif retrace > 0.4:
        return 10, "回调一般"
    elif retrace < 0.3:
        return 14, "回调较浅，趋势偏强"
    return 10, "回调一般"

def compute_abnormal_kline(df_h):
    if len(df_h) < 3: return 10, "数据不足"
    last3 = df_h.tail(3)
    signals = []
    for _, k in last3.iterrows():
        body = abs(k['close'] - k['open'])
        kr = k['high'] - k['low']
        if kr == 0: continue
        if k['close'] > k['open'] and k['open'] < k['low'] + kr * 0.3 and body > kr * 0.5:
            signals.append("假弱势")
        elif k['open'] > k['close'] and k['open'] > k['high'] - kr * 0.3 and body > kr * 0.5:
            signals.append("假强势")
    if len(signals) >= 2:
        return 18, f"K线存在强势信号：" + "/".join(signals)
    elif len(signals) >= 1:
        return 14, "K线存在偏强信号"
    return 10, "K线存在弱势隐患"

def compute_resonance(df_h):
    if len(df_h) < 40: return 10, "数据不足，无法评估多周期共振"
    df = df_h.copy()
    df['ma5'] = df['close'].rolling(5).mean()
    df['ma10'] = df['close'].rolling(10).mean()
    df['ma20'] = df['close'].rolling(20).mean()
    df['ma40'] = df['close'].rolling(40).mean()
    periods = ['ma5', 'ma10', 'ma20', 'ma40']
    pnames = ['1h', '4h', '日', '周']
    trends = []
    for p in periods:
        vals = df[p].dropna().values
        if len(vals) >= 5:
            slope = (vals[-1] - vals[-5]) / abs(vals[-5]) * 100 if vals[-5] != 0 else 0
            if slope > 0.3: trends.append(1)
            elif slope < -0.3: trends.append(-1)
            else: trends.append(0)
        else: trends.append(0)
    bull = sum(1 for t in trends if t == 1)
    bear = sum(1 for t in trends if t == -1)
    trend_desc = '/'.join([f"{p}{'多' if t==1 else '空' if t==-1 else '震荡'}" for p, t in zip(pnames, trends)])
    if bull >= 3:
        return 20, f"多周期共振偏强 {trend_desc}"
    elif bear >= 3:
        return 5, f"多周期共振偏弱 {trend_desc}"
    elif bull >= 2:
        return 15, f"多周期共振偏强 {trend_desc}"
    elif bear >= 2:
        return 8, f"多周期共振偏弱 {trend_desc}"
    return 10, f"多周期分化 {trend_desc}"

def compute_intraday_strength(df_h):
    if len(df_h) < 5: return 10, "数据不足"
    recent = df_h.tail(10)
    rh = recent['high'].max()
    rl = recent['low'].min()
    rang = rh - rl
    if rang == 0: return 10, "日内中性"
    last = df_h.iloc[-1]
    pos = (last['close'] - rl) / rang
    tail5 = df_h.tail(5)
    above = len(tail5[tail5['close'] > tail5['open']])
    if pos > 0.7 and above >= 3:
        return 16, "日内强势：多数K线运行在开盘区间之上"
    elif pos < 0.3 and above <= 2:
        return 6, "日内弱势：多数K线运行在开盘区间之下"
    elif pos > 0.6:
        return 13, "日内偏强"
    elif pos < 0.4:
        return 8, "日内偏弱"
    return 10, "日内中性"



def main():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    print("=" * 80)
    print("期货主力前40 - 小时线五维技术分析")
    print("=" * 80)
    conn = sqlite3.connect(DB_PATH)
    top40 = pd.read_sql("SELECT symbol, name, exchange FROM futures_top40 ORDER BY 排名", conn)
    conn.close()
    print(f"从数据库读取 {len(top40)} 个主力合约")
    print("\n[1/3] 建立合约映射...")
    mapping = build_contract_mapping(top40)
    print(f"  成功映射 {len(mapping)}/{len(top40)} 个合约")
    for s, c in mapping.items():
        print(f"    {s} -> {c}")
    unmapped = [r['symbol'] for _, r in top40.iterrows() if r['symbol'] not in mapping]
    if unmapped:
        print(f"  ⚠ 未映射: {unmapped}")
    print("\n[2/3] 获取小时线数据并分析...")
    all_results = []
    errors = []
    for i, (_, r) in enumerate(top40.iterrows()):
        sym = r['symbol']
        name = r['name']
        contract = mapping.get(sym)
        if not contract:
            errors.append((sym, name, "未找到合约映射"))
            continue
        print(f"  [{i+1}/{len(top40)}] {sym} ({contract} {name})...", end=' ', flush=True)
        try:
            df_h = get_60min_kline(contract)
            if df_h is None or len(df_h) < 10:
                print("无数据或数据不足")
                errors.append((sym, name, "无小时线数据"))
                time.sleep(0.3)
                continue
            print(f"{len(df_h)}根K线", end=' ', flush=True)
            s1, v1 = compute_breakthrough_quality(df_h)
            s2, v2 = compute_pullback_quality(df_h)
            s3, v3 = compute_abnormal_kline(df_h)
            s4, v4 = compute_resonance(df_h)
            s5, v5 = compute_intraday_strength(df_h)
            total = s1 + s2 + s3 + s4 + s5
            if total >= 75: verdict = "★★★★★ 强势"
            elif total >= 65: verdict = "★★★★ 偏强"
            elif total >= 55: verdict = "★★★ 中性偏强"
            elif total >= 45: verdict = "★★ 中性偏弱"
            elif total >= 35: verdict = "★ 偏弱"
            else: verdict = "弱势"
            all_results.append({
                'symbol': sym, 'contract': contract, 'name': name,
                '成交量': int(df_h['volume'].sum()), '持仓量': int(df_h['hold'].iloc[-1]),
                '突破质量': round(s1, 1), '回调强弱': round(s2, 1),
                '异常K线': round(s3, 1), '多周期共振': round(s4, 1),
                '日内强弱': round(s5, 1), '总分': round(total, 1),
                '综合判定': verdict,
                '突破说明': v1, '回调说明': v2, '异常K说明': v3,
                '共振说明': v4, '日内说明': v5,
            })
            print(f"总分={total:.1f} {verdict}")
            time.sleep(0.35)
        except Exception as e:
            print(f"错误: {e}")
            errors.append((sym, name, str(e)))
            time.sleep(0.3)
    print(f"\n[3/3] 写入SQLite (成功{len(all_results)}/失败{len(errors)})...")
    if len(all_results) == 0:
        print("无有效结果，退出")
        return
    results_df = pd.DataFrame(all_results)
    results_df.sort_values('总分', ascending=False, inplace=True)
    results_df.reset_index(drop=True, inplace=True)
    results_df['强弱势排名'] = range(1, len(results_df) + 1)
    conn = sqlite3.connect(DB_PATH)
    results_df.to_sql('hourly_analysis_all', conn, if_exists='replace', index=False)
    vol_df = pd.DataFrame(all_results)
    vol_df.sort_values('成交量', ascending=False, inplace=True)
    vol_df.reset_index(drop=True, inplace=True)
    vol_df.to_sql('hourly_analysis_top40', conn, if_exists='replace', index=False)
    strong3 = results_df.head(3).copy()
    strong3['强弱势排名'] = [1, 2, 3]
    strong3.to_sql('hourly_strong_top3', conn, if_exists='replace', index=False)
    weak3 = results_df.tail(3).copy()
    weak3['强弱势排名'] = [len(results_df)-2, len(results_df)-1, len(results_df)]
    weak3.to_sql('hourly_weak_bottom3', conn, if_exists='replace', index=False)
    conn.commit()
    conn.close()
    print(f"\n数据库已更新: {DB_PATH}")
    # 报告
    print("\n" + "=" * 110)
    print(f"  期货主力前40 - 小时线技术分析报告  ({datetime.now().strftime('%Y-%m-%d %H:%M')})")
    print(f"  成功: {len(all_results)} | 失败: {len(errors)}")
    print("=" * 110)
    hdr = f"{'排名':>4} {'合约':>6} {'名称':<8} {'突破':>6} {'回调':>6} {'异常K':>6} {'共振':>6} {'日内':>6} {'总分':>6}  {'综合判定'}"
    print("\n★ 强势前三")
    print(hdr)
    print("-" * 110)
    for _, rr in strong3.iterrows():
        print(f"{int(rr['强弱势排名']):>4} {rr['symbol']:>6} {rr['name']:<8} "
              f"{rr['突破质量']:>6.1f} {rr['回调强弱']:>6.1f} {rr['异常K线']:>6.1f} "
              f"{rr['多周期共振']:>6.1f} {rr['日内强弱']:>6.1f} {rr['总分']:>6.1f}  {rr['综合判定']}")
    print()
    for _, rr in strong3.iterrows():
        print(f"  {rr['symbol']} ({rr['contract']} {rr['name']}):")
        print(f"    突破: {rr['突破说明']}")
        print(f"    回调: {rr['回调说明']}")
        print(f"    异常K: {rr['异常K说明']}")
        print(f"    共振: {rr['共振说明']}")
        print(f"    日内: {rr['日内说明']}")
        print()
    print("☆ 弱势前三")
    print(hdr)
    print("-" * 110)
    for _, rr in weak3.iterrows():
        print(f"{int(rr['强弱势排名']):>4} {rr['symbol']:>6} {rr['name']:<8} "
              f"{rr['突破质量']:>6.1f} {rr['回调强弱']:>6.1f} {rr['异常K线']:>6.1f} "
              f"{rr['多周期共振']:>6.1f} {rr['日内强弱']:>6.1f} {rr['总分']:>6.1f}  {rr['综合判定']}")
    print()
    for _, rr in weak3.iterrows():
        print(f"  {rr['symbol']} ({rr['contract']} {rr['name']}):")
        print(f"    突破: {rr['突破说明']}")
        print(f"    回调: {rr['回调说明']}")
        print(f"    异常K: {rr['异常K说明']}")
        print(f"    共振: {rr['共振说明']}")
        print(f"    日内: {rr['日内说明']}")
        print()
    if errors:
        print(f"\n⚠ 以下 {len(errors)} 个合约分析失败:")
        for sym, nm, err in errors:
            print(f"  {sym} ({nm}): {err}")
    print(f"\n完成!")

if __name__ == '__main__':
    main()
