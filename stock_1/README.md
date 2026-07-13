# stock-chan-selector

基于 AkShare 日线行情的 A 股量化选股系统，严格实现以下规则：

- `SMA_Fast`: 8 日简单移动平均线
- `SMA_Slow`: 21 日简单移动平均线
- `VOL_MA20`: 20 日均量
- 倍量：当日成交量 >= 前一日成交量 2 倍，或当日成交量 >= 20 日均量 1.5 倍
- 前 60 个交易日内均线至少发生 2 次交叉且交替，交叉期间收盘价始终在 `SMA_Slow` 上下 5% 范围内
- 最近交叉必须是金叉，金叉日收盘价站上 `SMA_Slow`，并突破金叉前 5 日最高价
- 倍量必须发生在突破当日、前一日或后一日；后一日补量时，必须收阳且收盘价继续高于金叉日

> 仅用于研究和筛选，不构成投资建议。

## 安装

```bash
cd /home/phcho99/stock
pip install -r requirements.txt
```

## 使用

扫描指定股票：

```bash
python -m stock_selector.cli --symbols 000001,600519 --start-date 20230101 --end-date 20260612
```

扫描文件股票池：

```bash
python -m stock_selector.cli --symbol-file symbols.txt --start-date 20230101
```

扫描全市场：

```bash
python -m stock_selector.cli --all --start-date 20240101
```

## 输出

默认输出到 `/home/phcho99/stock/output`：

- `analysis.csv`: 逐段分析结果，包含日期、交叉次数、金叉日收盘价、倍量数值
- `signals.csv`: 最终信号表，包含信号类型、日期、触发价格、置信度、理由
- `errors.csv`: 单只股票数据拉取或解析失败原因
- `report.md`: 中文报告；无信号时输出“当前数据无信号”

可用 `--output-dir` 指定其他目录。
