import struct
import datetime
import os
import glob
import json
from collections import defaultdict

def read_tdx_day_file(filepath):
    """读取通达信日线数据文件"""
    records = []
    try:
        with open(filepath, 'rb') as f:
            while True:
                data = f.read(32)
                if not data or len(data) < 32:
                    break
                date_raw, open_p, high_p, low_p, close_p, amount, vol, reserved = struct.unpack('<IIIIIIII', data)
                year = date_raw // 10000
                month = (date_raw % 10000) // 100
                day = date_raw % 100
                try:
                    date = datetime.date(year, month, day)
                    records.append({
                        'date': date,
                        'open': open_p / 100,
                        'high': high_p / 100,
                        'low': low_p / 100,
                        'close': close_p / 100,
                        'amount': amount,
                        'vol': vol
                    })
                except:
                    pass
    except Exception as e:
        pass
    return records

def get_latest_date():
    """获取最新数据日期"""
    records = read_tdx_day_file(r'D:\new_tdx\vipdoc\sh\lday\sh000001.day')
    if records:
        return records[-1]['date']
    return None

def read_all_stocks_latest(latest_date, min_vol=1000000):
    """读取所有股票最新一天的数据"""
    stocks = []
    
    # 上海主板
    sh_files = glob.glob(r'D:\new_tdx\vipdoc\sh\lday\sh6*.day')
    for filepath in sh_files:
        code = os.path.basename(filepath).replace('.day', '')
        records = read_tdx_day_file(filepath)
        if records and records[-1]['date'] == latest_date:
            r = records[-1]
            prev_close = records[-2]['close'] if len(records) > 1 else r['open']
            change_pct = (r['close'] - prev_close) / prev_close * 100 if prev_close > 0 else 0
            stocks.append({
                'code': code,
                'market': 'sh',
                'date': r['date'],
                'open': r['open'],
                'high': r['high'],
                'low': r['low'],
                'close': r['close'],
                'prev_close': prev_close,
                'change_pct': change_pct,
                'amount': r['amount'],
                'vol': r['vol'],
                'history': records[-15:]  # 最近15天数据
            })
    
    # 深圳主板
    sz_files = glob.glob(r'D:\new_tdx\vipdoc\sz\lday\sz00*.day')
    for filepath in sz_files:
        code = os.path.basename(filepath).replace('.day', '')
        records = read_tdx_day_file(filepath)
        if records and records[-1]['date'] == latest_date:
            r = records[-1]
            prev_close = records[-2]['close'] if len(records) > 1 else r['open']
            change_pct = (r['close'] - prev_close) / prev_close * 100 if prev_close > 0 else 0
            stocks.append({
                'code': code,
                'market': 'sz',
                'date': r['date'],
                'open': r['open'],
                'high': r['high'],
                'low': r['low'],
                'close': r['close'],
                'prev_close': prev_close,
                'change_pct': change_pct,
                'amount': r['amount'],
                'vol': r['vol'],
                'history': records[-15:]
            })
    
    # 创业板
    cy_files = glob.glob(r'D:\new_tdx\vipdoc\sz\lday\sz3*.day')
    for filepath in cy_files:
        code = os.path.basename(filepath).replace('.day', '')
        records = read_tdx_day_file(filepath)
        if records and records[-1]['date'] == latest_date:
            r = records[-1]
            prev_close = records[-2]['close'] if len(records) > 1 else r['open']
            change_pct = (r['close'] - prev_close) / prev_close * 100 if prev_close > 0 else 0
            stocks.append({
                'code': code,
                'market': 'cy',
                'date': r['date'],
                'open': r['open'],
                'high': r['high'],
                'low': r['low'],
                'close': r['close'],
                'prev_close': prev_close,
                'change_pct': change_pct,
                'amount': r['amount'],
                'vol': r['vol'],
                'history': records[-15:]
            })
    
    return stocks

def analyze_consecutive_limit_up(stocks):
    """分析连板股"""
    consecutive_up = []
    for s in stocks:
        hist = s['history']
        if len(hist) < 2:
            continue
        
        # 计算每天的涨跌幅
        daily_changes = []
        for i in range(1, len(hist)):
            prev = hist[i-1]['close']
            curr = hist[i]['close']
            change = (curr - prev) / prev * 100 if prev > 0 else 0
            daily_changes.append(change)
        
        # 检查末尾连续涨停/大涨
        limit_threshold = 19.5 if s['market'] == 'cy' or s['code'].startswith('sh688') else 9.5
        
        consecutive_days = 0
        for i in range(len(daily_changes) - 1, -1, -1):
            if daily_changes[i] >= limit_threshold:
                consecutive_days += 1
            else:
                break
        
        if consecutive_days >= 2:
            recent_changes = daily_changes[-consecutive_days:]
            consecutive_up.append({
                'code': s['code'],
                'market': s['market'],
                'days': consecutive_days,
                'changes': [round(c, 2) for c in recent_changes],
                'latest_close': s['close'],
                'latest_change': round(s['change_pct'], 2),
                'amount': s['amount'],
                'vol': s['vol']
            })
    
    # 按连板天数排序
    consecutive_up.sort(key=lambda x: (-x['days'], -x['latest_change']))
    return consecutive_up

