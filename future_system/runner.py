"""统一运行：双账本 ×（环境闸 + 趋势主信号 / 震荡旁路）→ risk。"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path

import pandas as pd

_WORK = Path(__file__).resolve().parents[1]
if str(_WORK) not in sys.path:
    sys.path.insert(0, str(_WORK))

from future_signal.config import SignalConfig
from future_signal.daily_filter import apply_daily_filter, load_or_compute_daily_ranks
from future_signal.pipeline import _combine_strength, _fetch_60m_bars, _sector_dedup
from future_signal.second_breakout import evaluate_second_breakout
from future_risk.config import RiskConfig, SECTOR_MAP
from future_risk.engine import RiskEngine, SignalInput

from .books import BookId, BookConfig, book_config, split_universe
from .range_signal import RangeSignalConfig, evaluate_range_signal
from .regime import Regime, RegimeConfig, classify_regime


@dataclass
class UnifiedSignal:
    book: str
    symbol: str
    name: str
    exchange: str
    sector: str
    sleeve: str                 # trend | range
    regime: str
    regime_reason: str
    direction: int
    strength: float
    price: float
    daily_vol: float
    daily_rank: int | None
    daily_score: float | None
    reason: str
    filter_reason: str
    order_now: bool
    entry_tier: str = ""
    setup: str = ""
    # 震荡出场（趋势腿可空）
    entry: float | None = None
    stop: float | None = None
    tp1: float | None = None
    tp2: float | None = None
    current_stop: float | None = None
    tp1_fraction: float | None = None
    initial_risk: float | None = None
    rr_tp1: float | None = None
    rr_tp2: float | None = None
    remaining_frac: float | None = None


@dataclass
class BookResult:
    book: BookConfig
    regime_counts: dict[str, int] = field(default_factory=dict)
    trend_signals: list[UnifiedSignal] = field(default_factory=list)
    range_signals: list[UnifiedSignal] = field(default_factory=list)
    trend_risk: object | None = None
    range_risk: object | None = None
    notes: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    # 账簿（run 后由 ledger 填充）
    ledger_equity: float | None = None
    ledger_cash: float | None = None
    ledger_positions: int | None = None

    @property
    def active_trend(self) -> list[UnifiedSignal]:
        return [s for s in self.trend_signals if s.direction != 0]

    @property
    def active_range(self) -> list[UnifiedSignal]:
        return [s for s in self.range_signals if s.direction != 0]


@dataclass
class SystemResult:
    books: dict[BookId, BookResult] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    ledger: object | None = None  # LedgerRunResult
    prices: dict[str, float] = field(default_factory=dict)


@dataclass
class SystemConfig:
    signal: SignalConfig = field(default_factory=SignalConfig)
    regime: RegimeConfig = field(default_factory=RegimeConfig)
    range_sig: RangeSignalConfig = field(default_factory=RangeSignalConfig)
    # 震荡执行周期
    range_period: str = "30"
    range_bar_length: int = 800
    # 趋势环境用 60m（与主信号一致）
    force_refresh_klines: bool = False
    force_refresh_daily: bool = False
    cache_only: bool = False
    max_per_sector_trend: int = 2
    max_per_sector_range: int = 2
    # 震荡日线过滤：与趋势相同规则
    apply_daily_filter: bool = True
    # 交易记录 / 账簿
    enable_ledger: bool = True
    ledger_db: str | None = None  # 默认 future_system/data/trading_ledger.db
    max_symbol_loss_frac: float = 0.0095


def _fetch_period_bars(
    universe: list[tuple[str, str, str]],
    period: str,
    length: int,
    cfg: SystemConfig,
) -> dict[str, pd.DataFrame]:
    """通用周期拉取。"""
    # 复用 future_signal 的 60m 拉取逻辑：临时改 SignalConfig
    sc = SignalConfig(
        signal_period=period,
        bar_length_60m=length,
        cache_only=cfg.cache_only,
        force_refresh_klines=cfg.force_refresh_klines,
        kline_ttl_hours=cfg.signal.kline_ttl_hours,
        top40_json=cfg.signal.top40_json,
        db_path=cfg.signal.db_path,
    )
    # _fetch_60m_bars 用 cfg.signal_period / bar_length_60m
    return _fetch_60m_bars(universe, sc)


def _size_sleeve(
    sigs: list[UnifiedSignal],
    capital: float,
    target_vol: float,
    max_positions: int = 8,
):
    if not sigs:
        return None
    rcfg = RiskConfig(
        capital=capital,
        target_vol_annual=target_vol,
        max_positions=max_positions,
        max_symbol_risk_weight=0.15 if target_vol < 0.10 else 0.10,
        max_symbol_loss_frac=0.0095,  # 单品种亏损 ≥ 总资金 0.95% → 无条件止损
    )
    engine = RiskEngine(rcfg)
    inputs = []
    for s in sigs:
        if s.direction == 0:
            continue
        # 震荡腿：用 entry/stop 钳制「打到止损不超过 0.95% 资金」
        entry = s.entry if s.entry is not None else (s.price if s.price > 0 else None)
        stop = s.current_stop if s.current_stop is not None else s.stop
        inputs.append(
            SignalInput(
                symbol=s.symbol,
                direction=s.direction,
                strength=s.strength,
                price=s.price if s.price > 0 else None,
                daily_vol=s.daily_vol,
                current_lots=0,
                entry_price=entry,
                stop_price=stop,
            )
        )
    if not inputs:
        return None
    return engine.size(inputs)


def run_book(
    book_id: BookId,
    sys_cfg: SystemConfig,
    bars_60: dict[str, pd.DataFrame],
    bars_range: dict[str, pd.DataFrame],
    daily,
) -> BookResult:
    bcfg = book_config(book_id)
    universe = split_universe()[book_id]
    result = BookResult(book=bcfg)
    result.notes.append(
        f"{bcfg.name} capital={bcfg.capital:,.0f} "
        f"trend_vol={bcfg.trend_vol_annual:.0%} range_vol={bcfg.range_vol_annual:.0%} "
        f"n={len(universe)}"
    )

    scfg = sys_cfg.signal
    scfg.force_refresh_daily = sys_cfg.force_refresh_daily
    # 板块去重用的临时结构（复用 SymbolSignal 形状）
    from future_signal.pipeline import SymbolSignal

    trend_raw: list[SymbolSignal] = []
    range_raw: list[SymbolSignal] = []
    regime_counts = {"TREND": 0, "RANGE": 0, "NEUTRAL": 0}

    for sym, name, ex in universe:
        df60 = bars_60.get(sym)
        if df60 is None or df60.empty:
            result.errors.append(f"{sym}: no 60m")
            continue

        reg = classify_regime(df60, sys_cfg.regime)
        regime_counts[reg.regime.value] = regime_counts.get(reg.regime.value, 0) + 1
        sector = SECTOR_MAP.get(sym, "other")
        rank_row = daily.ranks.get(sym) if daily else None

        # ---- 趋势腿：仅 TREND 环境允许新方向；hold 在 NEUTRAL 也可保留 ----
        snap_t = evaluate_second_breakout(sym, df60, scfg)
        t_dir, t_rank_score, t_filt = apply_daily_filter(snap_t.direction, sym, daily)
        # 环境闸
        if t_dir != 0:
            if reg.regime == Regime.RANGE:
                t_filt = f"regime_block_RANGE ({reg.reason})"
                t_dir = 0
            elif reg.regime == Regime.NEUTRAL and snap_t.order_now:
                # 中性带禁止新开，允许 hold
                t_filt = f"regime_block_NEUTRAL_new ({reg.reason})"
                t_dir = 0
            elif reg.regime == Regime.NEUTRAL and not snap_t.is_fresh_entry:
                # hold 可过
                pass
            elif reg.regime == Regime.TREND:
                pass

        t_str = (
            _combine_strength(snap_t.strength, t_rank_score, t_dir) if t_dir != 0 else 0.0
        )
        # 试探仓 strength 已在 snap 内缩放
        trend_raw.append(
            SymbolSignal(
                symbol=sym,
                name=name,
                exchange=ex,
                sector=sector,
                direction=t_dir,
                strength=t_str,
                price=snap_t.price,
                daily_vol=snap_t.daily_vol,
                bar_time=snap_t.bar_time,
                breakout_reason=snap_t.reason,
                daily_rank=rank_row.rank if rank_row else None,
                daily_score=rank_row.score if rank_row else None,
                filter_reason=t_filt if snap_t.direction != 0 else snap_t.reason,
                raw_direction=snap_t.direction,
                raw_strength=snap_t.strength,
                entry_tier=snap_t.entry_tier,
                order_now=snap_t.order_now and t_dir != 0,
                order_kind=snap_t.order_kind,
                engine_state=snap_t.engine_state,
            )
        )
        if t_dir != 0:
            result.trend_signals.append(
                UnifiedSignal(
                    book=book_id.value,
                    symbol=sym,
                    name=name,
                    exchange=ex,
                    sector=sector,
                    sleeve="trend",
                    regime=reg.regime.value,
                    regime_reason=reg.reason,
                    direction=t_dir,
                    strength=t_str,
                    price=snap_t.price,
                    daily_vol=snap_t.daily_vol,
                    daily_rank=rank_row.rank if rank_row else None,
                    daily_score=rank_row.score if rank_row else None,
                    reason=snap_t.reason,
                    filter_reason=t_filt,
                    order_now=snap_t.order_now and t_dir != 0,
                    entry_tier=snap_t.entry_tier,
                )
            )

        # ---- 震荡腿：仅 RANGE 环境 ----
        df_r = bars_range.get(sym)
        if df_r is not None and not df_r.empty and reg.allow_range_entry:
            snap_r = evaluate_range_signal(sym, df_r, sys_cfg.range_sig)
            r_dir = snap_r.direction
            r_filt = "range_ok"
            r_rank_score = 0.5
            if r_dir != 0 and sys_cfg.apply_daily_filter:
                r_dir, r_rank_score, r_filt = apply_daily_filter(r_dir, sym, daily)
            # 与趋势互斥：同品种趋势已有方向则关震荡
            if r_dir != 0 and t_dir != 0:
                r_filt = "mutex_trend_active"
                r_dir = 0
            r_str = (
                _combine_strength(snap_r.strength, r_rank_score, r_dir) if r_dir != 0 else 0.0
            )
            # 震荡整体再乘 0.85，避免与趋势抢风险
            r_str *= 0.85
            if r_dir != 0:
                range_raw.append(
                    SymbolSignal(
                        symbol=sym,
                        name=name,
                        exchange=ex,
                        sector=sector,
                        direction=r_dir,
                        strength=r_str,
                        price=snap_r.price,
                        daily_vol=snap_r.daily_vol,
                        bar_time=snap_r.bar_time,
                        breakout_reason=snap_r.reason,
                        daily_rank=rank_row.rank if rank_row else None,
                        daily_score=rank_row.score if rank_row else None,
                        filter_reason=r_filt,
                        raw_direction=snap_r.direction,
                        raw_strength=snap_r.strength,
                        order_now=snap_r.order_now,
                    )
                )
                result.range_signals.append(
                    UnifiedSignal(
                        book=book_id.value,
                        symbol=sym,
                        name=name,
                        exchange=ex,
                        sector=sector,
                        sleeve="range",
                        regime=reg.regime.value,
                        regime_reason=reg.reason,
                        direction=r_dir,
                        strength=r_str,
                        price=snap_r.price,
                        daily_vol=snap_r.daily_vol,
                        daily_rank=rank_row.rank if rank_row else None,
                        daily_score=rank_row.score if rank_row else None,
                        reason=snap_r.reason,
                        filter_reason=r_filt,
                        order_now=snap_r.order_now and r_dir != 0,
                        setup=snap_r.setup,
                        entry_tier=(
                            f"sc={snap_r.failure_score:.0f}|{snap_r.stop_source}|{snap_r.tp1_source}"
                            if snap_r.stop is not None
                            else ""
                        ),
                        entry=snap_r.entry,
                        stop=snap_r.stop,
                        tp1=snap_r.tp1,
                        tp2=snap_r.tp2,
                        current_stop=snap_r.current_stop,
                        tp1_fraction=snap_r.tp1_fraction,
                        initial_risk=snap_r.initial_risk,
                        rr_tp1=snap_r.reward_risk_tp1,
                        rr_tp2=snap_r.reward_risk_tp2,
                        remaining_frac=snap_r.remaining_frac,
                    )
                )
        elif not reg.allow_range_entry:
            pass

    # 板块去重
    t_active = [s for s in trend_raw if s.direction != 0]
    t_dedup = _sector_dedup(t_active, sys_cfg.max_per_sector_trend)
    # 同步 result.trend_signals
    keep_t = {s.symbol for s in t_dedup if s.direction != 0}
    result.trend_signals = [s for s in result.trend_signals if s.symbol in keep_t]
    for s in t_dedup:
        if s.direction == 0 and "sector_dedup" in s.filter_reason:
            result.notes.append(f"trend dedup {s.symbol}: {s.filter_reason}")

    r_active = [s for s in range_raw if s.direction != 0]
    r_dedup = _sector_dedup(r_active, sys_cfg.max_per_sector_range)
    keep_r = {s.symbol for s in r_dedup if s.direction != 0}
    result.range_signals = [s for s in result.range_signals if s.symbol in keep_r]

    result.regime_counts = regime_counts
    result.notes.append(f"regime counts: {regime_counts}")
    result.notes.append(
        f"trend active={len(result.active_trend)} range active={len(result.active_range)}"
    )

    # 仓位：趋势腿用完整资金的目标 vol；震荡腿用独立更小 vol
    # 实操：两腿分开 size，资金同一账户但风险预算分离（简化：各按 capital 计，
    # 震荡 vol 已更小，避免双倍满仓）
    result.trend_risk = _size_sleeve(
        result.active_trend, bcfg.capital, bcfg.trend_vol_annual, max_positions=6
    )
    result.range_risk = _size_sleeve(
        result.active_range, bcfg.capital, bcfg.range_vol_annual, max_positions=5
    )
    return result


def run_system(sys_cfg: SystemConfig | None = None) -> SystemResult:
    sys_cfg = sys_cfg or SystemConfig()
    scfg = sys_cfg.signal
    scfg.force_refresh_daily = sys_cfg.force_refresh_daily
    scfg.force_refresh_klines = sys_cfg.force_refresh_klines
    scfg.cache_only = sys_cfg.cache_only

    out = SystemResult()
    uni_all = []
    for bid in (BookId.FINANCIAL, BookId.COMMODITY):
        uni_all.extend(split_universe()[bid])
    # 去重
    seen = set()
    universe = []
    for t in uni_all:
        if t[0] not in seen:
            seen.add(t[0])
            universe.append(t)

    out.notes.append(f"total universe={len(universe)}")

    # 日线强弱（全市场一份，过滤规则相同）
    daily = load_or_compute_daily_ranks(scfg)
    out.notes.append(f"daily source={daily.source} asof={daily.asof} n={daily.n}")

    # 60m（趋势 + 环境）
    print("[data] loading 60m ...", flush=True)
    bars_60 = _fetch_period_bars(universe, "60", scfg.bar_length_60m, sys_cfg)
    out.notes.append(f"60m loaded={len(bars_60)}")

    # 30m（震荡）
    print("[data] loading 30m ...", flush=True)
    bars_30 = _fetch_period_bars(
        universe, sys_cfg.range_period, sys_cfg.range_bar_length, sys_cfg
    )
    out.notes.append(f"{sys_cfg.range_period}m loaded={len(bars_30)}")

    for bid in (BookId.FINANCIAL, BookId.COMMODITY):
        print(f"[scan] book={bid.value} ...", flush=True)
        out.books[bid] = run_book(bid, sys_cfg, bars_60, bars_30, daily)

    # 价格：用于账簿盯市与成交
    from .ledger import apply_system_ledger, collect_prices_from_bars

    prices = collect_prices_from_bars(bars_60, bars_30)
    out.prices = prices
    out.notes.append(f"mark prices={len(prices)}")

    if sys_cfg.enable_ledger:
        print("[ledger] apply trades / update books ...", flush=True)
        led = apply_system_ledger(
            out,
            prices=prices,
            db_path=sys_cfg.ledger_db,
            max_symbol_loss_frac=sys_cfg.max_symbol_loss_frac,
        )
        out.ledger = led
        out.notes.extend(led.notes)
        for bid in (BookId.FINANCIAL, BookId.COMMODITY):
            acc = led.accounts.get(bid.value)
            if acc and bid in out.books:
                out.books[bid].ledger_equity = acc.equity
                out.books[bid].ledger_cash = acc.cash
                out.books[bid].ledger_positions = sum(
                    1 for p in led.positions if p.book_id == bid.value
                )

    return out


def result_frames(sys_res: SystemResult) -> tuple[pd.DataFrame, pd.DataFrame]:
    """返回 (signals_df, risk_df)。"""
    rows = []
    for bid, br in sys_res.books.items():
        for s in br.trend_signals + br.range_signals:
            rows.append(asdict(s))
    sig_df = pd.DataFrame(rows) if rows else pd.DataFrame()

    risk_rows = []
    for bid, br in sys_res.books.items():
        for sleeve, snap in (("trend", br.trend_risk), ("range", br.range_risk)):
            if snap is None:
                continue
            for p in snap.positions:
                if p.target_lots <= 0:
                    continue
                risk_rows.append(
                    {
                        "book": bid.value,
                        "sleeve": sleeve,
                        "symbol": p.symbol,
                        "direction": p.direction,
                        "lots": p.target_lots,
                        "margin": p.margin,
                        "risk_weight": p.risk_weight,
                        "sector": p.sector,
                        "capital": br.book.capital,
                        "margin_usage": snap.margin_usage,
                    }
                )
    risk_df = pd.DataFrame(risk_rows) if risk_rows else pd.DataFrame()
    return sig_df, risk_df
