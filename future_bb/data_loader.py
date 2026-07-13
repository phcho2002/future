"""
数据获取模块
使用 akshare 获取期货数据
"""

import akshare as ak
import pandas as pd
import os
import pickle
from datetime import datetime, timedelta
from typing import Optional, List
import time


class DataLoader:
    """期货数据加载器（基于akshare）"""

    def __init__(self, cache_dir: str = "./cache"):
        """
        初始化数据加载器

        Args:
            cache_dir: 数据缓存目录
        """
        self.cache_dir = cache_dir
        os.makedirs(cache_dir, exist_ok=True)

    def _get_cache_path(self, symbol: str, period: str = "daily") -> str:
        """获取缓存文件路径"""
        return os.path.join(self.cache_dir, f"{symbol}_{period}.pkl")

    def _load_from_cache(self, symbol: str, period: str = "daily") -> Optional[pd.DataFrame]:
        """从缓存加载数据"""
        cache_path = self._get_cache_path(symbol, period)
        if os.path.exists(cache_path):
            # 检查缓存是否过期（当日数据，缓存24小时）
            cache_mtime = os.path.getmtime(cache_path)
            if time.time() - cache_mtime < 86400:  # 24小时
                try:
                    with open(cache_path, 'rb') as f:
                        df = pickle.load(f)
                    print(f"[缓存] 加载 {symbol} {period} 数据，共 {len(df)} 条")
                    return df
                except Exception as e:
                    print(f"[缓存] 加载失败: {e}")
        return None

    def _save_to_cache(self, df: pd.DataFrame, symbol: str, period: str = "daily"):
        """保存数据到缓存"""
        cache_path = self._get_cache_path(symbol, period)
        try:
            with open(cache_path, 'wb') as f:
                pickle.dump(df, f)
            print(f"[缓存] 保存 {symbol} {period} 数据，共 {len(df)} 条")
        except Exception as e:
            print(f"[缓存] 保存失败: {e}")

    def get_futures_list(self) -> pd.DataFrame:
        """
        获取期货主力合约列表

        Returns:
            DataFrame with columns: symbol, name, exchange, etc.
        """
        try:
            df = ak.futures_main_sina()
            print(f"[数据] 获取期货列表成功，共 {len(df)} 个合约")
            return df
        except Exception as e:
            print(f"[错误] 获取期货列表失败: {e}")
            return pd.DataFrame()

    def get_daily_data(
        self,
        symbol: str,
        start_date: Optional[str] = None,
        use_cache: bool = True
    ) -> pd.DataFrame:
        """
        获取期货日线数据

        Args:
            symbol: 合约代码（如 "RB0" 表示螺纹钢主力）
            start_date: 开始日期（格式：YYYYMMDD）
            use_cache: 是否使用缓存

        Returns:
            DataFrame with columns: date, open, high, low, close, volume, hold
        """
        # 尝试从缓存加载
        if use_cache:
            cached_df = self._load_from_cache(symbol, "daily")
            if cached_df is not None:
                if start_date:
                    cached_df = cached_df[cached_df['date'] >= start_date]
                return cached_df

        # 从API获取
        try:
            print(f"[数据] 正在获取 {symbol} 日线数据...")
            df = ak.futures_zh_daily_sina(symbol=symbol)

            # 数据清洗
            df = self._clean_daily_data(df)

            # 保存到缓存
            if use_cache:
                self._save_to_cache(df, symbol, "daily")

            # 过滤日期
            if start_date:
                df = df[df['date'] >= start_date]

            print(f"[数据] 获取 {symbol} 日线数据成功，共 {len(df)} 条")
            return df

        except Exception as e:
            print(f"[错误] 获取 {symbol} 日线数据失败: {e}")
            return pd.DataFrame()

    def get_minute_data(
        self,
        symbol: str,
        period: str = "60",
        use_cache: bool = True
    ) -> pd.DataFrame:
        """
        获取期货分钟数据

        Args:
            symbol: 合约代码
            period: 周期（"5", "15", "30", "60"）
            use_cache: 是否使用缓存

        Returns:
            DataFrame with columns: datetime, open, high, low, close, volume
        """
        cache_key = f"minute_{period}"

        # 尝试从缓存加载
        if use_cache:
            cached_df = self._load_from_cache(symbol, cache_key)
            if cached_df is not None:
                return cached_df

        # 从API获取
        try:
            print(f"[数据] 正在获取 {symbol} {period}分钟数据...")
            df = ak.futures_zh_minute_sina(symbol=symbol, period=period)

            # 数据清洗
            df = self._clean_minute_data(df)

            # 保存到缓存
            if use_cache:
                self._save_to_cache(df, symbol, cache_key)

            print(f"[数据] 获取 {symbol} {period}分钟数据成功，共 {len(df)} 条")
            return df

        except Exception as e:
            print(f"[错误] 获取 {symbol} {period}分钟数据失败: {e}")
            return pd.DataFrame()

    def get_position_data(self, symbol: str) -> pd.DataFrame:
        """
        获取期货持仓数据

        Args:
            symbol: 品种代码（如 "RB" 表示螺纹钢）

        Returns:
            DataFrame with position ranking data
        """
        try:
            print(f"[数据] 正在获取 {symbol} 持仓数据...")
            df = ak.futures_position_sina(symbol=symbol)
            print(f"[数据] 获取 {symbol} 持仓数据成功")
            return df
        except Exception as e:
            print(f"[错误] 获取 {symbol} 持仓数据失败: {e}")
            return pd.DataFrame()

    def _clean_daily_data(self, df: pd.DataFrame) -> pd.DataFrame:
        """清洗日线数据"""
        if df.empty:
            return df

        # 重命名列（akshare返回的列名可能不同）
        column_mapping = {
            'date': 'date',
            'open': 'open',
            'high': 'high',
            'low': 'low',
            'close': 'close',
            'volume': 'volume',
            'hold': 'open_interest'  # 持仓量
        }

        # 检查并重命名存在的列
        existing_cols = {k: v for k, v in column_mapping.items() if k in df.columns}
        df = df.rename(columns=existing_cols)

        # 转换日期格式
        if 'date' in df.columns:
            df['date'] = pd.to_datetime(df['date']).dt.strftime('%Y%m%d')

        # 转换数值类型
        numeric_cols = ['open', 'high', 'low', 'close', 'volume', 'open_interest']
        for col in numeric_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')

        # 删除缺失值
        df = df.dropna()

        # 按日期排序
        if 'date' in df.columns:
            df = df.sort_values('date').reset_index(drop=True)

        return df

    def _clean_minute_data(self, df: pd.DataFrame) -> pd.DataFrame:
        """清洗分钟数据"""
        if df.empty:
            return df

        # 重命名列
        column_mapping = {
            'datetime': 'datetime',
            'open': 'open',
            'high': 'high',
            'low': 'low',
            'close': 'close',
            'volume': 'volume'
        }

        existing_cols = {k: v for k, v in column_mapping.items() if k in df.columns}
        df = df.rename(columns=existing_cols)

        # 转换时间格式
        if 'datetime' in df.columns:
            df['datetime'] = pd.to_datetime(df['datetime'])

        # 转换数值类型
        numeric_cols = ['open', 'high', 'low', 'close', 'volume']
        for col in numeric_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')

        # 删除缺失值
        df = df.dropna()

        # 按时间排序
        if 'datetime' in df.columns:
            df = df.sort_values('datetime').reset_index(drop=True)

        return df

    def clear_cache(self, symbol: Optional[str] = None):
        """
        清除缓存

        Args:
            symbol: 指定清除某个合约的缓存，None表示清除所有
        """
        if symbol:
            # 清除指定合约
            for file in os.listdir(self.cache_dir):
                if file.startswith(symbol):
                    os.remove(os.path.join(self.cache_dir, file))
                    print(f"[缓存] 清除 {file}")
        else:
            # 清除所有缓存
            for file in os.listdir(self.cache_dir):
                os.remove(os.path.join(self.cache_dir, file))
                print(f"[缓存] 清除 {file}")
        print("[缓存] 清除完成")


# 测试代码
if __name__ == "__main__":
    loader = DataLoader()

    # 测试获取日线数据
    print("\n=== 测试获取螺纹钢日线数据 ===")
    df_daily = loader.get_daily_data("RB0", start_date="20240101")
    if not df_daily.empty:
        print(df_daily.head())
        print(f"数据形状: {df_daily.shape}")

    # 测试获取分钟数据
    print("\n=== 测试获取螺纹钢60分钟数据 ===")
    df_minute = loader.get_minute_data("RB0", period="60")
    if not df_minute.empty:
        print(df_minute.head())
        print(f"数据形状: {df_minute.shape}")
