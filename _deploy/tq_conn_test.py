"""tqsdk 连通性最小测试：单品种拉少量 K 线，验证账号登录 + 数据返回。
成功后立刻退出，避免长连接。在 VPS: cd /root/future_vps && venv/bin/python <this>
"""
import sys
import time
from future_data.provider import fetch_kline

print("=" * 55)
print("tqsdk 连通性测试：拉取 RB0 (螺纹钢主力) 15m x 50 根")
print("=" * 55)

t0 = time.time()
try:
    df = fetch_kline("RB0", "shfe", period="15", length=50, wait_timeout=30.0)
    elapsed = time.time() - t0
    if df is None or df.empty:
        print(f"[FAIL] {elapsed:.1f}s — 返回空数据")
        sys.exit(1)
    print(f"[OK] {elapsed:.1f}s — 拿到 {len(df)} 根 K 线")
    print(f"  首根: {df.iloc[0]['datetime']}  O={df.iloc[0]['open']:.0f} H={df.iloc[0]['high']:.0f}")
    print(f"  末根: {df.iloc[-1]['datetime']}  C={df.iloc[-1]['close']:.0f} V={df.iloc[-1]['volume']:.0f}")
    print("\n>>> tqsdk 账号可用、数据通道正常 <<<")
    sys.exit(0)
except Exception as e:
    elapsed = time.time() - t0
    print(f"[FAIL] {elapsed:.1f}s — {type(e).__name__}: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
