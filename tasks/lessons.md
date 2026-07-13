# Lessons Learned

<!-- After any correction from the user, document the pattern and the rule to prevent recurrence. -->

## stock_new (A股量化)

### Lesson: 通达信 .day 文件 amount 字段是 float32，不是 int

- **What happened:** `read_tdx_day_file` 用 `struct "<IIIIIIIi"` 把第6字段 amount 当 uint32 读，
  实际它是 float32（成交额，元）。int 解释 float 位模式会让全市场成交额都塌缩到 ~1.3e9（巧合），
  导致维度①成交额分位、维度②龙头成交额、维度③缩量全部失真（缩量条件几乎不触发）。
- **Rule going forward:** 解析通达信 `.day` 一律用 `<IIIIIfII`（date/o/h/l/c/amount=float32/vol/pad）。
  新增任何依赖 amount/volume 的指标前，先用已知大盘股（如招商银行 600036）校验：
  amount ≈ close × volume（股），量级应在亿元级而非统一塌缩。

### Lesson: mootdx stocks(market=) 的 market 映射 0=深 1=沪

- **What happened:** 假设 0=沪/1=深 反了，导致按前缀过滤时把沪市 000001(上证指数) 当深市 A 股收进名称表，
  丢掉全部真 A 股名称（只剩 200 条指数）。
- **Rule going forward:** 用 mootdx `stocks()` 取名称时按「market + 代码前缀」双重过滤：
  market=1(沪) 只取 60/688；market=0(深) 只取 000/001/002/003/300/301。
  先用 600036→招商银行、000001→平安银行 校验再投入使用。

### Lesson: 清理临时脚本时 glob 不要误伤同名正式文件

- **What happened:** 用 `_probe_*.py` 通配清理临时探针时，误删了正式文件 `_probe_env.py`（DESIGN 引用的自检脚本）。
- **Rule going forward:** 清理临时文件用更窄的前缀（如 `_tmp_*`）或显式枚举文件名，避免与正式文件同名；
  删除后立刻核对目录清单。
