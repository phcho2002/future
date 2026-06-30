"""符号解析器：把 DB / 脚本里的输入符号映射成 tqsdk instrument_id。

输入风格（全系统归纳后的两类）：
    1. 主力连续占位符：  "RB0" / "AU0" / "IM0"
       —— DB futures_top40 的 symbol 列、各顶层 akshare 脚本用的就是这种。
       映射为 tqsdk 主力连续：  KQ.m@SHFE.rb  /  KQ.m@SHFE.au  /  KQ.m@CFFEX.IM
    2. 具体月份合约：    "RB2610" / "TA2609" / "IF2412"
       —— futures_hourly_analysis / run_futures_30m / backtest_albrooks 用。
       映射为精确合约：  SHFE.rb2610 / CZCE.TA609 / CFFEX.IF2412

规则（沿用 future_1 build_tq_symbol 的安全契约）：
    - cffex / czce 的 base 保持大写（CFFEX.IF, CZCE.CF）
    - shfe / dce / gfex / ine 的 base 小写（SHFE.rb, DCE.m）
    - 主力占位符去掉结尾的单个 "0"（用 [:-1] 而非 rstrip，避免误伤合法 0）
"""

from __future__ import annotations

import re

# DB exchange code -> tqsdk exchange prefix
EXCHANGE_MAP = {
    "cffex": "CFFEX",
    "shfe": "SHFE",
    "dce": "DCE",
    "czce": "CZCE",
    "gfex": "GFEX",
    "ine": "INE",
}

# 这些交易所的 tqsdk 合约代码保持大写；其余小写
UPPERCASE_EXCHANGES = {"cffex", "czce"}

# 具体月份合约的判定：base（去掉主力 0 后）后面跟着 4 位数字（YYMM）。
# 例：RB2610 → base=RB + 2610；TA609 → base=TA + 609（郑商所是 3 位）。
_SPEC_RE = re.compile(r"^(?P<base>[A-Za-z]+)(?P<month>\d{3,4})$")


def _is_continuous_placeholder(symbol: str) -> bool:
    """是否为新浪主力占位符（结尾单个 0，且去掉后是纯字母）。"""
    if not symbol.endswith("0"):
        return False
    base = symbol[:-1]
    return bool(re.fullmatch(r"[A-Za-z]+", base))


def _exchange_prefix(exchange: str) -> str:
    ex = exchange.lower()
    return EXCHANGE_MAP.get(ex, exchange.upper())


def build_tq_symbol(symbol: str, exchange: str, continuous: bool = True) -> str:
    """把 (symbol, exchange) 映射成 tqsdk 合约号。

    与原 future_1 build_tq_symbol 行为一致：
        - continuous=True（默认）：主力占位符去结尾单个 0，并加 KQ.m@ 前缀。
        - continuous=False：不加前缀，原样（适合具体合约）。

    主力/具体合约的智能区分请用 :func:`resolve_symbol`（它会判断月份后缀）。
    本函数保留是为了与原 future_1/future_2 的调用兼容。

    Examples
    --------
    >>> build_tq_symbol("RB0", "shfe")
    'KQ.m@SHFE.rb'
    >>> build_tq_symbol("RB0", "shfe", continuous=False)
    'SHFE.rb'
    >>> build_tq_symbol("IF0", "cffex")
    'KQ.m@CFFEX.IF'
    """
    ex = _exchange_prefix(exchange)
    if continuous:
        # 主力占位符去掉结尾单个 0
        base = symbol[:-1] if symbol.endswith("0") else symbol
    else:
        base = symbol
    base = base if exchange.lower() in UPPERCASE_EXCHANGES else base.lower()
    sym = f"{ex}.{base}"
    return f"KQ.m@{sym}" if continuous else sym


def resolve_symbol(symbol: str, exchange: str) -> tuple[str, str]:
    """解析输入符号，返回 (tqsdk_instrument_id, kind)。

    kind ∈ {"continuous", "specific"}，便于上层日志/重试策略区分。

    Examples
    --------
    >>> resolve_symbol("RB0", "shfe")
    ('KQ.m@SHFE.rb', 'continuous')
    >>> resolve_symbol("RB2610", "shfe")
    ('SHFE.rb2610', 'specific')
    >>> resolve_symbol("IF0", "cffex")
    ('KQ.m@CFFEX.IF', 'continuous')
    >>> resolve_symbol("TA609", "czce")
    ('CZCE.TA609', 'specific')
    """
    ex = _exchange_prefix(exchange)
    upper = exchange.lower() in UPPERCASE_EXCHANGES

    sym_clean = symbol.strip()

    # 先判具体合约：字母 base + 3~4 位月份
    m = _SPEC_RE.match(sym_clean)
    # 但 "RB0" 这类主力占位符也会被当成 base=R + 月份=B0？不会，\d{3,4} 不匹配单字符。
    # 真正的主力占位符（结尾单 0）优先走连续分支。
    if _is_continuous_placeholder(sym_clean):
        base = sym_clean[:-1]
        base = base if upper else base.lower()
        return f"KQ.m@{ex}.{base}", "continuous"

    if m:  # 具体月份合约
        base = m.group("base")
        month = m.group("month")
        base = base if upper else base.lower()
        # 郑商所月份是 3 位（如 TA609），其余 4 位；保持原样
        return f"{ex}.{base}{month}", "specific"

    # 兜底：既不是主力占位符也看不出月份 —— 当成主力连续（不剥 0），
    # 这样 "RB"（无 0）也能用。
    base = sym_clean if upper else sym_clean.lower()
    return f"KQ.m@{ex}.{base}", "continuous"


def explain(symbol: str, exchange: str) -> str:
    """人话解释解析结果，调试用。"""
    inst, kind = resolve_symbol(symbol, exchange)
    if kind == "continuous":
        return f"{symbol}({exchange}) -> 主力连续 {inst}"
    return f"{symbol}({exchange}) -> 具体合约 {inst}"
