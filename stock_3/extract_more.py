# -*- coding: utf-8 -*-
import json
d = json.load(open('market_analysis.json', encoding='utf-8'))
print('=== INDEX_5D_SH ===')
for r in d['index']['sh']['prev_5day']:
    print(r['date'], round(r['close'],2), str(r['change'])+'%')
print()
print('=== CONSEC ===')
for c in d['consecutive_up']:
    print(c['code'], c['days'], 'days', 'today', c['latest_change'], 'hist', c['changes'], 'close', c['latest_close'])
print()
print('=== QUALITY filtered (一字/爆量/缩量) ===')
for qa in d['quality_analysis'][:50]:
    feats=[]
    if qa['is_yizi']: feats.append('一字')
    if qa['is_tzi']: feats.append('T字')
    if qa['is_lanban']: feats.append('烂板')
    if qa['is_baoliang']: feats.append('爆量')
    if qa['is_suoliang']: feats.append('缩量')
    if feats:
        print(qa['code'], str(qa['change_pct'])+'%', qa['fengban_time'], 'vr'+str(qa['vol_ratio']), 'amp'+str(qa['amplitude']), ','.join(feats))
