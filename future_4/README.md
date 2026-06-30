# 双均线缠绕放量突破量化系统 (future_4)

基于 AKShare 期货历史数据的完整量化平台：回测、信号扫描、参数优化、绩效分析。

## 策略逻辑

1. **均线参数**：短期 EMA(10)，长期 EMA(26)（可在 `config.yaml` / `optimize.py` 中调整）。
2. **交叉结**：短期均线上/下穿长期均线一次计为一个交叉结。
3. **缠绕带**：最近 30 根 K 线内出现 2 个及以上交叉结。
4. **放量中阳/中阴**：
   - 成交量 >= `vol_factor` × 最近 20 根成交量均值，**或**
   - 成交量 >= `prev_vol_factor` × 前一根成交量。
   - 中阳线：收盘价同时站上短期均线和长期均线，且实体占比 >= `body_pct`。
   - 中阴线：收盘价同时跌破短期均线和长期均线，且实体占比 >= `body_pct`。
5. **无未来函数**：信号以放量 K 收盘确认，下一根开盘入场。
6. **加分项**：
   - 向上突破前出现“底部抬高”加 1 星。
   - 向下突破前出现“高点降低”加 1 星。

## 目录结构

```
future_4/
├── config.yaml          # 策略/回测/优化/输出配置
├── data_loader.py       # 从 futures_data.db 读品种 + AKShare 拉 K 线 + parquet 缓存
├── indicators.py        # EMA、ATR、成交量、缠绕带、底部抬高/高点降低
├── signals.py           # 信号识别
├── backtest.py          # ATR 止损 + 跟踪止盈回测 + 绩效指标
├── optimize.py          # 网格搜索优化均线周期、放量因子、实体阈值
├── run_scan.py          # 扫描最新信号并输出 CSV + 图表
├── analyze.py           # 对指定品种做深度分析并画图
├── run.ps1              # PowerShell 统一入口
└── README.md
```

## 快速开始 (PowerShell)

```powershell
# 1. 安装依赖（首次）
.\run.ps1 install

# 2. 回测单个品种
.\run.ps1 backtest --symbol TA0

# 3. 扫描全市场最新信号
.\run.ps1 scan

# 4. 参数优化（默认 futures_top40）
.\run.ps1 optimize

# 5. 深度分析单个品种
.\run.ps1 analyze --symbol AU0
```

## 配置说明

编辑 `config.yaml` 调整：

- `data.symbol_table`：`futures_top40` 或 `futures_all`
- `strategy.*`：策略参数，优化后会自动写回
- `backtest.*`：ATR 止损/跟踪止盈参数
- `optimize.grid`：优化网格
- `output.*`：输出路径

## 数据源

- 品种列表：`D:/work_ai/futures_data.db`
- K 线：`akshare.futures_zh_minute_sina(symbol=..., period="30")`
- 本地 parquet 缓存：`future_4/cache/`，6 小时 TTL。
