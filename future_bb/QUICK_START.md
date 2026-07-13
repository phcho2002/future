# 期货突破交易系统 - 快速上手指南

## ✅ 系统状态

**当前版本**：v1.1（优化版）  
**信号数量**：已优化，5品种18个信号  
**测试状态**：✅ 全部通过  
**可用性**：立即可用

---

## 🚀 5分钟快速开始

### 1. 安装依赖（首次使用）

```bash
cd future_bb
pip install -r requirements.txt
```

### 2. 运行测试（验证系统）

```bash
python test_demo.py
```

### 3. 分析单个品种

```bash
# 分析螺纹钢
python main.py analyze RB0

# 分析铁矿石
python main.py analyze I0 --start-date 20240101
```

### 4. 扫描品种池（推荐）

```bash
# 扫描黑色系品种
python main.py scan --symbols RB0,HC0,I0,J0,JM0

# 扫描全部40个品种（耗时较长）
python main.py scan --all

# 保存结果到CSV
python main.py scan --symbols RB0,HC0,I0 --output signals.csv
```

### 5. 获取今日实盘信号

```bash
# 获取前10个最高分信号
python main.py live --top 10

# 显示详细信息
python main.py live --top 10 --detail

# 监控所有品种
python main.py live --all --top 20
```

---

## 📊 系统性能（优化后）

### 信号数量测试

| 测试范围 | 信号数量 | 说明 |
|---------|---------|------|
| 螺纹钢（RB0）2024-2026 | 6个 | 平均每季度1-2个 |
| 5品种扫描（黑色系） | 18个 | 覆盖2024-2026 |
| 全品种扫描（40个） | 预计70-100个 | 需实际测试 |

### 信号示例（实际捕获）

```
品种    日期      价格    评分  成交量
RB0  20260107  3187.0   70  1,937,222
HC0  20260506  3493.0   70    591,507
I0   20260107   828.0   70    490,615
JM0  20260601  1377.0   70  1,562,733
```

---

## 🎯 核心功能

### 1. 突破检测（三维验证）

**价格维度**：
- 突破前20日最高价
- 突破幅度 > 0.3 ATR
- 阈值：0.3%

**量能维度**：
- 成交量 > 20日均量 × 1.3
- 持仓量增长（期货特有）

**时间维度**：
- 收盘价站稳突破位
- 强势K线实体
- 上影线短

### 2. 过滤条件（智能筛选）

**必要条件（满足2/3即可）**：
- ✅ 短期多头：MA5 > MA20
- ✅ 趋势强度：ADX > 20
- ✅ 多头占优：+DI > -DI

**波动率（满足1/3即可）**：
- ✅ ATR压缩后扩张
- ✅ ATR在合理区间

**结构质量（满足1/2即可）**：
- ✅ 阻力位触碰2次+
- ✅ 连续3日上升趋势

### 3. 评分系统

```
基础分：70分（通过必要条件）
+ 相对强度：+15分
+ 多周期共振：+20分
+ 二次突破：+20分
━━━━━━━━━━━━━━
最高分：125分
入场要求：≥70分
```

### 4. 仓位管理（金字塔加仓）

```
入场：30%
├─ 盈利2%：+20% → 总50%
├─ 盈利5%：+20% → 总70%
└─ 盈利10%：+10% → 总80%

止损：动态上移
止盈：分批离场（50% / 30% / 20%）
```

---

## 📖 命令详解

### analyze - 分析单个品种

```bash
python main.py analyze <品种代码> [--start-date YYYYMMDD]

示例：
python main.py analyze RB0 --start-date 20240101
```

**输出**：
- 数据概况（K线数、日期范围、价格范围）
- 最新技术指标（MA/ATR/ADX）
- 突破信号统计
- 最终交易信号详情

### scan - 扫描品种池

```bash
python main.py scan [--symbols 品种列表] [--all] [--output 文件名]

示例：
# 扫描指定品种
python main.py scan --symbols RB0,HC0,I0,J0,JM0

# 扫描全部品种
python main.py scan --all

# 保存结果
python main.py scan --symbols RB0,HC0,I0 --output signals.csv
```

**输出**：
- 各品种扫描进度
- 发现的信号汇总表
- 按评分排序的信号列表
- CSV文件（可选）

### live - 获取实盘信号

```bash
python main.py live [--symbols 品种列表] [--top N] [--detail]

示例：
# 获取前10个信号
python main.py live --top 10

# 显示详细信息
python main.py live --top 10 --detail

# 监控所有品种
python main.py live --all --top 20
```

**输出**：
- 最新日期的信号
- 按评分排序（高分优先）
- 详细信息（可选）：入场价、止损位、ATR等

### backtest - 运行回测

