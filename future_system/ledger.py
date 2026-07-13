"""交易记录 + 账簿（SQLite）。

每次运行：
  1. 读取账户资金与持仓
  2. 按最新价检查硬止损 / 止损止盈
  3. 结合信号目标手数：开仓 / 加仓 / 减仓 / 平仓
  4. 写入 trades，更新 positions 与 accounts

库路径默认：future_system/data/trading_ledger.db
同时导出 JSON 快照：future_system/data/ledger_snapshot.json
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from future_risk.config import RiskConfig, SECTOR_MAP, DEFAULT_MARGIN_RATE
from future_risk.engine import (
    RiskEngine,
    SignalInput,
    position_pnl_cny,
)
from future_risk.universe import load_multipliers, load_universe

from .books import BookId, book_config

WORK_AI = Path(__file__).resolve().parents[1]
DEFAULT_DB = WORK_AI / "future_system" / "data" / "trading_ledger.db"
DEFAULT_JSON = WORK_AI / "future_system" / "data" / "ledger_snapshot.json"

# 动作常量
ACT_OPEN = "open"
ACT_ADD = "add"
ACT_REDUCE = "reduce"
ACT_CLOSE = "close"
ACT_STOP = "stop"
ACT_TP1 = "tp1"
ACT_TP2 = "tp2"
ACT_HARD_STOP = "hard_stop"
ACT_FLIP_CLOSE = "flip_close"
ACT_FLIP_OPEN = "flip_open"


@dataclass
class PositionRow:
    book_id: str
    sleeve: str
    symbol: str
    name: str
    direction: int
    lots: int
    entry_price: float
    stop: float | None = None
    tp1: float | None = None
    tp2: float | None = None
    tp1_hit: bool = False
    remaining_frac: float = 1.0
    opened_at: str = ""
    updated_at: str = ""


@dataclass
class AccountRow:
    book_id: str
    initial_capital: float
    cash: float                 # 可用（未占用保证金）
    reserved_margin: float      # 持仓占用保证金
    equity: float               # cash + reserved + unrealized
    realized_pnl: float = 0.0
    updated_at: str = ""


@dataclass
class TradeRow:
    run_id: str
    book_id: str
    sleeve: str
    symbol: str
    action: str
    direction: int
    lots: int
    price: float
    pnl: float
    fee: float
    cash_after: float
    equity_after: float
    reason: str
    created_at: str = ""


@dataclass
class LedgerRunResult:
    run_id: str
    trades: list[TradeRow] = field(default_factory=list)
    accounts: dict[str, AccountRow] = field(default_factory=dict)
    positions: list[PositionRow] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


class TradeLedger:
    """SQLite 账簿。"""

    def __init__(self, db_path: Path | str | None = None, fee_rate: float = 0.0001):
        self.db_path = Path(db_path) if db_path else DEFAULT_DB
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.fee_rate = float(fee_rate)
        self._init_db()
        # 乘数缓存
        try:
            self._mult = load_multipliers()
        except Exception:
            self._mult = {}
        try:
            self._universe = {p.symbol: p for p in load_universe()}
        except Exception:
            self._universe = {}

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS accounts (
                    book_id TEXT PRIMARY KEY,
                    initial_capital REAL NOT NULL,
                    cash REAL NOT NULL,
                    reserved_margin REAL NOT NULL DEFAULT 0,
                    equity REAL NOT NULL,
                    realized_pnl REAL NOT NULL DEFAULT 0,
                    updated_at TEXT
                );
                CREATE TABLE IF NOT EXISTS positions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    book_id TEXT NOT NULL,
                    sleeve TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    name TEXT,
                    direction INTEGER NOT NULL,
                    lots INTEGER NOT NULL,
                    entry_price REAL NOT NULL,
                    stop REAL,
                    tp1 REAL,
                    tp2 REAL,
                    tp1_hit INTEGER DEFAULT 0,
                    remaining_frac REAL DEFAULT 1.0,
                    opened_at TEXT,
                    updated_at TEXT,
                    UNIQUE(book_id, sleeve, symbol)
                );
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    book_id TEXT NOT NULL,
                    sleeve TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    action TEXT NOT NULL,
                    direction INTEGER NOT NULL,
                    lots INTEGER NOT NULL,
                    price REAL NOT NULL,
                    pnl REAL NOT NULL DEFAULT 0,
                    fee REAL NOT NULL DEFAULT 0,
                    cash_after REAL,
                    equity_after REAL,
                    reason TEXT,
                    created_at TEXT
                );
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    started_at TEXT,
                    finished_at TEXT,
                    notes TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_trades_run ON trades(run_id);
                CREATE INDEX IF NOT EXISTS idx_trades_book ON trades(book_id, created_at);
                """
            )

    # ── 账户 / 持仓读写 ──────────────────────────────────────
    def ensure_account(self, book_id: str, initial_capital: float) -> AccountRow:
        now = _now()
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM accounts WHERE book_id=?", (book_id,)
            ).fetchone()
            if row:
                return _account_from_row(row)
            conn.execute(
                """INSERT INTO accounts
                   (book_id, initial_capital, cash, reserved_margin, equity, realized_pnl, updated_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (book_id, initial_capital, initial_capital, 0.0, initial_capital, 0.0, now),
            )
        return AccountRow(
            book_id=book_id,
            initial_capital=initial_capital,
            cash=initial_capital,
            reserved_margin=0.0,
            equity=initial_capital,
            realized_pnl=0.0,
            updated_at=now,
        )

    def load_account(self, book_id: str) -> AccountRow | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM accounts WHERE book_id=?", (book_id,)
            ).fetchone()
        return _account_from_row(row) if row else None

    def load_positions(self, book_id: str | None = None) -> list[PositionRow]:
        with self._conn() as conn:
            if book_id:
                rows = conn.execute(
                    "SELECT * FROM positions WHERE book_id=? AND lots<>0", (book_id,)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM positions WHERE lots<>0"
                ).fetchall()
        return [_position_from_row(r) for r in rows]

    def save_account(self, acc: AccountRow) -> None:
        acc.updated_at = _now()
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO accounts
                   (book_id, initial_capital, cash, reserved_margin, equity, realized_pnl, updated_at)
                   VALUES (?,?,?,?,?,?,?)
                   ON CONFLICT(book_id) DO UPDATE SET
                     cash=excluded.cash,
                     reserved_margin=excluded.reserved_margin,
                     equity=excluded.equity,
                     realized_pnl=excluded.realized_pnl,
                     updated_at=excluded.updated_at
                """,
                (
                    acc.book_id, acc.initial_capital, acc.cash, acc.reserved_margin,
                    acc.equity, acc.realized_pnl, acc.updated_at,
                ),
            )

    def upsert_position(self, pos: PositionRow) -> None:
        pos.updated_at = _now()
        if not pos.opened_at:
            pos.opened_at = pos.updated_at
        with self._conn() as conn:
            if pos.lots == 0:
                conn.execute(
                    "DELETE FROM positions WHERE book_id=? AND sleeve=? AND symbol=?",
                    (pos.book_id, pos.sleeve, pos.symbol),
                )
                return
            conn.execute(
                """INSERT INTO positions
                   (book_id, sleeve, symbol, name, direction, lots, entry_price,
                    stop, tp1, tp2, tp1_hit, remaining_frac, opened_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(book_id, sleeve, symbol) DO UPDATE SET
                     name=excluded.name,
                     direction=excluded.direction,
                     lots=excluded.lots,
                     entry_price=excluded.entry_price,
                     stop=excluded.stop,
                     tp1=excluded.tp1,
                     tp2=excluded.tp2,
                     tp1_hit=excluded.tp1_hit,
                     remaining_frac=excluded.remaining_frac,
                     updated_at=excluded.updated_at
                """,
                (
                    pos.book_id, pos.sleeve, pos.symbol, pos.name, pos.direction,
                    pos.lots, pos.entry_price, pos.stop, pos.tp1, pos.tp2,
                    1 if pos.tp1_hit else 0, pos.remaining_frac,
                    pos.opened_at, pos.updated_at,
                ),
            )

    def insert_trade(self, t: TradeRow) -> None:
        if not t.created_at:
            t.created_at = _now()
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO trades
                   (run_id, book_id, sleeve, symbol, action, direction, lots, price,
                    pnl, fee, cash_after, equity_after, reason, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    t.run_id, t.book_id, t.sleeve, t.symbol, t.action, t.direction,
                    t.lots, t.price, t.pnl, t.fee, t.cash_after, t.equity_after,
                    t.reason, t.created_at,
                ),
            )

    def start_run(self, notes: str = "") -> str:
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid4().hex[:6]
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO runs (run_id, started_at, notes) VALUES (?,?,?)",
                (run_id, _now(), notes),
            )
        return run_id

    def finish_run(self, run_id: str, notes: str = "") -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE runs SET finished_at=?, notes=COALESCE(notes,'')||? WHERE run_id=?",
                (_now(), (" | " + notes) if notes else "", run_id),
            )

    def recent_trades(self, book_id: str | None = None, limit: int = 50) -> list[dict]:
        with self._conn() as conn:
            if book_id:
                rows = conn.execute(
                    "SELECT * FROM trades WHERE book_id=? ORDER BY id DESC LIMIT ?",
                    (book_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM trades ORDER BY id DESC LIMIT ?", (limit,)
                ).fetchall()
        return [dict(r) for r in rows]

    # ── 工具 ────────────────────────────────────────────────
    def multiplier(self, symbol: str) -> float:
        if symbol in self._mult:
            return float(self._mult[symbol])
        prod = self._universe.get(symbol)
        if prod:
            return float(prod.multiplier)
        return 1.0

    def margin_rate(self, symbol: str) -> float:
        sector = SECTOR_MAP.get(symbol, "chem")
        return float(DEFAULT_MARGIN_RATE.get(sector, 0.10))

    def margin_for(self, symbol: str, lots: int, price: float) -> float:
        return abs(lots) * self.multiplier(symbol) * abs(price) * self.margin_rate(symbol)

    def fee_for(self, symbol: str, lots: int, price: float) -> float:
        notional = abs(lots) * self.multiplier(symbol) * abs(price)
        return notional * self.fee_rate

    def mark_unrealized(
        self, positions: list[PositionRow], prices: dict[str, float]
    ) -> float:
        total = 0.0
        for p in positions:
            px = prices.get(p.symbol)
            if px is None or p.lots == 0:
                continue
            total += position_pnl_cny(
                p.direction, p.lots, p.entry_price, px, self.multiplier(p.symbol)
            )
        return total

    def refresh_equity(
        self, acc: AccountRow, positions: list[PositionRow], prices: dict[str, float]
    ) -> AccountRow:
        unreal = self.mark_unrealized(positions, prices)
        acc.equity = acc.cash + acc.reserved_margin + unreal
        return acc

    def export_json(self, path: Path | str | None = None) -> Path:
        path = Path(path) if path else DEFAULT_JSON
        path.parent.mkdir(parents=True, exist_ok=True)
        books = {}
        for bid in (BookId.FINANCIAL.value, BookId.COMMODITY.value):
            acc = self.load_account(bid)
            pos = self.load_positions(bid)
            books[bid] = {
                "account": asdict(acc) if acc else None,
                "positions": [asdict(p) for p in pos],
                "recent_trades": self.recent_trades(bid, 30),
            }
        path.write_text(
            json.dumps({"updated_at": _now(), "books": books}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return path


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _account_from_row(r: sqlite3.Row) -> AccountRow:
    return AccountRow(
        book_id=r["book_id"],
        initial_capital=float(r["initial_capital"]),
        cash=float(r["cash"]),
        reserved_margin=float(r["reserved_margin"]),
        equity=float(r["equity"]),
        realized_pnl=float(r["realized_pnl"] or 0),
        updated_at=r["updated_at"] or "",
    )


def _position_from_row(r: sqlite3.Row) -> PositionRow:
    return PositionRow(
        book_id=r["book_id"],
        sleeve=r["sleeve"],
        symbol=r["symbol"],
        name=r["name"] or r["symbol"],
        direction=int(r["direction"]),
        lots=int(r["lots"]),
        entry_price=float(r["entry_price"]),
        stop=float(r["stop"]) if r["stop"] is not None else None,
        tp1=float(r["tp1"]) if r["tp1"] is not None else None,
        tp2=float(r["tp2"]) if r["tp2"] is not None else None,
        tp1_hit=bool(r["tp1_hit"]),
        remaining_frac=float(r["remaining_frac"] or 1.0),
        opened_at=r["opened_at"] or "",
        updated_at=r["updated_at"] or "",
    )


# ═══════════════════════════════════════════════════════════
# 运行时：结合信号 / 风控目标 更新账簿
# ═══════════════════════════════════════════════════════════

@dataclass
class SleeveTarget:
    """某 sleeve 上风控给出的目标。"""
    symbol: str
    name: str
    direction: int
    target_lots: int
    price: float
    stop: float | None = None
    tp1: float | None = None
    tp2: float | None = None
    strength: float = 1.0
    daily_vol: float | None = None
    force_stopped: bool = False


def process_book_trades(
    ledger: TradeLedger,
    book_id: str,
    initial_capital: float,
    prices: dict[str, float],
    trend_targets: list[SleeveTarget],
    range_targets: list[SleeveTarget],
    run_id: str,
    max_symbol_loss_frac: float = 0.0095,
    trend_vol: float = 0.12,
    range_vol: float = 0.05,
) -> tuple[AccountRow, list[PositionRow], list[TradeRow], list[str]]:
    """对单个账本：出场检查 → 按目标调仓 → 落库。"""
    notes: list[str] = []
    trades: list[TradeRow] = []
    acc = ledger.ensure_account(book_id, initial_capital)
    positions = ledger.load_positions(book_id)
    pos_map: dict[tuple[str, str], PositionRow] = {
        (p.sleeve, p.symbol): p for p in positions
    }

    # 先刷新权益
    acc = ledger.refresh_equity(acc, list(pos_map.values()), prices)
    loss_cap = acc.equity * max_symbol_loss_frac

    # ── 1) 现有持仓：硬止损 + 结构止损/止盈 ──
    for key in list(pos_map.keys()):
        p = pos_map[key]
        px = prices.get(p.symbol)
        if px is None or p.lots <= 0:
            continue
        mult = ledger.multiplier(p.symbol)
        pnl = position_pnl_cny(p.direction, p.lots, p.entry_price, px, mult)

        # 硬止损
        if pnl <= -loss_cap:
            t, acc = _close_position(
                ledger, acc, p, px, run_id, ACT_HARD_STOP,
                f"loss {pnl:,.0f} <= -{loss_cap:,.0f} ({max_symbol_loss_frac:.2%} equity)",
                lots=p.lots,
            )
            trades.append(t)
            del pos_map[key]
            notes.append(f"HARD_STOP {p.sleeve}/{p.symbol} pnl={pnl:,.0f}")
            continue

        # 止损价
        if p.stop is not None:
            hit = (p.direction == 1 and px <= p.stop) or (
                p.direction == -1 and px >= p.stop
            )
            if hit:
                t, acc = _close_position(
                    ledger, acc, p, px, run_id, ACT_STOP,
                    f"stop hit @ {p.stop}", lots=p.lots,
                )
                trades.append(t)
                del pos_map[key]
                notes.append(f"STOP {p.sleeve}/{p.symbol} @ {px}")
                continue

        # TP1 分批
        if p.tp1 is not None and not p.tp1_hit and p.remaining_frac > 0.99:
            hit_tp1 = (p.direction == 1 and px >= p.tp1) or (
                p.direction == -1 and px <= p.tp1
            )
            if hit_tp1:
                close_lots = max(1, int(round(p.lots * 0.5)))
                close_lots = min(close_lots, p.lots)
                t, acc, p = _reduce_position(
                    ledger, acc, p, px, run_id, ACT_TP1,
                    f"tp1 @ {p.tp1}", close_lots,
                )
                trades.append(t)
                p.tp1_hit = True
                p.remaining_frac = p.lots / max(p.lots + close_lots, 1)  # 剩余比例粗记
                if p.lots <= 0:
                    del pos_map[key]
                else:
                    pos_map[key] = p
                    ledger.upsert_position(p)
                notes.append(f"TP1 {p.sleeve}/{p.symbol} lots={close_lots}")
                continue

        # TP2 全平剩余
        if p.tp2 is not None and p.lots > 0:
            hit_tp2 = (p.direction == 1 and px >= p.tp2) or (
                p.direction == -1 and px <= p.tp2
            )
            if hit_tp2:
                t, acc = _close_position(
                    ledger, acc, p, px, run_id, ACT_TP2,
                    f"tp2 @ {p.tp2}", lots=p.lots,
                )
                trades.append(t)
                del pos_map[key]
                notes.append(f"TP2 {p.sleeve}/{p.symbol} @ {px}")
                continue

    acc = ledger.refresh_equity(acc, list(pos_map.values()), prices)

    # ── 2) 按 sleeve 目标调仓（信任上游 risk 目标手数，再做现金/硬止损约束）──
    for sleeve, targets, tvol in (
        ("trend", trend_targets, trend_vol),
        ("range", range_targets, range_vol),
    ):
        _ = tvol  # 预留：若将来要二次 vol 缩放
        sleeve_pos = {k[1]: v for k, v in pos_map.items() if k[0] == sleeve}
        tgt_map = {t.symbol: t for t in targets}

        # 账上有、信号无 → 目标 0（平掉）
        for sym, p in sleeve_pos.items():
            if sym not in tgt_map:
                tgt_map[sym] = SleeveTarget(
                    symbol=sym, name=p.name, direction=p.direction,
                    target_lots=0, price=prices.get(sym, p.entry_price),
                    stop=p.stop, tp1=p.tp1, tp2=p.tp2,
                )

        # signed 目标手数；force_stopped / 浮亏触线 → 0
        sized: dict[str, int] = {}
        for sym, tgt in tgt_map.items():
            px = prices.get(sym, tgt.price)
            cur = sleeve_pos.get(sym)
            if tgt.force_stopped or tgt.target_lots <= 0:
                sized[sym] = 0
                continue
            # 二次硬止损检查（持仓中）
            if cur and cur.lots > 0 and px:
                unreal = position_pnl_cny(
                    cur.direction, cur.lots, cur.entry_price, px,
                    ledger.multiplier(sym),
                )
                if unreal <= -loss_cap:
                    sized[sym] = 0
                    notes.append(
                        f"[{sleeve}] HARD_STOP resize {sym} pnl={unreal:,.0f}"
                    )
                    continue
            # 按止损距离钳制手数（0.95% 资金）
            lots = int(tgt.target_lots)
            entry = float(cur.entry_price) if cur else float(px)
            stop = tgt.stop
            if stop is not None and lots > 0:
                from future_risk.engine import loss_per_lot_to_stop

                r1 = loss_per_lot_to_stop(
                    tgt.direction, entry, float(stop), ledger.multiplier(sym)
                )
                if r1 > 0:
                    max_l = int(loss_cap // r1)
                    if lots > max_l:
                        notes.append(
                            f"[{sleeve}] {sym} lots {lots}->{max_l} (0.95% loss cap)"
                        )
                        lots = max(0, max_l)
            sized[sym] = lots * int(tgt.direction) if lots > 0 else 0

        # 应用 delta
        for sym, signed_tgt in sized.items():
            px = prices.get(sym) or tgt_map[sym].price
            cur = sleeve_pos.get(sym)
            cur_signed = (cur.lots * cur.direction) if cur else 0
            tgt_lots = abs(int(signed_tgt))
            tgt_dir = 1 if signed_tgt > 0 else (-1 if signed_tgt < 0 else 0)

            if cur_signed == signed_tgt:
                # 更新止损止盈价
                if cur and sym in tgt_map:
                    t = tgt_map[sym]
                    if t.stop is not None:
                        cur.stop = t.stop
                    if t.tp1 is not None:
                        cur.tp1 = t.tp1
                    if t.tp2 is not None:
                        cur.tp2 = t.tp2
                    ledger.upsert_position(cur)
                continue

            # 反向：先平后开
            if cur and tgt_dir != 0 and cur.direction != tgt_dir and cur.lots > 0:
                t, acc = _close_position(
                    ledger, acc, cur, px, run_id, ACT_FLIP_CLOSE,
                    "flip close", lots=cur.lots,
                )
                trades.append(t)
                pos_map.pop((sleeve, sym), None)
                sleeve_pos.pop(sym, None)
                cur = None

            if tgt_dir == 0:
                if cur and cur.lots > 0:
                    t, acc = _close_position(
                        ledger, acc, cur, px, run_id, ACT_CLOSE,
                        "target flat", lots=cur.lots,
                    )
                    trades.append(t)
                    pos_map.pop((sleeve, sym), None)
                    sleeve_pos.pop(sym, None)
                continue

            if cur is None or cur.lots == 0:
                # 开仓
                lots = tgt_lots
                lots = _affordable_lots(ledger, acc, sym, lots, px)
                if lots <= 0:
                    notes.append(f"skip open {sleeve}/{sym}: insufficient cash")
                    continue
                tinfo = tgt_map.get(sym)
                name = tinfo.name if tinfo else sym
                t, acc, new_p = _open_position(
                    ledger, acc, book_id, sleeve, sym, name, tgt_dir, lots, px, run_id,
                    stop=tinfo.stop if tinfo else None,
                    tp1=tinfo.tp1 if tinfo else None,
                    tp2=tinfo.tp2 if tinfo else None,
                    reason="signal open",
                )
                trades.append(t)
                pos_map[(sleeve, sym)] = new_p
                sleeve_pos[sym] = new_p
                notes.append(f"OPEN {sleeve}/{sym} dir={tgt_dir:+d} lots={lots} @ {px}")
            else:
                # 同向加减
                if tgt_lots > cur.lots:
                    add = tgt_lots - cur.lots
                    add = _affordable_lots(ledger, acc, sym, add, px)
                    if add <= 0:
                        continue
                    t, acc, cur = _add_position(
                        ledger, acc, cur, add, px, run_id, "signal add",
                    )
                    trades.append(t)
                    pos_map[(sleeve, sym)] = cur
                    sleeve_pos[sym] = cur
                    notes.append(f"ADD {sleeve}/{sym} +{add} @ {px}")
                elif tgt_lots < cur.lots:
                    red = cur.lots - tgt_lots
                    t, acc, cur = _reduce_position(
                        ledger, acc, cur, px, run_id, ACT_REDUCE,
                        "signal reduce", red,
                    )
                    trades.append(t)
                    if cur.lots <= 0:
                        pos_map.pop((sleeve, sym), None)
                        sleeve_pos.pop(sym, None)
                    else:
                        pos_map[(sleeve, sym)] = cur
                        sleeve_pos[sym] = cur
                    notes.append(f"REDUCE {sleeve}/{sym} -{red} @ {px}")

    # ── 3) 落盘账户 ──
    final_pos = list(pos_map.values())
    acc = ledger.refresh_equity(acc, final_pos, prices)
    ledger.save_account(acc)
    # 清理 0 手
    for p in final_pos:
        ledger.upsert_position(p)

    return acc, final_pos, trades, notes


def _affordable_lots(
    ledger: TradeLedger, acc: AccountRow, symbol: str, lots: int, price: float
) -> int:
    if lots <= 0:
        return 0
    m1 = ledger.margin_for(symbol, 1, price)
    fee1 = ledger.fee_for(symbol, 1, price)
    need1 = m1 + fee1
    if need1 <= 0:
        return lots
    max_lots = int(acc.cash // need1)
    return max(0, min(lots, max_lots))


def _open_position(
    ledger: TradeLedger,
    acc: AccountRow,
    book_id: str,
    sleeve: str,
    symbol: str,
    name: str,
    direction: int,
    lots: int,
    price: float,
    run_id: str,
    stop: float | None,
    tp1: float | None,
    tp2: float | None,
    reason: str,
) -> tuple[TradeRow, AccountRow, PositionRow]:
    margin = ledger.margin_for(symbol, lots, price)
    fee = ledger.fee_for(symbol, lots, price)
    acc.cash -= margin + fee
    acc.reserved_margin += margin
    pos = PositionRow(
        book_id=book_id,
        sleeve=sleeve,
        symbol=symbol,
        name=name,
        direction=direction,
        lots=lots,
        entry_price=price,
        stop=stop,
        tp1=tp1,
        tp2=tp2,
        tp1_hit=False,
        remaining_frac=1.0,
        opened_at=_now(),
    )
    ledger.upsert_position(pos)
    acc = ledger.refresh_equity(acc, ledger.load_positions(book_id), {symbol: price})
    tr = TradeRow(
        run_id=run_id, book_id=book_id, sleeve=sleeve, symbol=symbol,
        action=ACT_OPEN, direction=direction, lots=lots, price=price,
        pnl=0.0, fee=fee, cash_after=acc.cash, equity_after=acc.equity, reason=reason,
    )
    ledger.insert_trade(tr)
    ledger.save_account(acc)
    return tr, acc, pos


def _add_position(
    ledger: TradeLedger,
    acc: AccountRow,
    pos: PositionRow,
    add_lots: int,
    price: float,
    run_id: str,
    reason: str,
) -> tuple[TradeRow, AccountRow, PositionRow]:
    margin = ledger.margin_for(pos.symbol, add_lots, price)
    fee = ledger.fee_for(pos.symbol, add_lots, price)
    acc.cash -= margin + fee
    acc.reserved_margin += margin
    # 均价
    new_lots = pos.lots + add_lots
    pos.entry_price = (pos.entry_price * pos.lots + price * add_lots) / new_lots
    pos.lots = new_lots
    ledger.upsert_position(pos)
    acc = ledger.refresh_equity(
        acc, ledger.load_positions(pos.book_id), {pos.symbol: price}
    )
    tr = TradeRow(
        run_id=run_id, book_id=pos.book_id, sleeve=pos.sleeve, symbol=pos.symbol,
        action=ACT_ADD, direction=pos.direction, lots=add_lots, price=price,
        pnl=0.0, fee=fee, cash_after=acc.cash, equity_after=acc.equity, reason=reason,
    )
    ledger.insert_trade(tr)
    ledger.save_account(acc)
    return tr, acc, pos


def _reduce_position(
    ledger: TradeLedger,
    acc: AccountRow,
    pos: PositionRow,
    price: float,
    run_id: str,
    action: str,
    reason: str,
    lots: int,
) -> tuple[TradeRow, AccountRow, PositionRow]:
    lots = min(lots, pos.lots)
    mult = ledger.multiplier(pos.symbol)
    pnl = position_pnl_cny(pos.direction, lots, pos.entry_price, price, mult)
    # 释放保证金按比例
    if pos.lots > 0:
        frac = lots / pos.lots
    else:
        frac = 1.0
    # 估算占用：用 entry 保证金近似
    margin_release = ledger.margin_for(pos.symbol, lots, pos.entry_price)
    fee = ledger.fee_for(pos.symbol, lots, price)
    acc.reserved_margin = max(0.0, acc.reserved_margin - margin_release)
    acc.cash += margin_release + pnl - fee
    acc.realized_pnl += pnl - fee
    pos.lots -= lots
    if pos.lots <= 0:
        pos.lots = 0
    ledger.upsert_position(pos)
    acc = ledger.refresh_equity(
        acc, ledger.load_positions(pos.book_id), {pos.symbol: price}
    )
    tr = TradeRow(
        run_id=run_id, book_id=pos.book_id, sleeve=pos.sleeve, symbol=pos.symbol,
        action=action, direction=pos.direction, lots=lots, price=price,
        pnl=pnl, fee=fee, cash_after=acc.cash, equity_after=acc.equity, reason=reason,
    )
    ledger.insert_trade(tr)
    ledger.save_account(acc)
    return tr, acc, pos


def _close_position(
    ledger: TradeLedger,
    acc: AccountRow,
    pos: PositionRow,
    price: float,
    run_id: str,
    action: str,
    reason: str,
    lots: int | None = None,
) -> tuple[TradeRow, AccountRow]:
    lots = pos.lots if lots is None else min(lots, pos.lots)
    tr, acc, pos = _reduce_position(
        ledger, acc, pos, price, run_id, action, reason, lots
    )
    if pos.lots <= 0:
        # 确保删除
        pos.lots = 0
        ledger.upsert_position(pos)
    return tr, acc


def apply_system_ledger(
    sys_res: Any,
    prices: dict[str, float],
    db_path: Path | str | None = None,
    max_symbol_loss_frac: float = 0.0095,
) -> LedgerRunResult:
    """对 SystemResult 两账本执行账簿更新。"""
    ledger = TradeLedger(db_path=db_path)
    run_id = ledger.start_run(notes="future_system run")
    out = LedgerRunResult(run_id=run_id)

    from .books import BookId  # local

    for bid in (BookId.FINANCIAL, BookId.COMMODITY):
        br = sys_res.books.get(bid)
        if br is None:
            continue
        bcfg = br.book
        # 从 risk snapshot 构造 targets
        trend_tgts = _targets_from_risk(br.trend_risk, br.active_trend, "trend")
        range_tgts = _targets_from_risk(br.range_risk, br.active_range, "range")

        acc, positions, trades, notes = process_book_trades(
            ledger=ledger,
            book_id=bid.value,
            initial_capital=bcfg.capital,
            prices=prices,
            trend_targets=trend_tgts,
            range_targets=range_tgts,
            run_id=run_id,
            max_symbol_loss_frac=max_symbol_loss_frac,
            trend_vol=bcfg.trend_vol_annual,
            range_vol=bcfg.range_vol_annual,
        )
        out.accounts[bid.value] = acc
        out.positions.extend(positions)
        out.trades.extend(trades)
        out.notes.extend([f"[{bid.value}] {n}" for n in notes])
        out.notes.append(
            f"[{bid.value}] equity={acc.equity:,.0f} cash={acc.cash:,.0f} "
            f"margin={acc.reserved_margin:,.0f} pos={len(positions)} trades={len(trades)}"
        )

    ledger.finish_run(run_id, notes="; ".join(out.notes[:20]))
    snap_path = ledger.export_json()
    out.notes.append(f"ledger db={ledger.db_path}")
    out.notes.append(f"ledger snapshot={snap_path}")
    return out


def _targets_from_risk(risk_snap, signals, sleeve: str) -> list[SleeveTarget]:
    """合并 risk 手数与信号上的止损止盈。"""
    sig_map = {s.symbol: s for s in signals}
    out: list[SleeveTarget] = []
    if risk_snap is None:
        # 无 risk 结果但有信号：目标 0（或仅用信号 strength  squashed）
        for s in signals:
            out.append(
                SleeveTarget(
                    symbol=s.symbol,
                    name=s.name,
                    direction=s.direction,
                    target_lots=0,
                    price=s.price,
                    stop=s.current_stop or s.stop,
                    tp1=s.tp1,
                    tp2=s.tp2,
                    strength=s.strength,
                    daily_vol=s.daily_vol,
                )
            )
        return out

    seen = set()
    for p in risk_snap.positions:
        seen.add(p.symbol)
        s = sig_map.get(p.symbol)
        out.append(
            SleeveTarget(
                symbol=p.symbol,
                name=p.name or (s.name if s else p.symbol),
                direction=p.direction if p.target_lots > 0 else (s.direction if s else p.direction),
                target_lots=p.target_lots,
                price=p.price,
                stop=(s.current_stop or s.stop) if s else None,
                tp1=s.tp1 if s else None,
                tp2=s.tp2 if s else None,
                strength=s.strength if s else 0.5,
                daily_vol=p.daily_vol,
                force_stopped=p.force_stopped,
            )
        )
    # risk 里没有的信号（被 size 成 0）也带上，便于平旧仓
    for sym, s in sig_map.items():
        if sym not in seen:
            out.append(
                SleeveTarget(
                    symbol=sym,
                    name=s.name,
                    direction=s.direction,
                    target_lots=0,
                    price=s.price,
                    stop=s.current_stop or s.stop,
                    tp1=s.tp1,
                    tp2=s.tp2,
                    strength=0.0,
                    daily_vol=s.daily_vol,
                )
            )
    return out


def collect_prices_from_bars(
    bars_60: dict,
    bars_30: dict | None = None,
) -> dict[str, float]:
    """从 K 线取最新收盘价。"""
    prices: dict[str, float] = {}
    for src in (bars_60, bars_30 or {}):
        for sym, df in src.items():
            if df is None or getattr(df, "empty", True):
                continue
            if "close" not in df.columns:
                continue
            try:
                prices[sym] = float(df["close"].iloc[-1])
            except Exception:
                continue
    return prices
