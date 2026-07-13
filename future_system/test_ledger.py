"""账簿 / 交易记录单元测试（临时 SQLite）。"""

from __future__ import annotations

import tempfile
from pathlib import Path

from future_system.ledger import (
    SleeveTarget,
    TradeLedger,
    process_book_trades,
)


def test_open_and_hard_stop():
    tmp = Path(tempfile.mkdtemp()) / "t.db"
    led = TradeLedger(db_path=tmp)
    run_id = led.start_run("test")
    prices = {"RB0": 3000.0}
    # 开仓
    acc, pos, trades, notes = process_book_trades(
        ledger=led,
        book_id="commodity",
        initial_capital=4_000_000,
        prices=prices,
        trend_targets=[
            SleeveTarget(
                symbol="RB0",
                name="螺纹",
                direction=1,
                target_lots=5,
                price=3000,
                stop=2950,
                strength=0.8,
                daily_vol=0.012,
            )
        ],
        range_targets=[],
        run_id=run_id,
    )
    assert any(t.action == "open" for t in trades), trades
    assert any(p.symbol == "RB0" and p.lots > 0 for p in pos)
    eq1 = acc.equity
    print("after open", acc.cash, acc.reserved_margin, pos)

    # 价格暴跌触发硬止损（0.95% * equity）
    # 5手 * 10乘数 * 跌幅  → 需要亏损 > ~3.8万
    # 跌 800 点: 5*10*800=40000
    prices2 = {"RB0": 2200.0}
    acc2, pos2, trades2, notes2 = process_book_trades(
        ledger=led,
        book_id="commodity",
        initial_capital=4_000_000,
        prices=prices2,
        trend_targets=[],  # 无新信号，应平掉或硬止损
        range_targets=[],
        run_id=run_id + "_2",
    )
    # 无信号会把旧仓目标设为 0 → close；或 hard stop
    assert len(pos2) == 0 or all(p.lots == 0 for p in pos2)
    assert any(t.action in ("hard_stop", "close", "stop") for t in trades2), trades2
    print("after stop", acc2.equity, trades2, notes2)
    print("test_open_and_hard_stop OK")


def test_persist_reload():
    tmp = Path(tempfile.mkdtemp()) / "t2.db"
    led = TradeLedger(db_path=tmp)
    run_id = led.start_run("t2")
    process_book_trades(
        ledger=led,
        book_id="financial",
        initial_capital=4_000_000,
        prices={"IF0": 4000.0},
        trend_targets=[
            SleeveTarget("IF0", "沪深300", 1, 2, 4000, stop=3900, strength=1.0, daily_vol=0.01)
        ],
        range_targets=[],
        run_id=run_id,
    )
    led2 = TradeLedger(db_path=tmp)
    acc = led2.load_account("financial")
    pos = led2.load_positions("financial")
    assert acc is not None and acc.equity > 0
    assert any(p.symbol == "IF0" for p in pos)
    print("test_persist_reload OK", acc.equity, pos)


if __name__ == "__main__":
    test_open_and_hard_stop()
    test_persist_reload()
    print("all ledger tests passed")
