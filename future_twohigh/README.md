# future_twohigh — 两高两低顺势突破系统

> **趋势 → 整理（两高两低）→ 顺势放量突破**。
> 用 ZigZag 把 K 线压缩成转折点序列，取最近 4 个交替摆动点判定矩形/收敛三角整理，
> 在整理末端等**顺势放量突破**入场。

## 与同库其它系统的关系

| 系统 | 体系 | 核心 |
|------|------|------|
| future_zigzag | **反转** | Three Push 衰竭 → 反转信号 |
| future_8 | **假突破反转** | 突破失败 → 反向入场 |
| **future_twohigh**（本系统） | **顺势突破** | 两高两低整理 → 顺势突破 |

本系统复用 `future_zigzag` 的 anti-repaint ZigZag 切分与回测引擎，参考 `future_bb`
已验证的三维突破阈值（量能 1.5×、实体 1.5×、幅度 0.5×ATR）。

## 策略逻辑

### 三层闸门

```
detect_zigzag(df)          → 交替摆动点序列（复用 future_zigzag，anti-repaint）
detect_two_high(zz)        → 形态判定（硬门槛：交替性 + 间隔 + 矩形/三角几何）
evaluate_quality(pattern)  → 整理质量软评分（量能萎缩 + 斐波那契回撤 + 时长）
detect_breakout(pattern)   → 顺势放量突破信号（硬：价格 + 量能 + 实体 + 幅度）
```

### ① 形态判定（硬门槛）— `pattern.py`

取最近 4 个交替摆动点。多头形态 `H1→L1→H2→L2`（向上突破 H2），空头镜像
`L1→H1→L2→H2`（向下突破 L2）。三个硬条件必须全过：

- **交替性**：kind 必须 H-L-H-L 严格交替（ZigZag 状态机天然保证，显式校验防退化）。
- **间隔**：相邻点 index 差 ≥ `min_bars_between_pivots`（默认 3，防毛刺）。
- **几何分类**（满足其一）：
  - **矩形**：`|H2−H1|/H1 < 容差`（2%）且 `|L2−L1|/L1 < 容差` → 上下轨近似水平。
  - **收敛三角**：`H2<H1`（高点下移）且 `L2>L1`（低点上移），
    且 `(H1−L1)>(H2−L2)`（振幅收敛）。
  - 都不满足 → 丢弃（如扩张三角）。

### ② 整理质量（软评分）— `quality.py`

三维各 sigmoid → [0,1]，加权成总分（默认门槛 0.40）：

| 维度 | 含义 | 判据 | 权重 |
|------|------|------|------|
| 成交量萎缩 | 量能蓄势 | 整理区间后半均量 / 前半均量，越小越萎缩 | 0.45 |
| 斐波那契回撤 | 中继 vs 反转 | 回撤深度落在 50%~61.8% 给满分（钟形） | 0.30 |
| 整理时长 | 噪音 vs 衰竭 | 10~60 根区间满分（钟形），太短噪音太长衰竭 | 0.25 |

### ③ 顺势放量突破（硬触发）— `signals.py`

形态最后拐点确认后 20 根内，首根满足四个 AND 条件的 K 线：

- **价格**：收盘越过 H2（多）/ 跌破 L2（空）+ 0.05×ATR buffer。
- **量能**：成交量 ≥ 1.5× 近 20 根均量。
- **实体**：K 线实体 ≥ 1.5× 近 20 根平均实体。
- **幅度**：突破位移 > 0.5×ATR。

止损 = 整理区间对侧（多=L2，空=H2）∓ 0.3×ATR（结构止损）。目标 = 2.0R。

## 用法

### 实盘扫描

```bash
python -m future_twohigh.scan                         # TOP40, 15min
python -m future_twohigh.scan --symbols PP0 AG0 SN0
python -m future_twohigh.scan --period 30 --top 5
python -m future_twohigh.scan --source trend_rank --recent 5  # 仅最近5根内有信号
```

### 回测

```bash
python -m future_twohigh.run_backtest                 # TOP40, 15/30min
python -m future_twohigh.run_backtest --period 60 --source trend_rank
```

输出按周期/品种/方向拆分的胜率/PF/avgR + 逐笔明细 CSV。

## 编程接口

```python
from future_data import get_klines
from future_zigzag.config import ZigZagConfig
from future_zigzag.zigzag import detect_zigzag
from future_twohigh import (
    TwoHighConfig, ConsolidationConfig, BreakoutConfig, BacktestConfig,
    detect_two_high, evaluate_quality, detect_breakout, run_backtest,
)

df = get_klines("PP0", "DCE", period="15", length=2000)
zz = detect_zigzag(df, ZigZagConfig(min_bars_between_pivots=3))

for p in detect_two_high(zz, TwoHighConfig()):
    cr = evaluate_quality(p, df, ConsolidationConfig())
    if not cr.passed:
        continue
    sig = detect_breakout(p, df, zz.atr, BreakoutConfig())
    if sig:
        print(f"{p.direction} {p.kind} 入场={sig.entry} 止损={sig.stop}")
```

## 设计要点

1. **anti-repaint**：基于 `confirmed_pivots` + `confirmed_at`，回测用 `as_of` 截断。
2. **硬门控 + 软评分**：矩形/三角几何 = 硬（必须满足）；量能/斐波那契/时长 = 软（加权打分）。
   与 future_zigzag 的风格一致，可调不过紧。
3. **双向对称**：多空镜像，扫描器分做多/做空两栏输出。
4. **复用而非重写**：ZigZag 切分、回测引擎、ATR 全部复用 future_zigzag，零重复代码。
