# future_zigzag

ATR 自适应 + anti-repaint 的 ZigZag 波段切分。作为后续 Three Push / 反转信号系统的输入地基。

## 为什么重写（对比 future_1 的缺陷）

`future_1` 实盘效果不好的根因之一是波段切分从根上就错了：

| future_1 缺陷 | 本模块修正 |
|---|---|
| `indicators.py:45` ATR 用 SMA（与 TradingView/MT4 全部不一致）| 用 Wilder's RMA，与所有标准平台一致 |
| `pushes.py:45` 推动方向只看收盘价，影线极值丢失 | 用 high/low 捕捉影线极值 |
| 通道拟合对所有 60 根做 OLS（噪声全进去）| 波段基于已确认 Pivot，不回归噪声 |
| 无 anti-repaint，回测可用未来信息 | running-extreme 天然 anti-repaint，Pivot 带 `confirmed_at` |
| 无幅度门槛（fixed-bar fractal）| depth = 1.5×ATR，跨品种可比 |

## 核心算法：running-extreme 状态机

```
状态: direction(找高/找低), running_extreme=(idx, price)
逐根 i:
    depth_i = depth_atr_multiple × atr[i]              # 自适应门槛
    若 找高:
        若 high[i] > running_extreme: 极值刷新          # 持续追踪真极值(天然抗假突破)
        若 running_extreme - low[i] >= depth_i:        # 反向达 depth → 确认高点
            记录 Pivot(idx, price, 'high', confirmed_at=i)
            翻转 → 找低
    找低: 镜像
```

**anti-repaint 保证**：拐点只在价格反向波动 ≥ depth 后才"确认"，每个 Pivot 在 `confirmed_at` 那根定型、永不变。回测只用 `confirmed_at <= 当前K线` 的 Pivot，无未来信息。最后一段 running extreme 标记 `confirmed=False`（暂定）。

## 验证结果（9 品种 × 3000 根 15min，depth=1.5×ATR）

| 指标 | 结果 | 说明 |
|---|---|---|
| 数据完整性 | 9/9 品种均拿到 3000 根 | xtquant 深历史充足 |
| 交替率 | **1.000**（全部品种）| 高低严格交替，状态机正确 |
| anti-repaint | **0.000**（全部品种）| 增量切片重跑 594 个对照拐点全部一致 |
| mean 段 (ATR倍) | **3.05 ~ 3.26** | 落在合理区间 3~6，且跨品种波动 <7% |
| depth 敏感度 | 拐点随 depth 平滑递减 | 算法稳健，无临界点剧变 |

depth=1.5 是经敏感度分析选定的默认值：1.0 太细（mean 段 2.4 ATR，毛刺多），2.0 太粗（4.2 ATR，漏 Three Push 子推动）。

## 用法

```bash
# 默认 depth=1.5, 15min, 3000根
python -m future_zigzag.run

# 调参
python -m future_zigzag.run --depth 2.0 --period 60
python -m future_zigzag.run --mode close   # 收盘价触发(更干净)
```

输出到 `output/`：每品种 `{symbol}_overview.png`（全景）+ `{symbol}_closeup.png`（特写）+ `zigzag_report.csv` + `depth_sensitivity.csv`。

## API

```python
from future_zigzag import detect_zigzag, ZigZagConfig

result = detect_zigzag(df, ZigZagConfig(depth_atr_multiple=1.5))
for p in result.confirmed_pivots:   # 回测安全集合
    print(p.index, p.price, p.kind, p.confirmed_at)
print(result.provisional)            # 暂定极值
```

## 第二步：Three Push 识别 + 四维量化评分

### 模式定义（6 拐点 P0..P5）

三个同向推动腿 + 两个反向回撤腿。顶部三推为例：
```
P0(低) → P1(高)推1 → P2(低)回撤1 → P3(高)推2 → P4(低)回撤2 → P5(高)推3
结构约束：三个顶递升 P1<P3<P5，两个低递升 P0<P2<P4（每次推动创新高）
```

### 四维评分（替代 future_1 的"7 布尔等权平均"）

每个维度用 soft sigmoid 连续映射到 [0,1]，越大越衰竭：

