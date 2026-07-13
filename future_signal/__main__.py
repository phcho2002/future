"""CLI: python -m future_signal [--force-daily] [--no-risk]"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 包外执行兼容
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from future_signal.config import SignalConfig
from future_signal.pipeline import result_to_frame, run_scan, size_with_risk


def main() -> None:
    ap = argparse.ArgumentParser(
        description="60m Donchian breakout + daily strength filter (top40)"
    )
    ap.add_argument("--force-daily", action="store_true", help="强制重算日线强弱")
    ap.add_argument(
        "--force-refresh",
        action="store_true",
        help="60m 全品种强制联网（不用本地缓存）",
    )
    ap.add_argument("--no-risk", action="store_true", help="不跑 future_risk 仓位")
    ap.add_argument("--cache-only", action="store_true", help="60m 只读本地缓存，不联网")
    ap.add_argument(
        "--no-first-probe",
        action="store_true",
        help="禁用首次突破试探小仓（仅二次真突破下单）",
    )
    ap.add_argument(
        "--probe-scale",
        type=float,
        default=0.20,
        help="首次试探 strength 相对满仓比例，默认 0.20",
    )
    ap.add_argument("--capital", type=float, default=5_000_000.0)
    ap.add_argument("--target-vol", type=float, default=0.15)
    ap.add_argument("--max-per-sector", type=int, default=2)
    ap.add_argument("--csv", type=str, default="", help="信号输出 CSV 路径")
    ap.add_argument("--json", type=str, default="", help="摘要 JSON 路径")
    args = ap.parse_args()

    cfg = SignalConfig(
        capital=args.capital,
        target_vol_annual=args.target_vol,
        max_per_sector=args.max_per_sector,
        force_refresh_daily=args.force_daily,
        force_refresh_klines=args.force_refresh,
        size_with_risk=not args.no_risk,
        cache_only=args.cache_only and not args.force_refresh,
        allow_first_breakout_probe=not args.no_first_probe,
        first_probe_strength_scale=args.probe_scale,
    )

    print("=" * 72)
    print(" future_signal | 60m 假突破→真突破 + 日线强弱过滤")
    probe = (
        f"first_probe ON scale={cfg.first_probe_strength_scale:.0%}"
        if cfg.allow_first_breakout_probe
        else "first_probe OFF (仅二次下单)"
    )
    print(
        f" entry: second_full 必做 | {probe} | "
        f"long ban bottom {cfg.ban_long_bottom_n} | "
        f"short ban top {cfg.ban_short_top_n} | sector={cfg.max_per_sector}"
    )
    print("=" * 72)

    scan = run_scan(cfg)
    for n in scan.notes:
        print(f"  · {n}")
    if scan.errors:
        print("Errors / warnings:")
        for e in scan.errors[:30]:
            print(f"  ! {e}")

    print()
    print(f"Orders THIS bar (仅本根下单): {len(scan.orders_now)}")
    if scan.orders_now:
        for s in scan.orders_now:
            print(
                f"  >> {s.symbol:6} {s.order_kind:16} dir={s.direction:+d} "
                f"str={s.strength:.2f} tier={s.entry_tier} rank={s.daily_rank} px={s.price:.2f}"
            )
    else:
        print("  (本根无开/加仓事件)")

    print()
    print(f"In-position (试探小仓 + 二次满仓 hold): {len(scan.active)}")
    if scan.active:
        hdr = (
            f"{'sym':6} {'name':8} {'dir':>3} {'str':>5} {'tier':12} {'rank':>5} "
            f"{'px':>10}  reason"
        )
        print(hdr)
        print("-" * len(hdr))
        for s in sorted(scan.active, key=lambda x: -x.strength):
            tag = "ORDER" if s.order_now else "HOLD"
            print(
                f"{s.symbol:6} {s.name[:8]:8} {s.direction:>+3} {s.strength:>5.2f} "
                f"{s.entry_tier:12} {str(s.daily_rank) if s.daily_rank else '-':>5} "
                f"{s.price:>10.2f}  [{tag}] {s.breakout_reason}"
            )
    else:
        print("  (当前无持仓；见 setup 监控)")

    # 状态机进行中：假突破路径上的品种
    setups = scan.setups
    if setups:
        print()
        print(f"Setups in progress (假突破路径中，尚未二次真突破入场): {len(setups)}")
        prio = {
            "FAILED_LONG": 0, "FAILED_SHORT": 1,
            "BROKEN_LONG": 2, "BROKEN_SHORT": 3,
            "PRIMED_LONG": 4,
        }
        for s in sorted(setups, key=lambda x: prio.get(x.engine_state, 9)):
            res = f"{s.pattern_resistance:.2f}" if s.pattern_resistance else "-"
            sup = f"{s.pattern_support:.2f}" if s.pattern_support else "-"
            print(
                f"  {s.symbol:6} {s.name[:8]:8} state={s.engine_state:14} "
                f"px={s.price:.2f}  R={res} S={sup}  rank={s.daily_rank}"
            )

    # 近期历史二次突破（即使已出场）
    recent = [
        s for s in scan.all_rows
        if s.last_signal_bars_ago is not None and s.last_signal_bars_ago <= 40
    ]
    if recent:
        print()
        print("Recent second-breakouts (≤40 根 60m 内，含已出场):")
        for s in sorted(recent, key=lambda x: x.last_signal_bars_ago or 999):
            side = s.last_signal_side or 0
            print(
                f"  {s.symbol:6} side={side:+d}  {s.last_signal_bars_ago} bars ago  "
                f"hist_n={s.hist_signal_count}  now={s.engine_state}"
            )

    banned = [
        s for s in scan.rejected
        if s.raw_direction != 0 and s.direction == 0
        and ("banned" in s.filter_reason or "sector_dedup" in s.filter_reason)
    ]
    if banned:
        print()
        print(f"Filtered by daily/sector: {len(banned)}")
        for s in banned:
            print(
                f"  {s.symbol:6} raw={s.raw_direction:+d} str={s.raw_strength:.2f} "
                f"rank={s.daily_rank}  → {s.filter_reason}"
            )

    snap = None
    if cfg.size_with_risk and scan.active:
        print()
        print("-" * 72)
        print(
            f" future_risk sizing | capital={cfg.capital:,.0f} "
            f"target_vol={cfg.target_vol_annual:.0%}"
        )
        print("-" * 72)
        snap = size_with_risk(scan, cfg)
        print(
            f"targets={snap.n_targets}  margin={snap.total_margin:,.0f} "
            f"({snap.margin_usage:.1%})  gross={snap.gross_notional:,.0f}"
        )
        for p in sorted(snap.positions, key=lambda x: -x.margin):
            if p.target_lots <= 0:
                continue
            print(
                f"  {p.symbol:6} dir={p.direction:+d} lots={p.target_lots:>4}  "
                f"margin={p.margin:>10,.0f}  w={p.risk_weight:.1%}  {p.sector}"
            )
        if snap.notes:
            print("risk notes:")
            for n in snap.notes[:15]:
                print(f"  - {n}")

    if args.csv:
        df = result_to_frame(scan, include_rejected=True)
        Path(args.csv).parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(args.csv, index=False, encoding="utf-8-sig")
        print(f"\nCSV → {args.csv}")

    if args.json:
        payload = {
            "notes": scan.notes,
            "active": [
                {
                    "symbol": s.symbol,
                    "direction": s.direction,
                    "strength": s.strength,
                    "daily_rank": s.daily_rank,
                    "daily_score": s.daily_score,
                    "price": s.price,
                    "reason": s.breakout_reason,
                }
                for s in scan.active
            ],
            "risk": None
            if snap is None
            else {
                "n_targets": snap.n_targets,
                "margin_usage": snap.margin_usage,
                "positions": [
                    {
                        "symbol": p.symbol,
                        "direction": p.direction,
                        "lots": p.target_lots,
                        "margin": p.margin,
                    }
                    for p in snap.positions
                    if p.target_lots > 0
                ],
            },
        }
        Path(args.json).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"JSON → {args.json}")


if __name__ == "__main__":
    main()
