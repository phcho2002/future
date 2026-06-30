"""
bigquant_provider.py
====================
BigQuant (DAI) 数据后端 —— future_6 的临时数据通道。

背景
====
原系统经 future_data 走 tqsdk 拉取 60 分钟 K 线。本次「临时换用 bigquant 账号
bq5wec8s」期间，改用 BigQuant SDK 的 DAI 接口取数，**策略层零改动**（renko /
signals / indicators 只消费 [datetime, open, high, low, close, volume]）。

BigQuant DAI 没有期货 60 分钟表（仅 bar1m / bar1d），因此本 provider：
    1. 从 cn_future_bar1m 拉主力连续合约（如 rb0000.SHF）的 1 分钟行情；
    2. 按自然小时重采样为 60 分钟（open=first / high=max / low=min / close=last / volume=sum）；
    3. 规范化列契约，与 tqsdk provider 输出完全一致，下游无需感知差异。

合约映射
========
DB futures_top40 的 (symbol, exchange) —— 例如 ("RB0","shfe") —— 映射成 BigQuant
主力连续 instrument：
    - 交易所后缀取 3 字母：shfe→SHF, dce→DCE, czce→CZC, cffex→CFE, gfex→GFE, ine→INE
    - 大小写规则与 future_data.symbols 一致：cffex / czce 品种代码大写（IF0000.CFE），
      其余小写（rb0000.SHF）。已实测 40/40 品种全部解析。

鉴权
====
    bq auth --apikey AK.SK        # 一次性，写入 ~/.bigquant/config.json
    bigquant.init_from_config()   # 运行时自动读取，无需在代码里硬编码凭证
"""
from __future__ import annotations

import time
from pathlib import Path

import pandas as pd

# ----------------------------------------------------------------- 映射表
# DB exchange code -> BigQuant 3字母后缀（与 future_data.symbols.EXCHANGE_MAP 不同：
# tqsdk 用全称 SHFE/CFFEX，DAI 用 3 字母 SHF/CFE）
BQ_EXCHANGE_SUFFIX = {
    "cffex": "CFE",
    "shfe": "SHF",
    "dce": "DCE",
    "czce": "CZC",
    "gfex": "GFE",
    "ine": "INE",
}

# 与 future_data.symbols.UPPERCASE_EXCHANGES 一致：这两个交易所的品种代码保持大写
UPPERCASE_EXCHANGES = {"cffex", "czce"}

# BigQuant DAI 期货主力连续 instrument 的月份占位符
_MAIN_CONTINUOUS_MONTH = "0000"

# 与 tqsdk provider 一致的列契约
OHLC_COLUMNS = ("datetime", "open", "high", "low", "close", "volume")

# SDK 只初始化一次（避免每品种重复建 Flight 连接）
_SDK_READY = False


def _ensure_sdk() -> None:
    """惰性初始化 BigQuant SDK（读取 ~/.bigquant/config.json）。"""
    global _SDK_READY
    if _SDK_READY:
        return
    import bigquant  # noqa: WPS433  惰性导入，避免无 bigquant 时整个模块炸掉

    cfg = Path.home() / ".bigquant" / "config.json"
    if not cfg.exists():
        raise FileNotFoundError(
            "未找到 BigQuant 凭证 ~/.bigquant/config.json。请先执行："
            " bq auth --apikey AK.SK"
        )
    bigquant.init_from_config()
    _SDK_READY = True


def resolve_bq_instrument(symbol: str, exchange: str) -> str:
    """把 DB 符号 (RB0, shfe) 映射成 BigQuant 主力连续 instrument (rb0000.SHF)。

    规则：
      - 去掉结尾的单个 "0"（主力占位符），得到品种 base；
      - cffex / czce 的 base 保持大写（IF, TA, CF...），其余小写（rb, au, lc...）；
      - 拼上 0000 主力占位 + 3 字母交易所后缀。

    Examples
    --------
    >>> resolve_bq_instrument("RB0", "shfe")
    'rb0000.SHF'
    >>> resolve_bq_instrument("IF0", "cffex")
    'IF0000.CFE'
    >>> resolve_bq_instrument("TA0", "czce")
    'TA0000.CZC'
    >>> resolve_bq_instrument("SC0", "ine")
    'sc0000.INE'
    """
    ex = exchange.lower()
    suffix = BQ_EXCHANGE_SUFFIX.get(ex)
    if suffix is None:
        raise ValueError(f"未知交易所: {exchange}（支持: {list(BQ_EXCHANGE_SUFFIX)}）")

    base = symbol[:-1] if symbol.endswith("0") else symbol
    base = base if ex in UPPERCASE_EXCHANGES else base.lower()
    return f"{base}{_MAIN_CONTINUOUS_MONTH}.{suffix}"


