"""
N型主升浪交易系统 — 选股器
==============================
批量扫描A股，筛选满足N型结构条件的品种。
"""

import time
import numpy as np
import pandas as pd
from typing import List, Optional
from pathlib import Path
from tqdm import tqdm

from config import Config
from data_utils import (
    fetch_stock_hist, get_stock_list, add_all_indicators, resample_to_weekly
)
from n_pattern import NPatternDetector, check_buy_signal


class StockScreener:
    """
    A股批量筛选器

    流程:
        1. 获取股票池
        2. 逐只获取周线数据
        3. 计算指标 + N型检测
        4. 评分排序
        5. 输出Top N
    """

    def __init__(self, cfg: Config):
        self.cfg = cfg

    def get_stock_pool(self) -> pd.DataFrame:
        """获取待筛选股票池"""
        pool_type = self.cfg.screener.stock_pool

        if pool_type == 'custom' and self.cfg.screener.custom_symbols:
            df = pd.DataFrame({
                'code': self.cfg.screener.custom_symbols,
                'name': [''] * len(self.cfg.screener.custom_symbols),
            })
            return df

        # 获取全市场
        df = get_stock_list()

        if pool_type == 'hs300':
            # 沪深300
            import akshare as ak
            try:
                hs300 = ak.index_stock_cons_csindex(symbol='000300')
                codes = hs300['成分券代码'].tolist()
                df = df[df['code'].isin(codes)]
            except Exception:
                pass  # 退化为全市场

        elif pool_type == 'zz500':
            # 中证500
            import akshare as ak
            try:
                zz500 = ak.index_stock_cons_csindex(symbol='000905')
                codes = zz500['成分券代码'].tolist()
                df = df[df['code'].isin(codes)]
            except Exception:
                pass

        # 过滤
        if self.cfg.screener.exclude_st and 'name' in df.columns:
            df = df[~df['name'].str.contains('ST|退|N', na=False)]

        return df

    def scan_single(self, symbol: str) -> Optional[dict]:
        """
        扫描单只股票

        返回
        ----
        dict or None: 包含评分、N型状态、关键价位等信息
        """
        try:
            # 获取周线数据
            df = fetch_stock_hist(
                symbol=symbol,
                period=self.cfg.data.default_period,
                start_date='20180101',
                end_date='20991231',
                adjust=self.cfg.data.adjust,
                cache_dir=self.cfg.data.cache_dir,
            )

            if df is None or len(df) < 60:
                return None

            # 计算指标
            df = add_all_indicators(df, self.cfg)

            # N型检测
            detector = NPatternDetector(self.cfg)
            last_idx = len(df) - 1

            for idx, (_, row) in enumerate(df.iterrows()):
                if idx == last_idx:
                    n_result = detector.process_bar(idx, row)
                    _, signal = check_buy_signal(row, n_result, self.cfg)

                    if signal.score < self.cfg.screener.min_score:
                        return None

                    return {
                        'code': symbol,
                        'date': str(row.name)[:10] if row.name else '',
                        'close': round(float(row['close']), 2),
                        'score': signal.score,
                        'grade': signal.grade,
                        'phase': n_result.phase,
                        'ready': n_result.ready,
                        'breakout': n_result.breakout,
                        'l1': round(n_result.l1_price, 2) if not np.isnan(n_result.l1_price) else None,
                        'h1': round(n_result.h1_price, 2) if not np.isnan(n_result.h1_price) else None,
                        'l2': round(n_result.l2_price, 2) if not np.isnan(n_result.l2_price) else None,
                        'leg_pct': round(n_result.leg_pct, 1) if not np.isnan(n_result.leg_pct) else None,
                        'retrace_pct': round(n_result.retrace_pct, 1) if not np.isnan(n_result.retrace_pct) else None,
                        'ma_bullish': signal.ma_bullish,
                        'rsi': round(float(row.get('rsi', np.nan)), 1),
                        'vol_ratio': round(float(row.get('vol_ratio', np.nan)), 2),
                        'buy_signal': signal.buy_signal,
                        'stop_loss': round(signal.stop_loss, 2) if not np.isnan(signal.stop_loss) else None,
                        'tp1': round(signal.tp1, 2) if not np.isnan(signal.tp1) else None,
                        'tp2': round(signal.tp2, 2) if not np.isnan(signal.tp2) else None,
                    }
                detector.process_bar(idx, row)

        except Exception as e:
            # 静默处理单只失败
            return None

    def run(self, symbols: Optional[List[str]] = None,
            show_progress: bool = True) -> pd.DataFrame:
        """
        执行批量筛选

        参数
        ----
        symbols : 指定股票列表，None则从股票池获取
        show_progress : 是否显示进度条

        返回
        ----
        DataFrame: 按评分降序排列的筛选结果
        """
        if symbols is None:
            pool = self.get_stock_pool()
            symbols = pool['code'].tolist()
            print(f'股票池: {len(symbols)} 只')

        results = []
        iterator = tqdm(symbols, desc='扫描进度', ncols=80) if show_progress else symbols

        for symbol in iterator:
            result = self.scan_single(symbol)
            if result is not None:
                results.append(result)

            # 请求间隔
            if self.cfg.data.request_delay > 0 and len(symbols) > 1:
                time.sleep(self.cfg.data.request_delay)

        if not results:
            print('未找到符合条件的品种')
            return pd.DataFrame()

        df = pd.DataFrame(results)
        df = df.sort_values('score', ascending=False).reset_index(drop=True)

        # 显示 Top N
        top_n = min(self.cfg.screener.top_n, len(df))
        return df.head(top_n)

    def print_results(self, df: pd.DataFrame):
        """格式化打印筛选结果"""
        if df.empty:
            print('\n⚠ 未筛选到满足条件的品种。')
            print('  建议：降低最低评分 / 放宽N型结构参数')
            return

        print(f'\n{"="*90}')
        print(f'  N型主升浪选股结果 — Top {len(df)}')
        print(f'{"="*90}')
        print(f'  {"代码":<8} {"现价":>8} {"评分":>5} {"等级":>4} {"阶段":>10} '
              f'{"首波%":>7} {"回调%":>7} {"均线":>5} {"RSI":>5} {"量比":>5} {"买入":>5}')
        print(f'  {"-"*86}')

        for _, row in df.iterrows():
            buy_mark = '🔥' if row.get('buy_signal') else ''
            print(f'  {row["code"]:<8} {row["close"]:>8.2f} {row["score"]:>5} {row["grade"]:>4} '
                  f'{row["phase"]:>10} '
                  f'{row.get("leg_pct") or "-":>7} '
                  f'{row.get("retrace_pct") or "-":>7} '
                  f'{"✅" if row.get("ma_bullish") else "❌":>5} '
                  f'{row.get("rsi", "-"):>5} '
                  f'{row.get("vol_ratio", "-"):>5} '
                  f'{buy_mark:>5}')

        print(f'  {"="*90}')

        # 统计
        s_count = len(df[df['grade'] == 'S'])
        a_count = len(df[df['grade'] == 'A'])
        b_count = len(df[df['grade'] == 'B'])
        buy_count = len(df[df['buy_signal'] == True])

        print(f'  S级:{s_count}  A级:{a_count}  B级:{b_count}  '
              f'买入信号:{buy_count}个')
        print(f'{"="*90}\n')


def quick_screen(symbols: List[str], cfg: Optional[Config] = None) -> pd.DataFrame:
    """
    快捷筛选（给定股票列表）

    ```python
    from screener import quick_screen
    from config import Config

    cfg = Config()
    cfg.screener.min_score = 50
    results = quick_screen(['000001', '000002', '600519', '000858'], cfg)
    ```
    """
    if cfg is None:
        cfg = Config()
    screener = StockScreener(cfg)
    return screener.run(symbols=symbols)
