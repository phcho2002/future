# future_6: 基于 Renko Chart 的期货量化系统

## 功能

1. **ATR 定制砖块大小**：读取期货 Top40 小时线，用 ATR(14) × 系数为每个品种定制 Renko 砖块价格梯度。
2. **生成 Renko Chart**：采用传统 2 砖反转规则，把价格序列转换为砖块序列。
3. **砖块 RSI**：用砖块数量代替时间周期重新计算 RSI(14)。
4. **水平密集区与支撑阻力**：识别 Renko close 的密集成交区，输出突破 / 遇阻回落（回升）信号。
5. **趋势回撤再启动**：趋势确认后，回调不超过前期波段的 1/2，再出现 2 块同向砖即触发信号。
6. **RSI 风险警示**：结合砖块 RSI 提示严重超买 / 超卖风险。

## 文件结构

```
future_6/
├── config.yaml          # 数据源、Renko/RSI/信号参数、输出路径
├── data_loader.py       # 读取 futures_top40 并拉取 60m K 线（按 backend 路由）
├── bigquant_provider.py # BigQuant DAI 后端：1m → 60m 重采样（账号 bq5wec8s 临时用）
├── indicators.py        # ATR(Wilder)、砖块大小圆整
├── renko.py             # Renko 生成、砖块 RSI
├── signals.py           # 密集区、突破/遇阻、趋势回撤、RSI 风险
├── scanner.py           # 主入口：扫描 40 品种 → CSV
├── plot.py              # Renko 图绘制
├── README.md
└── __init__.py
```

## 依赖

- Python >= 3.10
- pandas, numpy, matplotlib, pyyaml
- **二选一的数据后端**（见下「数据后端切换」）：
  - `tqsdk`：future_data（位于 `D:/work_ai/future_data`）+ 账号文件 `D:/work_ai/tq_auth.py`
  - `bigquant`：BigQuant SDK（`pip install bigquant -i https://pypi.bigquant.com/simple/`）+ 凭证 `~/.bigquant/config.json`

## 数据后端切换

数据层做了后端抽象，**切换账号/数据源 = 改 `config.yaml` 一行，量化策略零改动**。
两条路径共用 `D:/work_ai/quote_cache` 缓存目录，切换后无需清缓存。

| 后端 | `config.yaml` 写法 | 数据源 | 鉴权 |
|------|-------------------|--------|------|
| 天勤 tqsdk（默认） | `backend: "tqsdk"` | tqsdk 直拉 60m K 线 | `D:/work_ai/tq_auth.py` |
| BigQuant DAI | `backend: "bigquant"` | DAI 拉 1m → 重采样 60m | `~/.bigquant/config.json`（`bq auth` 生成） |

切换到 BigQuant 的步骤：

```bash
# 1) 安装 SDK（仅首次）
pip install bigquant -i https://pypi.bigquant.com/simple/

# 2) 鉴权（仅首次，写入 ~/.bigquant/config.json）
bq auth --apikey 你的AK.SK

# 3) config.yaml: data.backend 改为 bigquant

# 4) 正常运行，无任何其它改动
python scanner.py
```

切回 tqsdk：把 `data.backend` 改回 `"tqsdk"` 即可。

> ⚠️ BigQuant 新平台**没有期货 60 分钟表**，bigquant_provider 通过 `cn_future_bar1m`
> 拉主力连续 1 分钟行情再按自然小时重采样为 60 分钟。数据为 T+1（最新到昨日收盘），
> 非实时；做日内实时扫描时请用 tqsdk 后端。

## 环境搭建

使用 work_ai 统一的全局 Python（已安装 tqsdk、numpy、pandas、matplotlib、pyyaml 等依赖），
无需为本项目单独建立虚拟环境：

```bash
cd future_6
python -m pip install -r requirements.txt   # 首次或依赖更新时执行
```

## 用法

```bash
cd future_6

# 全量扫描 Top40
python scanner.py

# 仅扫描前 5 个品种（测试）
python scanner.py --limit 5

# 强制刷新缓存
python scanner.py --force

# 不生成图表
python scanner.py --no-plot
```

## 输出

- `future_6/output/renko_signals_YYYYMMDD_HHMMSS.csv`：信号明细
- `future_6/output/renko_summary_YYYYMMDD_HHMMSS.csv`：每个品种的 K 线数、砖块数、砖块大小
- `future_6/output/charts/*.png`：前 N 个信号品种的 Renko 图

## 信号类型说明

| 信号类型 | 含义 |
|----------|------|
| `zone_breakout_up` | 向上突破密集成交区 |
| `zone_breakout_down` | 向下突破密集成交区 |
| `zone_rejection_up` | 在密集区附近遇阻回升 |
| `zone_rejection_down` | 在密集区附近遇阻回落 |
| `trend_pullback_long` | 上涨趋势回撤 ≤ 50% 后再启动 |
| `trend_pullback_short` | 下跌趋势回撤 ≤ 50% 后再启动 |
| `w_bottom_breakout` | W 底形态突破颈线 |
| `m_top_breakout` | M 顶形态突破颈线 |
| `rsi_risk` | RSI 严重超买/超卖风险警示 |

## 参数调整

编辑 `config.yaml`：

- `renko.brick_atr_mult`：砖块大小 = ATR × 此系数，越大过滤噪音越强。
- `zones.min_cluster_bricks`：密集区最少砖块数。
- `trend.pullback_max_ratio`：最大允许回撤比例（默认 0.5）。
- `wm.lookback_bricks`：W/M 形态回看砖数。
- `wm.neckline_breakout_bricks`：突破颈线需越过的砖数。
- `rsi.overbought` / `rsi.oversold`：超买超卖阈值。
