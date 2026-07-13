"""
配置文件
包含所有可调参数
"""

# ============ 突破参数（做多）============
LOOKBACK_PERIOD = 20  # 前高/前低计算周期
BREAKOUT_THRESHOLD = 0.005  # 突破阈值（0.5%，严格设计值）
VOLUME_MULTIPLIER = 1.5  # 量能放大倍数（严格设计值）
BODY_SIZE_MULTIPLIER = 1.5  # 突破K线实体倍数（严格设计值）
BREAKOUT_ATR_MULTIPLIER = 0.5  # 突破幅度需 > 0.5×ATR（严格设计值）

# ============ 突破参数（做空，对称镜像，单独命名便于分方向调参）============
SHORT_BREAKOUT_THRESHOLD = 0.005   # 跌破阈值
SHORT_VOLUME_MULTIPLIER = 1.5      # 放量倍数
SHORT_BODY_SIZE_MULTIPLIER = 1.5   # 强势阴线实体倍数
SHORT_BREAKOUT_ATR_MULTIPLIER = 0.5  # 跌破幅度需 > 0.5×ATR

# ============ 持仓量要求 ============
# True：有 open_interest 列时持仓增长为必要条件；无该列时自动降级（仅用放量确认 + 一次性告警）
OI_INCREASING_REQUIRED = True

# ============ 二次突破系统参数（蓄势形态 + 假突破 + 二次突破）============
PATTERN_LOOKBACK = 20        # 蓄势形态回看窗口（K线根数）
BOX_RANGE_MAX = 0.02         # 箱体最大波幅（近N根 最高-最低 / 最低，2%）
BOX_TOUCH_MIN = 2            # 箱体上下边界各自最小触碰次数
BOX_TOUCH_TOL = 0.005        # 边界触碰容差（0.5%）
WEDGE_ATR_SHRINK = 0.8       # 收敛楔形：近期ATR / 前期ATR < 此值（收敛≥20%）
WEDGE_RANGE_SHRINK = 0.8     # 收敛楔形：后半段波幅 / 前半段波幅 < 此值
BREAKOUT_THRESHOLD_2ND = 0.003  # 二次突破价格阈值（0.3%，比首次松，因已是确认突破）
FAIL_CONFIRM_BARS = 2        # 失败确认：收盘回到形态内需持续的根数
SECOND_BREAK_MAX_GAP = 20    # 首次突破→二次突破的最大间隔（超时作废，回到IDLE）
SECOND_BREAK_MIN_GAP = 2     # 首次突破→二次突破的最小间隔（至少经历失败确认）
BREAK_EXIT_BO_ATR = 1.0      # 二次突破后初始止损 = 突破点 ∓ 1.0×ATR
TRAIL_ATR_MULT_2ND = 1.0     # 二次突破移动止损：跟踪极值 ∓ 1.0×ATR（让利润奔跑）
BREAKEVEN_TRIGGER_R = 1.0    # 浮盈达1R后止损上移至盈亏平衡点（保护）
ADD_ON_R = 1.0               # 浮盈达1R时加仓1手（单次加仓）

# ============ 过滤参数（严格AND，恢复原设计值）============
ADX_THRESHOLD = 25  # ADX强度阈值（严格：25）
ATR_COMPRESSION = 0.7  # 压缩阈值：突破前近期曾出现 ATR < 0.7×自身20日均线
ATR_COMPRESSION_LOOKBACK = 20  # 压缩观察窗口：突破前N根内曾出现ATR压缩即视为"先压缩"（一个完整波动周期）
ATR_EXPANSION = 1.2  # 扩张阈值：突破当日真实波幅 TR > 1.2×ATR（捕捉波动瞬间放大）
RESISTANCE_TOUCHES = 3  # 阻力/支撑位最小触碰次数（严格：3）

# ============ 均线参数 ============
MA_PERIODS = [5, 10, 20, 60]  # 均线周期
ADX_PERIOD = 14  # ADX计算周期
ATR_PERIOD = 14  # ATR计算周期

# ============ 仓位参数 ============
INITIAL_POSITION = 0.3  # 初始仓位30%
ADD_POSITION_1 = 0.2  # 第一次加仓20%
ADD_POSITION_2 = 0.2  # 第二次加仓20%
ADD_POSITION_3 = 0.1  # 第三次加仓10%
MAX_POSITION = 0.8  # 最大总仓位80%

