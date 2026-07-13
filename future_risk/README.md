# future_risk — 波动率目标 + 动态风险预算（国内期货 top40）

面向 `futures_top40.json` 全 40 品种，默认资金 **500 万**。  
本模块是**仓位与风控层**，不产生 alpha；上游给方向/强度，这里输出目标手数、调仓意图与换月双腿。

## 约束与约定

| 项 | 默认 |
|----|------|
| 资金 | 5,000,000 |
| 组合年化波动目标 | 15% |
| 组合保证金上限 | 权益 × 40% |
| 单品种保证金上限 | 权益 × 8% |
| 单品种风险权重上限 | 10% |
| **单品种最大亏损** | **总资金 × 0.95%**（触及无条件止损；开仓按止损距离钳手数） |
| 最大持仓品种数 | 12 |
| 品种池 | `D:\work_ai\futures_top40.json` |
| 合约乘数 | `futures_data.db` → `futures_top40.合约乘数` |
| 换月 | **平旧 + 开新同时生成**（`roll_simultaneous=True`） |

## 快速演示

```bash
python -m future_risk.demo
python -m future_risk.demo --signals RB0:1,I0:1:0.8,AU0:-1,SC0:1 --capital 5000000
python -m future_risk.demo --roll RB0:SHFE.rb2510:SHFE.rb2511:0:1 --json-out future_risk/out_demo.json
```

`--signals` 格式：`品种:方向[:强度]`，方向 `1`/`-1`，强度默认 `1`。

## 接入策略信号

```python
from future_risk import RiskConfig, RiskEngine, SignalInput

cfg = RiskConfig(capital=5_000_000, target_vol_annual=0.15)
eng = RiskEngine(cfg)

snap = eng.size([
    SignalInput("RB0", direction=1, strength=1.0, price=3087, daily_vol=0.012,
                contract="SHFE.rb2510", current_lots=0),
    SignalInput("AU0", direction=-1, strength=0.8, price=891, daily_vol=0.010,
                contract="SHFE.au2512", current_lots=0),
])

for p in snap.positions:
    print(p.symbol, p.direction, p.target_lots, p.margin)

# 换月：平开同时（lots=0 时请显式传入当前持仓手数）
rolls = eng.plan_rolls(
    [("RB0", "SHFE.rb2510", "SHFE.rb2511", 10, 1)],
    snapshot=snap,
)
for plan in rolls:
    for leg in plan.legs:
        print(leg.role, leg.side, leg.offset, leg.contract, leg.lots)
```

## 仓位公式（摘要）

1. 原始风险权重 \(w_i^{raw} \propto strength_i / \sigma_i^{daily}\)
2. 归一化后施加：单品种 cap、板块 cap、最大持仓数
3. 目标手数：
   \[
   lots_i = \frac{Equity \times \sigma_{target}^{daily} \times w_i}
                {Multiplier_i \times Price_i \times \sigma_i}
   \]
4. 再过：单品种保证金、**单品种最大亏损 0.95% 资金（按止损距/波动估）**、组合保证金
5. 若持仓浮亏 ≤ −0.95%×资金 → **强制 target=0 平仓**（`HARD_STOP`）
6. 与当前持仓比较，超过阈值才生成开平意图（强平忽略阈值）

## 换月（平开同时）

- 多头：`SELL CLOSE` 旧合约 + `BUY OPEN` 新合约，手数相同  
- 空头：`BUY CLOSE` 旧合约 + `SELL OPEN` 新合约，手数相同  
- 若换月日同时调仓：先完整滚到新合约，再在新合约上补加减仓（`merge_roll_with_rebalance`）  
- **不要**在旧合约上调完仓再换月，避免双倍滑点与隔夜断档

## 板块预算（默认）

见 `config.py` 中 `SECTOR_MAP` / `DEFAULT_SECTOR_CAP`：  
股指 30%、黑色 30%、化工 25%、有色 25%、农产品 25% …  

保证金比例为近似值，实盘请用期货公司标准覆盖 `margin_rate_by_sector`。

## 文件

| 文件 | 作用 |
|------|------|
| `config.py` | 500万参数、板块、保证金、cap |
| `universe.py` | top40 JSON + DB 乘数 |
| `vol.py` | EWMA / realized 日波动 |
| `engine.py` | 风险预算引擎 |
| `roll.py` | 换月双腿 |
| `demo.py` | CLI 演示 |
