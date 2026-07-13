# future_system — 双账本 · 趋势主信号 · 震荡旁路

## 账本

| 账本 | 资金 | 品种 |
|------|------|------|
| **financial** | 400 万 | 股指 IM/IC/IF/IH + 国债 TF/TS |
| **commodity** | 400 万 | top40 其余商品 |

信号逻辑与风控引擎相同，**资金与品种隔离**。

### 风控硬规则（`future_risk`）

| 规则 | 默认 |
|------|------|
| 单品种最大亏损 | **总资金 × 0.95%** |
| 触及 | **无条件止损**（`HARD_STOP`，目标手数 0） |
| 开仓 | 按止损距离（或 2×日波动估算）钳制手数，使「打到止损」不超过该额度 |

例：400 万账本 → 单品种最多亏 **3.8 万** 即强制平仓。

## 双腿

| 腿 | 周期 | 信号 | 环境 | 目标波动 |
|----|------|------|------|----------|
| **趋势** | 60m | 假突破→真突破（+可选首次试探） | 仅 TREND（NEUTRAL 可 hold） | 金融 12% / 商品 15% |
| **震荡** | 30m | 假突破反转（主）+ 箱体边沿回归（辅）+ 持仓止损止盈 | 仅 RANGE | 各 5% |

### 震荡腿细节（`range_signal.py` + `range_exits.py`）

```text
箱体/楔形确认
  → 刺穿上/下沿 ≥ pierce_atr×ATR
  → recover_within 根内收回 + 失败质量分 ≥ 40
  → 反向开仓（failed_break_*）

无假突破时：
  贴边 + 拒绝K → fade_*（strength 打 0.75 折）
```

#### 止损 / 止盈（参数见 `range_exit_params.yaml`）

| 环节 | 默认设计 |
|------|----------|
| 假突破止损 | 刺穿极值外侧 `failed_stop_buffer_atr=0.35`×ATR |
| 边沿止损 | 箱外 `fade_stop_buffer_atr=0.30`×ATR |
| 风险钳制 | 止损距 ∈ `[0.45, 1.60]`×ATR |
| 结构失效 | 收盘反向再破箱 `rebreak_atr=0.20`×ATR |
| TP1 | hybrid：中轨与 1R 取更近；平 **50%** |
| TP2 | 对侧与 2R 取更远；平剩余 |
| 保本 | 浮盈 ≥0.8R → 止损移到成本+锁利 |
| 移动止损 | TP1 后或 ≥1R：极值回撤 `trail_atr=1.0` |
| 时间 | 最长 24 根；不足 0.25R 可时间离场 |
| 过滤 | TP1 盈亏比 <0.8 弃单 |

优化：改 `RangeExitConfig` 或 `range_exit_params.yaml`；网格见 `exit_param_space()`。

互斥：同品种趋势有仓则不再开震荡。

## 环境闸（默认参数）

| 参数 | 值 | 含义 |
|------|-----|------|
| `adx_range_max` | 22 | ADX≤22 偏震荡 |
| `adx_trend_min` | 26 | ADX≥26 偏趋势 |
| `width_lookback` | 40 | 波幅窗口（60m） |
| `width_range_max` | 3.2% | 窄箱 → 震荡 |
| `width_trend_min` | 5.5% | 宽通道 → 趋势 |
| `atr_compress_max` | 0.88 | ATR 压缩 |
| `atr_expand_min` | 1.15 | ATR 扩张 |

综合打分 → `TREND` / `RANGE` / `NEUTRAL`。

## 运行

数据源默认 **akshare**（经 `future_data.get_klines`）。切回天勤：

```bash
set FUTURE_DATA_BACKEND=tqsdk
```

```bash
# 本地缓存快速扫描（自动读写账簿）
python -m future_system --cache-only

# 全量刷新（akshare 联网写缓存）
python -m future_system --force-refresh --force-daily

# 趋势禁用首次试探
python -m future_system --cache-only --no-first-probe

# 只看账簿持仓 / 近期成交
python -m future_system --show-ledger

# 不写账簿（纯信号）
python -m future_system --cache-only --no-ledger
```

输出：

- `future_system/out_signals.csv` — 信号
- `future_system/out_risk.csv` — 风控目标手数
- `future_system/out_trades.csv` — **本轮成交**
- `future_system/out.json` — 摘要含账簿
- `future_system/data/trading_ledger.db` — **SQLite 账簿（持久化）**
- `future_system/data/ledger_snapshot.json` — 账簿快照

### 交易记录（SQLite）

| 表 | 内容 |
|----|------|
| `accounts` | 双账本 cash / 保证金占用 / equity / 已实现盈亏 |
| `positions` | 持仓（sleeve+symbol、手数、均价、止损止盈） |
| `trades` | 开仓/加仓/减仓/平仓/止损/止盈/硬止损 |
| `runs` | 每次运行 run_id |

每次运行会：读旧持仓与资金 → 止损止盈/硬止损 → 按信号目标开加减仓 → 回写账簿。
