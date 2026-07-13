"""单品种 0.95% 资金硬止损测试。"""

from __future__ import annotations

from future_risk.config import RiskConfig
from future_risk.engine import RiskEngine, SignalInput, position_pnl_cny


def test_force_flatten_on_loss():
    capital = 4_000_000.0
    cap = capital * 0.0095  # 38000
    cfg = RiskConfig(capital=capital, max_symbol_loss_frac=0.0095, max_positions=8)
    eng = RiskEngine(cfg)

    # 直接给 unrealized_pnl 触线
    snap = eng.size(
        [
            SignalInput(
                "RB0",
                direction=1,
                strength=1.0,
                price=3100,
                daily_vol=0.012,
                current_lots=10,
                entry_price=3200,
                unrealized_pnl=-cap - 100,  # 略超
            )
        ]
    )
    assert any("HARD STOP" in n for n in snap.notes), snap.notes
    pos = next(p for p in snap.positions if p.symbol == "RB0")
    assert pos.force_stopped
    assert pos.target_lots == 0
    assert pos.delta_lots == -10
    assert any(o.comment.startswith("HARD_STOP") for o in snap.orders)
    print("test_force_flatten_on_loss OK", snap.orders)


def test_lots_capped_by_stop_distance():
    capital = 4_000_000.0
    cfg = RiskConfig(
        capital=capital,
        max_symbol_loss_frac=0.0095,
        target_vol_annual=0.50,  # 故意放大 vol 目标，逼出大手数
        max_symbol_margin_frac=0.5,
        max_portfolio_margin_frac=0.9,
        max_positions=5,
    )
    eng = RiskEngine(cfg)
    # 仅一个品种，止损很远 → 手数被 0.95% 钳住
    # RB0 mult=10，假设 entry=3000 stop=2970 → 30 点 ×10 = 300 元/手
    # 38000/300 ≈ 126 手上限
    snap = eng.size(
        [
            SignalInput(
                "RB0",
                direction=1,
                strength=1.0,
                price=3000,
                daily_vol=0.02,
                entry_price=3000,
                stop_price=2970,
            )
        ]
    )
    pos = next((p for p in snap.positions if p.symbol == "RB0"), None)
    assert pos is not None
    loss_cap = capital * 0.0095
    risk_per_lot = 30 * 10  # 300
    max_lots = int(loss_cap // risk_per_lot)
    assert pos.target_lots <= max_lots, (pos.target_lots, max_lots, snap.notes)
    print(
        "test_lots_capped_by_stop_distance OK",
        pos.target_lots,
        "max",
        max_lots,
        [n for n in snap.notes if "max loss" in n],
    )


def test_pnl_helper():
    # 多 2 手，跌 10 点，乘数 10 → -200
    assert position_pnl_cny(1, 2, 100, 90, 10) == -200
    # 空 2 手，涨 10 点 → -200
    assert position_pnl_cny(-1, 2, 100, 110, 10) == -200
    print("test_pnl_helper OK")


if __name__ == "__main__":
    test_pnl_helper()
    test_force_flatten_on_loss()
    test_lots_capped_by_stop_distance()
    print("all hard stop tests passed")
