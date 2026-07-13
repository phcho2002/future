# Task Plan

## Current: stock_new P1 — 板块映射 + 维度②主力板块龙头 + 维度③蓄势板块

数据源决策：本地通达信 `tdxzs.cfg`+`tdxhy.cfg`+`sh880xxx.day` 为主(离线完整)，
akshare 作为在线增强(当前网络不可达，做兜底)。所有内容可离线验证。

- [x] 1. 指标库 `indicators/volatility.py`(ATR/布林/区间收敛) + `indicators/strength.py`(RPS/板块强度)
- [x] 2. 数据层 `data/sector.py`(解析 tdxzs.cfg+tdxhy.cfg → 行业→成分股+880xxx指数码) + `data/store.py`(SQLite 缓存)
- [x] 3. 扩展 `data/tdx_local.py`：实现 `sector_map()` + `sector_index()` 读 sh880xxx.day
- [x] 4. 维度② `analysis/sector_leaders.py`：板块强度(涨幅中位×0.3+RPS×0.4+成交额占比变化×0.3) + 龙头评分 + 类型标签
- [x] 5. 维度③ `analysis/accumulation.py`：四条件(波动收敛/均线粘合/缩量/位置量能) + 蓄势分+阶段，板块指数口径(880xxx)
- [x] 6. 串联 `engine.py`/`cli.py`/`report.py`/`config.yaml`(维度②③阈值权重)
- [x] 7. 测试：指标单测(合成数据) + sector 解析(真实本地文件) + accumulation(真实880xxx) + sector_leaders(小池) + 冒烟扩展
- [x] 8. 运行验证：pytest 全绿(16 passed) + 跑 `sectors`/`accumulation` 产出真实报告
- [x] 9. 清理临时探针脚本(_probe_*.py)

## Review

### 完成
P1 全量落地并端到端验证（数据日期 2026-07-02）：
- **数据层**：发现并复用本地 `tdxzs.cfg`(605板块,含880xxx指数码) + `tdxhy.cfg`(5622股→111叶行业)
  → 109 个行业板块(>=3成分股, 5208 成分股人次)，全部 `sh880xxx.day` 板块指数本地可得。
  akshare 在线不可达(ProxyError/ConnectionError) → 本地为主，akshare 做兜底。
- **维度②**：板块强度 Top10 + 每板块 Top3 龙头(名称/类型/连板/封板/成交额)。例：化学制药 海南海药 4连板领涨龙、
  仓储物流 *ST瑞茂 5连板封板1.0(ST 5%幅度的5.34%涨停，ST检测正确)。
- **维度③**：71 个蓄势候选板块(四条件+蓄势分+阶段)。化工原料「临近突破」(价分位0.967+量升)，
  电气设备「试探」(收敛+缩量+价处60日低+量升)，符合偏弱/下行市况。
- **名称**：本地无 code→name 文件，改用 mootdx 在线(TCP，绕过 HTTP 代理) + SQLite 缓存，5205 个 A 股名称。

### 顺带修复的 P0 隐患
1. **`.day` amount 字段是 float32(成交额元) 非 int**：原 `read_tdx_day_file` 用 `<IIIIIIIi` 把 amount 当 int 读，
   导致全市场成交额都塌缩到 ~1.3e9(int 解释 float 位模式的巧合)。改为 `<IIIIIfII` 后，
   招商银行 34.42亿 / 宁德时代 131.4亿 等真实成交额恢复，维度①资金/③缩量/②龙头成交额全部变正确。
2. **`stock_names()` 原用 `_parse_tdx_cfg` 解析 `tdxhy.cfg`**：但 tdxhy.cfg 是 `market|code|T码` 格式不含名称，
   解析结果几乎全空(只剩3个垃圾键)→ ST 检测长期失效。已改为 mootdx 在线+缓存。
3. **mootdx `stocks(market=)` 映射**：0=深,1=沪（与常见猜想相反），按市场+前缀双重过滤避免 000001 沪深碰撞。

### 后续
- 维度②「封板强度」仍为 P0 日级近似(一字/T字=1, 普通涨停=0.6)；P1 回测校准 RPS 周期(20 vs 60)与蓄势阈值。
- P2：维度④ 异动(引入 mootdx 在线分钟) + 维度⑤ 启动标志(整合 stock_3 Wyckoff / stock_1 缠论 / stock N字)。
- 可选：把 akshare 板块映射接回作为本地 tdxzs 的在线校验/补充（待网络恢复）。

