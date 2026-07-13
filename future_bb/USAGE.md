# 期货突破交易系统

基于价格突破的期货量化交易系统，通过多维度过滤提高胜率，用仓位管理优化收益率。

## 系统特点

- **三维突破验证**：价格 + 量能 + 时间
- **多重过滤条件**：趋势背景 + 波动率模式 + 结构质量
- **智能评分系统**：对信号质量评分，只做高分信号
- **金字塔加仓**：初始30%，分批加仓至80%
- **动态止损止盈**：移动止损保护利润，分批止盈
- **完整回测引擎**：逐K线回放，真实模拟交易

## 安装依赖

```bash
pip install akshare pandas numpy
```

## 快速开始

### 1. 分析单个品种

```bash
python main.py analyze RB0
```

### 2. 扫描品种池

```bash
# 扫描指定品种
python main.py scan --symbols RB0,HC0,I0

# 扫描所有品种
python main.py scan --all

# 保存结果
python main.py scan --symbols RB0,HC0,I0 --output signals.csv
```

### 3. 运行回测

```bash
# 回测指定品种
python main.py backtest --symbols RB0,HC0 --start-date 20240101

# 回测所有品种
python main.py backtest --all --start-date 20200101 --capital 100000
```

### 4. 获取实盘信号

```bash
# 获取最新信号
python main.py live --top 10

# 显示详细信息
python main.py live --top 10 --detail

# 监控所有品种
python main.py live --all --top 20
```

## 项目结构

```
future_bb/
├── README.md               # 完整设计文档
├── config.py               # 配置参数
├── data_loader.py          # 数据获取（akshare）
├── indicators.py           # 技术指标计算
├── breakout_detector.py    # 突破识别引擎
├── filter_conditions.py    # 过滤条件模块
├── signal_generator.py     # 信号生成器
├── position_manager.py     # 仓位管理
├── backtest_engine.py      # 回测引擎
├── strategy.py             # 策略主逻辑
├── main.py                 # 主程序入口
├── cache/                  # 数据缓存目录
├── logs/                   # 日志目录
└── output/                 # 回测结果输出
```

## 核心逻辑

### 突破定义（三维）

1. **价格维度**：收盘价突破前N日最高价 + 突破幅度 > 0.5 ATR
2. **量能维度**：成交量 > 20日均量 * 1.5 + 持仓量增长
3. **时间维度**：收盘价站稳 + 强势K线实体 + 上影线短

### 过滤条件

**必要条件（AND逻辑）**：
- 趋势背景：多头排列 + ADX > 25 + +DI > -DI
- 量能确认：放量 + 持仓增长
- 波动率：先压缩后扩张
- 结构质量：阻力位充分测试

**加分条件**：
- 相对强度 +20分
- 多周期共振 +30分
- 二次突破 +25分

最低入场评分：80分

### 仓位管理

**金字塔加仓**：
```
初始仓位：30%（突破确认）
├─ 加仓1：+20%（盈利2%，止损上移至突破点）
├─ 加仓2：+20%（盈利5%，止损移至盈亏平衡点）
└─ 加仓3：+10%（盈利10%，止损保护80%利润）
```

**分批止盈**：
```
50%仓位：2倍止损空间
30%仓位：5倍止损空间
20%仓位：趋势反转信号
```

## 配置参数

所有参数可在 `config.py` 中调整：

```python
# 突破参数
LOOKBACK_PERIOD = 20        # 前高计算周期
BREAKOUT_THRESHOLD = 0.005   # 突破阈值（0.5%）
VOLUME_MULTIPLIER = 1.5      # 量能放大倍数

# 过滤参数
ADX_THRESHOLD = 25           # ADX强度阈值
ATR_COMPRESSION = 0.7        # ATR压缩阈值
RESISTANCE_TOUCHES = 3       # 最小触碰次数

# 仓位参数
INITIAL_POSITION = 0.3       # 初始仓位30%
MAX_POSITION = 0.8           # 最大总仓位80%

# 风险参数
MAX_SINGLE_LOSS = 0.02       # 单笔最大亏损2%
MAX_DAILY_LOSS = 0.05        # 单日最大亏损5%
MAX_DRAWDOWN = 0.20          # 最大回撤20%
```

## 性能目标

- ✅ 胜率 ≥ 60%
- ✅ 盈亏比 ≥ 1:3
- ✅ 夏普比率 ≥ 1.5
- ✅ 最大回撤 ≤ 20%
- ✅ 年化收益率 ≥ 30%

## 数据源

使用 **akshare** 获取新浪财经期货数据：
- 日线数据：`ak.futures_zh_daily_sina()`
- 分钟数据：`ak.futures_zh_minute_sina()`
- 持仓数据：`ak.futures_position_sina()`

数据自动缓存，缓存有效期24小时。

## 注意事项

1. **数据质量**：akshare数据可能有延迟或错误，建议交叉验证
2. **合约换月**：主力合约换月时需要注意连续性处理
3. **滑点成本**：实盘滑点可能大于回测设定，需要预留余量
4. **风险控制**：严格执行止损，不要因为"看好"而扩大亏损
5. **参数优化**：避免过度拟合历史数据，保持参数的鲁棒性

## 开发计划

- [x] 数据获取模块
- [x] 技术指标计算
- [x] 突破检测引擎
- [x] 过滤条件模块
- [x] 信号生成器
- [x] 仓位管理
- [x] 回测引擎
- [x] 策略主逻辑
- [x] 命令行接口
- [ ] 实盘接口对接（CTP）
- [ ] 实时监控和报警
- [ ] Web可视化界面
- [ ] 机器学习模型集成

## 许可证

个人学习和研究使用，不构成投资建议。

## 作者

资深冠军交易员 + Claude Code

---

**免责声明**：期货交易有风险，投资需谨慎。本系统仅供学习研究，不构成任何投资建议。实盘交易需自行承担风险。
