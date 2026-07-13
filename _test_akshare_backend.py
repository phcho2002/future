import os, sys
os.environ['FUTURE_DATA_BACKEND'] = 'akshare'
sys.path.insert(0, 'future_2')

from future_data import get_backend, fetch_many
print('Backend:', get_backend())
# Test one symbol
symbols = [('RB0', '螺纹钢', 'shfe')]
data = fetch_many(symbols, period='60', length=200, sleep_s=0, verbose=True)
print('RB0:', len(data.get('RB0', [])) if 'RB0' in data else 'no data')
