#!/usr/bin/env python3
"""
通达信数据驱动的 N型主升浪选股器
使用 D:/new_tdx/vipdoc 已下载的 .day 文件，
在周K线和日K线两个周期上进行N型结构检测，输出选股结果CSV。
"""
import sys, os, time, struct
from datetime import datetime, timedelta
from pathlib import Path
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

# 将 ./stock 加入模块搜索路径
sys.path.insert(0, str(Path(__file__).parent.resolve()))

# ─── 通达信 .day 文件读取 ─────────────────────────────────────────────
DAY_STRUCT = struct.Struct("<IIIIIIIi")
RECORD_BYTES = 32

def read_tdx_day_file(path: Path) -> pd.DataFrame:
    """读取单只股票的通达信 .day 文件"""
    raw = path.read_bytes()
    n = len(raw) // RECORD_BYTES
    if n == 0:
        return pd.DataFrame()
    records = []
    for i in range(n):
        offset = i * RECORD_BYTES
        chunk = raw[offset:offset + RECORD_BYTES]
        date_int, o, h, l, c, amt, vol, *_ = DAY_STRUCT.unpack(chunk)
        try:
            dt = datetime.strptime(str(date_int), "%Y%m%d")
        except ValueError:
            continue
        records.append({
            "date": dt, "open": o/100.0, "high": h/100.0,
            "low": l/100.0, "close": c/100.0, "volume": vol,
            "amount": amt,
        })
    if not records:
        return pd.DataFrame()
    df = pd.DataFrame(records)
    df = df.sort_values("date").reset_index(drop=True)
    return df

# ─── 技术指标计算 ─────────────────────────────────────────────────────
def calc_ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()

def calc_sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(window=period).mean()

def calc_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(span=period, adjust=False).mean()
    avg_loss = loss.ewm(span=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))

def calc_macd(close, fast=12, slow=26, signal=9):
    ema_f = calc_ema(close, fast)
    ema_s = calc_ema(close, slow)
    macd_line = ema_f - ema_s
    macd_sig = calc_ema(macd_line, signal)
    macd_hist = (macd_line - macd_sig) * 2
    return macd_line, macd_sig, macd_hist

def calc_atr(df, period=14):
    high, low, close = df['high'], df['low'], df['close']
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()

def calc_adx(df, period=14):
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
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx = calc_ema(dx, period)
    return adx, plus_di, minus_di

def calc_pivots(series, left=5, right=5, mode='high'):
    n = len(series)
    result = pd.Series(np.nan, index=series.index, dtype=float)
    values = series.values
    for i in range(left, n - right):
        is_pivot = True
        if mode == 'high':
            for j in range(i - left, i):
                if values[j] > values[i]:
                    is_pivot = False; break
            if is_pivot:
                for j in range(i + 1, i + right + 1):
                    if values[j] > values[i]:
                        is_pivot = False; break
        else:
            for j in range(i - left, i):
                if values[j] < values[i]:
                    is_pivot = False; break
            if is_pivot:
                for j in range(i + 1, i + right + 1):
                    if values[j] < values[i]:
                        is_pivot = False; break
        if is_pivot:
            result.iloc[i] = values[i]
    return result

def add_indicators(df):
    """给DataFrame添加所有技术指标（与 ./stock 系统一致）"""
    df = df.copy()
    # 均线 EMA10/20/50
    df['ema_s'] = calc_ema(df['close'], 10)
    df['ema_m'] = calc_ema(df['close'], 20)
    df['ema_l'] = calc_ema(df['close'], 50)
    # RSI
    df['rsi'] = calc_rsi(df['close'], 14)
    # 量
    df['vol_ma'] = calc_sma(df['volume'], 20)
    df['vol_ratio'] = df['volume'] / df['vol_ma'].replace(0, np.nan)
    # ATR
    df['atr'] = calc_atr(df, 14)
    df['atr_pct'] = df['atr'] / df['close'] * 100
    # MACD
    df['macd_line'], df['macd_sig'], df['macd_hist'] = calc_macd(df['close'])
    # ADX
    df['adx'], df['di_plus'], df['di_minus'] = calc_adx(df, 14)
    # 枢轴 (left=5, right=5)
    df['ph'] = calc_pivots(df['high'], 5, 5, 'high')
    df['pl'] = calc_pivots(df['low'], 5, 5, 'low')
    return df

