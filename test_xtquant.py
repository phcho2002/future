"""xtquant 行情数据通路自检脚本
==========================
验证 future_data 包（xtquant 后端）能否正常拉取 K 线，分四层：

  1. token 解析 + 初始化（xtdatacenter.set_token + init）
  2. 单品种拉取（fetch_kline）
  3. 批量拉取（fetch_many，单次连接多品种）
  4. 缓存层（get_klines 第二次走 TTL 缓存）

用法：python test_xtquant.py
退出码：0 全部通过；非 0 表示有失败项。
"""
import sys
import time
import traceback

sys.path.insert(0, r"d:\work_ai")


def _ok(msg):
    print(f"  [OK]   {msg}")


def _fail(msg):
    print(f"  [FAIL] {msg}")


def test_init():
    """1. token 解析 + xtdatacenter 初始化。"""
    print("\n[1/4] token 解析 + xtdatacenter 初始化...")
    try:
        from future_data.xtquant_provider import _load_token, _ensure_init

        token = _load_token()
        if not token:
            _fail("token 为空")
            return False
        _ok(f"token 已加载（{token[:8]}...{token[-4:]}）")

        t0 = time.time()
        _ensure_init()  # 幂等，首次 ~10s
        dt = time.time() - t0
        _ok(f"xtdatacenter 初始化完成，耗时 {dt:.1f}s")
        return True
    except Exception as e:
        _fail(f"初始化失败: {e}")
        traceback.print_exc()
        return False


def test_single():
    """2. 单品种拉取（实盘联网）。"""
    print("\n[2/4] 单品种拉取 fetch_kline('RB0','shfe', period='15')...")
    from future_data.provider import fetch_kline
    try:
        t0 = time.time()
        df = fetch_kline("RB0", "shfe", period="15", length=100)
        dt = time.time() - t0
        if df is None or df.empty:
            _fail("返回空 DataFrame")
            return False, df
        # schema 自检
        need = ["datetime", "open", "high", "low", "close", "volume"]
        miss = [c for c in need if c not in df.columns]
        if miss:
            _fail(f"缺列: {miss}；实际列={list(df.columns)}")
            return False, df
        last = df.iloc[-1]
        _ok(f"拉到 {len(df)} 根 15m K 线，耗时 {dt:.1f}s")
        _ok(f"最新: {last['datetime']}  O={last['open']}  H={last['high']}  "
            f"L={last['low']}  C={last['close']}  V={last['volume']}")
        # 合理性：价格应为正数且 high>=low
        if last["close"] <= 0:
            _fail(f"最新价异常: {last['close']}")
            return False, df
        if not (last["low"] <= last["close"] <= last["high"]):
            _fail(f"OHLC 关系异常: L={last['low']} C={last['close']} H={last['high']}")
            return False, df
        _ok("数据合理性校验通过（价格>0、low<=close<=high）")
        return True, df
    except Exception as e:
        _fail(f"单品种拉取失败: {e}")
        traceback.print_exc()
        return False, None


def test_batch():
    """3. 批量拉取（单次连接多品种）。"""
    print("\n[3/4] 批量拉取 fetch_many（3 个品种，单次连接）...")
    from future_data.provider import fetch_many
    symbols = [
        ("RB0", "螺纹钢", "shfe"),
        ("AU0", "黄金", "shfe"),
        ("IF0", "沪深300", "cffex"),
    ]
    try:
        t0 = time.time()
        out = fetch_many(symbols, period="15", length=100)
        dt = time.time() - t0
        got = set(out.keys())
        want = {s[0] for s in symbols}
        missing = want - got
        if missing:
            _fail(f"缺品种: {missing}（仅拿到 {got}），耗时 {dt:.1f}s")
            return False, out
        _ok(f"3/3 品种全部成功，耗时 {dt:.1f}s（单次连接）")
        for sym, df in out.items():
            last = df.iloc[-1]
            _ok(f"  {sym}: {len(df)} 根, 最新 C={last['close']} @ {last['datetime']}")
        return True, out
    except Exception as e:
        _fail(f"批量拉取失败: {e}")
        traceback.print_exc()
        return False, None


def test_cache_layer():
    """4. 缓存层 get_klines（首次联网，第二次命中缓存）。"""
    print("\n[4/4] 缓存层 get_klines（先联网，再读缓存）...")
    try:
        from future_data import get_klines, clear_cache
    except Exception as e:
        _fail(f"无法导入 get_klines: {e}")
        return False

    sym, exch, period = "MA0", "czce", "15"
    try:
        clear_cache(symbol=sym, period=period)  # 清掉旧缓存确保测首次
    except Exception:
        pass

    try:
        t0 = time.time()
        df1 = get_klines(sym, exch, period=period, length=100)
        dt1 = time.time() - t0
        if df1 is None or df1.empty:
            _fail(f"首次拉取为空（{sym}）")
            return False
        _ok(f"首次（联网）: {len(df1)} 根，耗时 {dt1:.1f}s")

        t0 = time.time()
        df2 = get_klines(sym, exch, period=period, length=100)
        dt2 = time.time() - t0
        if df2 is None or df2.empty:
            _fail(f"第二次读取为空（{sym}）")
            return False
        # 缓存命中应明显更快
        faster = dt2 < dt1
        tag = "缓存命中" if faster else "疑似未命中"
        _ok(f"二次（缓存）: {len(df2)} 根，耗时 {dt2:.2f}s —— {tag}")
        if len(df1) != len(df2):
            _fail(f"两次行数不一致: {len(df1)} vs {len(df2)}")
            return False
        _ok("两次返回行数一致")
        return True
    except Exception as e:
        _fail(f"缓存层测试失败: {e}")
        traceback.print_exc()
        return False


def main():
    print("=" * 70)
    print("  xtquant 行情数据通路自检")
    print("=" * 70)
    results = {
        "初始化": test_init(),
        "单品种拉取": test_single()[0],
        "批量拉取": test_batch()[0],
        "缓存层": test_cache_layer(),
    }
    print("\n" + "=" * 70)
    print("  自检结果汇总")
    print("=" * 70)
    all_pass = True
    for name, ok in results.items():
        flag = "✓ PASS" if ok else "✗ FAIL"
        print(f"  {name:<10} {flag}")
        all_pass = all_pass and ok
    print("=" * 70)
    print("  总体: " + ("全部通过 ✓" if all_pass else "存在失败项 ✗"))
    sys.exit(0 if all_pass else 1)


if __name__ == "__main__":
    main()
