"""Daily strength filter via futures_strength_analysis.

Rules (user-specified):
  - Long  signals: daily strength rank must NOT be in the bottom N (default 10)
  - Short signals: daily strength rank must NOT be in the top N (default 10)

Ranking: 1 = strongest (highest 总分), N = weakest.
"""

from __future__ import annotations

import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from .config import SignalConfig

# 保证可 import 顶层 futures_strength_analysis
_WORK = Path(__file__).resolve().parents[1]
if str(_WORK) not in sys.path:
    sys.path.insert(0, str(_WORK))


@dataclass
class DailyRankRow:
    symbol: str
    name: str
    score: float
    rank: int                 # 1 = 最强
    n_universe: int
    allow_long: bool
    allow_short: bool


@dataclass
class DailyFilterResult:
    ranks: dict[str, DailyRankRow]
    source: str               # "db" | "live" | "empty"
    asof: str
    n: int

    def allow(self, symbol: str, direction: int) -> tuple[bool, str]:
        row = self.ranks.get(symbol)
        if row is None:
            return False, "no_daily_rank"
        if direction > 0:
            if not row.allow_long:
                return False, f"long_banned_bottom rank={row.rank}/{row.n_universe} score={row.score:.0f}"
            return True, f"long_ok rank={row.rank}/{row.n_universe} score={row.score:.0f}"
        if direction < 0:
            if not row.allow_short:
                return False, f"short_banned_top rank={row.rank}/{row.n_universe} score={row.score:.0f}"
            return True, f"short_ok rank={row.rank}/{row.n_universe} score={row.score:.0f}"
        return True, "flat"


def _build_ranks(df: pd.DataFrame, cfg: SignalConfig) -> dict[str, DailyRankRow]:
    """df 需含 symbol, name, 总分；按总分降序排名。"""
    if df is None or df.empty:
        return {}
    work = df.copy()
    if "总分" not in work.columns:
        raise ValueError("daily strength frame missing 总分")
    work = work.sort_values("总分", ascending=False).reset_index(drop=True)
    n = len(work)
    out: dict[str, DailyRankRow] = {}
    bottom_start = max(1, n - cfg.ban_long_bottom_n + 1)  # 后 N 名的起始 rank
    top_end = min(n, cfg.ban_short_top_n)                 # 前 N 名的结束 rank

    for i, r in work.iterrows():
        rank = int(i) + 1
        sym = str(r["symbol"])
        # 后 N 名禁多；前 N 名禁空
        allow_long = rank < bottom_start   # rank 1 .. n-N
        allow_short = rank > top_end       # rank N+1 .. n
        # 中间地带两者都允许
        out[sym] = DailyRankRow(
            symbol=sym,
            name=str(r.get("name", "")),
            score=float(r["总分"]),
            rank=rank,
            n_universe=n,
            allow_long=allow_long,
            allow_short=allow_short,
        )
    return out


def _load_daily_from_db(db_path: Path, max_age_hours: float) -> tuple[pd.DataFrame | None, str]:
    """尝试读 daily_analysis_all；用 run_log / 文件新鲜度粗判。"""
    if not Path(db_path).exists():
        return None, "db_missing"
    conn = sqlite3.connect(str(db_path))
    try:
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if "daily_analysis_all" not in tables:
            return None, "no_daily_analysis_all"
        df = pd.read_sql("SELECT * FROM daily_analysis_all", conn)
        if df.empty or "总分" not in df.columns or "symbol" not in df.columns:
            return None, "empty_or_bad_schema"

        # 新鲜度：run_log 里 prefix=daily 的最近时间
        asof = ""
        if "run_log" in tables:
            row = conn.execute(
                "SELECT run_at FROM run_log WHERE prefix='daily' "
                "ORDER BY run_at DESC LIMIT 1"
            ).fetchone()
            if row and row[0]:
                asof = str(row[0])
                try:
                    ts = datetime.strptime(asof[:19], "%Y-%m-%d %H:%M:%S")
                    if datetime.now() - ts > timedelta(hours=max_age_hours):
                        return None, f"stale_run_log asof={asof}"
                except ValueError:
                    pass
        if not asof and "最新日期" in df.columns:
            asof = str(df["最新日期"].iloc[0])
        return df, asof or "db_unknown_time"
    except Exception as e:  # noqa: BLE001
        return None, f"db_error:{e}"
    finally:
        conn.close()


def load_or_compute_daily_ranks(cfg: SignalConfig | None = None) -> DailyFilterResult:
    """加载或现场计算日线强弱排名。"""
    cfg = cfg or SignalConfig()

    if not cfg.force_refresh_daily:
        df, meta = _load_daily_from_db(cfg.db_path, cfg.reuse_daily_db_hours)
        if df is not None:
            ranks = _build_ranks(df, cfg)
            return DailyFilterResult(
                ranks=ranks,
                source="db",
                asof=meta,
                n=len(ranks),
            )

    # 现场调用 futures_strength_analysis.run
    import futures_strength_analysis as fsa

    results_df = fsa.run(
        period=str(cfg.daily_period),
        top=40,
        length=cfg.bar_length_daily,
        db_path=str(cfg.db_path),
        save=True,
        render=False,
    )
    if results_df is None or results_df.empty:
        return DailyFilterResult(ranks={}, source="empty", asof="", n=0)

    ranks = _build_ranks(results_df, cfg)
    return DailyFilterResult(
        ranks=ranks,
        source="live",
        asof=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        n=len(ranks),
    )


def apply_daily_filter(
    direction: int,
    symbol: str,
    daily: DailyFilterResult,
) -> tuple[int, float, str]:
    """返回 (filtered_direction, rank_score_01, reason).

    rank_score_01：用排名映射到 0~1，供 strength 微调（强→高）。
    """
    if direction == 0:
        return 0, 0.0, "flat"

    ok, reason = daily.allow(symbol, direction)
    row = daily.ranks.get(symbol)
    if row is None:
        return 0, 0.0, reason

    # rank 1 → 1.0, rank n → 0.0
    n = max(row.n_universe - 1, 1)
    rank_score = 1.0 - (row.rank - 1) / n
    if not ok:
        return 0, rank_score, reason
    return direction, rank_score, reason
