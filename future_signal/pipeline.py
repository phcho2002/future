"""End-to-end scan: 60m breakout × daily strength filter → risk signals."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path

import pandas as pd

from .config import SignalConfig
from .daily_filter import DailyFilterResult, apply_daily_filter, load_or_compute_daily_ranks
from .second_breakout import BreakoutSnapshot, evaluate_second_breakout

_WORK = Path(__file__).resolve().parents[1]
if str(_WORK) not in sys.path:
    sys.path.insert(0, str(_WORK))

# 板块映射与 future_risk 对齐
try:
    from future_risk.config import SECTOR_MAP
except Exception:  # noqa: BLE001
    SECTOR_MAP = {}


@dataclass
class SymbolSignal:
    symbol: str
    name: str
    exchange: str
    sector: str
    direction: int
    strength: float
    price: float
    daily_vol: float
    bar_time: str
    breakout_reason: str
    daily_rank: int | None
    daily_score: float | None
    filter_reason: str
    raw_direction: int
    raw_strength: float
    vol_ratio: float = 1.0
    engine_state: str = "IDLE"
    hist_signal_count: int = 0
    last_signal_bars_ago: int | None = None
    last_signal_side: int | None = None
    pattern_resistance: float | None = None
    pattern_support: float | None = None
    is_fresh_entry: bool = False
    entry_tier: str = "none"       # none | first_probe | second_full
    order_now: bool = False        # 本根是否应下单（开/加）
    order_kind: str = ""           # first_probe | second_full | second_full_add


@dataclass
class ScanResult:
    signals: list[SymbolSignal] = field(default_factory=list)
    rejected: list[SymbolSignal] = field(default_factory=list)
    all_rows: list[SymbolSignal] = field(default_factory=list)  # 含 flat，便于看状态机
    daily: DailyFilterResult | None = None
    notes: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def active(self) -> list[SymbolSignal]:
        return [s for s in self.signals if s.direction != 0]

    @property
    def orders_now(self) -> list[SymbolSignal]:
        """仅本根需要开/加仓的品种（二次满仓或首次试探）。"""
        return [s for s in self.signals if s.order_now and s.direction != 0]

    @property
    def setups(self) -> list[SymbolSignal]:
        """进行中的 setup（尚未持仓，或仅在监控）。"""
        watch = {
            "PRIMED_LONG", "BROKEN_LONG", "FAILED_LONG",
            "BROKEN_SHORT", "FAILED_SHORT",
        }
        return [
            s for s in self.all_rows
            if s.engine_state in watch and s.direction == 0
        ]


def _load_universe(cfg: SignalConfig) -> list[tuple[str, str, str]]:
    path = Path(cfg.top40_json)
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    return [(a, b, c) for a, b, c in raw["symbols"]]


def _fetch_60m_bars(
    universe: list[tuple[str, str, str]],
    cfg: SignalConfig,
) -> dict[str, pd.DataFrame]:
    """拉 60m K 线。

    - force_refresh_klines=True：全品种 get_klines(force=True) 联网，写回缓存
    - cache_only=True：只读本地
    - 默认：缓存命中且够长则用缓存，缺失再联网
    """
    out: dict[str, pd.DataFrame] = {}
    try:
        from future_data import get_klines, load_cached
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"future_data unavailable: {e}") from e

    period = str(cfg.signal_period)

    if cfg.force_refresh_klines:
        n = len(universe)
        for i, (sym, _name, ex) in enumerate(universe, 1):
            try:
                print(f"  [60m {i}/{n}] force fetch {sym} ...", flush=True)
                df = get_klines(
                    sym,
                    ex,
                    period=period,
                    length=int(cfg.bar_length_60m),
                    ttl_hours=float(cfg.kline_ttl_hours),
                    force=True,
                )
                if df is not None and not df.empty:
                    out[sym] = df
            except Exception as e:  # noqa: BLE001
                print(f"  [60m {i}/{n}] {sym} FAIL: {e}", flush=True)
        return out

    missing: list[tuple[str, str, str]] = []
    for sym, name, ex in universe:
        df = load_cached(sym, period)
        if df is not None and not df.empty and len(df) >= 80:
            out[sym] = df
        else:
            missing.append((sym, name, ex))

    if not missing or cfg.cache_only:
        return out

    for sym, _name, ex in missing:
        try:
            df = get_klines(
                sym,
                ex,
                period=period,
                length=int(cfg.bar_length_60m),
                ttl_hours=float(cfg.kline_ttl_hours),
                force=False,
            )
            if df is not None and not df.empty:
                out[sym] = df
        except Exception:
            continue
    return out


def _combine_strength(raw_s: float, rank_score: float, direction: int) -> float:
    """突破质量 70% + 日线排名分 30%（多头偏好高 rank_score，空头偏好低）。"""
    if direction > 0:
        rs = rank_score
    elif direction < 0:
        rs = 1.0 - rank_score  # 越弱越利于空
    else:
        return 0.0
    return float(min(1.0, max(0.0, 0.70 * raw_s + 0.30 * rs)))


def _sector_dedup(sigs: list[SymbolSignal], max_per_sector: int) -> list[SymbolSignal]:
    if max_per_sector <= 0:
        return sigs
    # 同向同板块按 strength 降序保留前 N
    from collections import defaultdict

    buckets: dict[tuple[str, int], list[SymbolSignal]] = defaultdict(list)
    flat: list[SymbolSignal] = []
    for s in sigs:
        if s.direction == 0:
            flat.append(s)
            continue
        buckets[(s.sector, s.direction)].append(s)

    kept: list[SymbolSignal] = list(flat)
    for (_sec, _d), items in buckets.items():
        items.sort(key=lambda x: x.strength, reverse=True)
        kept.extend(items[:max_per_sector])
        for drop in items[max_per_sector:]:
            drop.filter_reason = f"sector_dedup>{max_per_sector} ({drop.sector})"
            drop.direction = 0
            kept.append(drop)
    return kept


def run_scan(cfg: SignalConfig | None = None, bars: dict[str, pd.DataFrame] | None = None) -> ScanResult:
    """扫描 top40：60m 突破 + 日线过滤 + 板块去重。"""
    cfg = cfg or SignalConfig()
    result = ScanResult()

    universe = _load_universe(cfg)
    result.notes.append(f"universe={len(universe)} from {cfg.top40_json.name}")

    # 1) 日线强弱
    daily = load_or_compute_daily_ranks(cfg)
    result.daily = daily
    result.notes.append(
        f"daily_filter source={daily.source} asof={daily.asof} n={daily.n} "
        f"ban_long_bottom={cfg.ban_long_bottom_n} ban_short_top={cfg.ban_short_top_n}"
    )
    if daily.n == 0:
        result.errors.append("daily strength ranking empty — abort filter (all blocked)")
        return result

    # 2) 60m 数据
    if bars is None:
        try:
            bars = _fetch_60m_bars(universe, cfg)
        except Exception as e:  # noqa: BLE001
            result.errors.append(f"fetch_60m failed: {e}")
            return result
    result.notes.append(f"60m bars loaded: {len(bars)}/{len(universe)}")

    name_ex = {s: (n, e) for s, n, e in universe}
    raw_list: list[SymbolSignal] = []

    for sym, (_name, ex) in name_ex.items():
        name, exchange = name_ex[sym]
        df = bars.get(sym)
        if df is None:
            result.errors.append(f"{sym}: no 60m data")
            continue
        # 主信号：蓄势 → 假突破 → 失败确认 → 二次真突破
        snap: BreakoutSnapshot = evaluate_second_breakout(sym, df, cfg)
        sector = SECTOR_MAP.get(sym, "other")

        raw_dir = snap.direction
        raw_str = snap.strength
        filt_dir, rank_score, filt_reason = apply_daily_filter(raw_dir, sym, daily)

        rank_row = daily.ranks.get(sym)
        strength = (
            _combine_strength(raw_str, rank_score, filt_dir) if filt_dir != 0 else 0.0
        )

        sig = SymbolSignal(
            symbol=sym,
            name=name,
            exchange=exchange,
            sector=sector,
            direction=filt_dir,
            strength=strength,
            price=snap.price,
            daily_vol=snap.daily_vol,
            bar_time=snap.bar_time,
            breakout_reason=snap.reason,
            daily_rank=rank_row.rank if rank_row else None,
            daily_score=rank_row.score if rank_row else None,
            filter_reason=filt_reason if raw_dir != 0 else snap.reason,
            raw_direction=raw_dir,
            raw_strength=raw_str,
            vol_ratio=snap.vol_ratio,
            engine_state=snap.engine_state,
            hist_signal_count=snap.hist_signal_count,
            last_signal_bars_ago=snap.last_signal_bars_ago,
            last_signal_side=snap.last_signal_side,
            pattern_resistance=snap.pattern_resistance,
            pattern_support=snap.pattern_support,
            is_fresh_entry=snap.is_fresh_entry,
            entry_tier=snap.entry_tier,
            order_now=snap.order_now,
            order_kind=snap.order_kind,
        )
        raw_list.append(sig)

    # 3) 板块去重（仅对仍有效方向）
    active = [s for s in raw_list if s.direction != 0]
    inactive = [s for s in raw_list if s.direction == 0]
    deduped = _sector_dedup(active, cfg.max_per_sector)
    still_active = [s for s in deduped if s.direction != 0]
    newly_rejected = [s for s in deduped if s.direction == 0]

    result.signals = still_active
    result.rejected = inactive + newly_rejected
    result.all_rows = still_active + result.rejected
    hist_total = sum(s.hist_signal_count for s in raw_list)
    result.notes.append(
        f"active={len(still_active)} rejected={len(result.rejected)} "
        f"(in_position_raw={sum(1 for s in raw_list if s.raw_direction != 0)}) "
        f"hist_second_breakouts={hist_total} setups={sum(1 for s in raw_list if s.engine_state not in ('IDLE',))}"
    )
    return result


def to_risk_signals(scan: ScanResult, orders_only: bool = False):
    """转换为 future_risk.SignalInput 列表。

    Parameters
    ----------
    orders_only :
        True → 只含本根需下单的品种（二次满仓 / 首次试探开仓）。
        False → 含所有当前持仓（试探 hold + 满仓 hold），strength 已按档位缩放。
    """
    from future_risk.engine import SignalInput

    src = scan.orders_now if orders_only else scan.active
    out = []
    for s in src:
        out.append(
            SignalInput(
                symbol=s.symbol,
                direction=s.direction,
                strength=s.strength,  # first_probe 已 × first_probe_strength_scale
                price=s.price if s.price > 0 else None,
                daily_vol=s.daily_vol,
                contract=None,
                current_lots=0,
            )
        )
    return out


def size_with_risk(scan: ScanResult, cfg: SignalConfig | None = None):
    """跑 future_risk 仓位。"""
    from future_risk.config import RiskConfig
    from future_risk.engine import RiskEngine

    cfg = cfg or SignalConfig()
    rcfg = RiskConfig(capital=cfg.capital, target_vol_annual=cfg.target_vol_annual)
    engine = RiskEngine(rcfg)
    inputs = to_risk_signals(scan)
    return engine.size(inputs)


def result_to_frame(scan: ScanResult, include_rejected: bool = True) -> pd.DataFrame:
    rows = []
    for s in scan.signals:
        d = asdict(s)
        d["status"] = "active"
        rows.append(d)
    if include_rejected:
        for s in scan.rejected:
            if s.raw_direction == 0 and s.breakout_reason == "flat":
                continue  # 跳过纯空仓噪音
            d = asdict(s)
            d["status"] = "rejected"
            rows.append(d)
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows)
