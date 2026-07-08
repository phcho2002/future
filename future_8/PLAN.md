# future_8：假突破反转系统

> 昨天做突破被假突破打脸 → 今天反过来**赌突破失败**，在被套的突破交易者止损时反向开仓。

## 核心思路

- **空头 setup**：左侧明显下跌 → 反弹 → 向上假突破阻力（刺穿后收回 + 阴线放量）→ 做空
- **多头 setup**（镜像）：左侧明显上涨 → 回落 → 向下假跌破支撑（刺破后收回 + 阳线放量）→ 做多

这本质是**反趋势入场**（counter-trend at a failed re-test），与顺势突破天然对家。

## 已确认的设计决策

| 维度 | 选择 |
|------|------|
| 触发路径 | 先只做假突破失败（Spring/Upthrust），分型留二期 |
| 趋势闸 | N 根 K 线累计跌幅/涨幅 |
| 关键位 | 成交量加权密集区（借鉴 future_6 直方图+局部峰值算法，改为 volume-weighted，脱离 Renko） |
| 回测信号定位 | 宽松：触发后 N 根内有效 |
| 落地 | 新建 `future_8/`，只引 `future_data` 数据层 |

## 复用清单（不重复造轮子）

- **数据层**：`future_data.get_klines`（回测深历史）+ `inject_many`（实盘扫描单连接批量）
- **universe**：直接读 `futures_top40.json`（独立加载器，~15行）
- **回测模型**：移植 `future_1/future_quant/backtest/backtester.py` 的向量化滚动回测
- **信号核心**：移植 `future_2/wyckoff_quant/signal_generator.py` 的 `_detect_spring`/`_detect_upthrust`，关键位换密集区
- **密集区算法**：借鉴 `future_6/signals.py:detect_zones` 的直方图+局部峰值（38-87行），改 `weights=volume`，`bin_width` 改为 `atr*bin_mult`

## 目录结构

```
future_8/
├── README.md
├── config.yaml                  # 全部策略参数（可调）
├── scan_top40_15m.py            # 实盘扫描薄壳
├── backtest.py                  # 回测入口
├── fakebreak/
│   ├── __init__.py
│   ├── config.py                # FakeBreakConfig dataclass
│   ├── indicators.py            # ATR(14), RSI(14), 成交量均线
│   ├── levels.py                # ★ 成交量加权密集区 detect_volume_zones()
│   ├── trend.py                 # ★ 趋势闸 detect_trend()
│   ├── signal.py                # ★ 核心：detect_spring()/detect_upthrust()
│   └── types.py                 # Signal, TradeLevels dataclass
└── backtest/
    ├── __init__.py
    ├── backtester.py            # 移植 future_1 向量化回测
    └── runner.py                # 多品种批量回测 + 简易参数网格
```

## 信号管线（每根 K 线 i 上对 df.iloc[:i+1] 运行）

```
add_indicators(df)                         # ATR, RSI, vol_ma
    ↓
detect_trend(df, window, atr)              # 明显下跌? 最高价-当前价 ≥ drop_atr*ATR
    ↓                                       #   且 从最低价反弹 ≥ rebound_atr*ATR（非下跌中继）
detect_volume_zones(df, window, atr)       # 成交量加权直方图 → 局部峰值 → 密集区列表
    ↓
pick_nearest_zone(zones, price, side)      # 选当前价上方最近阻力 / 下方最近支撑
    ↓
detect_spring / detect_upthrust(df, zone)  # 假突破检测（核心移植）
    ↓
generate_signal()                          # 组装 Signal(entry/stop/target)
```

## 核心算法详述

### 1. 趋势闸 `trend.py`（用户选的"N根累计跌幅"）

```
窗口 = trend_window（默认40根）
window_high = df.high[-window:].max()
window_low  = df.low[-window:].min()
cur = df.close.iloc[-1]

明显下跌 = (window_high - cur) >= drop_atr * ATR     # 默认 3.0 ATR
反弹中   = (cur - window_low) >= rebound_atr * ATR    # 默认 1.0 ATR，过滤下跌中继
→ short_eligible = 明显下跌 and 反弹中
（多头镜像）
```

**趋势已破坏开关**：若反弹幅度 > 前段跌幅的 0.618，判定趋势可能真反转，不开仓（防 V 型反转打脸）。

### 2. 成交密集区 `levels.py`（借鉴 future_6，改 volume-weighted）

