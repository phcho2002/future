# future_vps — 期货量化扫描系统（Linux VPS 部署版）

三个独立策略系统 + 一个共享数据层的自包含目录，可在 Linux VPS 上直接运行。

```
future_vps/
├── futures_top40.json     # TOP40 品种表（排名/symbol/name/exchange）
├── tq_auth.py             # TqAuth 凭证（部署时填真实账号）
├── requirements.txt       # 合并依赖
├── future_data/           # 共享数据层（tqsdk 后端 + TTL/注入缓存）
│   ├── paths.py           #   路径中心（相对根 + 环境变量）
│   ├── provider.py        #   tqsdk 拉取（单/批量）
│   ├── cache.py           #   TTL 全量缓存 + 注入滚动缓存
│   ├── universe.py        #   品种表（从 JSON 读，原 SQLite）
│   └── symbols.py         #   合约号解析
├── future_1/              # 三推衰竭系统（5m / 15m）
│   ├── future_quant/      #   策略核心包
│   └── scan_top40_*.py    #   扫描脚本
├── future_4/              # 双均线缠绕放量突破（30m）
└── future_6/              # Renko 砖块系统（60m）
```

## 与原版（Windows 本地）的区别

| 项目 | 原版（Win 本地） | VPS 版（本目录） |
|------|------------------|------------------|
| 品种表 | SQLite `futures_data.db` | `futures_top40.json`（无需 sqlite） |
| 缓存目录 | 硬编码 `D:/work_ai/quote_cache` | `future_vps/quote_cache`（环境变量可覆盖） |
| TqAuth | 硬编码 `D:/work_ai/tq_auth.py` | `future_vps/tq_auth.py`（环境变量可覆盖） |
| 数据后端 | tqsdk + akshare + bigquant | **tqsdk（默认）+ akshare（回退）**，移除 bigquant |
| 路径风格 | 绝对路径 `D:/...` | 相对 `future_vps` 根 + 环境变量 |

## 部署步骤

```bash
# 1. 把整个 future_vps/ 拷到 VPS（如 ~/future_vps）
scp -r future_vps/ user@vps:~/

# 2. 建虚拟环境 + 装依赖
cd ~/future_vps
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 3. 填 TqAuth 凭证（编辑 tq_auth.py，替换 YOUR_USER / YOUR_PASSWORD）
nano tq_auth.py

# 4. 运行扫描
cd future_1 && python scan_top40_15m.py      # 三推衰竭 15m
cd future_4 && python run_scan.py            # 双均线缠绕 30m
cd future_6 && python scanner.py             # Renko 60m
```

> Python 版本要求 ≥ 3.10（future_quant 用了 `X | None` 语法）。

## 环境变量（可选，覆盖默认路径）

| 变量 | 默认 | 用途 |
|------|------|------|
| `QUOTE_CACHE_DIR` | `future_vps/quote_cache` | K 线缓存目录（可指向数据盘） |
| `TQ_AUTH_PATH` | `future_vps/tq_auth.py` | TqAuth 凭证文件位置 |
| `FUTURES_TOP40_JSON` | `future_vps/futures_top40.json` | 品种表 JSON 位置 |

例：把缓存放到挂载盘
```bash
export QUOTE_CACHE_DIR=/mnt/data/quote_cache
```

## 各系统入口

| 系统 | 周期 | 入口脚本 | 策略 |
|------|------|----------|------|
| future_1 | 5m / 15m | `future_1/scan_top40_15m.py`<br>`future_1/scan_top40_5m_batch.py` | 三推衰竭 / 楔形反转 |
| future_4 | 30m | `future_4/run_scan.py` | 双均线缠绕 + 放量突破 |
| future_6 | 60m | `future_6/scanner.py` | Renko 砖块 + RSI + 密集区 |

## 切换数据后端

默认全部走 **tqsdk**。若 tqsdk 限频或账号不可用，可切 akshare（无需账号）：

```yaml
# future_4/config.yaml 或 future_6/config.yaml
data:
  backend: "akshare"    # tqsdk / akshare
```

> future_1 固定走 tqsdk（三推衰竭需要深历史，akshare 单次仅 ~320 根不够）。

## 定时扫描（cron 示例）

```cron
# 每个交易日 09:05 跑 15m 三推扫描
5 9 * * 1-5  cd ~/future_vps/future_1 && ~/future_vps/venv/bin/python scan_top40_15m.py >> ~/logs/scan_15m.log 2>&1

# 每个交易日 10:35 跑 30m 双均线扫描
35 10 * * 1-5  cd ~/future_vps/future_4 && ~/future_vps/venv/bin/python run_scan.py >> ~/logs/scan_30m.log 2>&1
```

## 常见问题

**Q: 报错 `tq_auth.py 未找到`？**
A: 没填 TqAuth 凭证。编辑 `future_vps/tq_auth.py` 把 `YOUR_USER`/`YOUR_PASSWORD` 换成真实账号，或设置环境变量 `TQ_AUTH_PATH`。

**Q: tqsdk 拉不到数据？**
A: 切 akshare（改 config.yaml 的 `backend: akshare`），或检查 tq 账号是否过期。

**Q: 缓存目录越来越大？**
A: `future_vps/quote_cache` 会累积 parquet。定期删旧文件即可，或 `QUOTE_CACHE_DIR` 指向大盘。
