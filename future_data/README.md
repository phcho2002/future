# future_data — 统一期货行情数据入口

全系统统一的期货 K 线数据入口：**默认 xtquant（迅投 token 模式）+ TTL parquet 缓存**。  
备份数据源 akshare（新浪）：`set FUTURE_DATA_BACKEND=akshare`。

## 数据源

| 后端 | 说明 | 切换方式 |
|---|---|---|
| **xtquant**（默认） | 迅投行情 token 模式，直连服务器，无需 MiniQMT 客户端 | `FUTURE_DATA_BACKEND=xtquant`（默认） |
| akshare（备份） | 新浪期货 K 线，无需账号 | `FUTURE_DATA_BACKEND=akshare` |

### xtquant token 模式配置

1. **安装**：`pip install xtquant`
2. **token**：在 `D:/work_ai/xt_token.py` 写入 `XT_TOKEN = "your_token"`
   - 或设环境变量 `XT_TOKEN`
3. **VIP 地址池**：`xtquant_provider.py` 内置 VIP 服务器列表，通过
   `xtdatacenter.set_allow_optmize_address()` 配置连接池

```python
# 初始化（进程级一次，~10s 加载合约表）
from xtquant import xtdatacenter as xtdc
xtdc.set_token(token)
xtdc.set_allow_optmize_address(addr_list)  # VIP 地址池
xtdc.init()
```

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

### 2. 批量拉取（单次连接 —— 核心优化）

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

## 缓存提速

**实测基准**（8 品种 × 15min × 200 bars）：

| 路径 | 耗时 | 说明 |
|---|---|---|
| akshare 逐品种 | ~3s | 新浪 HTTP，含 0.3s sleep/品种 |
| xtquant 批量（单连接）| ~1s | token 模式直连 |
| **缓存命中** | **0.05s** | 纯 parquet 读盘 |

缓存的价值不在省首次拉取——而在**避免每次扫描都重复拉取**。量化系统每天/每小时
反复扫 top40，首次预热一次（几秒），之后全系统在 TTL 内命中缓存，每次扫描从秒级降到毫秒级。

缓存目录：`D:/work_ai/quote_cache/`（全系统共享），文件名 `{symbol}_{period}m.parquet`。
注入模式的滚动窗口缓存另存在 `quote_cache/inject/` 子目录，与 TTL 全量缓存隔离。

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

| 模式 | 入口 | 缓存位置 | 窗口 | 用途 |
|---|---|---|---|---|
| TTL 全量 | `get_klines` / `refresh` | `quote_cache/` | data_length（深历史，8000） | 回测、日级扫描 |
| 注入滚动 | `inject_klines` / `inject` | `quote_cache/inject/` | length（300） | 短周期实盘扫描 |

---

## 符号解析（迅投 xtquant 代码）

```python
from future_data import resolve_xt_symbol

resolve_xt_symbol("RB0", "shfe")     # -> ('rb00.SF', 'continuous')   主力连续
resolve_xt_symbol("RB2610", "shfe")  # -> ('rb2610.SF', 'specific')   具体合约
resolve_xt_symbol("IF0", "cffex")    # -> ('IF00.IF', 'continuous')
resolve_xt_symbol("TA609", "czce")   # -> ('TA609.ZF', 'specific')
```

迅投交易所后缀：

| 交易所 | 标准代码 | 迅投后缀 |
|---|---|---|
| 上期所 | SHFE | SF |
| 大商所 | DCE | DF |
| 郑商所 | CZCE | ZF |
| 中金所 | CFFEX | IF |
| 能源中心 | INE | INE |
| 广期所 | GFEX | GF |

规则：cffex/czce 品种大写（如 `IF00.IF`、`MA00.ZF`），其余交易所小写（如 `rb00.SF`）。

---

## 文件结构

```
future_data/
├── __init__.py             公开 API
├── symbols.py              符号解析（主力/具体合约 → 迅投 xtquant 代码）
├── provider.py             后端分发层：fetch_kline / fetch_many（xtquant / akshare）
├── xtquant_provider.py     xtquant 后端（token 模式 + VIP 地址池）
├── akshare_provider.py     akshare 后端（备份）
├── cache.py                TTL 缓存 + 注入模式：get_klines / inject_many / clear_cache
├── universe.py             品种表读取（futures_data.db → futures_top40）
├── __main__.py             CLI：refresh / inject / status / clear / bench / explain
├── bench_cache.py          性能基准
└── examples/
    └── ...
```

## 依赖

`xtquant`、`pandas`、`pyarrow`（parquet）。安装：`pip install xtquant pandas pyarrow`。

## 自检

```bash
python test_xtquant.py    # xtquant 通路自检（初始化→单品种→批量→缓存层）
```
