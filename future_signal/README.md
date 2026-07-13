# future_signal — 60m 假突破→真突破 + 日线强弱过滤

## 主信号（必须）

复刻 `future_bb` 二次突破状态机，并支持**双档入场**：

```text
蓄势(箱体/收敛楔形)
  → 首次突破边界 + EMA 顺势
        ├─ 可选：试探小仓（strength × 20%，紧止损 1×ATR）
        └─ 不选：不下单，只记状态
  → 收盘回到形态内 ≥2 根（假突破确认）→ 平掉试探仓
  → 再次突破 + EMA 顺势  ← 主仓满仓（必做）
```

| 事件 | 是否下单 | 仓位强度 |
|------|----------|----------|
| 首次突破 | 可选试探 | `first_probe_strength_scale`（默认 20%） |
| 假突破失败 | 平试探 | — |
| **二次真突破** | **必做** | 满仓 strength |
| hold | 不下新单 | 维持对应 strength |

```bash
# 默认：二次满仓 + 首次 20% 试探
python -m future_signal --cache-only

# 严格：仅二次真突破下单
python -m future_signal --cache-only --no-first-probe

# 试探改为 15%
python -m future_signal --cache-only --probe-scale 0.15
```

## 过滤

| 规则 | 说明 |
|------|------|
| 日线强弱 | `futures_strength_analysis` 五维总分排名 |
| 做多 | 禁止排名 **后 10 名** |
| 做空 | 禁止排名 **前 10 名** |
| 板块 | 同板块同向最多 2 个 |
| 仓位 | `future_risk`（默认 500 万 / 15% vol） |

## 运行

```bash
python -m future_signal --cache-only
python -m future_signal --force-daily --cache-only
python -m future_signal --cache-only --csv future_signal/out_signals.csv
```

## 参数（对齐 future_bb）

| 参数 | 默认 |
|------|------|
| `pattern_lookback` | 20 |
| `box_range_max` | 0.02 |
| `fail_confirm_bars` | 2 |
| `second_break_min_gap` | 2 |
| `second_break_max_gap` | 20 |
| `breakout_threshold_2nd` | 0.003 |
| `ema_trend_period` | 200 |
| `initial_stop_atr` | 1.5 |
| `trail_atr_mult` | 2.0 |
| `ban_long_bottom_n` | 10 |
| `ban_short_top_n` | 10 |

## 文件

| 文件 | 作用 |
|------|------|
| `second_breakout.py` | 假突破→真突破状态机 + 持仓跟踪 |
| `indicators_min.py` | 箱体/楔形/ATR/EMA |
| `daily_filter.py` | 日线排名闸 |
| `pipeline.py` | 扫描总装 |
| `breakout_60m.py` | 旧 Donchian（**不再作为主信号**，仅保留参考） |