def analyze_limit_up_quality(stocks, limit_up_codes):
    """分析涨停质量"""
    quality_analysis = []
    
    for s in stocks:
        if s['code'] not in limit_up_codes:
            continue
        
        r = s['history'][-1]
        prev = s['history'][-2] if len(s['history']) > 1 else r
        
        # 封板质量指标
        open_price = r['open']
        high = r['high']
        low = r['low']
        close = r['close']
        prev_close = prev['close']
        
        # 是否一字板 (开盘即涨停且全天未打开)
        is_yizi = abs(open_price - close) < 0.01 and close >= prev_close * 1.095
        
        # 是否T字板 (开盘涨停后打开再回封)
        is_tzi = open_price >= prev_close * 1.095 and low < open_price and close >= prev_close * 1.095
        
        # 是否烂板 (多次打开)
        # 简化判断：振幅较大但收盘涨停
        amplitude = (high - low) / prev_close * 100
        is_lanban = amplitude > 5 and close >= prev_close * 1.095 and not is_yizi
        
        # 是否放量 (成交量较前5日平均放大)
        if len(s['history']) >= 6:
            avg_vol = sum(h['vol'] for h in s['history'][-6:-1]) / 5
            vol_ratio = r['vol'] / avg_vol if avg_vol > 0 else 1
        else:
            vol_ratio = 1
        
        is_baoliang = vol_ratio > 2
        is_suoliang = vol_ratio < 0.7
        
        # 封板时间估算 (简化：根据开盘判断)
        if is_yizi:
            fengban_time = "开盘"
        elif open_price >= prev_close * 1.095:
            fengban_time = "早盘"
        elif close >= prev_close * 1.095:
            fengban_time = "盘中"
        else:
            fengban_time = "未知"
        
        quality_analysis.append({
            'code': s['code'],
            'market': s['market'],
            'change_pct': round(s['change_pct'], 2),
            'is_yizi': is_yizi,
            'is_tzi': is_tzi,
            'is_lanban': is_lanban,
            'is_baoliang': is_baoliang,
            'is_suoliang': is_suoliang,
            'vol_ratio': round(vol_ratio, 2),
            'amplitude': round(amplitude, 2),
            'fengban_time': fengban_time,
            'amount': s['amount'],
            'vol': s['vol']
        })
    
    return quality_analysis

