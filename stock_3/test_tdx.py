import struct
import datetime
import os
import glob

# Read TDX daily data
f = open(r'D:\new_tdx\vipdoc\sh\lday\sh000001.day', 'rb')
records = []
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
f.close()

for r in records[-5:]:
    print(f"{r['date']}: O={r['open']:.2f} H={r['high']:.2f} L={r['low']:.2f} C={r['close']:.2f} A={r['amount']} V={r['vol']}")
print(f'Total records: {len(records)}')
print(f'Latest date: {records[-1]["date"]}')
