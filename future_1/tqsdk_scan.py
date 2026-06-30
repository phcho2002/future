"""
Scan all futures symbols from futures_data.db using TqSdk via the unified
TqSdkProvider (single batched connection). Each symbol is analyzed with the
strict wedge-reversal engine.

Usage: cd /d/work_ai/future_1 && python tqsdk_scan.py
"""
import sqlite3
from datetime import datetime
from pathlib import Path

from future_quant.core.types import SignalSide
from future_quant.data.tqsdk_provider import TqSdkProvider
from future_quant.engine import QuantEngine

DB_PATH = Path("D:/work_ai/futures_data.db")


def get_top40() -> list[tuple[str, str, str]]:
    conn = sqlite3.connect(str(DB_PATH))
    try:
        cur = conn.cursor()
        cur.execute("SELECT symbol, name, exchange FROM futures_top40 ORDER BY 排名")
        return [(r[0], r[1], r[2]) for r in cur.fetchall()]
    finally:
        conn.close()


def run():
    engine = QuantEngine()
    symbols = get_top40()
    provider = TqSdkProvider(period="15", data_length=200, wait_timeout=25.0)

    print(f"[{datetime.now():%H:%M:%S}] TqSdk 扫描 — {len(symbols)} 品种, 15分钟K线 (单连接批量)")
    print()

    signals_found = []
    errors = []

    # Batched fetch over a single connection.
    klines = provider.fetch_many(symbols, period="15", length=200)

    for sym, name, ex in symbols:
        df = klines.get(sym)
        prefix = f"{sym:6s} {name:12s} ({ex})"
        if df is None or df.empty:
            errors.append((sym, name, "no data"))
            print(f"❌ {prefix}  无数据")
            continue

        try:
            result = engine.analyze_df(df)
            sig = result.signal

            if sig.is_valid and sig.side in (SignalSide.LONG, SignalSide.SHORT):
                side_str = "📈 LONG" if sig.side == SignalSide.LONG else "📉 SHORT"
                lv = sig.levels
                signals_found.append(
                    {
                        "symbol": sym,
                        "name": name,
                        "exchange": ex,
                        "side": sig.side.value,
                        "entry": lv.entry,
                        "stop": lv.stop,
                        "target_1": lv.target_1,
                        "target_2": lv.target_2,
                        "reward_risk": lv.reward_risk,
                        "channel_type": result.channel.channel_type.value,
                        "exhaustion_score": result.push_set.exhaustion_score,
                        "passed_checks": result.push_set.exhaustion_details.get("passed_checks"),
                        "market_state": result.market_state.regime.value,
                        "reason": sig.entry_reason,
                    }
                )
                rr = lv.reward_risk if lv.reward_risk is not None else 0
                print(f"⚠️  {prefix}  {side_str} 入场={lv.entry:.2f} 止损={lv.stop:.2f} RR={rr:.2f}")
            else:
                pushes = len(result.push_set.pushes)
                ch = result.channel.channel_type.value
                ex_sc = result.push_set.exhaustion_score
                ex_str = f"{ex_sc:.0%}" if ex_sc is not None else "N/A"
                print(f"    {prefix}  无信号 pushes={pushes} ch={ch} ex={ex_str} ({sig.entry_reason})")
        except Exception as e:  # noqa: BLE001
            errors.append((sym, name, str(e)))
            print(f"❌ {prefix}  {e}")

    # --- report ---
    print()
    print("=" * 78)
    print(f"  扫描完成  |  信号: {len(signals_found)}  |  错误: {len(errors)}")
    print("=" * 78)

    for i, s in enumerate(signals_found, 1):
        side_cn = "📈 做多" if s["side"] == "long" else "📉 做空"
        esc = f"{s['exhaustion_score']:.0%}" if s["exhaustion_score"] else "N/A"
        rr = s["reward_risk"] if s["reward_risk"] is not None else 0
        print()
        print(f"  ┌── 信号 #{i} ──────────────────────────────────────────────────")
        print(f"  │ {s['symbol']:6s} {s['name']:10s} ({s['exchange']})")
        print(f"  │ 方向: {side_cn}   风险回报: {rr:.2f}   衰竭: {esc} ({s['passed_checks']}/7)")
        print(f"  │ 入场: {s['entry']:>10.2f}  止损: {s['stop']:>10.2f}")
        print(f"  │ 目标1:{s['target_1']:>10.2f}  目标2:{s['target_2']:>10.2f}")
        print(f"  │ 通道: {s['channel_type']}   状态: {s['market_state']}")
        print(f"  └────────────────────────────────────────────────────────────────")

    if errors:
        print("\n错误明细:")
        for sym, name, err in errors:
            print(f"  {sym:6s} {name}: {err}")

    print(f"\n[{datetime.now():%H:%M:%S}] 结束")


if __name__ == "__main__":
    run()