# ============ 风险参数 ============
INITIAL_STOP_ATR = 1.0  # 初始止损（ATR倍数）
PROFIT_TAKE_1_RATIO = 2.0  # 第一止盈（2倍止损空间）
PROFIT_TAKE_2_RATIO = 5.0  # 第二止盈（5倍止损空间）
MAX_SINGLE_LOSS = 0.02  # 单笔最大亏损2%
MAX_DAILY_LOSS = 0.05  # 单日最大亏损5%
MAX_DRAWDOWN = 0.20  # 最大回撤20%
MAX_POSITIONS = 5  # 最大持仓品种数

# ============ 评分系统（质量分级，非硬门槛）============
# 说明：五块严格AND（三维突破+趋势+量能+波动+结构）已是非常严格的筛选，
# 通过即视为达标。评分用于质量分级展示（如二次突破加分标记高质量），
# 不再作为出信号的额外硬门槛——因相对强度/多周期加分依赖基准与多周期数据，
# 当前为 stub，强制 ≥80 会导致通过必要条件的信号被全部误杀。
BASE_SCORE = 60  # 基础分（满足三维突破+必要条件即得）
SCORE_RELATIVE_STRENGTH = 20  # 相对强度加分（需基准数据，当前 stub）
SCORE_MULTI_TIMEFRAME = 30  # 多周期共振加分（需多周期数据，当前 stub）
SCORE_SECOND_BREAKOUT = 25  # 二次突破加分（实际可触发）
MIN_ENTRY_SCORE = 60  # 最低入场评分（= BASE_SCORE，通过必要条件即达标；加分用于质量分级）

# ============ 回测参数 ============
INITIAL_CAPITAL = 100000  # 初始资金
COMMISSION_RATE = 0.0003  # 手续费率（万分之三，双边）
SLIPPAGE_TICKS = 1  # 滑点（跳数）

# ============ 数据参数 ============
START_DATE = "20200101"  # 回测开始日期
END_DATE = None  # 回测结束日期（None表示至今）
DATA_CACHE_DIR = "./cache"  # 数据缓存目录

# ============ 期货品种池 ============
# TOP40主力合约（根据流动性和波动率筛选）
FUTURES_UNIVERSE = [
    "RB0",  # 螺纹钢主力
    "HC0",  # 热轧卷板主力
    "I0",   # 铁矿石主力
    "J0",   # 焦炭主力
    "JM0",  # 焦煤主力
    "ZC0",  # 动力煤主力
    "FG0",  # 玻璃主力
    "MA0",  # 甲醇主力
    "TA0",  # PTA主力
    "PP0",  # 聚丙烯主力
    "V0",   # PVC主力
    "L0",   # 塑料主力
    "BU0",  # 沥青主力
    "RU0",  # 橡胶主力
    "NI0",  # 镍主力
    "CU0",  # 铜主力
    "AL0",  # 铝主力
    "ZN0",  # 锌主力
    "AU0",  # 黄金主力
    "AG0",  # 白银主力
    "C0",   # 玉米主力
    "CS0",  # 玉米淀粉主力
    "A0",   # 豆一主力
    "M0",   # 豆粕主力
    "Y0",   # 豆油主力
    "P0",   # 棕榈油主力
    "OI0",  # 菜油主力
    "RM0",  # 菜粕主力
    "SR0",  # 白糖主力
    "CF0",  # 棉花主力
    "CY0",  # 棉纱主力
    "AP0",  # 苹果主力
    "EG0",  # 乙二醇主力
    "EB0",  # 苯乙烯主力
    "PG0",  # 液化石油气主力
    "SA0",  # 纯碱主力
    "IF0",  # 沪深300股指主力
    "IH0",  # 上证50股指主力
    "IC0",  # 中证500股指主力
    "T0",   # 10年期国债主力
]

# ============ 日志配置 ============
LOG_LEVEL = "INFO"  # 日志级别：DEBUG/INFO/WARNING/ERROR
LOG_FILE = "./logs/strategy.log"  # 日志文件路径