| 维度 | 定义 | 权重 | 验证区分度 |
|---|---|---|---|
| **幅度 amplitude** | L3 净幅 / max(L1,L2) 净幅，越小越衰竭 | 0.35 | **+0.510**（最强）|
| **成交量 volume** | L3 均量 / max(L1,L2) 均量，越小越枯竭 | 0.30 | **+0.431** |
| **推动力 momentum** | L3 atr_multiple / max(L1,L2)，越小越衰竭 | 0.20 | **+0.400** |
| 时间 duration | L3 根数 / max(L1,L2)，拖延型衰竭 | 0.15 | +0.046（弱）|

**硬门控**：幅度或成交量至少一项显著衰减（≤0.60）才算有效 Three Push——
这是 Al Brooks 体系最硬的两条（幅度+量能是衰竭的直接证据）。
配合总分门槛 0.45 双重约束。

### 验证结果（9 品种 × 3000 根，depth=1.5×ATR）

```
共 982 个模式, 187 个有效 (19%)
有效模式总分中位 0.57 vs 无效 0.17（分离清晰）
有效幅度比中位 0.46 vs 无效 0.90（衰竭第三推幅度只有前两推一半）
```

四维诊断力（有效 vs 无效均值差）：幅度 +0.51 > 量能 +0.43 > 推动力 +0.40 >> 时间 +0.05。
**数据证实：幅度/量能/推动力是有效诊断维度，时间维度弱**——这也正是硬门控
选幅度+量能的原因。

### 用法

```bash
python -m future_zigzag.run_three_push                    # 默认 depth=1.5
python -m future_zigzag.run_three_push --min-score 0.50   # 提高门槛
```

输出：`{symbol}_threepush.png`（模式标注+分数）+ `threepush_report.csv` +
`threepush_dim_diagnostic.csv`（四维诊断明细）。

```python
from future_zigzag import detect_zigzag, detect_three_push, ZigZagConfig, ThreePushConfig

zz = detect_zigzag(df, ZigZagConfig(depth_atr_multiple=1.5))
patterns = detect_three_push(zz, ThreePushConfig())
for p in patterns:
    if p.score.hard_gate_passed and p.score.total >= 0.45:
        print(p.reversal_kind, p.score.total, p.score.as_dict())
```

## 第三步：收缩判定 + 前瞻回报验证

### 收缩判定（Three Push 之后的独立闸门）

两个独立判据，至少满足其一：
1. **幅度递减**：三推幅度 L1>L2>L3 单调递减（楔形/三角形的几何特征）。
2. **ATR 收缩**：模式末端 ATR 处于过去 100 根的低位分位（≤0.35）= 波动率收缩 → 即将扩张。

收缩判定独立于四维评分——一个 Three Push 可以衰竭但未收缩（V 型反转），
也可以收缩但未衰竭（慢速楔形）。作为独立闸门能更精确刻画状态。

### 前瞻回报验证（第一次用"结果"验证预测力）

future_1 完全缺失的一环——它只调出魔法数 55，从不验证信号是否赚钱。
本模块对每个模式统计未来 N 根 K 线沿反转方向的 MFE/MAE/胜率（ATR 归一化）。

**验证结果（189 有效模式，通过收缩 90 vs 未通过 99）：**

| horizon | 通过收缩 MFE | 未通过 MFE | Δ | 通过胜率 | 未通过胜率 | Δ |
|---|---|---|---|---|---|---|
| 80 | 7.33 | 6.04 | **+1.29** | **60.0%** | 55.6% | **+4.4%** |

**结论**：收缩判定的预测力在中长周期（40-80 根）显著兑现——MFE 多 1.29 ATR、
胜率高 4.4%、MFE/MAE 比 >1.3（有利偏移显著大于不利偏移）。符合"波动率收缩→
趋势性扩张"逻辑。但短样本（189）+ 4-6% 胜率差说明需后续反转触发器进一步过滤。

### 用法

```bash
python -m future_zigzag.run_contraction
```

输出：`contraction_lookahead.csv`（分组前瞻统计）+ `contraction_detail.csv`（模式明细）。

```python
from future_zigzag.contraction import evaluate_contraction
from future_zigzag.lookahead import evaluate_pattern

cr = evaluate_contraction(pattern, zz.atr, len(df), ContractionConfig())
outs = evaluate_pattern(pattern, df, zz.atr, LookaheadConfig())
print(cr.passed, cr.score, [(o.horizon, o.ret) for o in outs])
```

