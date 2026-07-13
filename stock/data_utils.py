"""
N型主升浪交易系统 — 数据获取 & 技术指标计算
================================================
封装 akshare 数据获取 + 所有技术指标计算。
"""

import os
import time
import pandas as pd
import numpy as np
from typing import Optional, List, Tuple
from pathlib import Path

from config import Config


# ==============================================================================
# 数据获取
# ==============================================================================

def fetch_stock_hist(
    symbol: str,
    period: str = 'weekly',
    start_date: str = '20180101',
    end_date: str = '20261231',
    adjust: str = 'qfq',
    cache_dir: str = './stock/cache'
) -> pd.DataFrame:
    """
    获取A股历史K线数据（带本地缓存）

    参数
    ----
    symbol : 股票代码，如 '000001', '600519'
    period : K线周期，'daily'/'weekly'/'monthly'
    start_date : 起始日期 YYYYMMDD
    end_date : 截止日期 YYYYMMDD
    adjust : 复权方式 'qfq'前复权 / 'hfq'后复权 / ''不复权
    cache_dir : CSV缓存目录

    返回
    ----
    DataFrame 列: date, open, high, low, close, volume, amount, amplitude, pct_change, turnover
    """
    # --- 缓存检查 ---
    cache_path = Path(cache_dir) / f'{symbol}_{period}_{adjust}.csv'
    cache_path.parent.mkdir(parents=True, exist_ok=True)

    # 读取缓存
    df_cached = None
    if cache_path.exists():
        try:
            df_cached = pd.read_csv(cache_path, parse_dates=['date'], index_col=0)
            # 检查缓存是否包含请求的日期范围
            if not df_cached.empty:
                cache_start = df_cached.index.min().strftime('%Y%m%d')
                cache_end = df_cached.index.max().strftime('%Y%m%d')
                if cache_start <= start_date and cache_end >= end_date:
                    return df_cached.loc[start_date:end_date].copy()
        except Exception:
            pass

    # --- 网络获取 ---
    try:
        import akshare as ak

        # 清理代码格式（去掉可能的前缀）
        clean_symbol = symbol.replace('sh', '').replace('sz', '').replace('.', '')

        df = ak.stock_zh_a_hist(
            symbol=clean_symbol,
            period=period,
            start_date=start_date,
            end_date=end_date,
            adjust=adjust
        )

        if df is None or df.empty:
            raise ValueError(f'股票 {symbol} 返回空数据')

    except ImportError:
        raise ImportError('请安装 akshare: pip install akshare')

    # --- 标准化列名 ---
    col_map = {
        '日期': 'date',
        '开盘': 'open',
        '收盘': 'close',
        '最高': 'high',
        '最低': 'low',
        '成交量': 'volume',
        '成交额': 'amount',
        '振幅': 'amplitude',
        '涨跌幅': 'pct_change',
        '涨跌额': 'change',
        '换手率': 'turnover',
    }
    df.rename(columns=col_map, inplace=True)

    # 设置日期索引
    if 'date' in df.columns:
        df['date'] = pd.to_datetime(df['date'])
        df.set_index('date', inplace=True)

    # 确保数据类型正确
    for col in ['open', 'high', 'low', 'close', 'volume']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')

    df.dropna(subset=['open', 'high', 'low', 'close'], inplace=True)
    df.sort_index(inplace=True)

    # --- 合并缓存 ---
    if df_cached is not None and not df_cached.empty:
        df_combined = pd.concat([df_cached, df])
        df_combined = df_combined[~df_combined.index.duplicated(keep='last')]
        df_combined.sort_index(inplace=True)
        df = df_combined

    try:
        df.to_csv(cache_path)
    except Exception:
        pass

    return df


def get_stock_list() -> pd.DataFrame:
    """
    获取A股全市场股票列表

    返回
    ----
    DataFrame 列: code, name, market, industry
    """
    import akshare as ak

    try:
        df = ak.stock_zh_a_spot_em()
    except Exception:
        # 备选方案
        df = ak.stock_info_a_code_name()

    if df is None or df.empty:
        raise ValueError('获取股票列表失败')

    # 标准化列名
    col_map = {
        '代码': 'code', '名称': 'name', '市场': 'market',
        '所属行业': 'industry',
    }
    # 处理可能的列名差异
    for old, new in col_map.items():
        if old in df.columns:
            df.rename(columns={old: new}, inplace=True)

    # 确保关键列存在
    std_cols = ['code', 'name']
    for c in std_cols:
        if c not in df.columns:
            first_col = df.columns[0]
            second_col = df.columns[1] if len(df.columns) > 1 else None
            if 'code' not in df.columns and first_col:
                df.rename(columns={first_col: 'code'}, inplace=True)
            if 'name' not in df.columns and second_col:
                df.rename(columns={second_col: 'name'}, inplace=True)

    # 过滤：排除ST、退市、新股（前60天）
    if 'name' in df.columns:
        df = df[~df['name'].str.contains('ST|退|N', na=False)]
    if 'code' in df.columns:
        df = df[~df['code'].str.startswith('8')]  # 排除北交所（以8开头）
        df = df[df['code'].str.match(r'^\d{6}$')]

    return df.reset_index(drop=True)


