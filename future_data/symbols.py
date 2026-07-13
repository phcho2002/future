"""符号解析器：把 DB / 脚本里的输入符号映射成迅投(xtquant)合约代码。

输入风格（全系统归纳后的两类）：
    1. 主力连续占位符：  "RB0" / "AU0" / "IM0"
       —— DB futures_top40 的 symbol 列、各顶层脚本用的就是这种。
       映射为迅投主力连续：  rb00.SF  /  au00.SF  /  IF00.IF
    2. 具体月份合约：    "RB2610" / "TA2609" / "IF2412"
       —— futures_hourly_analysis / run_futures_30m / backtest_albrooks 用。
       映射为精确合约：  rb2610.SF / TA609.ZF / IF2412.IF

规则（与 tqsdk 大小写规则一致）：
    - cffex / czce 的 base 保持大写（IF, TA, MA）
    - shfe / dce / gfex / ine 的 base 小写（rb, m, au）

迅投交易所后缀：
    SHFE → SF   上期所
    DCE  → DF   大商所
    CZCE → ZF   郑商所
    CFFEX→ IF   中金所
    GFEX → GF   广期所
    INE  → INE  能源中心
"""

from __future__ import annotations

import re

# DB exchange code → 迅投交易所后缀
EXCHANGE_MAP = {
    "cffex": "IF",
    "shfe": "SF",
    "dce": "DF",
    "czce": "ZF",
    "zce": "ZF",        # futures_top40.json 中 MA0 的笔误 ZCE → 同样映射
    "gfex": "GF",
    "ine": "INE",
}

# 这些交易所的迅投合约代码保持大写；其余小写
UPPERCASE_EXCHANGES = {"cffex", "czce", "zce"}

# 具体月份合约的判定：base（去掉主力 0 后）后面跟着 3~4 位数字（YYMM）。
# 例：RB2610 → base=RB + 2610；TA609 → base=TA + 609（郑商所是 3 位）。
_SPEC_RE = re.compile(r"^(?P<base>[A-Za-z]+)(?P<month>\d{3,4})$")


def _is_continuous_placeholder(symbol: str) -> bool:
    """是否为主力占位符（结尾单个 0，且去掉后是纯字母）。"""
    if not symbol.endswith("0"):
        return False
    base = symbol[:-1]
    return bool(re.fullmatch(r"[A-Za-z]+", base))


def _exchange_suffix(exchange: str) -> str:
    """交易所 → 迅投后缀。未知交易所回退为大写原值。"""
    ex = exchange.lower()
    return EXCHANGE_MAP.get(ex, exchange.upper())


def build_xt_symbol(symbol: str, exchange: str, continuous: bool = True) -> str:
    """把 (symbol, exchange) 映射成迅投合约号。

    Parameters
    ----------
    continuous : True 时主力占位符去结尾单个 0，加 00 后缀。

    Examples
    --------
    >>> build_xt_symbol("RB0", "shfe")
    'rb00.SF'
    >>> build_xt_symbol("RB0", "shfe", continuous=False)
    'rb.SF'
    >>> build_xt_symbol("IF0", "cffex")
    'IF00.IF'
    """
    suffix = _exchange_suffix(exchange)
    upper = exchange.lower() in UPPERCASE_EXCHANGES

    if continuous:
        base = symbol[:-1] if symbol.endswith("0") else symbol
    else:
        base = symbol
    base = base if upper else base.lower()
    return f"{base}00.{suffix}" if continuous else f"{base}.{suffix}"


def resolve_xt_symbol(symbol: str, exchange: str) -> tuple[str, str]:
    """解析输入符号，返回 (迅投合约号, kind)。

    kind ∈ {"continuous", "specific"}，便于上层日志/重试策略区分。

    主力连续格式：品种00.后缀（如 rb00.SF）
    具体月份格式：品种YYMM.后缀（如 rb2610.SF）

    Examples
    --------
    >>> resolve_xt_symbol("RB0", "shfe")
    ('rb00.SF', 'continuous')
    >>> resolve_xt_symbol("RB2610", "shfe")
    ('rb2610.SF', 'specific')
    >>> resolve_xt_symbol("IF0", "cffex")
    ('IF00.IF', 'continuous')
    >>> resolve_xt_symbol("TA609", "czce")
    ('TA609.ZF', 'specific')
    """
    suffix = _exchange_suffix(exchange)
    upper = exchange.lower() in UPPERCASE_EXCHANGES

    sym_clean = symbol.strip()

    # 先判具体合约：字母 base + 3~4 位月份
    m = _SPEC_RE.match(sym_clean)
    # 主力占位符（结尾单 0）优先走连续分支
    if _is_continuous_placeholder(sym_clean):
        base = sym_clean[:-1]
        base = base if upper else base.lower()
        return f"{base}00.{suffix}", "continuous"

    if m:  # 具体月份合约
        base = m.group("base")
        month = m.group("month")
        base = base if upper else base.lower()
        return f"{base}{month}.{suffix}", "specific"

    # 兜底：既不是主力占位符也看不出月份 —— 当成主力连续（不剥 0）
    base = sym_clean if upper else sym_clean.lower()
    return f"{base}00.{suffix}", "continuous"


def explain(symbol: str, exchange: str) -> str:
    """人话解释解析结果，调试用。"""
    inst, kind = resolve_xt_symbol(symbol, exchange)
    if kind == "continuous":
        return f"{symbol}({exchange}) -> 主力连续 {inst}"
    return f"{symbol}({exchange}) -> 具体合约 {inst}"


# ============================================================
# 向后兼容别名
# ============================================================
# 旧代码可能引用 build_tq_symbol / resolve_symbol，
# 保留函数名但内部映射到迅投符号。

def build_tq_symbol(symbol: str, exchange: str, continuous: bool = True) -> str:
    """[已废弃] 旧 tqsdk 符号映射的兼容别名，现映射到迅投符号。"""
    return build_xt_symbol(symbol, exchange, continuous=continuous)


def resolve_symbol(symbol: str, exchange: str) -> tuple[str, str]:
    """[已废弃] 旧 tqsdk 符号解析的兼容别名，现映射到迅投符号。"""
    return resolve_xt_symbol(symbol, exchange)
