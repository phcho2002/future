"""策略参数配置。从 config.yaml 加载，也可直接构造用于回测网格。"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[1]  # future_8/
DEFAULT_CONFIG_PATH = _REPO_ROOT / "config.yaml"


@dataclass
class FakeBreakConfig:
    """假突破反转系统全部参数。

    所有默认值与 config.yaml 一致；回测网格可直接构造多个实例覆盖单字段。
    """

    # ── 趋势闸（swing 波峰波谷结构）──
    swing_lookback: int = 3        # swing 点检测左右窗口
    n_swings: int = 2              # 判定趋势取最近几个 swing 点（2=最近一对同向比较）
    swing_window: int = 60         # swing 结构回看根数
    pullback_atr: float = 0.8      # 反弹/回落需达到的幅度(ATR倍)，过滤趋势中继

    # ── 成交密集区 ──
    zone_window: int = 60
    zone_bin_mult: float = 0.75
    zone_min_vol_ratio: float = 0.10

    # ── 假突破触发 ──
    signal_lookback: int = 12
    break_atr: float = 0.3           # 刺穿关键位的深度(ATR倍)
    post_break_bars: int = 5         # 突破后追踪多少根判定失败
    recover_volume_ratio: float = 1.2  # 收回放量倍数

    # ── 突破失败分档打分 ──
    # 三档特征可叠加（满分100），用 min_failure_score 过滤弱失败。
    # A 快速反击(40)：突破后第1根就是反向K且收回关键位 → 最早最弱
    # B 不创新极值(30)：post_break_bars 内未再突破 break_bar 的极值 → 中间档
    # C 跌破突破点(30)：收回不仅止于关键位，还越过突破起始点 → 2B最强
    score_fast_recoil: float = 40.0
    score_no_new_extreme: float = 30.0
    score_2b_beyond: float = 30.0
    min_failure_score: float = 40.0   # 低于此分不视为有效失败信号
    recoil_atr: float = 0.5           # A档"明显反击"幅度(ATR倍)：反向K实体≥此值
    beyond_atr: float = 0.3           # C档越过突破起始点的深度(ATR倍)

    # ── 出场 ──
    target_atr: float = 2.0

    # ── 指标周期（固定，一般不调）──
    atr_period: int = 14
    volume_ma_period: int = 20


def load_config(path: str | Path | None = None) -> FakeBreakConfig:
    """从 YAML 读取策略参数。缺失的键用 dataclass 默认值。"""
    p = Path(path) if path else DEFAULT_CONFIG_PATH
    if not p.exists():
        return FakeBreakConfig()

    with open(p, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    strategy = raw.get("strategy", {}) or {}
    valid_fields = {f for f in FakeBreakConfig.__dataclass_fields__}
    kwargs = {k: v for k, v in strategy.items() if k in valid_fields}
    return FakeBreakConfig(**kwargs)


# ── 品种池（跨周期稳定性筛选结果）──
# 第一梯队：30m/60m/日线 三周期全部盈利
TIER1 = ["PP0", "IM0"]
# 第二梯队：2/3 周期盈利且合计为正（30m+60m 强，日线弱）
TIER2 = ["LC0", "JM0", "SC0", "IC0"]
# 候选池 = 第一 + 第二梯队
CANDIDATES = TIER1 + TIER2
# 明确淘汰：跨周期全亏或仅1周期盈利
EXCLUDED = ["SN0", "MA0", "EB0", "AG0"]


def load_universe(path: str | Path | None = None) -> dict:
    """从 config.yaml 的 universe 段读取品种池。

    返回::

        {
            "tier1":   [{symbol, name, exchange, multiplier, note}, ...],
            "tier2":   [...],
            "excluded":[{symbol, reason}, ...],
        }

    供回测/扫描按梯队选取品种。config.yaml 缺 universe 段时回退到模块常量。
    """
    p = Path(path) if path else DEFAULT_CONFIG_PATH
    if not p.exists():
        return {
            "tier1": [{"symbol": s} for s in TIER1],
            "tier2": [{"symbol": s} for s in TIER2],
            "excluded": [{"symbol": s} for s in EXCLUDED],
        }

    with open(p, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    uni = raw.get("universe", {}) or {}
    return {
        "tier1": uni.get("tier1", []) or [],
        "tier2": uni.get("tier2", []) or [],
        "excluded": uni.get("excluded", []) or [],
    }


def candidate_symbols(path: str | Path | None = None, tiers: tuple[str, ...] = ("tier1", "tier2")) -> list[str]:
    """便捷方法：返回指定梯队的 symbol 列表。默认返回第一+第二梯队全部。"""
    uni = load_universe(path)
    out: list[str] = []
    for t in tiers:
        for item in uni.get(t, []):
            sym = item.get("symbol")
            if sym and sym not in out:
                out.append(sym)
    return out
