"""60m 假突破→真突破主信号 + 日线强弱过滤。

规则：
  - 主信号：蓄势 → 首次假突破 → 失败确认 → 二次真突破（future_bb 状态机）
  - 日线过滤：做多禁止强弱排名后 10 名；做空禁止前 10 名
  - 输出：可直接喂给 future_risk.SignalInput
"""

from .config import SignalConfig
from .pipeline import ScanResult, run_scan, to_risk_signals

__all__ = [
    "SignalConfig",
    "ScanResult",
    "run_scan",
    "to_risk_signals",
]
