"""akshare_provider.py - 为 future_6 提供 akshare 60分钟 K 线数据源。"""
from __future__ import annotations
import sys, time
import pandas as pd
from pathlib import Path

# 让 sibling 包 future_data（位于 future_vps 根）可被 import
_VPS_ROOT = Path(__file__).resolve().parents[1]  # future_6 -> future_vps
if str(_VPS_ROOT) not in sys.path:
    sys.path.insert(0, str(_VPS_ROOT))

try:
    import akshare as ak
    _HAS_AKSHARE = True
except Exception:
    _HAS_AKSHARE = False

# 合约代码映射：从 futures_top40 的 symbol 到具体合约
SPECIFIC_MAP = {}

def _load_specific_map():
    """加载每个 exchange 的当前主力合约列表。"""
    if not _HAS_AKSHARE:
        return
    global SPECIFIC_MAP
    SPECIFIC_MAP = {}
    for ex in ['czce', 'dce', 'shfe', 'cffex', 'gfex', 'ine']:
        try:
            result = ak.match_main_contract(symbol=ex)
            parts = [p.strip() for p in result.split(',') if p.strip() and '无主力合约' not in p]
            SPECIFIC_MAP[ex] = parts
            time.sleep(0.6)
        except Exception:
            SPECIFIC_MAP[ex] = []

_MANUAL_FIX = {
    'M0': 'M2609', 'P0': 'P2609', 'C0': 'C2607', 'SC0': 'SC2607',
    'CU0': 'CU2607', 'AU0': 'AU2607', 'AG0': 'AG2607', 'AL0': 'AL2607',
    'ZN0': 'ZN2607', 'NI0': 'NI2607', 'SN0': 'SN2607', 'RB0': 'RB2607',
    'HC0': 'HC2607', 'I0': 'I2607', 'JM0': 'JM2607', 'J0': 'J2607',
    'V0': 'V2609', 'PP0': 'PP2609', 'L0': 'L2609', 'TA0': 'TA2609',
    'MA0': 'MA2609', 'CF0': 'CF2609', 'SR0': 'SR2609', 'RM0': 'RM2609',
    'OI0': 'OI2609', 'FG0': 'FG2609', 'SA0': 'SA2609', 'SM0': 'SM2609',
    'EB0': 'EB2607', 'LH0': 'LH2607', 'LC0': 'LC2607', 'AO0': 'AO2607',
    'SP0': 'SP2607', 'RU0': 'RU2607', 'IF0': 'IF2606', 'IC0': 'IC2606',
    'IM0': 'IM2606', 'IH0': 'IH2606', 'TF0': 'TF2609', 'TS0': 'TS2609',
}

def _resolve_contract(symbol: str, exchange: str) -> str | None:
    if symbol in _MANUAL_FIX:
        return _MANUAL_FIX[symbol]
    if not SPECIFIC_MAP:
        _load_specific_map()
    specs = SPECIFIC_MAP.get(exchange, [])
    for plen in [2, 1]:
        prefix = symbol[:plen].upper()
        for s in specs:
            if s.startswith(prefix) and len(s) > plen:
                return s
    # fallback: shfe catches INE
    for s in SPECIFIC_MAP.get('shfe', []):
        for plen in [2, 1]:
            if s.startswith(symbol[:plen].upper()) and len(s) > plen:
                return s
    return None


def _fetch_with_headers(symbol: str, period: str = '60'):
    """直接使用 Sina endpoint + headers 拉取 60-min K线。"""
    import requests, json
    url = "https://stock2.finance.sina.com.cn/futures/api/jsonp.php/=/InnerFuturesNewService.getFewMinLine"
    params = {"symbol": symbol, "type": period}
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Referer": "https://vip.stock.finance.sina.com.cn/",
    }
    r = requests.get(url, params=params, headers=headers, timeout=15)
    r.raise_for_status()
    text = r.text
    start = text.find("=(")
    end = text.rfind(");")
    if start == -1 or end == -1:
        raise ValueError("Sina 返回格式异常")
    data = json.loads(text[start+2:end])
    df = pd.DataFrame(data)
    df.columns = ["datetime", "open", "high", "low", "close", "volume", "hold"]
    for col in ["open", "high", "low", "close", "volume", "hold"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["datetime"] = pd.to_datetime(df["datetime"])
    df = df.sort_values("datetime").reset_index(drop=True)
    return df


def fetch_klines_akshare(
    symbol: str,
    exchange: Optional[str] = None,
    period: str = "60",
    length: int = 2000,
) -> pd.DataFrame:
    """通过 akshare/Sina 拉取 60m K 线，返回与 xtquant 路径一致的 DataFrame。"""
    if not _HAS_AKSHARE:
        raise RuntimeError("akshare 未安装")
    specific = _resolve_contract(symbol, exchange or '')
    if specific is None:
        raise ValueError(f"无法解析 {symbol} 的具体合约")
    try:
        df = _fetch_with_headers(specific, period)
    except Exception as e:
        # fallback to akshare wrapper
        try:
            df = ak.futures_zh_minute_sina(symbol=specific, period=period)
            df.columns = [c.lower() for c in df.columns]
        except Exception:
            raise RuntimeError(f"akshare 获取 {symbol}({specific}) 失败: {e}")
    # keep last length rows
    if len(df) > length:
        df = df.iloc[-length:].reset_index(drop=True)
    # _normalize expects lowercase columns; datetime already converted
    df = df[['datetime','open','high','low','close','volume','hold']]
    return df


if __name__ == "__main__":
    df = fetch_klines_akshare("AU0", "shfe")
    print(df.tail(3))
    print("rows:", len(df))
