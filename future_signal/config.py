"""Signal layer defaults: 60m 假突破→真突破 + daily strength filter."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

WORK_AI = Path(__file__).resolve().parents[1]
TOP40_JSON = WORK_AI / "futures_top40.json"
DB_PATH = WORK_AI / "futures_data.db"


@dataclass
class SignalConfig:
    # ---- 周期 ----
    signal_period: str = "60"          # 主信号 K 线分钟
    daily_period: str = "1440"         # 强弱过滤用日线
    bar_length_60m: int = 1500         # 60m 根数（需覆盖 EMA200 + 形态）
    bar_length_daily: int = 200
    kline_ttl_hours: float = 24.0
    cache_only: bool = False
    force_refresh_klines: bool = False  # True=全品种强制联网，不用本地缓存

    # ---- 主信号：假突破 → 真突破（对齐 future_bb）----
    pattern_lookback: int = 20         # 蓄势形态窗口
    box_range_max: float = 0.02        # 箱体最大波幅 2%
    box_touch_min: int = 2
    box_touch_tol: float = 0.005
    wedge_atr_shrink: float = 0.8
    wedge_range_shrink: float = 0.8
    breakout_threshold_2nd: float = 0.003  # 突破阈值 0.3%
    fail_confirm_bars: int = 2         # 失败确认连续根数
    second_break_min_gap: int = 2      # 首次→二次最小间隔
    second_break_max_gap: int = 20     # 首次→二次最大间隔（超时作废）
    ema_trend_period: int = 200        # EMA 顺势过滤
    atr_period: int = 14

    # ---- 入场后持仓跟踪（扫描用方向；对齐 strategy_config 让利润奔跑）----
    initial_stop_atr: float = 1.5      # 二次满仓初始止损
    trail_atr_mult: float = 2.0        # 二次满仓移动止损

    # ---- 首次突破试探小仓（可选）----
    # True：第一次突破可开小仓；失败确认/超时/止损则平；二次突破升级满仓
    # False：仅二次真突破才下单（严格模式）
    allow_first_breakout_probe: bool = True
    first_probe_strength_scale: float = 0.20  # 相对满仓风险预算约 20%
    first_probe_stop_atr: float = 1.0         # 试探仓更紧止损
    first_probe_use_trail: bool = False       # 试探默认不用宽移动止损

    # ---- 量能（用于 strength，不硬挡二次突破入场）----
    vol_ma: int = 20
    vol_factor: float = 1.35

    # ---- 日线强弱过滤（futures_strength_analysis）----
    ban_long_bottom_n: int = 10
    ban_short_top_n: int = 10
    reuse_daily_db_hours: float = 20.0
    force_refresh_daily: bool = False

    # ---- 板块去重 ----
    max_per_sector: int = 2

    # ---- 强度 ----
    strength_floor: float = 0.35
    strength_ceil: float = 1.0

    # ---- 路径 ----
    top40_json: Path = field(default_factory=lambda: TOP40_JSON)
    db_path: Path = field(default_factory=lambda: DB_PATH)

    # ---- 对接 future_risk ----
    capital: float = 5_000_000.0
    target_vol_annual: float = 0.15
    size_with_risk: bool = True
