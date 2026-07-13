# Renko Chart 量化系统 (future_5)

基于 top40 期货**小时线**的 Renko 砖块量化系统：自适应砖块大小、砖块驱动的
RSI、水平密集成交区信号、趋势回调顺势信号、RSI 超买超卖风险提示。

数据复用全系统统一入口 `future_data`（tqsdk 后端 + TTL 缓存，全系统共享）。

## 核心思路

Renko（练烛图）用固定**价格梯度**代替时间生成"砖块"：价格涨/跌够一块砖才出
新砖，从而滤除时间噪声，只保留有效价格运动。本系统在此基础上：

1. **ATR 自适应砖块大小**（`renko.compute_brick_size`）
   每个品种按其小时线 ATR（默认 60 周期，约 1 周交易时）计算砖块大小
   `brick_size ≈ 1 × ATR`，并按品种最小变动价位 round，夹在
   `[close×0.15%, close×1.5%]` 之间。低波动品种不过粗、高波动品种不过细。
   可在 `config.yaml` 的 `brick.per_symbol_override` 手工指定。

2. **Renko 生成 + Renko-RSI**（`renko.build_renko` / `renko.renko_rsi`）
   - 经典 reversal 规则生成砖块（反转需 2 块），单根 K 线可产多块砖，
     算法保证终止（宽幅 K 线不会死循环）。
   - RSI 以**砖块数**为单位（默认 14 块），而非时间周期。因为每块砖要么涨
     要么跌，单边时 RSI 快速逼近 0/100，比时间 RSI 更早暴露极端。

3. **水平密集成交区**（`signals.find_zones` / `signals.zone_signals`）
   把砖的 open 价位按桶宽（=1 块砖）聚类，桶内砖数 ≥ 3 即为密集带。识别：
   - 遇阻回落（触及上方阻力带 → 连续下跌砖）
   - 遇阻回升（触及下方支撑带 → 连续上涨砖）
   - 突破阻力（穿越上沿 → 连续上涨砖）
   - 跌破支撑（穿越下沿 → 连续下跌砖）

4. **趋势回调顺势**（`signals.trend_signals`）
   在"运行段"（连续同向砖合并）上识别三元组：
   `大段趋势(≥6块) → 回调(反向段, 幅度≤前期1/2) → 恢复同向(≥2块)`
   恢复段的第 2 块同向砖即为确认信号（对应需求"2 块上涨砖"）。

5. **RSI 超买超卖风险**（`signals.rsi_extreme_signals`）
   Renko-RSI ≥ 80 → 严重超买（顶部反转预警）；≤ 20 → 严重超卖（底部反转
   预警）。作为风险提示与密集区反转信号共振时更强。

所有信号**无未来函数**：信号落在"确认砖"的收盘时刻，可于其下一块砖开盘入场。

## 目录结构

```
future_5/
├── config.yaml          # 数据/砖块/RSI/区域/趋势/输出 配置
├── data_loader.py       # 从 future_data 读小时线 + 品种表（复用全系统缓存）
├── renko.py             # ATR 砖块大小 + Renko 生成 + Renko-RSI + 运行段
├── signals.py           # 密集成交区 + 趋势回调 + RSI 风险 信号识别
├── run_scan.py          # 扫描 top40，输出 CSV + 图表
├── analyze.py           # 单品种深度分析
├── run.ps1              # PowerShell 统一入口
├── requirements.txt
└── README.md
```

## 快速开始 (PowerShell)

```powershell
# 1. 安装依赖（首次）
.\run.ps1 install

# 2. 自检模块
.\run.ps1 selftest

# 3. 扫描全市场（输出 output/*.csv + charts/*.png）
.\run.ps1 scan

# 4. 单品种深度分析
.\run.ps1 analyze --symbol AU0
.\run.ps1 analyze --symbol RB0 --lookback 150
```

## 输出说明（`future_5/output/`）

| 文件 | 内容 |
|------|------|
| `brick_sizes.csv` | 各品种自适应砖块大小、ATR、涨跌砖数、末值 RSI |
| `signals_latest.csv` | 最新有效信号（按 stars/新鲜度排序）|
| `zones_latest.csv` | 各品种距最新价最近的密集成交区（阻力/支撑）|
| `rsi_extremes.csv` | Renko-RSI 严重超买/超卖品种名单 |
| `charts/<symbol>.png` | Renko 砖块图 + 密集区带 + 信号标注 + RSI 子图 |

## 信号类型

| type | direction | 含义 |
|------|-----------|------|
| `zone_reversal_long`  | +1 | 触及支撑带遇阻回升（多）|
| `zone_reversal_short` | -1 | 触及阻力带遇阻回落（空）|
| `zone_breakout_long`  | +1 | 突破阻力带（多）|
| `zone_breakout_short` | -1 | 跌破支撑带（空）|
| `trend_pullback_long` | +1 | 上涨趋势回调后 2 块上涨（多）|
| `trend_pullback_short`| -1 | 下跌趋势反弹后 2 块下跌（空）|
| `rsi_overbought_risk` | -1 | RSI 严重超买风险 |
| `rsi_oversold_risk`   | +1 | RSI 严重超卖风险 |

## 配置说明（`config.yaml`）

- `data.period: "60"` —— 源数据周期（小时线）
- `brick.atr_period/atr_mult` —— 砖块大小自适应参数
- `brick.per_symbol_override` —— 手工指定单品种砖块大小
- `renko_rsi.*` —— RSI 周期与超买超卖阈值
- `zones.*` —— 密集成交区识别（桶宽/触及次数/确认砖数）
- `trend.*` —— 趋势段最小长度、回调上限比例、恢复确认砖数
- `output.*` —— 输出路径与画图品种数

## 数据源

- 品种列表：`D:/work_ai/futures_data.db` → `futures_top40`
- 小时 K 线：`future_data.get_klines`（tqsdk 后端），全系统共享缓存
  `D:/work_ai/quote_cache`
