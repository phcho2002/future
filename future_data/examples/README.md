# 各系统接入统一入口的示例

本目录给出 **改造后应长什么样** 的最小示例。原始文件保持不动，迁移时参考这里的写法。

## 通用：在任何系统顶部加这一行引用入口

future_2 / future_3 / 顶层脚本（都没有指向 future_data 的包路径），需先 sys.path：

```python
import sys
sys.path.insert(0, "D:/work_ai")          # 让 future_data 可被 import
from future_data import get_klines, fetch_many
```

future_1 本身就在 D:/work_ai 下，无需 sys.path。

---

## future_1（零改动，自动受益）
future_1 本来就用 `future_quant.data.tqsdk_provider.TqSdkProvider`，与统一入口同源。
若想统一走新入口，把 `engine.py` 第 6 行的 import 换成：

```python
# 原：from future_quant.data.tqsdk_provider import TqSdkProvider
from future_data import TqSdkProvider   # 接口完全一致，零下游改动
```

## future_2（最大收益：消除每品种重连）
`scanner.py` 原来每个品种 `TqApi()` 重连（见 scanner.py:65-67）。改为一次批量：

```python
# 原（每品种一个连接）：
#   for sym, name, ex in symbols:
#       api = TqApi(auth=auth)
#       df = fetch_kline(api, build_tq_symbol(sym, ex), period, data_length)
#       api.close()

# 改（单连接批量 + TTL 缓存）：
from future_data import fetch_many
out = fetch_many(symbols, period=period, length=data_length)   # 一次连接拿全部
for sym, name, ex in symbols:
    df = out.get(sym)
    if df is None or df.empty:
        continue
    result = engine.analyze_df(df)
    ...
```
之后可删除 `future_2/wyckoff_quant/data/tqsdk_provider.py`（逻辑已并入统一入口）。

## future_3（data_loader 换后端）
`data_loader.py` 的 `fetch_klines_ak` 换成统一入口：

```python
# 原：df = ak.futures_zh_minute_sina(symbol=symbol, period=period)
from future_data import get_klines
df = get_klines(symbol, exchange, period=period, length=8000)  # tqsdk 可拿深历史
# load_klines 的 TTL 逻辑可整体委托给 get_klines（避免双重缓存）
```
注意：tqsdk 单次可取约 8964 根（akshare 仅 ~320），回测深度会变 fuller。

## 顶层 akshare 分钟脚本（5 处替换 + 1 处 bug 修复）

```python
# futures_30min_signals.py:144 / futures_signal.py:190 / run_futures_30m.py:158
#   / backtest_albrooks.py:171 / futures_hourly_analysis.py:448
# 原：df = ak.futures_zh_minute_sina(symbol=contract, period="30")
from future_data import get_klines
df = get_klines(contract, exchange, period="30")   # exchange 从 DB futures_top40 查
```

**必修 bug**：`futures_hourly_analysis.py` 第 309、380 行把 datetime 当字符串切片，
tqsdk 返回的是 Timestamp，会报错。改为：

```python
# 原（L309 / L380）：df_h['day'] = df_h['datetime'].str[:10]
df_h['day'] = df_h['datetime'].dt.strftime('%Y-%m-%d')   # Timestamp 安全
```

## 缓存预热（盘前跑一次，之后扫描全部命中缓存）

```bash
python -m future_data refresh --period 15      # 预热 top40 全部品种
python -m future_data status                    # 查看新鲜度
python -m future_data bench                     # 看提速收益
```
