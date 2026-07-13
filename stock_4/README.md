# stock_4：假突破→真突破 股票日线系统

移植自 `future_bb` 期货系统的 `SecondBreakoutEngine` 四阶段状态机，适配 **A 股日线**，数据源为**通达信本地已下载的 `.day` 文件**（离线，无需联网）。

**股票池**：全部 A 股（沪市主板 + 深市主板 + 创业板 + 科创板，约 5200 只）。涨跌停按板块自动区分：主板 ±10%，创业板/科创板 ±20%。

## 核心策略

```
IDLE →(检测到蓄势形态)→ PRIMED(记录阻力位)
     →(首次收盘突破阻力位)→ BROKEN(可能是假突破)
     →(收盘跌回阻力位下方≥2根)→ FAILED ← 假突破确认
     →(再次突破阻力位 + close>EMA200)→ 入场做多 → IDLE
```

**原理**：很多突破是"假突破"（冲高后回落洗盘）。本系统专门等待假突破失败后，捕捉第二次、更可靠的真突破入场。

### 蓄势形态识别
- **箱体**：近 20 日波幅 ≤12%，且上下边界各被触碰 ≥2 次
- **收敛楔形**：近期 ATR 较前期收缩 ≥20%，后半段波幅 < 前半段 80%

## 快速开始

### 1. 环境准备
```bash
pip install -r requirements.txt   # pandas, numpy, pyyaml
```

### 2. 确认通达信数据路径
默认读取 `D:/new_tdx/vipdoc/{sh|sz}/lday/`。如路径不同，修改 `config.yaml`：
```yaml
data:
  tdx_vipdoc: "D:/你的通达信路径/vipdoc"
```
需先在通达信里**下载日线数据**（系统→盘后数据下载）。

### 3. 全市场扫描（找今日信号）
```bash
python run_scan.py
```
输出 `output/scan_signals_YYYYMMDD.csv`，列说明：

| 列 | 含义 |
|---|---|
| symbol / signal_date | 股票代码 / 信号日期 |
| close | 信号日收盘价 |
| resistance | 突破的阻力位 |
| atr / atr_pct | ATR 绝对值 / 占收盘价比 |
| pct_above_resistance | 收盘高于阻力位百分比 |
| box_range_pct | 蓄势期波幅（%） |
| pct_above_ema200 | 收盘高于 EMA200 百分比 |
| vol_ratio | 当日量 / MA20 量（流动性） |
| near_limit | 是否接近涨停（可能买不到） |

### 4. 历史回测
```bash
python run_backtest.py --symbol 600519      # 单股回测（打印每笔明细）
python run_backtest.py --all               # 全市场回测（输出汇总+交易CSV）
```
输出 `output/backtest_summary_*.csv`（每只股票统计）和 `output/backtest_trades_*.csv`（每笔交易明细）。

## 参数调优

所有参数在 `config.yaml`。关键参数：

| 参数 | 默认 | 说明 |
|---|---|---|
| `strategy.box_range_max` | 0.12 | 箱体最大波幅（调大→更多信号，调小→更严格） |
| `strategy.breakout_threshold` | 0.01 | 突破阈值 1%（收盘需超阻力位 1%） |
| `strategy.fail_confirm_bars` | 2 | 假突破确认根数（需跌回形态内 N 根） |
| `strategy.ema_period` | 200 | 趋势过滤均线周期 |
| `backtest.risk_per_trade` | 0.02 | 每笔风险占资金 2% |
| `backtest.initial_stop_atr` | 1.5 | 初始止损 = 阻力位 - 1.5×ATR |
| `backtest.trail_atr_mult` | 2.0 | 移动止损 = 跟踪最低 - 2.0×ATR |

### 策略特性
- **低胜率、高盈亏比**：胜率约 35-40%，但盈利单平均远大于亏损单（移动止损让利润奔跑）
- **最适合中小盘高波动股**：大盘蓝筹（如茅台）波动小、假突破形态少，信号稀缺
- **信号稀缺**：每只股票每年约 1-3 个信号，这是正常的（严格筛选）

## 文件结构

```
stock_4/
├── config.yaml       全部可调参数
├── tdx_reader.py     通达信 .day 二进制读取 + 主板股票池
├── indicators.py     ATR / EMA / detect_box / detect_wedge
├── engine.py         SecondBreakoutEngine 四阶段状态机（核心）
├── scanner.py        全市场扫描器
├── backtest.py       A 股回测引擎（T+1/佣金/印花税/涨跌停）
├── run_scan.py       扫描 CLI 入口
├── run_backtest.py   回测 CLI 入口
└── output/           输出 CSV 目录
```

## A 股适配要点

| 维度 | 期货版 (future_bb) | 本系统 (stock_4) |
|---|---|---|
| 方向 | 多空双向 | **仅做多** |
| 频率 | 小时线 | 日线 |
| 数据 | tqsdk 在线 | 通达信本地 .day |
| 仓位 | 保证金+手数 | 资金比例风险 + 100 股/手 |
| 费用 | 佣金万分之 3 | 佣金万 2.5 + 卖出印花税 0.05% |
| 限制 | 无涨跌停 | ±10% 涨跌停 + T+1 |
