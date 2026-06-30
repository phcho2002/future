"""示例：future_2 Wyckoff 扫描器改用统一入口（消除每品种重连）。

这是 future_2/wyckoff_quant/scanner.py 的"改造后版本"参考。
原始文件未动；实际迁移时按此替换 scanner.py 的 scan_all 主体。
"""

from __future__ import annotations

import sys
from datetime import datetime

# future_2 没有指向 D:/work_ai 的包路径，需先 sys.path
sys.path.insert(0, "D:/work_ai")

from future_data import fetch_many  # 单连接批量拉取
from future_data.universe import read_symbols
from wyckoff_quant.config import WyckoffConfig
from wyckoff_quant.engine import WyckoffEngine


def scan_all(period: str = "15", config: WyckoffConfig | None = None, data_length: int = 200):
    """改造版：一次连接拿全部品种（原来每品种一个 TqApi）。"""
    engine = WyckoffEngine(config=config)
    symbols = read_symbols()  # 内部读 futures_top40

    print(f"[{datetime.now():%H:%M:%S}] 威科夫扫描 — {len(symbols)} 品种, {period}分钟K线")
    print()

    # 关键差异：原 scanner.py 在这里 for 循环里每个品种 new TqApi；
    # 现在一次 fetch_many 全部拿到。
    data = fetch_many(symbols, period=period, length=data_length)

    signals_found = []
    for idx, (sym, name, ex) in enumerate(symbols, 1):
        df = data.get(sym)
        prefix = f"[{idx:02d}/{len(symbols)}] {sym:6s} {name:12s} ({ex})"
        if df is None or df.empty:
            print(f"{prefix}  ❌ 无数据")
            continue
        print(f"{prefix}  ... ", end="", flush=True)
        result = engine.analyze_df(df)
        sig = result.signal
        if sig.is_valid and sig.side in ("long", "short"):
            lv = sig.levels
            signals_found.append({
                "symbol": sym, "name": name, "exchange": ex,
                "side": sig.side, "entry": lv.entry, "stop": lv.stop, "target": lv.target,
                "rsi": sig.rsi_value, "phase": sig.phase.phase,
                "reason": sig.entry_reason, "metadata": sig.metadata,
            })
            side = "📈 LONG" if sig.side == "long" else "📉 SHORT"
            print(f"⚠️  {side} 入场={lv.entry:.2f} 止损={lv.stop:.2f}")
        else:
            print(f"无信号 ({sig.entry_reason})")

    print(f"\n[{datetime.now():%H:%M:%S}] 完成，信号 {len(signals_found)} 个")
    return signals_found


if __name__ == "__main__":
    scan_all(period="15")
