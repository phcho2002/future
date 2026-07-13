"""
运行威科夫1小时K线扫描，输出信号CSV到 ./future_2。
"""
import csv
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from wyckoff_quant.config import WyckoffConfig
from wyckoff_quant.scanner import scan_all


def main():
    output_dir = Path(__file__).parent.resolve()
    now = datetime.now()

    print(f"[{now:%H:%M:%S}] 威科夫1小时K线扫描启动")
    print(f"品种: TOP40  |  周期: 60分钟  |  输出: {output_dir}")
    print()

    config = WyckoffConfig()
    signals = scan_all(
        period="60",
        config=config,
        data_length=200,
    )

    # 写入 CSV
    csv_path = output_dir / "wyckoff_signals_1h.csv"
    if signals:
        fieldnames = [
            "symbol", "name", "exchange", "side", "entry", "stop",
            "target", "rsi", "phase", "stage", "phase_desc", "reason",
        ]
        with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(signals)
        print(f"\n✅ 信号已写入: {csv_path}  ({len(signals)} 条)")
    else:
        fieldnames = ["symbol", "name", "exchange", "side", "entry", "stop",
                       "target", "rsi", "phase", "stage", "phase_desc", "reason"]
        with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
        print(f"\n✅ 无信号，已写入空CSV: {csv_path}")

    ts = now.strftime("%Y%m%d_%H%M")
    bak_path = output_dir / f"wyckoff_signals_1h_{ts}.csv"
    csv_path.replace(bak_path)
    print(f"   备份: {bak_path.name}")


if __name__ == "__main__":
    main()
