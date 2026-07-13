"""Demo: 500万 × top40 波动率目标仓位 + 换月平开同时。

用法:
  python -m future_risk.demo
  python -m future_risk.demo --signals RB0:1,I0:1,AU0:-1,M0:1
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 允许直接 python future_risk/demo.py
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from future_risk.config import RiskConfig
from future_risk.engine import RiskEngine, SignalInput
from future_risk.universe import load_universe


def _parse_signals(spec: str) -> list[tuple[str, int, float]]:
    """RB0:1,I0:1:0.8,AU0:-1 -> [(sym, dir, strength)]"""
    out: list[tuple[str, int, float]] = []
    if not spec.strip():
        return out
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        bits = part.split(":")
        sym = bits[0].strip().upper()
        if not sym.endswith("0"):
            sym = sym + "0" if not sym[-1].isdigit() else sym
        # 兼容 rb0
        sym = bits[0].strip()
        if sym[-1] != "0" and not any(ch.isdigit() for ch in sym[1:]):
            sym = sym + "0"
        direction = int(bits[1]) if len(bits) > 1 else 1
        strength = float(bits[2]) if len(bits) > 2 else 1.0
        out.append((sym, direction, strength))
    return out


def default_demo_signals() -> list[tuple[str, int, float]]:
    """跨板块示意信号（非实盘建议）。"""
    return [
        ("RB0", 1, 1.0),
        ("I0", 1, 0.9),
        ("HC0", 1, 0.7),
        ("AU0", -1, 0.8),
        ("CU0", 1, 0.7),
        ("M0", 1, 0.6),
        ("Y0", 1, 0.5),
        ("SC0", 1, 0.7),
        ("TA0", -1, 0.5),
        ("IM0", 1, 0.6),
        ("LH0", -1, 0.4),
        ("LC0", 1, 0.5),
    ]


# 示意日波动（若无历史序列时用）；实盘应替换为 EWMA
DEMO_VOL: dict[str, float] = {
    "RB0": 0.012,
    "I0": 0.018,
    "HC0": 0.013,
    "AU0": 0.010,
    "CU0": 0.011,
    "M0": 0.012,
    "Y0": 0.013,
    "SC0": 0.016,
    "TA0": 0.014,
    "IM0": 0.015,
    "LH0": 0.020,
    "LC0": 0.025,
    "JM0": 0.022,
    "SA0": 0.018,
    "AG0": 0.016,
    "IF0": 0.012,
    "AL0": 0.010,
    "P0": 0.014,
}


def main() -> None:
    ap = argparse.ArgumentParser(description="future_risk demo (500万 top40)")
    ap.add_argument(
        "--signals",
        type=str,
        default="",
        help="e.g. RB0:1,I0:1:0.8,AU0:-1",
    )
    ap.add_argument("--capital", type=float, default=5_000_000.0)
    ap.add_argument("--target-vol", type=float, default=0.15, help="annual vol target")
    ap.add_argument(
        "--roll",
        type=str,
        default="RB0:SHFE.rb2510:SHFE.rb2511:0:1",
        help="symbol:from:to:lots:dir  (lots=0 表示用目标手数)",
    )
    ap.add_argument("--json-out", type=str, default="", help="optional path to dump snapshot")
    args = ap.parse_args()

    cfg = RiskConfig(capital=args.capital, target_vol_annual=args.target_vol)
    engine = RiskEngine(cfg)
    universe = load_universe(cfg)
    print(f"Universe: {len(universe)} products from {cfg.top40_json.name}")
    print(
        f"Capital: {cfg.capital:,.0f}  |  target vol: {cfg.target_vol_annual:.0%} ann "
        f"({cfg.target_vol_daily:.3%} daily)  |  max margin: {cfg.max_portfolio_margin_frac:.0%}"
    )
    print("-" * 88)

    raw = _parse_signals(args.signals) if args.signals else default_demo_signals()
    signals: list[SignalInput] = []
    for sym, direction, strength in raw:
        prod = engine.universe.get(sym)
        if prod is None:
            print(f"  WARN: {sym} not in universe, skip")
            continue
        signals.append(
            SignalInput(
                symbol=sym,
                direction=direction,
                strength=strength,
                price=prod.last_price,
                daily_vol=DEMO_VOL.get(sym),
                contract=None,
                current_lots=0,
            )
        )

    snap = engine.size(signals)

    print(
        f"Active targets: {snap.n_targets}  |  margin: {snap.total_margin:,.0f} "
        f"({snap.margin_usage:.1%})  |  gross notional: {snap.gross_notional:,.0f}"
    )
    print()
    hdr = (
        f"{'symbol':6} {'name':10} {'sec':14} {'dir':>3} {'lots':>5} "
        f"{'price':>10} {'dvol':>7} {'margin':>12} {'w':>6} {'Δ':>5}"
    )
    print(hdr)
    print("-" * len(hdr))
    for p in sorted(snap.positions, key=lambda x: -x.margin):
        if p.target_lots <= 0:
            continue
        print(
            f"{p.symbol:6} {p.name[:10]:10} {p.sector:14} {p.direction:>+3} {p.target_lots:>5} "
            f"{p.price:>10.2f} {p.daily_vol:>6.2%} {p.margin:>12,.0f} {p.risk_weight:>5.1%} "
            f"{p.delta_lots:>+5}"
        )

    if snap.orders:
        print()
        print("Rebalance intents:")
        for o in snap.orders:
            print(
                f"  {o.symbol:6} {o.side:4} {o.offset:5} x{o.lots}  "
                f"contract={o.contract or '-'}  ({o.comment})"
            )

    # 换月演示：默认用 RB 目标手数
    print()
    print("Roll plan (simultaneous close+open):")
    # parse roll spec
    try:
        rsym, rfrom, rto, rlots_s, rdir_s = args.roll.split(":")
        rlots = int(rlots_s)
        rdir = int(rdir_s)
    except Exception:
        rsym, rfrom, rto, rlots, rdir = "RB0", "SHFE.rb2510", "SHFE.rb2511", 0, 1

    rb_pos = next((p for p in snap.positions if p.symbol == rsym), None)
    if rlots <= 0:
        rlots = rb_pos.target_lots if rb_pos else 0
    if rb_pos:
        rdir = rb_pos.direction

    if rlots > 0:
        plans = engine.plan_rolls(
            [(rsym, rfrom, rto, rlots, rdir)],
            snapshot=snap,
        )
        for plan in plans:
            print(
                f"  {plan.symbol}: {plan.from_contract} -> {plan.to_contract}  "
                f"lots={plan.lots} dir={plan.direction:+d}  simultaneous={plan.simultaneous}"
            )
            for leg in plan.legs:
                print(
                    f"    [{leg.role:10}] {leg.side:4} {leg.offset:5} "
                    f"{leg.contract} x{leg.lots}  # {leg.comment}"
                )
    else:
        print("  (no lots to roll)")

    if snap.notes:
        print()
        print("Notes:")
        for n in snap.notes:
            print(f"  - {n}")

    if args.json_out:
        payload = {
            "capital": snap.capital,
            "target_vol_daily": snap.target_vol_daily,
            "margin_usage": snap.margin_usage,
            "positions": [
                {
                    "symbol": p.symbol,
                    "direction": p.direction,
                    "lots": p.target_lots,
                    "margin": p.margin,
                    "risk_weight": p.risk_weight,
                    "sector": p.sector,
                }
                for p in snap.positions
                if p.target_lots > 0
            ],
            "rolls": [
                {
                    "symbol": r.symbol,
                    "from": r.from_contract,
                    "to": r.to_contract,
                    "lots": r.lots,
                    "legs": [
                        {
                            "contract": leg.contract,
                            "side": leg.side,
                            "offset": leg.offset,
                            "lots": leg.lots,
                            "role": leg.role,
                        }
                        for leg in r.legs
                    ],
                }
                for r in snap.rolls
            ],
        }
        Path(args.json_out).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\nWrote {args.json_out}")


if __name__ == "__main__":
    main()
