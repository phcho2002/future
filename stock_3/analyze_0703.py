# -*- coding: utf-8 -*-
"""7.3复盘专项数据提取：直接从通达信日线读取，排除指数，输出完整统计"""
import struct, datetime, os, glob, json
from collections import defaultdict

def read_tdx_day_file(filepath):
    """通达信日线：日期(int) / OHLC(int÷100) / 成交额(float,元) / 成交量(int,股) / 保留(int)"""
    records = []
    try:
        with open(filepath, 'rb') as f:
            while True:
                data = f.read(32)
                if not data or len(data) < 32:
                    break
                date_raw, open_p, high_p, low_p, close_p, amount, vol, reserved = struct.unpack('<IIIIIfII', data)
                year = date_raw // 10000
                month = (date_raw % 10000) // 100
                day = date_raw % 100
                try:
                    date = datetime.date(year, month, day)
                    records.append({'date': date,'open': open_p/100,'high': high_p/100,
                                    'low': low_p/100,'close': close_p/100,'amount': amount,'vol': vol})
                except:
                    pass
    except:
        pass
    return records

def is_real_stock(code):
    # 排除指数（sh000*, sz399*）
    if code.startswith('sh000'): return False
    if code.startswith('sz399'): return False
    return True

def main():
    latest = read_tdx_day_file(r'D:\new_tdx\vipdoc\sh\lday\sh000001.day')[-1]['date']
    print('最新日期:', latest)
    stocks = []
    patterns = [(r'D:\new_tdx\vipdoc\sh\lday\sh6*.day','sh'),
                (r'D:\new_tdx\vipdoc\sz\lday\sz00*.day','sz'),
                (r'D:\new_tdx\vipdoc\sz\lday\sz30*.day','cy'),
                (r'D:\new_tdx\vipdoc\sh\lday\sh688*.day','kcb')]
    seen=set()
    for pat,mkt in patterns:
        for fp in glob.glob(pat):
            code=os.path.basename(fp).replace('.day','')
            if code in seen: continue
            seen.add(code)
            if not is_real_stock(code): continue
            recs=read_tdx_day_file(fp)
            if not recs or recs[-1]['date']!=latest: continue
            r=recs[-1]; prev=recs[-2]['close'] if len(recs)>1 else r['open']
            chg=(r['close']-prev)/prev*100 if prev>0 else 0
            lim=19.5 if (mkt=='cy' or code.startswith('sh688')) else 9.5
            # 量比：今日量 / 前5日平均量
            if len(recs)>=6:
                avg5=sum(h['vol'] for h in recs[-6:-1])/5
                vr=r['vol']/avg5 if avg5>0 else 1
            else:
                vr=1
            amp=(r['high']-r['low'])/prev*100 if prev>0 else 0
            stocks.append({'code':code,'mkt':mkt,'open':r['open'],'high':r['high'],'low':r['low'],
                           'close':r['close'],'prev':prev,'chg':round(chg,2),'amount':r['amount'],
                           'vol':r['vol'],'vr':round(vr,2),'amp':round(amp,2),'lim':lim,
                           'hist':recs[-20:]})
    print('真实个股数:', len(stocks))

    # 分类
    lim_up=[s for s in stocks if s['chg']>=s['lim']]
    lim_down=[s for s in stocks if s['chg']<=-s['lim']]
    strong_up=[s for s in stocks if s['chg']>=5]
    strong_down=[s for s in stocks if s['chg']<=-5]
    up=[s for s in stocks if s['chg']>0]
    down=[s for s in stocks if s['chg']<0]
    flat=[s for s in stocks if s['chg']==0]

    print('\n=== 涨跌统计 ===')
    print(f'涨停:{len(lim_up)} 跌停:{len(lim_down)} 涨幅>5%:{len(strong_up)} 跌幅>5%:{len(strong_down)}')
    print(f'上涨:{len(up)} 下跌:{len(down)} 平:{len(flat)} 上涨比例:{len(up)/len(stocks)*100:.1f}%')

    # 连板梯队
    def consec(s):
        h=s['hist']; chgs=[]
        for i in range(1,len(h)):
            chgs.append((h[i]['close']-h[i-1]['close'])/h[i-1]['close']*100 if h[i-1]['close']>0 else 0)
        days=0
        for i in range(len(chgs)-1,-1,-1):
            if chgs[i]>=s['lim']-0.5: days+=1
            else: break
        return days, chgs[-days:] if days>0 else []
    for s in stocks:
        s['days'], s['daily']=consec(s)
    consec_up=[s for s in stocks if s['days']>=2]
    consec_up.sort(key=lambda x:(-x['days'], -x['chg']))
    max_h=max([s['days'] for s in consec_up],default=0)
    print(f'\n=== 连板梯队 (最高{max_h}板) ===')
    for s in consec_up:
        print(f"  {s['code']}({s['mkt']}) {s['days']}板 今{s['chg']:+}% 价{s['close']:.2f} 量比{s['vr']} 振幅{s['amp']}% 历史{[round(c,1) for c in s['daily']]}")

    # 涨停质量
    print(f'\n=== 涨停质量分析 (共{len(lim_up)}只) ===')
    for s in sorted(lim_up,key=lambda x:-x['amount'])[:40]:
        feat=[]
        # 一字：开盘=收盘=涨停
        if abs(s['open']-s['close'])<0.01 and s['close']>=s['prev']*1.095: feat.append('一字')
        elif s['open']>=s['prev']*1.095 and s['low']<s['open'] and s['close']>=s['prev']*1.095: feat.append('T字')
        if s['amp']>5 and s['close']>=s['prev']*1.095: feat.append('烂板')
        if s['vr']>2: feat.append('爆量')
        if s['vr']<0.7: feat.append('缩量')
        print(f"  {s['code']}({s['mkt']}) +{s['chg']}% 价{s['close']:.2f} 成交{s['amount']/1e8:.2f}亿 量比{s['vr']} 振幅{s['amp']}% {','.join(feat) if feat else '正常'}")

    # 真实个股成交额TOP30
    print(f'\n=== 真实个股成交额TOP30 (亿元) ===')
    for i,s in enumerate(sorted(stocks,key=lambda x:-x['amount'])[:30],1):
        print(f"  {i}. {s['code']}({s['mkt']}) {s['chg']:+.2f}% 价{s['close']:.2f} 成交{s['amount']/1e8:.2f}亿")

    # 涨停成交额TOP15
    print(f'\n=== 涨停股成交额TOP15 (亿元) ===')
    for i,s in enumerate(sorted(lim_up,key=lambda x:-x['amount'])[:15],1):
        print(f"  {i}. {s['code']}({s['mkt']}) +{s['chg']}% 成交{s['amount']/1e8:.2f}亿 量比{s['vr']}")

    # 跌停股
    print(f'\n=== 跌停股 ({len(lim_down)}只, 亿元) ===')
    for s in sorted(lim_down,key=lambda x:x['chg']):
        print(f"  {s['code']}({s['mkt']}) {s['chg']:.2f}% 成交{s['amount']/1e8:.2f}亿")

    # 昨日连板今日表现（断板分析）- 用昨日数据找昨日连板
    # 昨日 = latest前一天有数据的
    prev_date=None
    for s in stocks:
        h=s['hist']
        if len(h)>=2 and h[-2]['date']!=latest:
            prev_date=h[-2]['date']; break
    print(f'\n=== 断板分析 (昨日{prev_date}连板股今日表现) ===')
    if prev_date:
        yest_consec=[]
        for s in stocks:
            h=s['hist']
            if len(h)<3: continue
            # 找昨日对应index
            idx=None
            for j in range(len(h)-1,-1,-1):
                if h[j]['date']==prev_date: idx=j; break
            if idx is None or idx<1: continue
            # 昨日及之前连板数
            chgs=[]
            for i in range(1,idx+1):
                chgs.append((h[i]['close']-h[i-1]['close'])/h[i-1]['close']*100 if h[i-1]['close']>0 else 0)
            days=0
            for i in range(len(chgs)-1,-1,-1):
                if chgs[i]>=s['lim']-0.5: days+=1
                else: break
            if days>=2:
                yest_close=h[idx]['close']
                today=s['close']
                ret=(today-yest_close)/yest_close*100
                yest_consec.append({'code':s['code'],'mkt':s['mkt'],'ydays':days,'ret':round(ret,2),'chg':s['chg']})
        yest_consec.sort(key=lambda x:-x['ydays'])
        succ=[y for y in yest_consec if y['chg']>=19.5 or (y['mkt']!='cy' and not y['code'].startswith('sh688') and y['chg']>=9.5)]
        for y in yest_consec:
            mark='✅晋级' if y in succ else '断板'
            print(f"  昨{y['ydays']}板 {y['code']}({y['mkt']}) 今日{y['chg']:+.2f}% [{mark}]")
        if yest_consec:
            avg=sum(y['ret'] for y in yest_consec)/len(yest_consec)
            dn=[y for y in yest_consec if y['ret']<-5]
            print(f'  → 共{len(yest_consec)}只昨日连板, 平均{avg:+.2f}%, 跌幅>5%:{len(dn)}只, 晋级{len(succ)}只')

    # 指数近6日
    print('\n=== 指数近6日 ===')
    for name,fp in [('上证',r'D:\new_tdx\vipdoc\sh\lday\sh000001.day'),
                    ('深成',r'D:\new_tdx\vipdoc\sz\lday\sz399001.day'),
                    ('创业板',r'D:\new_tdx\vipdoc\sz\lday\sz399006.day')]:
        r=read_tdx_day_file(fp)
        print(f'  {name}:')
        for i in range(-6,0):
            if abs(i)<=len(r):
                cur=r[i]; pv=r[i-1]
                chg=(cur['close']-pv['close'])/pv['close']*100
                print(f"    {cur['date']} 开{cur['open']:.2f} 高{cur['high']:.2f} 低{cur['low']:.2f} 收{cur['close']:.2f} {chg:+.2f}%")

if __name__=='__main__':
    main()