```bash
python main.py backtest [--symbols 品种列表] [--start-date YYYYMMDD] [--capital 金额]

示例：
# 回测指定品种
python main.py backtest --symbols RB0,HC0 --start-date 20240101

# 回测全部品种
python main.py backtest --all --start-date 20200101 --capital 100000
```

**输出**：
- 收益指标（总收益率、年化收益、最大回撤、夏普比率）
- 交易指标（胜率、盈亏比、交易次数）
- 权益曲线（保存至output/equity_curve.csv）
- 交易记录（保存至output/trades.csv）

---

## ⚙️ 参数调整

所有参数在 `config.py` 中集中管理：

### 快速调节信号数量

```python
# 增加信号数量（降低以下参数）
MIN_ENTRY_SCORE = 65          # 当前70，降低至65
BREAKOUT_THRESHOLD = 0.002    # 当前0.003，降低至0.002
ADX_THRESHOLD = 18            # 当前20，降低至18

# 减少信号数量（提高以下参数）
MIN_ENTRY_SCORE = 75          # 当前70，提高至75
BREAKOUT_THRESHOLD = 0.005    # 当前0.003，提高至0.005
ADX_THRESHOLD = 25            # 当前20，提高至25
```

### 调整风险控制

```python
# 更保守
INITIAL_POSITION = 0.2        # 初始仓位20%
MAX_POSITION = 0.6            # 最大仓位60%
MAX_SINGLE_LOSS = 0.015       # 单笔风险1.5%

# 更激进
INITIAL_POSITION = 0.4        # 初始仓位40%
MAX_POSITION = 1.0            # 最大仓位100%
MAX_SINGLE_LOSS = 0.03        # 单笔风险3%
```

---

## 📁 文件说明

### 核心代码
- `config.py` - 所有可调参数
- `data_loader.py` - 数据获取（akshare）
- `indicators.py` - 技术指标计算
- `breakout_detector.py` - 突破检测
- `filter_conditions.py` - 过滤条件
- `signal_generator.py` - 信号生成
- `position_manager.py` - 仓位管理
- `backtest_engine.py` - 回测引擎
- `strategy.py` - 策略主逻辑
- `main.py` - 命令行入口

### 文档
- `README.md` - 完整设计文档
- `USAGE.md` - 详细使用说明
- `OPTIMIZATION_SUMMARY.md` - 参数优化总结
- `PROJECT_SUMMARY.md` - 项目总结
- `QUICK_START.md` - 本文档

### 数据目录
- `cache/` - 数据缓存（自动管理）
- `logs/` - 日志文件
- `output/` - 回测结果输出

---

## 💡 使用建议

### 日常使用流程

1. **每日盘前**
   ```bash
   python main.py live --top 10 --detail
   ```
   查看最新信号，制定交易计划

2. **周末复盘**
   ```bash
   python main.py scan --all --output weekly_signals.csv
   ```
   全面扫描，发现潜在机会

3. **策略优化**
   ```bash
   python main.py backtest --all --start-date 20240101
   ```
   定期回测，验证策略有效性

### 风险控制建议

1. **严格止损**：突破点下方1 ATR，绝不扩大亏损
2. **分批建仓**：初始30%，盈利后加仓
3. **控制总仓位**：同时持仓不超过5个品种
4. **单笔风险**：每笔最大亏损不超过2%
5. **单日风险**：单日最大亏损不超过5%

### 信号质量判断

**高质量信号特征**：
- ✅ 评分 ≥ 90分
- ✅ 量能显著放大（2倍+）
- ✅ ADX > 30
- ✅ 二次突破或箱体突破

**谨慎对待**：
- ⚠️ 评分70-80分（基础信号）
- ⚠️ 量能勉强达标
- ⚠️ 市场极端情况（暴涨暴跌）

---

## 🔧 常见问题

### Q1: 如何清除缓存？
```bash
# 删除cache目录下所有文件
rm -rf cache/*
```

### Q2: 数据获取失败怎么办？
- 检查网络连接
- akshare可能需要更新：`pip install akshare --upgrade`
- 某些品种可能已停止交易

### Q3: 如何添加新品种？
编辑 `config.py`，在 `FUTURES_UNIVERSE` 列表中添加品种代码

### Q4: 回测很慢怎么办？
- 减少品种数量（先测试几个品种）
- 缩短回测周期（--start-date 20250101）
- 利用缓存机制（第二次运行会快很多）

### Q5: 如何对接实盘？
当前版本暂不支持，计划在v2.0中加入CTP接口

---

## 📞 技术支持

遇到问题或有改进建议，欢迎反馈交流。

---

## ⚠️ 风险声明

**重要提示**：
- 期货交易有风险，投资需谨慎
- 本系统仅供学习研究使用
- 历史回测不代表未来收益
- 实盘前务必进行充分测试
- 严格执行风险控制策略

---

**最后更新**：2026-07-07  
**系统版本**：v1.1（优化版）  
**文档版本**：1.0