def resample_to_weekly(df_daily: pd.DataFrame) -> pd.DataFrame:
    """日线合成为周线，以周五为周线收盘"""
    df = df_daily.copy()
    if 'date' in df.columns:
        df = df.set_index('date')
    weekly = pd.DataFrame()
    weekly['open'] = df['open'].resample('W-FRI').first()
    weekly['high'] = df['high'].resample('W-FRI').max()
    weekly['low'] = df['low'].resample('W-FRI').min()
    weekly['close'] = df['close'].resample('W-FRI').last()
    weekly['volume'] = df['volume'].resample('W-FRI').sum()
    if 'amount' in df.columns:
        weekly['amount'] = df['amount'].resample('W-FRI').sum()
    weekly.dropna(subset=['open', 'close'], inplace=True)
    weekly.reset_index(inplace=True)
    weekly.rename(columns={'index': 'date'}, inplace=True)
    return weekly

# ─── N型结构检测（精简版，与 ./stock/n_pattern.py 一致） ────────────
@dataclass
class NResult:
    found: bool = False
    l1_price: float = np.nan; l1_idx: int = -1
    h1_price: float = np.nan; h1_idx: int = -1
    l2_price: float = np.nan; l2_idx: int = -1
    leg_pct: float = np.nan; retrace_pct: float = np.nan
    ready: bool = False; breakout: bool = False; phase: str = 'SCANNING'

def detect_n_pattern(df):
    """
    在指标已添加的DataFrame上运行 N型结构检测
    一次性遍历完成，避免二次迭代。
    返回 (df_with_signals, latest_result)
    """
    detector = NPatternDetector()
    last_idx = len(df) - 1
    latest = None
    
    # 准备输出列
    n_ready_list = [False] * len(df)
    n_breakout_list = [False] * len(df)
    n_phase_list = ['SCANNING'] * len(df)
    
    for idx, row_tuple in enumerate(df.itertuples(index=True, name='Row')):
        # 将 namedtuple 转成兼容 dict
        row_dict = row_tuple._asdict()
        row_series = pd.Series(row_dict)
        res = detector.process_bar(idx, row_series)
        n_ready_list[idx] = res.ready
        n_breakout_list[idx] = res.breakout
        n_phase_list[idx] = res.phase
        if idx == last_idx:
            latest = res
    
    df_out = df.copy()
    df_out['n_ready'] = n_ready_list
    df_out['n_breakout'] = n_breakout_list
    df_out['n_phase'] = n_phase_list
    
    return df_out, latest

