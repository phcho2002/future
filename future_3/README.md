# 双均线缠绕放量突破系统 (future_3)

基于 **30 分钟 K 线** 的双均线缠绕 → 突破放量确认 → ATR 跟踪止盈的量化信号系统。
品种表与历史数据均来自本地 `futures_data.db` + akshare 期货分钟数据。

## 一、策略逻辑

多头信号（空头完全镜像）：

1. **缠绕带** —— 前 `twist_window`(默认30) 根 K 线内，`EMA(快)` 与 `EMA(慢)` 交叉次数 ≥ `min_crossings`(默认3)，
   且均线价差密集 (`|快-慢|/close ≤ twist_band_ratio`)，判定为均线密集缠绕 = 盘整收敛。
2. **金叉** —— 快线由下穿上慢线。
3. **放量中阳线** —— 金叉前后 ±`bar_window`(默认5) 根内出现一根同时满足：
   - 放量：`volume > vol_factor × MA(volume)`
   - 中阳：`|close-open|/close ≥ body_pct`，且收阳、收盘在实体上半
4. **加分项** —— 缠绕带内出现 **底部抬高**（多头）/ **高点降低**（空头）→ 信号星级提升。

满足 1+2+3 即为有效信号；4 仅影响评级。入场时机：放量 K 收盘确认 → **下一根开盘价入场**（无未来函数）。

## 二、文件结构

| 文件 | 作用 |
|------|------|
| `config.yaml` | 全部参数：默认值、优化网格、数据/路径 |
| `data_loader.py` | 读 DB 品种表 + akshare 拉 30 分 K + parquet 缓存 |
| `indicators.py` | EMA / ATR / 量能 MA / 缠绕交叉与密集度 / 底部抬高·高点降低 |
| `signals.py` | 缠绕带 + 金叉死叉 + 放量中阳中阴 + 加分评级 |
| `backtest.py` | ATR 止损 + 跟踪止盈、交易日志、绩效指标 |
| `optimize.py` | Top40 网格搜索，写回最优默认值 |
| `run_scan.py` | 主入口：扫描最新信号，输出排行 + K线图 |
| `requirements.txt` | 依赖 |

输出目录 `output/`：
- `signals_latest.csv` 最新有效信号排行
- `optimization_report.xlsx` / `optimization_all.csv` 优化报告（Top10 全局 + 每品种最优 + 全表）
- `charts/<symbol>.png` 信号 K 线图（双 EMA + 缠绕带高亮 + 信号标注 + 成交量）

## 三、快速开始

```bash
# 1) 安装依赖（国内镜像加速）
python -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 2) 参数优化（首次运行；联网拉 Top40 品种 30 分 K，缓存后做网格回测）
python optimize.py
# -> 全局最优自动写回 config.yaml 的 strategy 段
# -> 报告见 output/optimization_report.xlsx

# 3) 用最优参数扫描最新信号 + 出图
python run_scan.py

# 4) 单品种回测明细
python backtest.py --symbol AU0
```

## 四、关键参数 (`config.yaml`)

```yaml
strategy:
  ema_fast: 8            # 短期均线 (优化后自动更新)
  ema_slow: 21           # 长期均线 (优化后自动更新)
  twist_window: 30       # 缠绕回看窗口
  min_crossings: 3       # 窗口内最小交叉次数
  twist_band_ratio: 0.015# 均线密集度上界
  vol_factor: 1.5        # 放量因子 (优化后自动更新)
  vol_ma: 20             # 成交量均线周期
  body_pct: 0.006        # 中阳/中阴实体阈值 (优化后自动更新)
  bar_window: 5          # 金叉前后 ±N 根内出现放量K
  bonus_higher_low: true # 多头底部抬高加分
  bonus_lower_high: true # 空头高点降低加分

backtest:
  atr_period: 14
  atr_stop_mult: 1.5     # 初始止损 = 入场 ± 1.5×ATR
  trailing_enable: true  # 跟踪止盈
  trailing_atr_mult: 1.5 # 跟踪止损 = max(止损, 极值价 ∓ 1.5×ATR)
```

## 五、优化说明

- **网格**：`EMA快∈{5,6,7,8,9,10} × EMA慢∈{18,20,21,23,26,30} × 放量因子∈{1.3,1.5,1.8,2.0} × 实体阈值∈{0.4,0.6,0.8,1.0}%` = 576 组合。
- **评估**：聚合 Top40 全部交易，主指标 **利润因子 (Profit Factor)**；约束 `样本数≥30 且 胜率≥0.35` 防过拟合与噪声；副指标含期望 R、最大回撤 R。
- **产出**：全局最优写回默认值；Top10 组合 + 每品种最优参数落盘 xlsx。

### 优化结果 (Top40, 30分钟K线, 约3个月样本)

| 指标 | 最优值 |
|------|--------|
| EMA快 / EMA慢 | **9 / 23** |
| 放量因子 | **1.3** |
| 中阳/中阴实体阈值 | **0.6%** |
| 全局利润因子 PF | **2.005** |
| 交易笔数 | 168 |
| 胜率 | 47.0% |
| 期望R | +0.363 R/笔 |

> 注：默认参数 EMA(8/21) 已被优化结果 **EMA(9/23)** 覆盖并写回 `config.yaml`。
> 若网格搜索被超时打断，可用 `python optimize.py --resume` 从 checkpoint 续跑报告与写回。

## 六、设计要点

- **无未来函数**：缠绕/金叉/放量判定只用截至当根数据；入场固定在确认根的下一根开盘。
- **缓存优先**：K 线落 parquet，`cache_ttl_hours=6` 自动刷新，避免重复联网。
- **容错**：单品种拉取/回测失败不影响整体。
- **评级(stars)**：基础 1 星；加分项命中 +1；放量K实体≥2×阈值或缠绕≥5 次 +1（封顶 3 星）。
