"""
N型主升浪交易系统 — 全局配置
================================
所有可调参数集中管理，方便优化和回测。
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class PivotConfig:
    """枢轴点检测参数"""
    left: int = 5          # 左侧K线数（周线5周≈1个月）
    right: int = 5         # 右侧确认K线数


@dataclass
class NPatternConfig:
    """N型结构识别参数"""
    fib_min: float = 0.30        # 最小回调比例（30%，略低于0.382以捕捉更多机会）
    fib_max: float = 0.65        # 最大回调比例（65%，允许略超0.618的深度回调）
    min_leg_pct: float = 10.0    # 首波最小涨幅（%）
    min_leg_bars: int = 3        # 首波最少K线数
    reset_bars: int = 200        # 结构失效周期（周线200周≈4年）


@dataclass
class MAConfig:
    """均线系统参数"""
    enabled: bool = True
    ema_short: int = 10          # 10周均线
    ema_mid: int = 20            # 20周均线（牛熊分界）
    ema_long: int = 50           # 50周均线


@dataclass
class RSIConfig:
    """RSI 过滤参数"""
    enabled: bool = True
    period: int = 14
    low: int = 40                # 避免底部弱势
    high: int = 75               # 避免追高超买


@dataclass
class VolumeConfig:
    """成交量过滤参数"""
    enabled: bool = True
    ma_period: int = 20
    burst_mult: float = 1.3      # 突破放量倍数（>均量×1.3）
    contract_ratio: float = 0.7  # 回调缩量阈值（<均量×0.7）


@dataclass
class MACDConfig:
    """MACD 参数"""
    fast: int = 12
    slow: int = 26
    signal: int = 9


@dataclass
class ADXConfig:
    """ADX 趋势强度参数"""
    enabled: bool = True
    period: int = 14
    threshold: int = 20          # ADX>20为趋势市


@dataclass
class RiskConfig:
    """风控参数"""
    stop_atr_mult: float = 2.0      # 止损 = 入场价 - ATR×倍数
    risk_percent: float = 2.0       # 单笔风险占账户%
    tp_rr_1: float = 2.0            # 第一止盈 RR（平50%）
    tp_rr_2: float = 3.5            # 第二止盈 RR（平50%）
    use_trailing: bool = True       # 启用移动止损
    trail_atr_mult: float = 3.0     # 移动止损 ATR 倍数
    max_holding_bars: int = 52      # 最大持仓周数（约1年）


@dataclass
class ScoreWeights:
    """综合评分权重"""
    n_pattern: int = 40          # N型结构完成
    ma: int = 15                 # 均线多头排列
    rsi: int = 10                # RSI健康
    volume: int = 15             # 量价配合
    macd: int = 10               # MACD多头
    adx: int = 5                 # ADX趋势
    mtf: int = 10                # 多周期共振（额外加分）


@dataclass
class DataConfig:
    """数据获取参数"""
    default_period: str = 'weekly'      # weekly / daily
    lookback_years: int = 5
    adjust: str = 'qfq'                 # 前复权
    cache_dir: str = './stock/cache'    # 缓存目录
    request_delay: float = 0.5          # API请求间隔（秒）


@dataclass
class ScreenerConfig:
    """选股器参数"""
    min_score: int = 50
    top_n: int = 20
    stock_pool: str = 'all'             # all / hs300 / zz500 / custom
    custom_symbols: list = field(default_factory=list)
    exclude_st: bool = True             # 排除ST股
    min_volume_amount: float = 1e8      # 最小成交额（1亿）


@dataclass
class BacktestConfig:
    """回测参数"""
    initial_capital: float = 100_000.0
    commission: float = 0.0003          # 万三佣金
    slippage: float = 0.001             # 0.1%滑点


@dataclass
class Config:
    """总配置"""
    pivot: PivotConfig = field(default_factory=PivotConfig)
    n_pattern: NPatternConfig = field(default_factory=NPatternConfig)
    ma: MAConfig = field(default_factory=MAConfig)
    rsi: RSIConfig = field(default_factory=RSIConfig)
    volume: VolumeConfig = field(default_factory=VolumeConfig)
    macd: MACDConfig = field(default_factory=MACDConfig)
    adx: ADXConfig = field(default_factory=ADXConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    score: ScoreWeights = field(default_factory=ScoreWeights)
    data: DataConfig = field(default_factory=DataConfig)
    screener: ScreenerConfig = field(default_factory=ScreenerConfig)
    backtest: BacktestConfig = field(default_factory=BacktestConfig)


# 预设配置方案
def get_aggressive_config() -> Config:
    """激进型配置：更宽松的条件，更多交易机会"""
    c = Config()
    c.n_pattern.fib_min = 0.25
    c.n_pattern.fib_max = 0.70
    c.n_pattern.min_leg_pct = 8.0
    c.volume.burst_mult = 1.1
    c.rsi.low = 35
    c.rsi.high = 78
    c.risk.stop_atr_mult = 1.5
    c.risk.tp_rr_1 = 1.5
    c.risk.tp_rr_2 = 2.5
    c.screener.min_score = 40
    return c


def get_conservative_config() -> Config:
    """保守型配置：更严格的条件，更高胜率"""
    c = Config()
    c.n_pattern.fib_min = 0.35
    c.n_pattern.fib_max = 0.55
    c.n_pattern.min_leg_pct = 15.0
    c.n_pattern.min_leg_bars = 5
    c.volume.burst_mult = 1.5
    c.rsi.low = 45
    c.rsi.high = 70
    c.risk.stop_atr_mult = 2.5
    c.risk.tp_rr_1 = 2.5
    c.risk.tp_rr_2 = 4.0
    c.screener.min_score = 65
    return c