class NPatternDetector:
    """与 ./stock/n_pattern.py NPatternDetector 逻辑一致"""
    def __init__(self):
        self.reset()
    def reset(self):
        self.l1_price = np.nan; self.l1_idx = -1
        self.h1_price = np.nan; self.h1_idx = -1
        self.l2_price = np.nan; self.l2_idx = -1
        self.ready = False; self.phase = 'SCANNING'; self._entered = False
    def process_bar(self, idx, row):
        pl_val = row.get('pl', np.nan)
        ph_val = row.get('ph', np.nan)
        # 枢轴低点
        if not np.isnan(pl_val):
            if np.isnan(self.l1_price):
                self.l1_price = pl_val; self.l1_idx = idx
                self.h1_price = np.nan; self.h1_idx = -1
                self.l2_price = np.nan; self.l2_idx = -1
                self.ready = False; self._entered = False; self.phase = 'BASE'
            elif not np.isnan(self.h1_price) and idx > self.h1_idx:
                leg_pct = (self.h1_price - self.l1_price)/self.l1_price*100
                retrace = (self.h1_price - pl_val)/(self.h1_price - self.l1_price)
                leg_bars = self.h1_idx - self.l1_idx
                if leg_pct >= 10 and leg_bars >= 3 and 0.30 <= retrace <= 0.65 and pl_val > self.l1_price:
                    self.l2_price = pl_val; self.l2_idx = idx
                    self.ready = True; self._entered = False; self.phase = 'READY'
                elif pl_val < self.l1_price:
                    self.l1_price = pl_val; self.l1_idx = idx
                    self.h1_price = np.nan; self.h1_idx = -1
                    self.l2_price = np.nan; self.l2_idx = -1
                    self.ready = False; self._entered = False; self.phase = 'BASE'
            elif np.isnan(self.h1_price) and pl_val < (self.l1_price if not np.isnan(self.l1_price) else float('inf')):
                if pl_val < self.l1_price:
                    self.l1_price = pl_val; self.l1_idx = idx; self.phase = 'BASE'
        # 枢轴高点
        if not np.isnan(ph_val) and not np.isnan(self.l1_price) and idx > self.l1_idx:
            if np.isnan(self.h1_price) or idx > self.h1_idx:
                if np.isnan(self.l2_price) or idx > self.l2_idx:
                    leg_pct = (ph_val - self.l1_price)/self.l1_price*100
                    leg_bars = idx - self.l1_idx
                    if leg_pct >= 10 and leg_bars >= 3:
                        self.h1_price = ph_val; self.h1_idx = idx; self.phase = 'LEG1'
        # 突破检测
        breakout = False
        if self.ready and not np.isnan(self.h1_price):
            if row['close'] > self.h1_price:
                breakout = True
                if not self._entered:
                    self._entered = True; self.phase = 'BREAKOUT'
        return NResult(
            found=not np.isnan(self.l1_price),
            l1_price=self.l1_price, l1_idx=self.l1_idx,
            h1_price=self.h1_price, h1_idx=self.h1_idx,
            l2_price=self.l2_price, l2_idx=self.l2_idx,
            leg_pct=np.nan if np.isnan(self.l1_price) or np.isnan(self.h1_price)
                    else (self.h1_price - self.l1_price)/self.l1_price*100,
            retrace_pct=np.nan if not self.ready
                    else (self.h1_price - self.l2_price)/(self.h1_price - self.l1_price)*100,
            ready=self.ready, breakout=breakout, phase=self.phase,
        )

def compute_score(row, n_result):
    """综合评分 0-100（与 ./stock/n_pattern.py compute_signal_score 一致）"""
    score = 0
    if n_result.ready:
        score += 40            # N型结构
    # 均线 15分
    try:
        if row['ema_s'] > row['ema_m'] > row['ema_l']:
            score += 15
        elif row['ema_s'] > row['ema_m']:
            score += 7
    except (KeyError, TypeError):
        pass
    # RSI 10分
    try:
        rsi = row['rsi']
        if 40 <= rsi <= 75:
            score += 10
    except (KeyError, TypeError):
        pass
    # 成交量 15分
    try:
        vr = row['vol_ratio']
        if vr >= 1.3:
            score += 15
        elif vr >= 1.0:
            score += 7
    except (KeyError, TypeError):
        pass
    # MACD 10分
    try:
        if row['macd_line'] > row['macd_sig']:
            score += 5
        if row['macd_line'] > 0:
            score += 5
    except (KeyError, TypeError):
        pass
    # ADX 5分
    try:
        if row['adx'] > 20 and row['di_plus'] > row['di_minus']:
            score += 5
    except (KeyError, TypeError):
        pass
    return min(score, 100)

def get_grade(score):
    if score >= 80: return 'S'
    elif score >= 65: return 'A'
    elif score >= 50: return 'B'
    elif score >= 30: return 'C'
    return 'D'

