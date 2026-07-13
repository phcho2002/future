"""止损止盈单元测试。"""

from __future__ import annotations

from future_system.range_exits import (
    RangeExitConfig,
    build_exit_plan,
    open_exit_state,
    on_bar_update,
    exit_param_space,
)


def test_build_failed_short_plan():
    cfg = RangeExitConfig()
    plan = build_exit_plan(
        side=-1,
        setup="failed_break_short",
        entry=100.0,
        box_h=101.0,
        box_l=99.0,
        pierce_extreme=101.8,
        atr=1.0,
        cfg=cfg,
    )
    assert plan.valid
    assert plan.stop > plan.entry  # 空单止损在上
    assert plan.tp1 < plan.entry
    assert plan.tp2 <= plan.tp1
    assert plan.initial_risk > 0
    assert plan.reward_risk_tp1 >= cfg.min_reward_risk_tp1 - 1e-6
    print("test_build_failed_short_plan OK", plan.to_dict())


def test_build_fade_long_plan():
    cfg = RangeExitConfig()
    plan = build_exit_plan(
        side=1,
        setup="fade_long",
        entry=99.2,
        box_h=101.0,
        box_l=99.0,
        pierce_extreme=99.0,
        atr=1.0,
        cfg=cfg,
    )
    assert plan.valid
    assert plan.stop < plan.entry
    assert plan.tp1 > plan.entry
    print("test_build_fade_long_plan OK", plan.stop, plan.tp1, plan.tp2)


def test_partial_tp_and_trail():
    cfg = RangeExitConfig(
        tp_mode="r_multiple",
        tp1_r=1.0,
        tp2_r=2.0,
        tp1_fraction=0.5,
        min_reward_risk_tp1=0.5,
        enable_trail_after_tp1=True,
        trail_atr=1.0,
        enable_breakeven=True,
        breakeven_trigger_r=0.8,
        min_stop_atr=0.5,
        max_stop_atr=2.0,
    )
    plan = build_exit_plan(1, "failed_break_long", 100.0, 104.0, 96.0, 97.5, 1.0, cfg)
    assert plan.valid, plan.reject_reason
    st = open_exit_state(plan)
    # 走到 TP1（high 触及）
    st, ev = on_bar_update(st, plan.tp1 + 0.01, plan.entry, plan.tp1, 2, 1.0, cfg)
    assert st.tp1_hit, (plan.tp1, plan.stop, plan.entry, ev, st)
    assert any(r == "tp1" for r, _ in ev)
    assert abs(st.remaining_frac - 0.5) < 1e-6
    # 再触当前止损
    st2, ev2 = on_bar_update(st, plan.tp1, st.stop - 0.01, st.stop - 0.02, 3, 1.0, cfg)
    assert st2.remaining_frac == 0.0 or any(r == "stop" for r, _ in ev2)
    print("test_partial_tp_and_trail OK", plan.tp1, ev, ev2, st2.exit_reason)


def test_param_space_keys():
    space = exit_param_space()
    cfg = RangeExitConfig()
    for k in space:
        assert hasattr(cfg, k), k
    print("test_param_space_keys OK", len(space))


if __name__ == "__main__":
    test_build_failed_short_plan()
    test_build_fade_long_plan()
    test_partial_tp_and_trail()
    test_param_space_keys()
    print("all exit tests passed")
