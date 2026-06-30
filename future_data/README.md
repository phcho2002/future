# future_data — 统一期货行情数据入口

全系统统一的期货**分钟 K 线**数据入口，tqsdk 后端 + TTL parquet 缓存。

## 为什么需要它

本机原本有 **8+ 个分散的行情数据入口**：

| 系统 | 原数据源 | 周期 | 缓存 | 问题 |
|---|---|---|---|---|
| future_1/future_quant | tqsdk | 15 | parquet | 已有 fetch_many 批量（**入口基础**） |
| future_2/wyckoff_quant | tqsdk | 15/30/60/120 | 无 | **每个品种 new TqApi 重连**，最浪费 |
| future_3 | akshare | 30 | parquet TTL6h | 缓存模式可借鉴 |
| futures_30min_signals.py | akshare | 30 | 无 | — |
| futures_hourly_analysis.py | akshare | 60 | 无 | 有 `.str[:10]` datetime bug |
| future/run_futures_30m.py | akshare | 30 | 无 | — |
| future/futures_signal.py | akshare | 60 | 无 | — |
| future/backtest_albrooks.py | akshare | 30 | 无 | — |

鉴权（`_load_auth`）、连续合约映射（`build_tq_symbol`）、缓存逻辑各抄一遍。
本包把它们收口到一个入口，**下游分析器零改动**（两套 tqsdk provider 本就返回相同 schema）。

> akshare 的日线/沉淀资金/品种列表（`futures_top40.py` 等）**不在本入口范围**，
> 因为 tqsdk 缺动态结算价/手续费表，保留 akshare 处理。

---

## mootdx 测试结论：**不可用，已放弃**

