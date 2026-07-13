"""A-Stock Wyckoff scanner — batch scan all A-shares using TDX data.
Outputs buy signals (Spring) from the last N trading days.

修复点:
  B1 - 信号日期使用 Spring/Upthrust 真实触发日 (而非最后一根K线日期)
  B2 - 仅扫描真正的 A 股股票 (过滤指数/ETF/可转债/B股/基金)
  O1 - 从通达信代码表加载股票名称
  O4 - 按强度等级过滤 (可选), 输出包含 strength 字段
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from stock_wyckoff.config import WyckoffConfig
from stock_wyckoff.data.tdx_provider import TDXStockProvider
from stock_wyckoff.engine import WyckoffEngine


@dataclass(frozen=True)
class StockSignal:
    """A single stock Wyckoff signal record."""

    symbol: str
    name: str
    date: str            # B1修复: 真实触发日
    side: str
    phase: str
    phase_desc: str
    strength: str        # 新增: 信号强度 (强/中/弱)
    entry: float
    stop: float
    target: float
    rsi: float
    reason: str
    pattern: str
    volume_confirm: bool
    volume_ok: bool      # 新增: 成交量枯竭验证
    stop_ok: bool        # 新增: 停止行为方向验证
    support: float
    resistance: float
    test_count: int
    contraction_ratio: float
    volume_trend: str
    effort_divergence: bool
    stop_behavior: bool


@dataclass(frozen=True)
class ScanResult:
    """Result of scanning all A-shares."""

    signals: list[StockSignal]
    errors: dict[str, str]
    total_scanned: int


def scan_all_a_shares(
    lookback_days: int = 20,
    config: WyckoffConfig | None = None,
    data_years: int = 2,
    min_strength: str = "弱",   # "强" / "中" / "弱" (过滤阈值)
    tdx_dir: str = "D:/new_tdx/vipdoc",
) -> ScanResult:
    """Scan all A-shares for Wyckoff Spring (buy) signals.

    Parameters
    ----------
    lookback_days : int
        Only keep signals from the last N trading days.
    config : WyckoffConfig | None
        Analysis configuration.
    data_years : int
        How many years of historical data to load.
    min_strength : str
        最低信号强度阈值 ("弱"=全部, "中"=中强, "强"=仅强).
    tdx_dir : str
        通达信 vipdoc 目录.
    """
    provider = TDXStockProvider(tdx_dir=Path(tdx_dir), stocks_only=True)
    engine = WyckoffEngine(config=config)

    symbols = provider.all_a_share_symbols()
    stock_names = provider.load_stock_names()

    print(f"[{datetime.now():%H:%M:%S}] 威科夫A股扫描启动")
    print(f"数据源: {tdx_dir}  |  共 {len(symbols)} 只A股 (已过滤指数/ETF/可转债/B股)")
    if stock_names:
        print(f"已加载股票名称: {len(stock_names)} 条")
    print(f"信号过滤: 最近 {lookback_days} 个交易日 | Spring做多 | 最低强度={min_strength}")
    print()

    now = datetime.now()
    # buffer for weekends/holidays
    cutoff = now - timedelta(days=lookback_days + 10)
    start_date = (now - timedelta(days=365 * data_years)).strftime("%Y%m%d")

    strength_rank = {"强": 3, "中": 2, "弱": 1}
    min_rank = strength_rank.get(min_strength, 1)

    signals: list[StockSignal] = []
    errors: dict[str, str] = {}
    skipped_by_strength = 0

    for idx, symbol in enumerate(symbols, 1):
        if idx % 200 == 0 or idx == len(symbols):
            print(f"[{idx:05d}/{len(symbols)}] {symbol}  进度 {idx / len(symbols):.1%}",
                  end="\r", flush=True)

        try:
            df = provider.history(symbol, start_date=start_date)
            if len(df) < 60:
                continue

            result = engine.analyze_df(df)
            sig = result.signal

            if not sig.is_valid or sig.side != "long":
                continue

            # ── B1修复: 使用触发日真实日期 ──
            scan_window = max(8, config.spring_recover_bars + 5) if config else 8
            trigger_idx_in_df = len(df) - scan_window + sig.metadata.get("trigger_bar_idx", 0)
            trigger_idx_in_df = max(0, min(trigger_idx_in_df, len(df) - 1))
            signal_date = df.iloc[trigger_idx_in_df]["date"]
            if isinstance(signal_date, str):
                signal_date = datetime.strptime(signal_date, "%Y-%m-%d")
            if signal_date < cutoff:
                continue

            # ── O4: 强度过滤 ──
            strength = sig.metadata.get("strength", "弱")
            if strength_rank.get(strength, 1) < min_rank:
                skipped_by_strength += 1
                continue

            signal_date_str = (
                signal_date.strftime("%Y-%m-%d")
                if hasattr(signal_date, "strftime") else str(signal_date)
            )

            signals.append(StockSignal(
                symbol=symbol,
                name=stock_names.get(symbol, ""),
                date=signal_date_str,
                side=sig.side,
                phase=sig.phase.phase,
                phase_desc=sig.phase.description,
                strength=strength,
                entry=sig.levels.entry or 0.0,
                stop=sig.levels.stop or 0.0,
                target=sig.levels.target or 0.0,
                rsi=sig.rsi_value or 0.0,
                reason=sig.entry_reason,
                pattern=sig.metadata.get("pattern", ""),
                volume_confirm=sig.metadata.get("volume_confirm", False),
                volume_ok=sig.metadata.get("volume_ok", False),
                stop_ok=sig.metadata.get("stop_ok", False),
                support=result.range_analysis.support_level,
                resistance=result.range_analysis.resistance_level,
                test_count=result.range_analysis.test_count,
                contraction_ratio=result.range_analysis.contraction_ratio,
                volume_trend=result.volume_analysis.volume_trend,
                effort_divergence=result.volume_analysis.effort_result_divergence,
                stop_behavior=result.stop_behavior.has_stop,
            ))

        except Exception as exc:
            errors[symbol] = str(exc)

    print()
    print(f"[{datetime.now():%H:%M:%S}] 扫描完成")
    print(f"  扫描A股总数: {len(symbols)}")
    print(f"  买入信号:    {len(signals)}")
    print(f"  强度过滤剔除: {skipped_by_strength}")
    print(f"  错误数:      {len(errors)}")
    print()

    return ScanResult(signals=signals, errors=errors, total_scanned=len(symbols))


def write_outputs(result: ScanResult, output_dir: Path, lookback_days: int) -> None:
    """Write scan results to CSV and Markdown report."""
    output_dir.mkdir(parents=True, exist_ok=True)

    # Write signals CSV
    if result.signals:
        df = pd.DataFrame([s.__dict__ for s in result.signals])
        df = df.sort_values(["strength", "date"], ascending=[True, False])
        df.to_csv(output_dir / "wyckoff_signals.csv", index=False, encoding="utf-8-sig")

    # Write errors CSV
    if result.errors:
        error_df = pd.DataFrame(
            [{"symbol": k, "error": v} for k, v in result.errors.items()]
        )
        error_df.to_csv(output_dir / "errors.csv", index=False, encoding="utf-8-sig")

    # Write Markdown report
    with (output_dir / "report.md").open("w", encoding="utf-8") as fh:
        fh.write("# 威科夫量价分析 — A股买入信号报告\n\n")
        fh.write(f"**生成时间**: {datetime.now():%Y-%m-%d %H:%M:%S}\n\n")
        fh.write(f"**扫描范围**: 全部A股 ({result.total_scanned} 只, 已过滤非股票品种)\n\n")
        fh.write(f"**信号过滤**: 最近{lookback_days}个交易日 + Spring(弹簧效应)做多信号\n\n")
        fh.write("---\n\n")

        if not result.signals:
            fh.write("## 当前无买入信号\n\n未检测到符合威科夫Spring条件的买入信号。\n")
        else:
            fh.write(f"## 买入信号列表 ({len(result.signals)} 只)\n\n")
            fh.write("| 代码 | 名称 | 触发日 | 强度 | 阶段 | 入场 | 止损 | 目标 | RSI | 量枯竭 | 停止行为 | 放量确认 |\n")
            fh.write("| --- | --- | --- | --- | --- | ---: | ---: | ---: | --- | --- | --- | --- |\n")
            for s in sorted(result.signals, key=lambda x: (x.strength, x.date), reverse=True):
                fh.write(
                    f"| {s.symbol} | {s.name} | {s.date} | {s.strength} | "
                    f"{s.phase} | {s.entry:.2f} | {s.stop:.2f} | {s.target:.2f} | "
                    f"{s.rsi:.1f} | {'是' if s.volume_ok else '否'} | "
                    f"{'是' if s.stop_ok else '否'} | {'是' if s.volume_confirm else '否'} |\n"
                )

            fh.write("\n## 信号详细分析\n\n")
            for s in sorted(result.signals, key=lambda x: (x.strength, x.date), reverse=True):
                fh.write(f"### {s.symbol} {s.name} ({s.date}) [{s.strength}]\n\n")
                fh.write(f"- **阶段**: {s.phase_desc}\n")
                fh.write(f"- **入场价**: {s.entry:.2f}\n")
                fh.write(f"- **止损价**: {s.stop:.2f} (支撑极值点)\n")
                fh.write(f"- **目标价**: {s.target:.2f}\n")
                fh.write(f"- **RSI**: {s.rsi:.1f}\n")
                fh.write(f"- **支撑位**: {s.support:.2f} / **阻力位**: {s.resistance:.2f}\n")
                fh.write(f"- **测试次数**: {s.test_count} / **波动收敛**: {s.contraction_ratio:.3f}\n")
                fh.write(f"- **成交量趋势**: {s.volume_trend} (枯竭验证: {'通过' if s.volume_ok else '未通过'})\n")
                fh.write(f"- **Effort-Result背离**: {'是' if s.effort_divergence else '否'}\n")
                fh.write(f"- **停止行为**: {'是' if s.stop_behavior else '否'} (方向验证: {'通过' if s.stop_ok else '未通过'})\n")
                fh.write(f"- **放量确认**: {'是' if s.volume_confirm else '否'}\n")
                fh.write(f"- **理由**: {s.reason}\n\n")

        if result.errors:
            fh.write(f"\n## 数据错误 ({len(result.errors)} 只)\n\n")
            for symbol, error in result.errors.items():
                fh.write(f"- {symbol}: {error}\n")

    print(f"输出文件已写入: {output_dir}")
    for f in sorted(output_dir.iterdir()):
        print(f"  {f.name} ({f.stat().st_size / 1024:.1f} KB)")