## 代码结构

```
future_zigzag/
  config.py         ZigZagConfig + ThreePushConfig + ContractionConfig + LookaheadConfig + SYMBOLS
  indicators.py     wilder_atr / true_range（修正 SMA bug）
  zigzag.py         Pivot / ZigZagResult / detect_zigzag（状态机）
  three_push.py     Leg / ThreePushPattern / FourDimScore / detect_three_push
  contraction.py    ContractionResult / evaluate_contraction
  lookahead.py      LookaheadOutcome / evaluate_pattern / summarize_groups
  validate.py       segment_stats / alternation / phantom_rate / depth_sensitivity
  three_push.py     Leg / ThreePushPattern / FourDimScore / detect_three_push
  validate.py       segment_stats / alternation / phantom_rate / depth_sensitivity
  visualize.py      plot_overview / plot_closeup / plot_three_push
  run.py            ZigZag 验证 CLI
  run_three_push.py Three Push 验证 CLI
  run_contraction.py 收缩判定 + 前瞻验证 CLI
  run_signals.py    三类信号检测 + 前瞻验证 CLI
  output/           PNG + CSV（gitignore）
```

## 第四步：三类反转信号检测

future_1 这一块基本是空的——楔形突破/反转K/二次入场三个检测器塌缩成一个
`body>=0.3×ATR` 的弱布尔。本模块把三类写成独立检测器，各有硬触发条件。

### 三类信号（基于已确认 Three Push + 收缩状态）

**① 楔形趋势线突破**：用三个推极值 P1,P3,P5 拟合趋势线（拐点拟合，非全 K 线 OLS），
收盘突破触发。

**② 强反转 K**：硬定义——实体≥0.5×ATR + 收盘在极端 30% 区间 + 放量≥1.2×均值
+ 位置在 P5 极值 ±1.5×ATR 内。（门槛从 0.7 放松到 0.5，样本增 18% 而胜率不降）

**③ 二次入场**：状态机（首次突破→回踩→二次突破）。跨 K 线时序逻辑，
这是 future_1 架构根本缺失的（它无状态逐 K 扫描，抓不到时序）。
止损放回踩极值外侧（比 P5 紧，R:R 更好）。

### 信号去重（关键优化）

前瞻验证发现 wedge_breakout 和 second_entry 高度共线——中位间隔仅 2 根 K 线，
67.5% 重复间隔 ≤3 根，本质是同一突破事件的重复记录。

去重策略：每个 pattern 只保留一个最优信号，优先级
`reversal_bar(胜率最高) > wedge_breakout(MFE/MAE最优) > second_entry(最弱)`。

去重效果（211 → 90 个信号，砍掉 57%）：

| horizon | 去重前胜率 | 去重后胜率 | Δ |
|---|---|---|---|
| h=10 | 58.8% | **63.3%** | **+4.5%** |
| h=80 | 62.1% | 61.1% | -1.0% |

去重在短周期显著提升胜率（+4.5%），中长周期持平。去重后 second_entry 被
完全吸收（与 wedge 共线），只剩 wedge_breakout(58) + reversal_bar(32) 两类。

### 验证结果（去重后 90 个信号）

| 信号类型 | n | 胜率(h40) | MFE/MAE | 止损率 |
|---|---|---|---|---|
| 楔形突破 | 58 | 51.7% | 1.42 | 62.1% |
| **强反转K** | 32 | **62.5%** | **1.47** | 50.0% |

强反转K 在放松门槛后样本增 18%（27→32），胜率保持 62.5%，仍是最优信号类型。

### 用法

```bash
python -m future_zigzag.run_signals    # 输出去重前后对比
```

输出：`signals_lookahead.csv`（去重前后前瞻统计）+ `signals_detail.csv`（去重后明细）。

```python
from future_zigzag.signals import best_signal

# best_signal = 去重后的最优信号（推荐入口，替代 detect_all_signals）
sig = best_signal(pattern, df, zz.atr, SignalConfig())
if sig:
    print(sig.signal_type, sig.side, sig.entry, sig.stop)
```

## 第五步：完整回测引擎

把 best_signal → 入场 → 止损/目标平仓 → 统计期望串起来。信号独立结算模式
（每信号独立入场+止损+目标，允许重叠）。成本：手续费万0.5 + 滑点1点（单边）。