def _resample_to_60m(df_1m: pd.DataFrame) -> pd.DataFrame:
    """1 分钟 OHLCV -> 60 分钟。

    按自然小时聚合（label/closed=left，与期货整点切分一致），丢弃成交量恒为
    0 的空小时桶（dropna on open），保证最后一根是已收盘的真实小时线。
    """
    df = df_1m.set_index("datetime").sort_index()
    bars = df.resample("60min", label="left", closed="left").agg(
        {
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
        }
    )
    # 没有任何 1 分钟线的空桶直接丢掉（含成交量0的纯空小时）
    bars = bars.dropna(subset=["open"])
    bars = bars.reset_index().rename(columns={"index": "datetime"})
    return bars[list(OHLC_COLUMNS)]


def fetch_kline_60m(
    symbol: str,
    exchange: str,
    length: int = 2000,
    fetch_1m_bars: int = 130_000,
) -> pd.DataFrame:
    """从 BigQuant DAI 拉主力连续 1 分钟行情并重采样为 60 分钟 K 线。

    Parameters
    ----------
    symbol, exchange : DB 符号，如 ("RB0","shfe")。
    length : 期望的 60 分钟根数（仅用于决定拉多少 1 分钟数据，默认拉 ~2000 小时）。
    fetch_1m_bars : 单次拉的 1 分钟根数上限。60m×60 ≈ 60，乘以安全冗余取 130000
        （约 540 个交易日的 1 分钟），足够覆盖 2000 根小时线。

    Returns
    -------
    DataFrame[datetime, open, high, low, close, volume] —— 升序、整数 Index。
    """
    _ensure_sdk()
    import bigquant  # noqa: WPS433

    instrument = resolve_bq_instrument(symbol, exchange)
    n_1m = max(int(length) * 70, fetch_1m_bars)  # 70 根1m/小时（含冗余）

    sql = (
        "SELECT instrument, date AS datetime, open, high, low, close, volume "
        "FROM cn_future_bar1m "
        f"WHERE instrument = '{instrument}' "
        "ORDER BY date DESC "
        f"LIMIT {int(n_1m)}"
    )
    # DAI 按 date 分区；近端数据用 filters 收窄分区，避免全表扫描计费
    res = bigquant.dai.query(sql, filters={"date": ["2015-01-01", "2099-12-31"]})
    df = res.df()

    if df is None or df.empty:
        raise ValueError(f"BigQuant DAI 无数据返回: {instrument} ({symbol}/{exchange})")

    df["datetime"] = pd.to_datetime(df["datetime"])
    for c in ("open", "high", "low", "close", "volume"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=list(OHLC_COLUMNS)[1:]).sort_values("datetime")

    bars = _resample_to_60m(df)
    # 只保留最近 length 根小时线（与 tqsdk data_length 语义一致）
    if length and len(bars) > length:
        bars = bars.tail(length).reset_index(drop=True)
    return bars


# --------------------------------------------------------------------- 缓存
# 复用全系统共享缓存目录 D:/work_ai/quote_cache，文件名 {symbol}_{period}m.parquet
# 与 future_data.get_klines 完全一致 —— 因此 bigquant / tqsdk 两条路径的缓存可互换，
# 切换 backend 后命中缓存的行为完全相同。
DEFAULT_CACHE_DIR = Path("D:/work_ai/quote_cache")


def _cache_path(cache_dir: Path, symbol: str, period: str) -> Path:
    safe = symbol.replace("/", "_")
    return cache_dir / f"{safe}_{period}m.parquet"


def get_klines(
    symbol: str,
    exchange: str,
    period: str = "60",
    length: int = 2000,
    ttl_hours: float = 2.0,
    force: bool = False,
    cache_dir: Path | str | None = None,
) -> pd.DataFrame:
    """BigQuant 后端入口：TTL 缓存优先，过期/缺失/force 时联网刷新。

    与 future_data.get_klines 同名同参，便于在 data_loader 里直接替换。
    """
    cdir = Path(cache_dir) if cache_dir else DEFAULT_CACHE_DIR
    cdir.mkdir(parents=True, exist_ok=True)
    path = _cache_path(cdir, symbol, period)

    if not force and path.exists():
        age_hours = (time.time() - path.stat().st_mtime) / 3600.0
        if age_hours < ttl_hours:
            try:
                cached = pd.read_parquet(path)
                if cached is not None and not cached.empty:
                    return cached
            except Exception:
                pass  # 缓存损坏 → 重新拉

    # period>60 的其它周期目前只支持 60（future_6 默认就是 60）
    if str(period) not in ("60", "60m", "1h"):
        raise ValueError(f"BigQuant 后端当前仅支持 60 分钟周期，收到 period={period}")

    df = fetch_kline_60m(symbol, exchange, length=length)
    try:
        df.to_parquet(path, index=False)
    except Exception:
        pass  # 写盘失败不影响返回
    return df


if __name__ == "__main__":
    # 自测：拉 RB 主力 60 分钟，打印首尾几根
    df = get_klines("RB0", "shfe", period="60", length=500, ttl_hours=0, force=True)
    print(f"RB0 60m bars: {len(df)}")
    print("cols:", list(df.columns))
    print(df.tail(5).to_string(index=False))