```python
def detect_volume_zones(df, window=60, atr, bin_mult=0.75, min_vol_ratio=0.10):
    recent = df.tail(window)
    prices = (recent.high + recent.low + recent.close) / 3   # 典型价
    vols = recent.volume
    bin_width = max(atr * bin_mult, 1e-9)
    bins = np.arange(prices.min()-bin_width, prices.max()+bin_width, bin_width)
    # ★ 关键改造：weights=volume，统计每个价位带的累计成交量
    vol_per_bin, edges = np.histogram(prices, bins=bins, weights=vols)
    total_vol = vol_per_bin.sum()
    zones = []
    for i in range(1, len(vol_per_bin)-1):
        v = vol_per_bin[i]
        if v < total_vol * min_vol_ratio:        # 成交量占比门槛
            continue
        if v < vol_per_bin[i-1] or v < vol_per_bin[i+1]:  # 局部峰值
            continue
        zones.append({center, lower, upper, strength=v/total_vol})
    return sorted(zones, key=lambda z: -z['strength'])
```

输出：每个密集区是 `{center, lower, upper, strength}`，宽度≈0.75 ATR。

### 3. 假突破检测 `signal.py`（移植 future_2，关键位换密集区）

**Upthrust（做空）**：
```
resistance = nearest_upper_zone.center       # 上方最近密集区
for 窗口内每根 break_bar (倒序找最近):
    if break_bar.high > resistance + break_atr*ATR:          # ① 刺穿阻力
        for 随后 recover_bars 根内:
            if recover_bar.close < resistance:               # ② 收回阻力下
                if recover_bar.close < recover_bar.open \    # ③ 阴线
                   and recover_bar.volume > vol_ma * 1.2:    #   放量
                    → SIGNAL: entry=close, stop=break_bar.high, target=entry-2*ATR 或下方密集区
```
**Spring（做多）**：完全镜像。

### 4. 回测（移植 future_1 backtester.py）

- warmup=120 起，每根 i 对 `df.iloc[:i+1]` 跑信号管线
- **宽松信号定位**：信号触发后 `signal_valid_bars`（默认5）根内都视为可入场（符合用户选择，更贴近实盘）
- 下一根开盘入场（含滑点），持仓中每根检查 high/low 触及 stop/target，同根都触及保守按止损先成交
- 单仓，成本 = commission(5e-5) + slippage(1点) + multiplier(品种表)

### 5. 实盘扫描 `scan_top40_15m.py`（复用 future_1 范式）

- `inject_many` 单连接批量注入 15m 缓存 → 逐品种 `generate_signal` → 输出做多/做空 TOP3 + CSV
- 只看**最新几根**是否触发假突破

## config.yaml 默认参数

```yaml
data:
  period: "15"
  length: 300
  ttl_hours: 2

strategy:
  trend_window: 40           # 趋势闸回看根数
  drop_atr: 3.0              # 明显下跌：累计跌幅阈值(ATR倍)
  rebound_atr: 1.0           # 反弹中：从低点回升阈值
  trend_break_ratio: 0.618   # 反弹超此比例=趋势破坏，不做
  zone_window: 60            # 密集区回看根数
  zone_bin_mult: 0.75        # 密集区宽度 = ATR * 此值
  zone_min_vol_ratio: 0.10   # 密集区最小成交量占比
  break_atr: 0.3             # 假突破刺穿深度(ATR倍)
  recover_bars: 3            # 收回最大K线数
  recover_volume_ratio: 1.2  # 收回放量倍数
  long_rsi_max: 40           # 做多RSI上限(超卖)
  short_rsi_min: 60          # 做空RSI下限(超买)
  target_atr: 2.0            # 目标位ATR倍数

backtest:
  signal_valid_bars: 5       # 触发后N根内有效
  commission_rate: 0.00005
  slippage_points: 1.0
  warmup: 120
  initial_capital: 100000
```

## 实施步骤

1. **脚手架**：建目录 + `config.py`/`types.py`/`config.yaml`
2. **指标层** `indicators.py`：ATR/RSI/vol_ma（纯 pandas）
3. **趋势闸** `trend.py`：detect_trend()
4. **密集区** `levels.py`：detect_volume_zones()
5. **信号核心** `signal.py`：移植 Spring/Upthrust
6. **回测** `backtester.py` + `runner.py`
7. **跑回测验证**
8. **实盘扫描** `scan_top40_15m.py`
9. **README**

## 验证标准

- 回测能跑通，输出 trades/win_rate/pnl/sharpe/max_dd
- 趋势闸能正确区分"下跌后反弹"vs"震荡区间"vs"下跌中继"
- 密集区能落在肉眼可见的高成交价位带
- 与 future_1 做突破的信号在**同一位置出现但方向相反**（验证镜像关系）
- 实盘扫描输出格式与 future_1 一致（便于对比）