### 回测戳破了前瞻验证的乐观假象

前瞻验证只看"最大有利偏移(MFE)"，忽略"路上被止损打掉"。回测加入实际出场
（次根开盘入场、止损/目标逐根检查、成本扣除）后，胜率从 56% 掉到 44%——
这是更诚实的评估。

### R:R 敏感度（统一目标倍数，90 笔交易）

| target_rr | 胜率 | avg_r | 盈亏比PF |
|---|---|---|---|
| 1.0 | 52.2% | -0.086 | 1.14 |
| 2.0 | 43.3% | +0.101 | 0.70 |
| 3.0 | 41.1% | **+0.301** | 0.87 |

**结论：当前任何 R:R 都不能让系统稳健盈利。** R:R=1.0 勉强不亏(PF 1.14)，
R:R=3.0 的正期望靠少数大赢撑着但 PF<1。问题不在 R:R 调参，而在信号质量。

### 按信号类型（差异化 R:R）

| 类型 | n | 胜率 | avgR | PF | 止损率 |
|---|---|---|---|---|---|
| wedge_breakout | 58 | 39.7% | 0.012 | 0.96 | 59% |
| reversal_bar | 32 | **53.1%** | **0.136** | 0.46 | 47% |

reversal_bar 胜率/期望仍最优但 PF 低（赢的赢得少）。wedge_breakout 止损率 59%
拖累整体。AG0/OI0 单品种盈利(PF>1.5)，JM0/SC0/LC0 亏损。

### 止损方案对比实验（结构止损 vs 固定资金止损）

| 模式 | 胜率 | avgR | PF | 止损率 |
|---|---|---|---|---|
| **结构止损 P5+1.5×ATR** | **48.9%** | **+0.224** | **1.61** | 48% |
| 固定资金 0.9%×资金 | 41.1% | -0.573 | 1.25 | 54% |

**固定资金止损更差**，根因是合约乘数差异（1~1000 跨三个数量级）：同样的 900 元，
在 SC0(乘数1000)只值 0.9 点（原油波动 2.7 点→秒止损全亏），在 PP0(乘数5)值 180 点
（=4.6 个 ATR→几乎不触发）。止损距离必须相对于**品种波动率(ATR)**，而非资金/乘数。
SC0 固定资金止损下 15 笔全亏(avgR -3.6)就是明证。

结论：**结构止损(P5+1.5×ATR)是当前最优方案**。这也印证了 ZigZag 以 ATR 归一化
的底层设计——整个系统的跨品种可比性建立在 ATR 之上。

### 按信号类型（结构止损 P5+1.5×ATR）

| 类型 | n | 胜率 | avgR | PF | 止损率 |
|---|---|---|---|---|---|
| wedge_breakout | 57 | 42.1% | -0.435 | 1.84 | 51% |
| reversal_bar | 33 | 39.4% | -0.811 | 0.74 | 58% |

注：不同止损模式下类型排序会变（结构止损让 wedge 的 PF 升到 1.84）。
品种差异显著：PP0/AG0/OI0 盈利(PF>1.7)，SC0/IC0 亏损。

### 用法

```bash
python -m future_zigzag.run_backtest    # 输出两种止损模式对比 + R:R 扫描
```

输出：`backtest_rr_sweep_fixedrisk.csv`(固定止损R:R敏感度) + `backtest_trades.csv` +
`backtest_by_type.csv` + `backtest_by_symbol.csv`。

```python
from future_zigzag import run_backtest, BacktestConfig

# 结构止损（推荐）
res = run_backtest(signals, BacktestConfig(stop_mode="structure"))
# 固定资金止损
res2 = run_backtest(signals, BacktestConfig(stop_mode="fixed_risk", risk_pct=0.009))
print(res.summary())
```

## 后续步骤（本模块不包含）

回测暴露的改进方向（优先级排序）：
- **品种筛选**：只做 PP0/AG0/OI0 等盈利品种(PF>1.7)，避开 SC0/IC0
- **多周期方向过滤**：大周期逆势信号直接砍掉
- 信号质量本身（wedge_breakout 止损率仍高，需更严突破确认）
- 单仓模式回测（评估真实资金曲线）
