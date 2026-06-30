# future-quant

基于 Python + 天勤 (TqSdk) 的量化交易研究系统。当前版本重点实现：

- 模块 1.1：市场结构判定，识别牛市、熊市、交易区间、窄通道、宽通道。
- 模块 1.2：通道类型识别（斜率按 ATR 归一化，跨价位品种可比）。
- 模块 2-6：推动识别、力量衰竭评分、严谨化入场信号、目标止损与风险管理。
- 回测与参数优化：向量回测引擎 + 网格搜索，用于客观验证并优化信号参数。

> 该项目用于研究与回测，不构成投资建议。实盘前必须加入滑点、手续费、合约乘数、交易时段、限价/市价成交模型和完整回测验证。

## 数据源

数据层完全基于 [天勤 TqSdk](https://www.shinnytech.com/tqsdk/)，通过主力连续合约（`KQ.m@`）获取 15 分钟 K 线。凭证从 `D:/work_ai/tq_auth.py` 自动读取（`TqAuth(user, pwd)`）。

> 免费版单次最多 8964 根 K 线；15 分钟周期约对应数月历史，足够回测。需要更长历史请使用付费版。

## 安装

```bash
pip install -e .
```

## 快速运行

单品种分析（`--exchange` 用于构造 TqSdk 合约代码）：

```bash
python -m future_quant.cli --symbol IF0 --exchange cffex --period 15
```

全市场扫描（单连接批量订阅 40 品种）：

```bash
python tqsdk_scan.py
```

TOP5 接近度评分（可作 cron 每 15 分钟运行）：

```bash
python tqsdk_scan_top5.py
```

## 回测与参数优化

```python
from future_quant.backtest import CacheManager, grid_search, report

cm = CacheManager()
cm.build_cache(length=2000)              # 首次从天勤拉取并缓存为 parquet
data = cm.load_all()                     # 后续直接读缓存
results = grid_search(data, rank_by="sharpe")
print(report(results, top_n=10))
results.save_csv("grid_results.csv")
```

回测模型为研究级简化版：滚动调用引擎、次日开盘入场、止损/目标1 触发平仓（同根 K 线同时触及则保守按止损先成交），计入手续费与滑点。详细假设见 `future_quant/backtest/backtester.py`。

## 数据格式

核心模块使用标准 OHLCV 列：

```text
datetime, open, high, low, close, volume
```

## 代码结构

```text
future_quant/
  cli.py                  # 命令行入口
  config.py               # 参数配置（衰竭阈值/RR/确认/反转K/斜率归一化等）
  engine.py               # 分析引擎
  core/types.py           # 结构化结果类型
  data/tqsdk_provider.py  # TqSdk 数据适配（统一符号/批量订阅/parquet 缓存）
  indicators.py           # ATR、K线特征、重叠度、归一化斜率等指标
  market_state.py         # 模块 1.1 市场结构
  channels.py             # 模块 1.2 通道类型（归一化斜率）
  pushes.py               # 模块 2 推动识别与衰竭评分
  signals.py              # 模块 3 严谨化交易信号
  risk.py                 # 模块 4/5 目标、止损、仓位、风险回报
  backtest/
    cache.py              # parquet 缓存管理
    backtester.py         # 向量回测引擎
    grid_search.py        # 参数网格搜索
```

## 示例

```python
from future_quant.engine import QuantEngine

engine = QuantEngine()
result = engine.analyze_df(df)

print(result.market_state)
print(result.channel)
print(result.signal)
```
