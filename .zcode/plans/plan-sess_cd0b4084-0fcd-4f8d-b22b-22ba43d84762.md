# stock_4：假突破→真突破 股票日线系统

## 核心思路
移植 `future_bb/SecondBreakoutEngine` 四阶段状态机到 A 股日线，数据源改为通达信本地 `.day` 文件。

**四阶段（做多单向）：**
```
IDLE →(检测到蓄势形态:箱体/收敛楔形)→ PRIMED(记录阻力位)
     →(首次收盘突破阻力位)→ BROKEN(可能是假突破)
     →(收盘跌回阻力位下方≥2根)→ FAILED ← 假突破确认
     →(再次收盘突破阻力位 + close>EMA200)→ 入场信号 → 回到IDLE
```

## 目录结构 `./stock_4/`
```
stock_4/
├── config.yaml        # 全部可调参数（策略+回测+数据路径）
├── tdx_reader.py      # 通达信 .day 二进制读取 + 股票池发现
├── indicators.py      # ATR / EMA / detect_box / detect_wedge（移植 future_bb）
├── engine.py          # SecondBreakoutEngine 四阶段状态机（做多单向版）
├── scanner.py         # 全市场扫描器：扫当日信号 → 输出 CSV
├── backtest.py        # 历史回测引擎（A股仓位/止损/止盈/手续费）
├── run_scan.py        # CLI：扫描入口
├── run_backtest.py    # CLI：回测入口
├── requirements.txt   # pandas, numpy, pyyaml
└── README.md          # 使用说明
```

## 各文件职责

### 1. `tdx_reader.py` — 数据层
- `read_tdx_day_file(path)`：读取 `.day`，struct 格式 `<IIIIIfII>`（已实测验证，amount 为 float32 元，vol 为 uint32 股），OHLC ÷100
- `is_main_board(symbol)`：仅 `60xxxx`(沪主板) + `00/001/002/003xxxx`(深主板)，排除创业板/科创板/指数/ETF/可转债/B股
- `TDXLocalProvider`：封装路径解析（`6`开头→sh，否则→sz）、日期过滤、`all_symbols()` 全主板代码发现
- 路径默认 `D:/new_tdx/vipdoc/{sh|sz}/lday/{sh|sz}{6位}.day`
- 可选：加载本地股票名称（从 mootdx 缓存或用户提供的 CSV），用于 ST 过滤；无名称时跳过 ST 过滤并告警

### 2. `indicators.py` — 指标层（移植自 future_bb/indicators.py）
- `ATR(high, low, close, period=14)` — TR 取三者最大值，SMA 平滑
- `EMA(data, period)` — ewm(span, adjust=False)
- `detect_box(high, low, period, range_max, touch_min, touch_tol)` — 近N根波幅≤range_max 且上下边界各触碰≥touch_min次
- `detect_wedge(high, low, close, period, atr_shrink, range_shrink)` — ATR收敛 + 后半段波幅 < 前半段×range_shrink

### 3. `engine.py` — 状态机（核心）
- `SecondBreakoutEngine`：移植 `future_bb` 的四阶段状态机，**简化为做多单向**（删除 PRIMED_SHORT / BROKEN_SHORT / FAILED_SHORT 及 `_primed_short` 逻辑）
- 构造时向量化预计算：setup_pattern、resistance_level / support_level（rolling(N).max/min.shift(1)）、atr、ema200
- `run()` 返回 `[(idx, side=+1, resistance_level, atr), ...]`
- 参数全部从 config 读取（不硬编码）

### 4. `scanner.py` — 扫描器
- 遍历全部主板股票，对每只读取日线 → 运行 `SecondBreakoutEngine`
- **筛选最近 N 天（默认1天=今日）产生的信号**，附加信号质量信息（ATR比率、距EMA200距离、箱体波幅等）
- 涨跌停检测：信号日收盘≈涨停(+10%)时标记"可能买不到"
- 可选过滤：排除当日成交量 < MA20×0.5 的低流动性信号
- 输出 `output/scan_signals_YYYYMMDD.csv`，列：`symbol, name, signal_date, close, resistance, atr, pct_above_resistance, box_range_pct, above_ema200, near_limit`
- 支持并行扫描（concurrent.futures，可选）

