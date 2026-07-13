"""CLI: python -m future_system [--cache-only] [--force-refresh] [--force-daily]"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from future_signal.config import SignalConfig
from future_system.books import BookId
from future_system.runner import SystemConfig, result_frames, run_system


def main() -> None:
    ap = argparse.ArgumentParser(description="双账本：趋势+震荡 统一系统")
    ap.add_argument("--cache-only", action="store_true")
    ap.add_argument("--force-refresh", action="store_true")
    ap.add_argument("--force-daily", action="store_true")
    ap.add_argument("--no-first-probe", action="store_true")
    ap.add_argument("--no-ledger", action="store_true", help="不写交易记录/账簿")
    ap.add_argument(
        "--ledger-db",
        type=str,
        default="",
        help="账簿 SQLite 路径（默认 future_system/data/trading_ledger.db）",
    )
    ap.add_argument(
        "--show-ledger",
        action="store_true",
        help="仅打印当前账簿持仓后退出（不跑扫描）",
    )
    ap.add_argument("--csv", type=str, default="future_system/out_signals.csv")
    ap.add_argument("--risk-csv", type=str, default="future_system/out_risk.csv")
    ap.add_argument("--json", type=str, default="future_system/out.json")
    ap.add_argument(
        "--trades-csv",
        type=str,
        default="future_system/out_trades.csv",
        help="本轮成交明细 CSV",
    )
    args = ap.parse_args()

    if args.show_ledger:
        from future_system.ledger import TradeLedger

        led = TradeLedger(db_path=args.ledger_db or None)
        print("=" * 72)
        print(f" ledger: {led.db_path}")
        print("=" * 72)
        for bid in ("financial", "commodity"):
            acc = led.load_account(bid)
            pos = led.load_positions(bid)
            print(f"\n[{bid}]")
            if acc:
                print(
                    f"  equity={acc.equity:,.0f} cash={acc.cash:,.0f} "
                    f"margin={acc.reserved_margin:,.0f} realized={acc.realized_pnl:,.0f}"
                )
            else:
                print("  (no account yet)")
            for p in pos:
                print(
                    f"  {p.sleeve:5} {p.symbol:6} dir={p.direction:+d} "
                    f"lots={p.lots} entry={p.entry_price:.4g} "
                    f"stop={p.stop} tp1={p.tp1} tp2={p.tp2}"
                )
            for t in led.recent_trades(bid, 8):
                print(
                    f"  trade {t['created_at']} {t['action']:10} "
                    f"{t['symbol']} lots={t['lots']} px={t['price']} pnl={t['pnl']:.0f}"
                )
        return

    sig = SignalConfig(
        cache_only=args.cache_only and not args.force_refresh,
        force_refresh_klines=args.force_refresh,
        force_refresh_daily=args.force_daily,
        allow_first_breakout_probe=not args.no_first_probe,
    )
    cfg = SystemConfig(
        signal=sig,
        force_refresh_klines=args.force_refresh,
        force_refresh_daily=args.force_daily,
        cache_only=args.cache_only and not args.force_refresh,
        enable_ledger=not args.no_ledger,
        ledger_db=args.ledger_db or None,
    )

    print("=" * 72)
    print(" future_system | 双账本 400万金融 + 400万商品")
    print(" 趋势: 60m 假突破→真突破 | 震荡: 30m 假突破反转/边沿")
    print(" 环境闸: ADX/波幅/ATR压缩 → TREND|RANGE|NEUTRAL")
    print(
        f" 账簿: {'ON' if cfg.enable_ledger else 'OFF'} "
        f"(单品种硬止损 {cfg.max_symbol_loss_frac:.2%} 资金)"
    )
    print("=" * 72)

    res = run_system(cfg)
    for n in res.notes:
        print(f"  · {n}")

    summary = {"notes": res.notes, "books": {}, "ledger": None}

    for bid in (BookId.FINANCIAL, BookId.COMMODITY):
        br = res.books[bid]
        print()
        print("-" * 72)
        eq = (
            f" equity={br.ledger_equity:,.0f} cash={br.ledger_cash:,.0f}"
            if br.ledger_equity is not None
            else ""
        )
        print(
            f" 【{br.book.name}】 capital={br.book.capital:,.0f}  "
            f"trend_vol={br.book.trend_vol_annual:.0%}  "
            f"range_vol={br.book.range_vol_annual:.0%}{eq}"
        )
        print(f" regime: {br.regime_counts}")
        for n in br.notes:
            print(f"  · {n}")
        if br.errors:
            for e in br.errors[:8]:
                print(f"  ! {e}")

        print(f"\n  TREND sleeve active={len(br.active_trend)}")
        for s in sorted(br.active_trend, key=lambda x: -x.strength):
            print(
                f"    {s.symbol:6} {s.name[:8]:8} dir={s.direction:+d} "
                f"str={s.strength:.2f} tier={s.entry_tier or '-':12} "
                f"reg={s.regime} | {s.reason}"
            )
        if br.trend_risk:
            print(
                f"  trend risk: targets={br.trend_risk.n_targets} "
                f"margin={br.trend_risk.total_margin:,.0f} "
                f"({br.trend_risk.margin_usage:.1%})"
            )
            for p in br.trend_risk.positions:
                if p.target_lots > 0:
                    print(
                        f"      {p.symbol} dir={p.direction:+d} lots={p.target_lots} "
                        f"margin={p.margin:,.0f}"
                    )

        print(f"\n  RANGE sleeve active={len(br.active_range)}")
        for s in sorted(br.active_range, key=lambda x: -x.strength):
            levels = ""
            if s.stop is not None and s.tp1 is not None and s.tp2 is not None:
                cs = s.current_stop if s.current_stop is not None else s.stop
                rr1 = f"{s.rr_tp1:.2f}" if s.rr_tp1 is not None else "-"
                rr2 = f"{s.rr_tp2:.2f}" if s.rr_tp2 is not None else "-"
                left = f"{s.remaining_frac:.0%}" if s.remaining_frac is not None else "-"
                risk = f"{s.initial_risk:.4g}" if s.initial_risk is not None else "-"
                levels = (
                    f" | stop={cs:.4g} tp1={s.tp1:.4g} tp2={s.tp2:.4g}"
                    f" R={risk} rr1={rr1} rr2={rr2} left={left}"
                )
            print(
                f"    {s.symbol:6} {s.name[:8]:8} dir={s.direction:+d} "
                f"str={s.strength:.2f} setup={s.setup:18} "
                f"reg={s.regime} | {s.reason}{levels}"
            )
        if br.range_risk:
            print(
                f"  range risk: targets={br.range_risk.n_targets} "
                f"margin={br.range_risk.total_margin:,.0f} "
                f"({br.range_risk.margin_usage:.1%})"
            )
            for p in br.range_risk.positions:
                if p.target_lots > 0:
                    print(
                        f"      {p.symbol} dir={p.direction:+d} lots={p.target_lots} "
                        f"margin={p.margin:,.0f}"
                    )

        summary["books"][bid.value] = {
            "capital": br.book.capital,
            "regime_counts": br.regime_counts,
            "trend": [
                {
                    "symbol": s.symbol,
                    "direction": s.direction,
                    "strength": s.strength,
                    "tier": s.entry_tier,
                    "reason": s.reason,
                    "regime": s.regime,
                }
                for s in br.active_trend
            ],
            "range": [
                {
                    "symbol": s.symbol,
                    "direction": s.direction,
                    "strength": s.strength,
                    "setup": s.setup,
                    "reason": s.reason,
                    "regime": s.regime,
                    "entry": s.entry,
                    "stop": s.stop,
                    "tp1": s.tp1,
                    "tp2": s.tp2,
                    "current_stop": s.current_stop,
                    "tp1_fraction": s.tp1_fraction,
                    "initial_risk": s.initial_risk,
                    "rr_tp1": s.rr_tp1,
                    "rr_tp2": s.rr_tp2,
                    "remaining_frac": s.remaining_frac,
                }
                for s in br.active_range
            ],
            "trend_margin_usage": (
                br.trend_risk.margin_usage if br.trend_risk else 0.0
            ),
            "range_margin_usage": (
                br.range_risk.margin_usage if br.range_risk else 0.0
            ),
            "ledger_equity": br.ledger_equity,
            "ledger_cash": br.ledger_cash,
            "ledger_positions": br.ledger_positions,
        }

    # 本轮成交
    if res.ledger is not None:
        led = res.ledger
        print()
        print("=" * 72)
        print(f" LEDGER run_id={led.run_id}  trades={len(led.trades)}")
        print("=" * 72)
        for t in led.trades[:40]:
            print(
                f"  {t.book_id:10} {t.sleeve:5} {t.action:12} {t.symbol:6} "
                f"dir={t.direction:+d} lots={t.lots} px={t.price:.4g} "
                f"pnl={t.pnl:,.0f} | {t.reason}"
            )
        if len(led.trades) > 40:
            print(f"  ... {len(led.trades) - 40} more")
        summary["ledger"] = {
            "run_id": led.run_id,
            "n_trades": len(led.trades),
            "accounts": {
                k: {
                    "equity": a.equity,
                    "cash": a.cash,
                    "reserved_margin": a.reserved_margin,
                    "realized_pnl": a.realized_pnl,
                }
                for k, a in led.accounts.items()
            },
            "positions": [
                {
                    "book_id": p.book_id,
                    "sleeve": p.sleeve,
                    "symbol": p.symbol,
                    "direction": p.direction,
                    "lots": p.lots,
                    "entry_price": p.entry_price,
                    "stop": p.stop,
                    "tp1": p.tp1,
                    "tp2": p.tp2,
                }
                for p in led.positions
            ],
        }
        if args.trades_csv:
            import pandas as pd

            Path(args.trades_csv).parent.mkdir(parents=True, exist_ok=True)
            pd.DataFrame([t.__dict__ for t in led.trades]).to_csv(
                args.trades_csv, index=False, encoding="utf-8-sig"
            )
            print(f"CSV trades → {args.trades_csv}")

    sig_df, risk_df = result_frames(res)
    if args.csv:
        Path(args.csv).parent.mkdir(parents=True, exist_ok=True)
        sig_df.to_csv(args.csv, index=False, encoding="utf-8-sig")
        print(f"\nCSV signals → {args.csv} ({len(sig_df)} rows)")
    if args.risk_csv:
        risk_df.to_csv(args.risk_csv, index=False, encoding="utf-8-sig")
        print(f"CSV risk → {args.risk_csv} ({len(risk_df)} rows)")
    if args.json:
        Path(args.json).write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"JSON → {args.json}")


if __name__ == "__main__":
    main()