def resample_to_weekly(df_daily: pd.DataFrame) -> pd.DataFrame:
    """将日线数据合成周线"""
    df = df_daily.copy()
    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index)

    weekly = pd.DataFrame()
    weekly['open'] = df['open'].resample('W').first()
    weekly['high'] = df['high'].resample('W').max()
    weekly['low'] = df['low'].resample('W').min()
    weekly['close'] = df['close'].resample('W').last()
    weekly['volume'] = df['volume'].resample('W').sum()
    if 'amount' in df.columns:
        weekly['amount'] = df['amount'].resample('W').sum()
    if 'turnover' in df.columns:
        weekly['turnover'] = df['turnover'].resample('W').sum()

    weekly.dropna(inplace=True)
    return weekly


# ==============================================================================
# 技术指标计算
# ==============================================================================

def calc_ema(series: pd.Series, period: int) -> pd.Series:
    """指数移动平均"""
    return series.ewm(span=period, adjust=False).mean()


def calc_sma(series: pd.Series, period: int) -> pd.Series:
    """简单移动平均"""
    return series.rolling(window=period).mean()


def calc_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """RSI 指标"""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(span=period, adjust=False).mean()
    avg_loss = loss.ewm(span=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi


def calc_macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
              ) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """MACD 指标，返回 (MACD线, 信号线, 柱状图)"""
    ema_fast = calc_ema(close, fast)
    ema_slow = calc_ema(close, slow)
    macd_line = ema_fast - ema_slow
    macd_signal_line = calc_ema(macd_line, signal)
    macd_hist = (macd_line - macd_signal_line) * 2  # 乘以2便于观察
    return macd_line, macd_signal_line, macd_hist


def calc_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """ATR 平均真实波幅"""
    high, low, close = df['high'], df['low'], df['close']
    tr1 = high - low
    tr2 = abs(high - close.shift(1))
    tr3 = abs(low - close.shift(1))
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()


def calc_adx(df: pd.DataFrame, period: int = 14
             ) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """ADX + DI+ + DI-"""
    high, low, close = df['high'], df['low'], df['close']
    atr = calc_atr(df, period)

    up_move = high.diff()
    down_move = -low.diff()

    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0)

    plus_dm = pd.Series(plus_dm, index=df.index)
    minus_dm = pd.Series(minus_dm, index=df.index)

    plus_di = 100 * calc_ema(plus_dm, period) / atr.replace(0, np.nan)
    minus_di = 100 * calc_ema(minus_dm, period) / atr.replace(0, np.nan)

    dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di).replace(0, np.nan)
    adx = calc_ema(dx, period)

    return adx, plus_di, minus_di


def calc_pivots(series: pd.Series, left: int, right: int, mode: str = 'high'
                ) -> pd.Series:
    """
    枢轴点检测（等价于 Pine Script 的 ta.pivothigh / ta.pivotlow）

    参数
    ----
    series : 价格序列（high或low）
    left : 左侧对比K线数
    right : 右侧确认K线数
    mode : 'high' 检测枢轴高点, 'low' 检测枢轴低点

    返回
    ----
    pd.Series: 枢轴位置的值为价格，非枢轴为 NaN
    """
    n = len(series)
    result = pd.Series(np.nan, index=series.index, dtype=float)
    values = series.values

    for i in range(left, n - right):
        is_pivot = True
        if mode == 'high':
            for j in range(i - left, i):
                if values[j] > values[i]:
                    is_pivot = False
                    break
            if is_pivot:
                for j in range(i + 1, i + right + 1):
                    if values[j] > values[i]:
                        is_pivot = False
                        break
        else:  # low
            for j in range(i - left, i):
                if values[j] < values[i]:
                    is_pivot = False
                    break
            if is_pivot:
                for j in range(i + 1, i + right + 1):
                    if values[j] < values[i]:
                        is_pivot = False
                        break

        if is_pivot:
            result.iloc[i] = values[i]

    return result


def add_all_indicators(df: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    """
    一次性计算所有技术指标并添加到 DataFrame

    返回的 DataFrame 新增列:
        ema_s, ema_m, ema_l        — 短/中/长期均线
        rsi                          — RSI
        vol_ma                       — 成交量均线
        vol_ratio                    — 量比
        atr                          — ATR
        atr_pct                      — ATR/收盘价%
        macd_line, macd_sig, macd_hist — MACD
        adx, di_plus, di_minus       — ADX系统
        ph, pl                       — 枢轴高、低点
    """
    df = df.copy()

    # --- 均线 ---
    df['ema_s'] = calc_ema(df['close'], cfg.ma.ema_short)
    df['ema_m'] = calc_ema(df['close'], cfg.ma.ema_mid)
    df['ema_l'] = calc_ema(df['close'], cfg.ma.ema_long)

    # --- RSI ---
    df['rsi'] = calc_rsi(df['close'], cfg.rsi.period)

    # --- 成交量 ---
    df['vol_ma'] = calc_sma(df['volume'], cfg.volume.ma_period)
    df['vol_ratio'] = df['volume'] / df['vol_ma'].replace(0, np.nan)

    # --- ATR ---
    df['atr'] = calc_atr(df, 14)
    df['atr_pct'] = df['atr'] / df['close'] * 100

    # --- MACD ---
    df['macd_line'], df['macd_sig'], df['macd_hist'] = calc_macd(
        df['close'], cfg.macd.fast, cfg.macd.slow, cfg.macd.signal)

    # --- ADX ---
    df['adx'], df['di_plus'], df['di_minus'] = calc_adx(df, cfg.adx.period)

    # --- 枢轴点 ---
    df['ph'] = calc_pivots(df['high'], cfg.pivot.left, cfg.pivot.right, mode='high')
    df['pl'] = calc_pivots(df['low'], cfg.pivot.left, cfg.pivot.right, mode='low')

    return df