### 5. `backtest.py` — 回测引擎
- 单股独立回测（参考 `future_bb/backtest_daily_breakout.py`）
- **A股适配：**
  - T+1：买入次日才能卖
  - 手 = 100股，买入按整手向下取整
  - 手续费：佣金万分之2.5（最低5元）+ 卖出印花税0.05%
  - 入场：信号日收盘确认 → 次日开盘买入
  - 初始止损 = 突破位 - 1.5×ATR
  - 移动止损 = 跟踪最低价 - 2.0×ATR（让利润奔跑）
  - 涨停过滤：次日开盘若涨停则放弃入场
  - 最大持仓时间限制（可选，防止长期套牢）
- 输出每笔交易明细 + 汇总统计（胜率、盈亏比、总收益、最大回撤、平均持仓天数）
- 支持：全市场批量回测、多股组合统计

### 6. `run_scan.py` / `run_backtest.py` — CLI 入口
- `python run_scan.py` → 扫描全主板，输出当日信号 CSV
- `python run_backtest.py --symbol 600519` → 单股回测
- `python run_backtest.py --all` → 全市场回测汇总

### 7. `config.yaml` — 参数（日线调优版）
```yaml
data:
  tdx_vipdoc: "D:/new_tdx/vipdoc"
  lookback_bars: 500          # 读取最近500根日线

strategy:
  pattern_lookback: 20        # 蓄势窗口（20个交易日）
  box_range_max: 0.12         # 箱体最大波幅 12%（日线比小时线宽）
  box_touch_min: 2
  box_touch_tol: 0.01         # 边界触碰容差 1%
  wedge_atr_shrink: 0.8
  wedge_range_shrink: 0.8
  breakout_threshold: 0.01    # 突破阈值 1%（期货0.3%→日线1%）
  fail_confirm_bars: 2        # 假突破确认根数
  second_break_max_gap: 20
  second_break_min_gap: 2
  ema_period: 200             # 200日均线趋势过滤
  atr_period: 14

backtest:
  initial_capital: 100000     # 初始资金10万
  risk_per_trade: 0.02        # 每笔风险2%
  commission_rate: 0.00025    # 佣金万2.5
  min_commission: 5.0         # 最低佣金5元
  stamp_duty: 0.0005          # 卖出印花税0.05%
  initial_stop_atr: 1.5       # 初始止损 1.5×ATR
  trail_atr_mult: 2.0         # 移动止损 2.0×ATR
  start_date: "20210101"

scan:
  signal_days: 1              # 扫描最近1天的信号
  min_volume_ratio: 0.5       # 最低量比（相对MA20）
  exclude_st: true
```

## A股 vs 期货的关键差异（已处理）
| 维度 | future_bb(期货) | stock_4(A股) |
|------|----------------|-------------|
| 方向 | 多空双向 | **仅做多** |
| 频率 | 小时线 | **日线** |
| 数据 | tqsdk在线 | **通达信本地.day** |
| 仓位 | 保证金+手数 | **资金比例+100股/手** |
| 费用 | 佣金万分之3 | **佣金万2.5+卖出印花税0.05%** |
| 限制 | 无涨跌停 | **±10%涨跌停 + T+1** |
| 趋势过滤 | EMA200 | **MA20/MA60多头排列(可选增强)** |

## 实现顺序
1. `config.yaml` → 2. `tdx_reader.py` → 3. `indicators.py` → 4. `engine.py` → 5. `scanner.py` + `run_scan.py` → 6. `backtest.py` + `run_backtest.py` → 7. `README.md` → 8. 实测运行验证