# ─── 股票名称查找 ─────────────────────────────────────────────────────
# 构建简单的 code→name 映射（从TDX文件或缓存）
def build_name_map():
    """从 stock_1/output 或现有缓存中构建名字映射"""
    name_map = {}
    # 尝试从 cache 文件读名字（文件名格式 code_weekly_qfq.csv）
    cache_dir = Path(r'D:\work_ai\stock\stock\cache')
    if cache_dir.exists():
        for f in cache_dir.glob('*_weekly_qfq.csv'):
            code = f.stem.split('_')[0]
            # 名字需从文件内容读第一行？算了，先留空
            pass
    # 从TDX目录读中文名？TDX的day文件只有代码没名字
    # 试从 stock_1/output 已有的结果读
    output_csv = Path(r'D:\work_ai\stock_1\output\analysis.csv')
    if output_csv.exists():
        try:
            df = pd.read_csv(output_csv)
            if 'symbol' in df.columns and 'name' not in df.columns:
                pass  # 没有名字列
        except:
            pass
    return name_map

def _save_interim(daily_results, weekly_results, errors, OUTPUT_DIR):
    """中间保存结果到CSV"""
    if daily_results:
        df = pd.DataFrame(daily_results).sort_values('评分', ascending=False).reset_index(drop=True)
        df.to_csv(OUTPUT_DIR / 'tdx_n_wave_daily.csv', index=False, encoding='utf-8-sig')
    if weekly_results:
        df = pd.DataFrame(weekly_results).sort_values('评分', ascending=False).reset_index(drop=True)
        df.to_csv(OUTPUT_DIR / 'tdx_n_wave_weekly.csv', index=False, encoding='utf-8-sig')
    combined = pd.concat([
        pd.DataFrame(daily_results), pd.DataFrame(weekly_results)
    ], ignore_index=True)
    if not combined.empty:
        combined = combined.sort_values('评分', ascending=False).reset_index(drop=True)
        combined.to_csv(OUTPUT_DIR / 'tdx_n_wave_combined.csv', index=False, encoding='utf-8-sig')
    if errors:
        pd.DataFrame(errors).to_csv(OUTPUT_DIR / 'tdx_n_wave_errors.csv', index=False, encoding='utf-8-sig')
    print(f"    → 中间保存完成 (日线{len(daily_results)}, 周线{len(weekly_results)})")