任务第 1 步实测了 [mootdx](https://github.com/mootdx/mootdx) 的期货行情支持：

- mootdx 启动即告警：`目前扩展市场行情接口已经失效, 后期有望修复.`
- 扩展市场（ext，期货所在）历史 K 线：`markets()` 能列出中金所等 31 条分类，
  但 `bars()` 对 `AU0 / rb0 / IF0` 等 **16 个组合全部返回空/异常（命中 0）**。
- 扩展市场实时：`ExtQuotes` 没有 `quotes()` 方法，`quote()` 同样无命中。
- 标准市场（股票）对照组正常 → 证明库本身完好，是**期货行情通道本身失效**。

**结论：mootdx 不能用于期货，继续用 tqsdk。**
（测试用的 mootdx/tdxpy 已卸载；曾误降级 httpx/tenacity 破坏 hermes-agent/mcp，
已 `--force-reinstall` 还原至 `httpx==0.28.1 / tenacity==9.1.4 / certifi==2026.5.20`。）

---

## 快速开始

### 1. 单品种拉取（自动 TTL 缓存）

```python
import sys; sys.path.insert(0, "D:/work_ai")
from future_data import get_klines

df = get_klines("RB0", "shfe", period="15")          # 命中缓存或联网
df = get_klines("RB2610", "shfe", period="15")       # 具体合约也行
df = get_klines("RB0", "shfe", period="15", force=True)  # 强制刷新
```

返回契约（全系统统一）：

```
DataFrame[datetime, open, high, low, close, volume]
  datetime : pd.Timestamp（注意：不是字符串）
  OHLCV    : float64
  升序、整数 RangeIndex
```

### 2. 批量拉取（单连接 —— future_2 的核心优化）

```python
from future_data import fetch_many, read_symbols

symbols = read_symbols()              # 读 futures_top40
out = fetch_many(symbols, period="15", length=200)   # 一次连接拿全部
```

### 3. 命令行：盘前预热缓存

```bash
python -m future_data refresh --period 15    # 预热 top40 全部品种（单连接，TTL 全量）
python -m future_data inject --period 15     # 注入模式：增量更新、滚动窗口（短周期实盘扫描）
python -m future_data status                  # 查看新鲜度
python -m future_data bench                   # 提速基准对比
python -m future_data explain RB0 shfe        # 解释符号解析
```

预热后，所有 `./future_*` 扫描在 TTL 内（默认 2 小时）全部命中缓存。

---

## 缓存提速（任务第 3 步结论）

**实测基准**（8 品种 × 15min × 200 bars，2026-06-22，`python -m future_data bench`）：

| 路径 | 耗时 | 说明 |
|---|---|---|
| akshare 逐品种 | 2.61s | 新浪 HTTP，含 0.3s sleep/品种 |
| tqsdk 批量（单连接）| 7.87s | 首次建连 + 等 K 线回传 |
| **缓存命中** | **0.05s** | 纯 parquet 读盘 |

- **缓存命中 vs akshare 提速 52×**
- **缓存命中 vs tqsdk 批量提速 157×**

> 注：akshare 对**小批量**反而比 tqsdk 首次建连快（新浪 HTTP 轻）。但缓存的价值
> 不在省首次拉取——而在**避免每次扫描都重复拉取**。量化系统每天/每小时反复扫 top40，
> 首次预热一次（几秒），之后全系统在 TTL 内（默认 6h）命中缓存，每次扫描从秒级降到毫秒级。

缓存目录：`D:/work_ai/quote_cache/`（全系统共享），文件名 `{symbol}_{period}m.parquet`，
新鲜度按文件 mtime vs TTL 判断。注入模式的滚动窗口缓存另存在 `quote_cache/inject/` 子目录，与 TTL 全量缓存隔离。

---

## 注入模式（rolling merge）—— 短周期实盘扫描专用

TTL 全量缓存适合「拉一次、长时间复用」（回测深历史、日级扫描）。但短周期
（5/15/30m）实盘扫描盘中要反复跑，用 TTL 模式每次过期就要整盘重拉，浪费；
且实盘信号只需要最近的滚动窗口，不需要深历史。

注入模式解决这个：**读一部分新数据注入缓存、删最老的，窗口恒定**。

```python
from future_data import inject_klines, inject_many

# 单品种：首次全量拉 300 根建仓；之后每次只增量拉几根新数据合并、丢最老的
df = inject_klines("RB0", "shfe", period="5", length=300)

# 批量（单连接）：盘中每隔几分钟跑一次，TOP40 全部滚动更新
out = inject_many([("RB0","螺纹","shfe"), ...], period="5", length=300)
```

**机制**：
- 首次（缓存文件不存在）：全量拉 `length` 根（默认 300）建仓。
- 之后每次：读缓存 → 只增量拉 `inject_size` 根新数据 → `concat`+按 `datetime` 去重
  （保留最新值，处理未收盘的当前K线）→ 升序排序 → `tail(length)`（删最老的）→ 写回。
- `inject_size` 按周期自动取约 1.5 小时量：5m→18 / 15m→6 / 30m→3 / 60m→2 根。
- 缓存文件落在 `quote_cache/inject/{symbol}_{period}m.parquet`，与 TTL 全量缓存
  （根目录，回测用）写不同文件，**互不覆盖**。

**命令行**：
```bash
python -m future_data inject --period 15              # 全品种滚动注入（默认 300 根窗口）
python -m future_data inject --period 5 --length 300  # 5分钟、300根窗口
```

**与 TTL 模式各司其职**：

| 模式 | 入口 | 缓存位置 | 窗口 | 用途 |
|---|---|---|---|---|
| TTL 全量 | `get_klines` / `refresh` | `quote_cache/` | data_length（深历史，8000） | 回测、日级扫描 |
| 注入滚动 | `inject_klines` / `inject` | `quote_cache/inject/` | length（300） | 短周期实盘扫描 |

> **依赖提示**：缓存写盘需要 `pyarrow`。已在 future_1/future_2 的 venv 装好；
> 若其他环境缺，`uv pip install pyarrow`（或对应包管理器）补一下即可。

---

## 符号解析（支持两种风格）

```python
from future_data import resolve_symbol

resolve_symbol("RB0", "shfe")     # -> ('KQ.m@SHFE.rb', 'continuous')   主力连续
resolve_symbol("RB2610", "shfe")  # -> ('SHFE.rb2610', 'specific')       具体合约
resolve_symbol("IF0", "cffex")    # -> ('KQ.m@CFFEX.IF', 'continuous')
resolve_symbol("TA609", "czce")   # -> ('CZCE.TA609', 'specific')
```

规则：cffex/czce 保持大写，shfe/dce/gfex/ine 小写；主力占位符去掉结尾单个 0。

---

## 迁移指南

详见 [`examples/README.md`](examples/README.md)。要点：

- **future_1**：零改动（同源）；想统一入口就把 `engine.py` 的 import 换成 `from future_data import TqSdkProvider`。
- **future_2**：`scanner.py` 的每品种 `TqApi` 重连循环换成一次 `fetch_many`（见 `examples/migration_future_2.py`），之后可删 `wyckoff_quant/data/tqsdk_provider.py`。
- **future_3**：`data_loader.py` 的 akshare 调用换成 `get_klines`，TTL 逻辑委托给统一入口（见 `examples/migration_future_3.py`）。
- **顶层 5 个 akshare 分钟脚本**：`futures_zh_minute_sina(...)` 换成 `get_klines(...)`（见 `examples/migration_toplevel.py`）。
  **必修**：`futures_hourly_analysis.py` L309/L380 的 `df['datetime'].str[:10]` 改成 `.dt.strftime('%Y-%m-%d')`。

**注意**：tqsdk 单次可取约 8964 根（akshare 仅 ~320），future_3 / backtest_albrooks 的回测深度会更 fuller，**结果数值会变**（属正向收益）。

---

## 文件结构

```
future_data/
├── __init__.py        公开 API
├── symbols.py         符号解析（主力/具体合约 → tqsdk instrument_id）
├── provider.py        tqsdk provider：fetch_kline / fetch_many（单连接批量）/ normalize
├── cache.py           TTL 缓存 + 注入模式：get_klines / inject_many / clear_cache / cache_status
├── universe.py        品种表读取（futures_data.db → futures_top40）
├── __main__.py        CLI：refresh / inject / status / clear / bench / explain
├── bench_cache.py     性能基准
└── examples/
    ├── README.md              各系统迁移要点
    ├── migration_future_2.py  future_2 改造参考
    ├── migration_future_3.py  future_3 改造参考
    └── migration_toplevel.py  顶层脚本改造参考（含 datetime bug 修复）
```

## 依赖

`tqsdk`、`pandas`、`pyarrow`（parquet）。各系统 venv 里已有。