def analyze_market():
    """分析市场数据"""
    latest_date = get_latest_date()
    print(f"最新数据日期: {latest_date}")
    
    if not latest_date:
        print("无法获取最新日期")
        return
    
    print("正在读取所有股票数据...")
    stocks = read_all_stocks_latest(latest_date)
    print(f"共读取 {len(stocks)} 只股票")
    
    # 计算指数数据
    sh_index = read_tdx_day_file(r'D:\new_tdx\vipdoc\sh\lday\sh000001.day')
    sz_index = read_tdx_day_file(r'D:\new_tdx\vipdoc\sz\lday\sz399001.day')
    cy_index = read_tdx_day_file(r'D:\new_tdx\vipdoc\sz\lday\sz399006.day')
    
    # 涨停股 (>=9.5% for main board, >=19.5% for cy/科创)
    limit_up = []
    for s in stocks:
        threshold = 19.5 if s['market'] == 'cy' or s['code'].startswith('sh688') else 9.5
        if s['change_pct'] >= threshold:
            limit_up.append(s)
    limit_up.sort(key=lambda x: x['change_pct'], reverse=True)
    
    # 跌停股
    limit_down = []
    for s in stocks:
        threshold = -19.5 if s['market'] == 'cy' or s['code'].startswith('sh688') else -9.5
        if s['change_pct'] <= threshold:
            limit_down.append(s)
    
    # 涨幅>5%的股票
    strong_up = [s for s in stocks if s['change_pct'] >= 5]
    
    # 跌幅>5%的股票
    strong_down = [s for s in stocks if s['change_pct'] <= -5]
    
    # 成交额前50
    top_amount = sorted(stocks, key=lambda x: x['amount'], reverse=True)[:50]
    
    # 连板分析
    consecutive_up = analyze_consecutive_limit_up(stocks)
    
    # 涨停质量分析
    limit_up_codes = {s['code'] for s in limit_up}
    quality_analysis = analyze_limit_up_quality(stocks, limit_up_codes)
    
    # 市场高度统计
    max_height = max([c['days'] for c in consecutive_up]) if consecutive_up else 0
    
    # 指数5日走势
    sh_5day = []
    if sh_index and len(sh_index) >= 5:
        for i in range(-5, 0):
            r = sh_index[i]
            prev = sh_index[i-1] if i > -len(sh_index) else sh_index[0]
            change = (r['close'] - prev['close']) / prev['close'] * 100 if prev['close'] > 0 else 0
            sh_5day.append({
                'date': r['date'].strftime('%m-%d'),
                'close': r['close'],
                'change': round(change, 2)
            })
    
    result = {
        'date': str(latest_date),
        'index': {
            'sh': {
                'latest': {
                    'open': sh_index[-1]['open'],
                    'high': sh_index[-1]['high'],
                    'low': sh_index[-1]['low'],
                    'close': sh_index[-1]['close'],
                    'change_pct': round((sh_index[-1]['close'] - sh_index[-2]['close']) / sh_index[-2]['close'] * 100, 2) if len(sh_index) > 1 else 0
                } if sh_index else None,
                'prev_5day': sh_5day
            },
            'sz': {
                'latest': {
                    'open': sz_index[-1]['open'],
                    'high': sz_index[-1]['high'],
                    'low': sz_index[-1]['low'],
                    'close': sz_index[-1]['close'],
                    'change_pct': round((sz_index[-1]['close'] - sz_index[-2]['close']) / sz_index[-2]['close'] * 100, 2) if len(sz_index) > 1 else 0
                } if sz_index else None
            },
            'cy': {
                'latest': {
                    'open': cy_index[-1]['open'],
                    'high': cy_index[-1]['high'],
                    'low': cy_index[-1]['low'],
                    'close': cy_index[-1]['close'],
                    'change_pct': round((cy_index[-1]['close'] - cy_index[-2]['close']) / cy_index[-2]['close'] * 100, 2) if len(cy_index) > 1 else 0
                } if cy_index else None
            }
        },
        'statistics': {
            'total_stocks': len(stocks),
            'limit_up_count': len(limit_up),
            'limit_down_count': len(limit_down),
            'strong_up_count': len(strong_up),
            'strong_down_count': len(strong_down),
            'up_ratio': round(len([s for s in stocks if s['change_pct'] > 0]) / len(stocks) * 100, 1) if stocks else 0,
            'max_height': max_height
        },
        'limit_up_stocks': [
            {
                'code': s['code'],
                'change_pct': round(s['change_pct'], 2),
                'amount': s['amount'],
                'vol': s['vol'],
                'open': s['open'],
                'close': s['close'],
                'prev_close': s['prev_close']
            } for s in limit_up[:50]
        ],
        'limit_down_stocks': [
            {
                'code': s['code'],
                'change_pct': round(s['change_pct'], 2)
            } for s in limit_down[:30]
        ],
        'top_amount_stocks': [
            {
                'code': s['code'],
                'change_pct': round(s['change_pct'], 2),
                'amount': s['amount']
            } for s in top_amount[:30]
        ],
        'consecutive_up': consecutive_up[:30],
        'quality_analysis': quality_analysis[:50]
    }
    
    # 保存分析结果
    with open(r'D:\work_ai\stock_3\market_analysis.json', 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2, default=str)
    
    print(f"\n=== 市场概况 ({latest_date}) ===")
    print(f"上证指数: {result['index']['sh']['latest']['close']:.2f} ({result['index']['sh']['latest']['change_pct']:+.2f}%)")
    print(f"深证成指: {result['index']['sz']['latest']['close']:.2f} ({result['index']['sz']['latest']['change_pct']:+.2f}%)")
    print(f"创业板指: {result['index']['cy']['latest']['close']:.2f} ({result['index']['cy']['latest']['change_pct']:+.2f}%)")
    print(f"\n涨停家数: {result['statistics']['limit_up_count']}")
    print(f"跌停家数: {result['statistics']['limit_down_count']}")
    print(f"涨幅>5%: {result['statistics']['strong_up_count']}")
    print(f"跌幅>5%: {result['statistics']['strong_down_count']}")
    print(f"上涨比例: {result['statistics']['up_ratio']:.1f}%")
    print(f"最高连板: {result['statistics']['max_height']}板")
    
    print(f"\n=== 连板梯队 ===")
    for cu in consecutive_up[:15]:
        print(f"  {cu['code']}: {cu['days']}连板, 今日+{cu['latest_change']}%, 历史: {cu['changes']}")
    
    print(f"\n=== 涨停质量分析 (前20) ===")
    for qa in quality_analysis[:20]:
        features = []
        if qa['is_yizi']: features.append('一字')
        if qa['is_tzi']: features.append('T字')
        if qa['is_lanban']: features.append('烂板')
        if qa['is_baoliang']: features.append('爆量')
        if qa['is_suoliang']: features.append('缩量')
        print(f"  {qa['code']}: +{qa['change_pct']}% | {qa['fengban_time']}封板 | 量比{qa['vol_ratio']} | 振幅{qa['amplitude']}% | {', '.join(features) if features else '正常'}")
    
    print(f"\n=== 成交额TOP20 ===")
    for s in top_amount[:20]:
        print(f"  {s['code']}: 成交额{s['amount']/10000:.0f}万, 涨幅{s['change_pct']:+.2f}%")
    
    return result

if __name__ == '__main__':
    analyze_market()
