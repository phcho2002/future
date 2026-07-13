"""冒烟测试：验证三个系统的 import 链路 + 品种表读取（不触发网络请求）。
在 VPS 上执行：cd /root/future_vps && venv/bin/python <this_file>
"""
import sys
import json

failures = []

# ---- 1) 共享数据层 future_data.universe.read_symbols ----
try:
    from future_data.universe import read_symbols, build_exchange_map, read_symbols_df
    syms = read_symbols()
    exmap = build_exchange_map()
    df = read_symbols_df()
    assert len(syms) == 40, f"expected 40 symbols, got {len(syms)}"
    assert len(exmap) == 40, f"exchange map size {len(exmap)}"
    assert len(df) == 40, f"df size {len(df)}"
    print(f"[OK] future_data.universe: {len(syms)} symbols, e.g. {syms[0]}")
except Exception as e:
    failures.append(f"future_data.universe: {e!r}")
    print(f"[FAIL] future_data.universe: {e!r}")

# ---- 2) future_1 三推衰竭：load_top40 + engine import 链路 ----
try:
    sys.path.insert(0, "future_1")
    from future_quant.data.universe import load_top40, load_top40_tuples
    rows = load_top40()
    tups = load_top40_tuples()
    assert len(rows) == 40 and len(tups) == 40
    # 核心包 import 链路（不实例化，避免触发 xtquant 连接）
    from future_quant import engine, signals, pushes, indicators, channels, market_state, risk
    from future_quant.data import tqsdk_provider
    from future_quant.backtest import backtester, cache, grid_search
    print(f"[OK] future_1 future_quant: {len(rows)} symbols, import chain intact")
except Exception as e:
    failures.append(f"future_1 future_quant: {e!r}")
    print(f"[FAIL] future_1 future_quant: {e!r}")
finally:
    if "future_1" in sys.path:
        sys.path.remove("future_1")

# ---- 3) future_4 双均线缠绕：data_loader + 入口 import ----
try:
    sys.path.insert(0, "future_4")
    import importlib
    for mod in ["data_loader", "indicators", "signals", "run_scan"]:
        importlib.import_module(mod)
    # future_4 的 read_symbols 走 data_loader（需 config）
    from data_loader import load_config, read_symbols as f4_read
    cfg = load_config("future_4/config.yaml")
    syms4 = f4_read(cfg, cfg["data"]["symbol_table"])
    assert len(syms4) == 40, f"future_4 got {len(syms4)}"
    print(f"[OK] future_4: {len(syms4)} symbols, import chain intact")
except Exception as e:
    failures.append(f"future_4: {e!r}")
    print(f"[FAIL] future_4: {e!r}")
finally:
    if "future_4" in sys.path:
        sys.path.remove("future_4")

# ---- 4) future_6 Renko：data_loader + 入口 import ----
try:
    sys.path.insert(0, "future_6")
    import importlib
    for mod in ["data_loader", "indicators", "renko", "signals", "scanner"]:
        importlib.import_module(mod)
    from data_loader import load_config, read_symbols as f6_read
    cfg = load_config("future_6/config.yaml")
    syms6 = f6_read(cfg)
    assert len(syms6) == 40, f"future_6 got {len(syms6)}"
    print(f"[OK] future_6: {len(syms6)} symbols, import chain intact")
except Exception as e:
    failures.append(f"future_6: {e!r}")
    print(f"[FAIL] future_6: {e!r}")
finally:
    if "future_6" in sys.path:
        sys.path.remove("future_6")

# ---- 5) xt_token.py 凭证已填（非占位）----
try:
    with open("xt_token.py", encoding="utf-8") as f:
        token_src = f.read()
    assert "your_token" not in token_src.lower(), "still placeholder!"
    assert "XT_TOKEN" in token_src, "no XT_TOKEN assignment"
    print("[OK] xt_token.py: real token present (no placeholder)")
except Exception as e:
    failures.append(f"xt_token: {e!r}")
    print(f"[FAIL] xt_token: {e!r}")

print("\n" + "=" * 50)
if failures:
    print(f"SMOKE TEST FAILED: {len(failures)} issue(s)")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
else:
    print("SMOKE TEST PASSED: all systems OK, 40 symbols x3, token filled")
    sys.exit(0)
