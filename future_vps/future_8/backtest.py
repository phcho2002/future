"""future_8 回测入口（薄壳）。

用法：
    python backtest.py                 # 回测全 TOP40（15m）
    python backtest.py --limit 5       # 只测前5个品种（快速冒烟）
    python backtest.py --period 60     # 60分钟
"""
from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_WORK_AI = _HERE.parent
for _p in (str(_WORK_AI), str(_HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from backtest.runner import main  # noqa: E402

if __name__ == "__main__":
    main()
