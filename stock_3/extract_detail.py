# -*- coding: utf-8 -*-
"""补充提取真实个股成交额排名及涨停个股详情（排除指数/北交所）"""
import json, struct, datetime, os, glob

def read_tdx_day_file(filepath):
    records = []
    try:
        with open(filepath, 'rb') as f:
            while True:
                data = f.read(32)
                if not data or len(data) < 32:
                    break
                # 价格字段为int×100，amount为float（元），vol为int（手）
                date_raw, o, h, l, c = struct.unpack('<IIIII', data[:20])
                amount = struct.unpack('<f', data[20:24])[0]
                vol = struct.unpack('<I', data[24:28])[0]
                year = date_raw // 10000
                month = (date_raw % 10000) // 100
                day = date_raw % 100
                try:
                    records.append({'date': datetime.date(year, month, day),
                                    'open': o/100, 'high': h/100, 'low': l/100,
                                    'close': c/100, 'amount': amount, 'vol': vol})
                except:
                    pass
    except:
        pass
    return records

def is_real_stock(code):
    # 排除指数(sh000xxx/sz399xxx)、北交所(sh8/sz4)、B股(sh9/sz2)、科创板不排除
    if code.startswith('sh000'): return False
    if code.startswith('sz399'): return False
    if code.startswith('sh8'): return False
    if code.startswith('sz4'): return False
    if code.startswith('sh9'): return False
    if code.startswith('sz2'): return False
    return True

latest_date = read_tdx_day_file(r'D:\new_tdx\vipdoc\sh\lday\sh000001.day')[-1]['date']
print('latest:', latest_date)

stocks = []
for pattern in [r'D:\new_tdx\vipdoc\sh\lday\sh6*.day',
                r'D:\new_tdx\vipdoc\sz\lday\sz00*.day',
                r'D:\new_tdx\vipdoc\sz\lday\sz3*.day']:
    for fp in glob.glob(pattern):
        code = os.path.basename(fp).replace('.day', '')
        if not is_real_stock(code):
            continue
        recs = read_tdx_day_file(fp)
        if not recs or recs[-1]['date'] != latest_date:
            continue
        r = recs[-1]
        prev = recs[-2]['close'] if len(recs) > 1 else r['open']
        chg = (r['close'] - prev) / prev * 100 if prev > 0 else 0
        # 前5日均量
        if len(recs) >= 6:
            avg_vol = sum(x['vol'] for x in recs[-6:-1]) / 5
            vol_ratio = r['vol'] / avg_vol if avg_vol > 0 else 1
        else:
            vol_ratio = 1
        stocks.append({'code': code, 'close': r['close'], 'open': r['open'],
                       'high': r['high'], 'low': r['low'], 'prev_close': prev,
                       'change_pct': chg, 'amount': r['amount'], 'vol': r['vol'],
                       'vol_ratio': vol_ratio})

print('total real stocks:', len(stocks))

# 成交额TOP30真实个股
top_amt = sorted(stocks, key=lambda x: x['amount'], reverse=True)[:30]
print('\n=== 真实个股成交额TOP30 ===')
for i, s in enumerate(top_amt, 1):
    print(f"{i}\t{s['code']}\t{round(s['change_pct'],2)}%\t{round(s['amount']/10000)}万\t收{s['close']}")

# 涨停个股（真实）按成交额排
def is_limit(s):
    if s['code'].startswith('sz30') or s['code'].startswith('sh688'):
        return s['change_pct'] >= 19.5
    return s['change_pct'] >= 9.7

lu = [s for s in stocks if is_limit(s)]
lu_by_amt = sorted(lu, key=lambda x: x['amount'], reverse=True)
print(f'\n=== 涨停真实个股 {len(lu)} 只，按成交额排序（前30） ===')
for i, s in enumerate(lu_by_amt[:30], 1):
    print(f"{i}\t{s['code']}\t{round(s['change_pct'],2)}%\t{round(s['amount']/10000)}万\t量比{round(s['vol_ratio'],2)}\t开{s['open']}\t收{s['close']}\t前收{s['prev_close']}")

# 跌停个股
def is_down(s):
    if s['code'].startswith('sz30') or s['code'].startswith('sh688'):
        return s['change_pct'] <= -19.5
    return s['change_pct'] <= -9.7
ld = [s for s in stocks if is_down(s)]
print(f'\n=== 跌停真实个股 {len(ld)} 只 ===')
for s in sorted(ld, key=lambda x: x['change_pct']):
    print(f"{s['code']}\t{round(s['change_pct'],2)}%\t收{s['close']}")

# 大跌股（跌幅<-9%）
big_down = sorted([s for s in stocks if s['change_pct'] <= -9 and not is_down(s)], key=lambda x: x['change_pct'])[:20]
print(f'\n=== 大跌股(跌幅<-9%) 前20 ===')
for s in big_down:
    print(f"{s['code']}\t{round(s['change_pct'],2)}%\t收{s['close']}")

# 保存补充数据
out = {
    'top_amount_real': [{'code': s['code'], 'change_pct': round(s['change_pct'],2),
                         'amount_wan': round(s['amount']/10000), 'close': s['close']} for s in top_amt],
    'limit_up_real': [{'code': s['code'], 'change_pct': round(s['change_pct'],2),
                       'amount_wan': round(s['amount']/10000), 'vol_ratio': round(s['vol_ratio'],2),
                       'open': s['open'], 'close': s['close'], 'prev_close': s['prev_close']} for s in lu_by_amt],
    'limit_down_real': [{'code': s['code'], 'change_pct': round(s['change_pct'],2)} for s in ld],
}
with open(r'D:\work_ai\stock_3\market_detail.json', 'w', encoding='utf-8') as f:
    json.dump(out, f, ensure_ascii=False, indent=2)
print('\nsaved market_detail.json')