# ─── 主流程 ────────────────────────────────────────────────────────────
def main():
    OUTPUT_DIR = Path(r'D:\work_ai\stock')
    TDX_DIR = Path(r'D:\new_tdx\vipdoc')
    START_DATE = '2024-01-01'
    
    print(f"{'='*70}")
    print(f"  通达信 N型主升浪选股器")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*70}")
    
    # 收集所有股票代码
    all_symbols = []
    for exch in ('sh', 'sz'):
        day_dir = TDX_DIR / exch / 'lday'
        if not day_dir.exists():
            continue
        for f in sorted(day_dir.iterdir()):
            if f.suffix.lower() == '.day':
                sym = f.stem[2:]  # sh000001 → 000001
                if sym.startswith('0') or sym.startswith('3') or sym.startswith('6'):
                    all_symbols.append(sym)
    
    print(f"  通达信共 {len(all_symbols)} 只股票")
    
    # 全市场分析：使用所有通达信已下载的A股
    print(f"  全市场分析: {len(all_symbols)} 只A股")
    
    # 结果收集
    weekly_results = []
    daily_results = []
    errors = []
    
    # 扫描每个股票（每500只中间保存一次防止超时丢失）
    start = time.time()
    last_save_count = 0
    for idx, symbol in enumerate(all_symbols):
        if (idx+1) % 200 == 0:
            elapsed = time.time() - start
            print(f"  进度: {idx+1}/{len(all_symbols)}  (耗时 {elapsed:.0f}s, 日线={len(daily_results)}, 周线={len(weekly_results)}, 错误={len(errors)})")
            # 每500只中间保存一次
            if (idx+1) - last_save_count >= 500:
                _save_interim(daily_results, weekly_results, errors, OUTPUT_DIR)
                last_save_count = idx + 1
        
        # 确定交易所
        exch = 'sh' if symbol.startswith('6') else 'sz'
        day_path = TDX_DIR / exch / 'lday' / f'{exch}{symbol}.day'
        if not day_path.exists():
            continue
        
        try:
            # 读取日线数据
            daily = read_tdx_day_file(day_path)
            if daily.empty or len(daily) < 60:
                continue
            
            # 过滤日期
            daily = daily[daily['date'] >= START_DATE].reset_index(drop=True)
            if len(daily) < 60:
                continue
            
            # ---- 日线分析 ----
            daily_ind = add_indicators(daily)
            daily_ind['close'] = daily['close']
            _, daily_n = detect_n_pattern(daily_ind)
            
            if daily_n.ready:
                last_row = daily_ind.iloc[-1]
                score = compute_score(last_row, daily_n)
                grade = get_grade(score)
                daily_results.append({
                    '代码': symbol, '周期': '日线',
                    '最新日期': daily['date'].iloc[-1].strftime('%Y-%m-%d'),
                    '收盘价': round(float(daily['close'].iloc[-1]), 2),
                    '评分': score, '等级': grade,
                    'L1': round(daily_n.l1_price, 2) if not np.isnan(daily_n.l1_price) else '',
                    'H1': round(daily_n.h1_price, 2) if not np.isnan(daily_n.h1_price) else '',
                    'L2': round(daily_n.l2_price, 2) if not np.isnan(daily_n.l2_price) else '',
                    '首波涨幅%': round(daily_n.leg_pct, 1) if not np.isnan(daily_n.leg_pct) else '',
                    '回调幅度%': round(daily_n.retrace_pct, 1) if not np.isnan(daily_n.retrace_pct) else '',
                    '阶段': daily_n.phase,
                    '突破前高': '是' if daily_n.breakout else '否',
                    '均线多头': '是' if (last_row.get('ema_s',0) > last_row.get('ema_m',0) > last_row.get('ema_l',0)) else '否',
                    'RSI': round(float(last_row.get('rsi', np.nan)), 1) if not np.isnan(last_row.get('rsi', np.nan)) else '',
                    '量比': round(float(last_row.get('vol_ratio', np.nan)), 2) if not np.isnan(last_row.get('vol_ratio', np.nan)) else '',
                    '止损价': round(daily_n.l2_price*0.98, 2) if not np.isnan(daily_n.l2_price) else '',
                })
            
            # ---- 周线分析 ----
            weekly = resample_to_weekly(daily)
            if len(weekly) < 30:
                continue
            weekly_ind = add_indicators(weekly)
            weekly_ind['close'] = weekly['close']
            _, weekly_n = detect_n_pattern(weekly_ind)
            
            if weekly_n.ready:
                last_row_w = weekly_ind.iloc[-1]
                score_w = compute_score(last_row_w, weekly_n)
                grade_w = get_grade(score_w)
                weekly_results.append({
                    '代码': symbol, '周期': '周线',
                    '最新日期': weekly['date'].iloc[-1].strftime('%Y-%m-%d'),
                    '收盘价': round(float(weekly['close'].iloc[-1]), 2),
                    '评分': score_w, '等级': grade_w,
                    'L1': round(weekly_n.l1_price, 2) if not np.isnan(weekly_n.l1_price) else '',
                    'H1': round(weekly_n.h1_price, 2) if not np.isnan(weekly_n.h1_price) else '',
                    'L2': round(weekly_n.l2_price, 2) if not np.isnan(weekly_n.l2_price) else '',
                    '首波涨幅%': round(weekly_n.leg_pct, 1) if not np.isnan(weekly_n.leg_pct) else '',
                    '回调幅度%': round(weekly_n.retrace_pct, 1) if not np.isnan(weekly_n.retrace_pct) else '',
                    '阶段': weekly_n.phase,
                    '突破前高': '是' if weekly_n.breakout else '否',
                    '均线多头': '是' if (last_row_w.get('ema_s',0) > last_row_w.get('ema_m',0) > last_row_w.get('ema_l',0)) else '否',
                    'RSI': round(float(last_row_w.get('rsi', np.nan)), 1) if not np.isnan(last_row_w.get('rsi', np.nan)) else '',
                    '量比': round(float(last_row_w.get('vol_ratio', np.nan)), 2) if not np.isnan(last_row_w.get('vol_ratio', np.nan)) else '',
                    '止损价': round(weekly_n.l2_price*0.98, 2) if not np.isnan(weekly_n.l2_price) else '',
                })
                
        except Exception as e:
            errors.append({'代码': symbol, '错误': str(e)[:80]})
            continue
    
    elapsed = time.time() - start
    print(f"\n  扫描完成: 耗时 {elapsed:.0f}s")
    print(f"  日线信号: {len(daily_results)} 个")
    print(f"  周线信号: {len(weekly_results)} 个")
    print(f"  错误: {len(errors)} 个")
    
    # 转DataFrame并按评分排序
    df_daily = pd.DataFrame(daily_results)
    df_weekly = pd.DataFrame(weekly_results)
    df_err = pd.DataFrame(errors)
    
    if not df_daily.empty:
        df_daily = df_daily.sort_values('评分', ascending=False).reset_index(drop=True)
        daily_path = OUTPUT_DIR / 'tdx_n_wave_daily.csv'
        df_daily.to_csv(daily_path, index=False, encoding='utf-8-sig')
        print(f"\n  日线结果: {daily_path}")
        print(f"  Top 10:")
        print(f"  {'代码':>8} {'周期':4} {'收盘价':>8} {'评分':>4} {'等级':2} {'阶段':10} {'突破':4} {'均线':4} {'RSI':>5} {'量比':>5}")
        print(f"  {'-'*60}")
        for _, r in df_daily.head(10).iterrows():
            print(f"  {r['代码']:>8} {r['周期']:4} {r['收盘价']:>8.2f} {r['评分']:>4} {r['等级']:>2} {r['阶段']:10} {r['突破前高']:4} {r['均线多头']:4} {r.get('RSI',''):>5} {r.get('量比',''):>5}")
    
    if not df_weekly.empty:
        df_weekly = df_weekly.sort_values('评分', ascending=False).reset_index(drop=True)
        weekly_path = OUTPUT_DIR / 'tdx_n_wave_weekly.csv'
        df_weekly.to_csv(weekly_path, index=False, encoding='utf-8-sig')
        print(f"\n  周线结果: {weekly_path}")
        print(f"  Top 10:")
        print(f"  {'代码':>8} {'周期':4} {'收盘价':>8} {'评分':>4} {'等级':2} {'阶段':10} {'突破':4} {'均线':4} {'RSI':>5} {'量比':>5}")
        print(f"  {'-'*60}")
        for _, r in df_weekly.head(10).iterrows():
            print(f"  {r['代码']:>8} {r['周期']:4} {r['收盘价']:>8.2f} {r['评分']:>4} {r['等级']:>2} {r['阶段']:10} {r['突破前高']:4} {r['均线多头']:4} {r.get('RSI',''):>5} {r.get('量比',''):>5}")
    
    # 合并结果（日线+周线一起）
    combined = pd.concat([df_daily, df_weekly], ignore_index=True)
    if not combined.empty:
        combined = combined.sort_values('评分', ascending=False).reset_index(drop=True)
        combined_path = OUTPUT_DIR / 'tdx_n_wave_combined.csv'
        combined.to_csv(combined_path, index=False, encoding='utf-8-sig')
        print(f"\n  合并结果: {combined_path} ({len(combined)} 条)")
    
    if not df_err.empty:
        err_path = OUTPUT_DIR / 'tdx_n_wave_errors.csv'
        df_err.to_csv(err_path, index=False, encoding='utf-8-sig')
        print(f"\n  错误日志: {err_path}")
    
    print(f"\n{'='*70}")
    print(f"  完成! 所有CSV已保存至 {OUTPUT_DIR}")
    print(f"{'='*70}")

if __name__ == '__main__':
    main()
