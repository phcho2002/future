"""
TqSdk 15-min scan: rank top 5 signal proximity scores, output long/short bias.
Designed for cron every 15 min during trading hours.

Uses the unified TqSdkProvider for symbol construction and batched fetch.
"""
import os
from datetime import datetime, time as dtime

from future_quant.core.types import ChannelType, SignalSide, TrendDirection
from future_quant.data.tqsdk_provider import TqSdkProvider
from future_quant.data.universe import load_top40_tuples
from future_quant.engine import QuantEngine

PERIOD = "15"  # minute K-line
DATA_LENGTH = 200
TOP_N = 5


def get_symbols() -> list[tuple[str, str, str]]:
    return load_top40_tuples()


def signal_proximity_score(result) -> dict:
    """Compute a 0-100 score indicating how close a symbol is to a valid
    wedge reversal signal. Higher = closer to triggering."""
    sig = result.signal
    ms = result.market_state
    ch = result.channel
    ps = result.push_set

    pushes = len(ps.pushes)
    ex_score = ps.exhaustion_score or 0.0
    ch_type = ch.channel_type

    # 1. Push count (0-30 points)
    push_score = min(pushes * 10, 30)

    # 2. Exhaustion score (0-30 points)
    ex_score_pts = ex_score * 30

    # 3. Channel type (0-25 points)
    wedge_types = {
        ChannelType.CONVERGING_WEDGE: 25,
        ChannelType.EXPANDING_TRIANGLE: 22,
        ChannelType.PARABOLIC_WEDGE: 25,
        ChannelType.THREE_PUSH_NON_WEDGE: 18,
        ChannelType.PARALLEL: 8,
        ChannelType.UNKNOWN: 3,
    }
    ch_pts = wedge_types.get(ch_type, 3)

    # 4. Market state allowance (0-15 points)
    state_pts = 15 if ms.allow_wedge_reversal else 5

    total = push_score + ex_score_pts + ch_pts + state_pts

    # Determine direction bias
    if sig.is_valid and sig.side in (SignalSide.LONG, SignalSide.SHORT):
        bias = "LONG" if sig.side == SignalSide.LONG else "SHORT"
    elif pushes >= 3 and ps.exhaustion_score and ps.exhaustion_score >= 0.50:
        third = ps.pushes[-1]
        bias = "SHORT" if third.direction == TrendDirection.BULL else "LONG"
    elif pushes >= 2:
        last_push = ps.pushes[-1]
        bias = "BULL" if last_push.direction == TrendDirection.BULL else "BEAR"
    else:
        bias = "NONE"

    return {
        "total": round(total, 1),
        "push_score": push_score,
        "exhaustion_pts": round(ex_score_pts, 1),
        "channel_pts": ch_pts,
        "state_pts": state_pts,
        "pushes": pushes,
        "exhaustion": round(ex_score, 3),
        "channel": ch_type.value,
        "bias": bias,
        "is_signal": sig.is_valid,
        "signal_side": sig.side.value if sig.is_valid else "none",
        "entry_reason": sig.entry_reason or "",
    }


def analyze_klines(engine: QuantEngine, klines: dict) -> list[dict]:
    scores = []
    for sym, df in klines.items():
        if df is None or df.empty:
            continue
        result = engine.analyze_df(df)
        score = signal_proximity_score(result)
        score["symbol"] = sym
        score["price"] = round(float(df.iloc[-1]["close"]), 2)
        scores.append(score)
    return scores


def format_feishu(top5: list[dict], now_str: str) -> str:
    lines = [
        f"📊 期货楔形反转信号 TOP5",
        f"🕐 {now_str}  |  15分钟K线  |  TqSdk",
        "",
    ]
    for i, s in enumerate(top5, 1):
        if s["bias"] == "LONG" or (s["bias"] == "BULL" and s["pushes"] >= 2):
            emoji = "🟢"
            dir_cn = "偏多"
        elif s["bias"] == "SHORT" or s["bias"] == "BEAR":
            emoji = "🔴"
            dir_cn = "偏空"
        else:
            emoji = "⚪"
            dir_cn = "中性"
        flag = "⚠️ SIGNAL!" if s["is_signal"] else ""
        lines.append(
            f"{i}. {emoji} {s['symbol']} {s.get('name', '')}  "
            f"评分:{s['total']}  {dir_cn}  {flag}"
        )
        lines.append(
            f"   价格:{s['price']}  推数:{s['pushes']}  "
            f"衰竭:{s['exhaustion']:.0%}  通道:{s['channel']}"
        )
        lines.append("")
    lines.append("—" * 30)
    lines.append("共扫描40品种，交易时段每15分钟自动更新")
    return "\n".join(lines)


def fmt_console(top5: list[dict], errors: int, now_str: str) -> str:
    lines = [
        "=" * 60,
        f"  期货楔形反转信号 TOP{TOP_N}  |  {now_str}",
        "=" * 60,
    ]
    for i, s in enumerate(top5, 1):
        flag = " ⚠️ SIGNAL!" if s["is_signal"] else ""
        lines.append(
            f"  #{i} {s['symbol']:6s}  评分:{s['total']:5.1f}  "
            f"方向:{s['bias']:6s}{flag}"
        )
        lines.append(
            f"      价格:{s['price']:>8.2f}  推数:{s['pushes']}  "
            f"衰竭:{s['exhaustion']:.0%}  通道:{s['channel']}"
        )
    lines.append(f"  — 错误:{errors}  /  共40品种")
    return "\n".join(lines)


def is_trading_time() -> bool:
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    t = now.hour * 100 + now.minute
    return (900 <= t <= 1130) or (1330 <= t <= 1500) or (2100 <= t <= 2300)


def is_cron_mode() -> bool:
    return "CRON_JOB_ID" in os.environ


def run():
    now = datetime.now()
    now_str = now.strftime("%Y-%m-%d %H:%M")
    cron_mode = is_cron_mode()

    if not is_trading_time():
        if not cron_mode:
            print(f"[{now_str}] 非交易时段，跳过扫描")
        return

    symbols = get_symbols()
    engine = QuantEngine()
    provider = TqSdkProvider(period=PERIOD, data_length=DATA_LENGTH, wait_timeout=25.0)

    try:
        klines = provider.fetch_many(symbols, period=PERIOD, length=DATA_LENGTH)
    except Exception as e:  # noqa: BLE001
        if not cron_mode:
            print(f"[{now_str}] TqSdk 获取失败: {e}")
        return

    if not klines:
        if not cron_mode:
            print(f"[{now_str}] 无有效数据")
        return

    # Re-attach names for display.
    name_map = {s: n for s, n, _ in symbols}
    scores = analyze_klines(engine, klines)
    for sc in scores:
        sc["name"] = name_map.get(sc["symbol"], "")

    errors = len(symbols) - len(scores)
    scores.sort(key=lambda x: x["total"], reverse=True)
    top5 = scores[:TOP_N]

    if cron_mode:
        print(format_feishu(top5, now_str))
    else:
        print(fmt_console(top5, errors, now_str))
        print()
        print("---FEISHU_DELIVER---")
        print(format_feishu(top5, now_str))
        print("---END_FEISHU_DELIVER---")


if __name__ == "__main__":
    run()
