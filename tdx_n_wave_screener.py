"""
通达信日线数据 + N型主升浪选股器
====================================
数据源：D:\new_tdx 已下载的日线数据（.day 文件）
策略：  复用 ./stock 的 N型主升浪选股逻辑
周期：  日线
范围：  全A非ST股
"""
import os
import sys
import struct
import time
import warnings
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from collections import namedtuple

import numpy as np
import pandas as pd

# 将 ./stock 加入路径以复用 config / n_pattern / data_utils
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'stock'))
from config import Config
from data_utils import add_all_indicators
from n_pattern import NPatternDetector, check_buy_signal

warnings.filterwarnings('ignore')

# 通达信安装目录
TDX_ROOT = Path(r"D:\new_tdx")
SH_DAY_DIR = TDX_ROOT / "vipdoc" / "sh" / "lday"
SZ_DAY_DIR = TDX_ROOT / "vipdoc" / "sz" / "lday"
BASE_DBF = TDX_ROOT / "T0002" / "hq_cache" / "base.dbf"


# ==============================================================================
# 通达信数据读取
# ==============================================================================

def read_tdx_base_dbf() -> pd.DataFrame:
    """
    读取通达信 base.dbf，返回 A 股代码列表
    字段：SC(市场 0=深圳 1=上海), GPDM(股票代码)
    """
    with open(BASE_DBF, 'rb') as f:
        data = f.read()

    num_records = struct.unpack('<I', data[4:8])[0]
    header_size = struct.unpack('<H', data[8:10])[0]
    record_size = struct.unpack('<H', data[10:12])[0]

    # 读取字段描述
    fields = []
    pos = 32
    while data[pos] != 0x0D:
        field_name = data[pos:pos+11].split(b'\x00')[0].decode('gbk', errors='ignore')
        field_type = chr(data[pos+11])
        field_length = data[pos+16]
        pos += 32
        fields.append((field_name, field_type, field_length))

    # 定位记录开始
    pos = header_size
    records = []
    for _ in range(num_records):
        deleted = data[pos]
        pos += 1
        record = {}
        for fname, ftype, flen in fields:
            value = data[pos:pos+flen]
            pos += flen
            s = value.decode('gbk', errors='ignore').strip()
            record[fname] = s
        if deleted != 0x2A:  # 跳过删除标记
            records.append(record)

    df = pd.DataFrame(records)
    df = df[['SC', 'GPDM']].copy()
    df.columns = ['market', 'code']
    return df


def read_tdx_day_file(code: str, market: str, min_days: int = 120) -> Optional[pd.DataFrame]:
    """
    读取单只股票的通达信日线数据

    参数
    ----
    code : 6位股票代码
    market : 'sh' 或 'sz'

    返回
    ----
    DataFrame 列：open, high, low, close, volume, amount，索引为 date
    """
    prefix = 'sh' if market == 'sh' else 'sz'
    path = TDX_ROOT / "vipdoc" / market / "lday" / f"{prefix}{code}.day"

    if not path.exists():
        return None

    records = []
    with open(path, 'rb') as f:
        while True:
            buf = f.read(32)
            if not buf or len(buf) < 32:
                break
            # 通达信日线格式：date(4), open(4), high(4), low(4), close(4), amount(4f), volume(4), reserved(4)
            date, open_p, high_p, low_p, close_p, amount, volume, _ = struct.unpack('IIIIIfII', buf)
            if date == 0:
                continue
            records.append({
                'date': datetime.strptime(str(date), '%Y%m%d'),
                'open': open_p / 100.0,
                'high': high_p / 100.0,
                'low': low_p / 100.0,
                'close': close_p / 100.0,
                'amount': float(amount),
                'volume': int(volume),
            })

    if len(records) < min_days:
        return None

    df = pd.DataFrame(records)
    df.set_index('date', inplace=True)
    df.sort_index(inplace=True)

    # 只保留最近 2 年数据，避免数据过长
    cutoff = df.index.max() - timedelta(days=730)
    df = df[df.index >= cutoff].copy()

    return df


def is_a_stock(code: str) -> bool:
    """判断是否为A股6位代码（主板、创业板、科创板），排除常见指数"""
    if not code or len(code) != 6 or not code.isdigit():
        return False

    # 上海主板：600-609、601、603、605、688（科创板）
    if code.startswith(('600', '601', '603', '605', '688')):
        return True

    # 深圳主板：0004-0009、001、002、003
    if code.startswith(('0004', '0005', '0006', '0007', '0008', '0009',
                        '001', '002', '003')):
        return True

    # 创业板/科创板：300、301、302
    if code.startswith(('300', '301', '302')):
        return True

    return False


def get_all_a_stock_codes() -> List[Tuple[str, str]]:
    """
    从通达信 base.dbf 获取全部A股代码

    返回
    ----
    [(code, market), ...]
    """
    print("[1/5] 从通达信 base.dbf 读取A股代码列表...")
    df = read_tdx_base_dbf()
    # market: 0=深圳(sz), 1=上海(sh)
    df['market_str'] = df['market'].apply(lambda x: 'sh' if str(x) == '1' else 'sz')
    df = df[df['code'].apply(is_a_stock)]
    # 排除 obvious 指数
    df = df[~df['code'].isin(['000001', '399001', '399006', '000016', '000300',
                               '000905', '000688', '399005', '399673'])]
    codes = list(zip(df['code'].tolist(), df['market_str'].tolist()))
    print(f"      共 {len(codes)} 只A股")
    return codes


# ==============================================================================
# ST 过滤（行情用通达信，ST列表用 akshare 在线获取一次）
# ==============================================================================

def get_st_codes() -> set:
    """从 akshare 获取当前 ST 股代码集合"""
    try:
        import akshare as ak
        print("[2/5] 从 akshare 获取 ST 股列表（仅用于过滤，行情仍用通达信）...")
        df = ak.stock_zh_a_st_em()
        if df is not None and not df.empty and '代码' in df.columns:
            return set(df['代码'].astype(str).str.strip().tolist())
    except Exception as e:
        print(f"      获取ST列表失败: {e}")
    return set()


# ==============================================================================
# 选股核心
# ==============================================================================

def get_daily_config() -> Config:
    """日线级别的 N型主升浪参数"""
    cfg = Config()
    cfg.pivot.left = 10          # 日线枢轴左侧K线数
    cfg.pivot.right = 5          # 日线枢轴右侧确认K线数
    cfg.n_pattern.fib_min = 0.30
    cfg.n_pattern.fib_max = 0.65
    cfg.n_pattern.min_leg_pct = 15.0    # 日线首波最小涨幅15%
    cfg.n_pattern.min_leg_bars = 5
    cfg.n_pattern.reset_bars = 250      # 日线约1年失效
    cfg.ma.ema_short = 10
    cfg.ma.ema_mid = 20
    cfg.ma.ema_long = 60
    cfg.rsi.low = 40
    cfg.rsi.high = 75
    cfg.volume.burst_mult = 1.5
    cfg.volume.ma_period = 20
    cfg.adx.threshold = 20
    cfg.screener.min_score = 50
    return cfg


def scan_stock(code: str, market: str, cfg: Config) -> Optional[Dict]:
    """扫描单只股票，返回信号信息"""
    df = read_tdx_day_file(code, market)
    if df is None or len(df) < 60:
        return None

    df = add_all_indicators(df, cfg)
    detector = NPatternDetector(cfg)
    last_idx = len(df) - 1

    signal = None
    for idx, (_, row) in enumerate(df.iterrows()):
        if idx == last_idx:
            n_result = detector.process_bar(idx, row)
            _, signal = check_buy_signal(row, n_result, cfg)
        else:
            detector.process_bar(idx, row)

    if signal is None:
        return None

    # 只返回满足买入信号或评分>=阈值的
    if not signal.buy_signal and signal.score < cfg.screener.min_score:
        return None

    latest = df.iloc[-1]
    return {
        'code': code,
        'market': market.upper(),
        'date': str(df.index[-1])[:10],
        'close': round(latest['close'], 2),
        'score': int(signal.score),
        'grade': signal.grade,
        'phase': signal.n_pattern.phase,
        'buy_signal': signal.buy_signal,
        'l1': round(signal.n_pattern.l1_price, 2) if not np.isnan(signal.n_pattern.l1_price) else None,
        'h1': round(signal.n_pattern.h1_price, 2) if not np.isnan(signal.n_pattern.h1_price) else None,
        'l2': round(signal.n_pattern.l2_price, 2) if not np.isnan(signal.n_pattern.l2_price) else None,
        'leg_pct': round(signal.n_pattern.leg_pct, 1) if not np.isnan(signal.n_pattern.leg_pct) else None,
        'retrace_pct': round(signal.n_pattern.retrace_pct, 1) if not np.isnan(signal.n_pattern.retrace_pct) else None,
        'ma_bullish': signal.ma_bullish,
        'rsi': round(latest['rsi'], 1) if not np.isnan(latest['rsi']) else None,
        'rsi_healthy': signal.rsi_healthy,
        'vol_ratio': round(latest['vol_ratio'], 2) if not np.isnan(latest['vol_ratio']) else None,
        'vol_burst': signal.vol_burst,
        'macd_bullish': signal.macd_bullish,
        'adx_trend': signal.adx_trend,
        'entry_price': round(signal.entry_price, 2) if signal.buy_signal else None,
        'stop_loss': round(signal.stop_loss, 2) if not np.isnan(signal.stop_loss) else None,
        'tp1': round(signal.tp1, 2) if not np.isnan(signal.tp1) else None,
        'tp2': round(signal.tp2, 2) if not np.isnan(signal.tp2) else None,
    }


def load_st_list_from_file(filepath: str) -> set:
    """从本地文件读取ST代码列表，每行一个代码"""
    st_codes = set()
    path = Path(filepath)
    if not path.exists():
        return st_codes
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            code = line.strip()
            if code and code.isdigit():
                st_codes.add(code)
    return st_codes


def load_names_from_tdx() -> Dict[str, str]:
    """
    尝试从通达信本地文件读取股票名称
    部分版本在 T0002/hq_cache/code2gp.dat 或类似文件中
    """
    return {}


def parse_args():
    import argparse
    parser = argparse.ArgumentParser(description='通达信日线 + N型主升浪选股器')
    parser.add_argument('--st-list', type=str, default='',
                        help='本地ST股列表文件路径，每行一个6位代码')
    parser.add_argument('--min-score', type=int, default=50,
                        help='最低入选评分，默认50')
    parser.add_argument('--top', type=int, default=0,
                        help='只输出前N个结果，默认全部')
    parser.add_argument('--offline', action='store_true',
                        help='完全离线模式，不尝试从akshare获取ST列表和名称')
    parser.add_argument('--output', type=str, default='',
                        help='输出CSV文件名')
    return parser.parse_args()


def main():
    args = parse_args()

    print("=" * 80)
    print("  通达信日线 + N型主升浪选股器")
    print("  " + datetime.now().strftime("%Y-%m-%d %H:%M"))
    print("=" * 80)

    cfg = get_daily_config()
    cfg.screener.min_score = args.min_score

    # 1. 获取代码列表
    codes = get_all_a_stock_codes()
    if not codes:
        print("  未读取到A股代码，请检查通达信安装路径")
        return

    # 2. 获取ST列表并过滤
    st_codes = set()
    if args.st_list:
        print(f"[2/5] 从本地文件读取ST列表: {args.st_list}")
        st_codes = load_st_list_from_file(args.st_list)
        print(f"      读取到 {len(st_codes)} 只ST股")
    elif not args.offline:
        st_codes = get_st_codes()
    else:
        print("[2/5] 离线模式，跳过ST列表获取")

    if st_codes:
        codes = [(c, m) for c, m in codes if c not in st_codes]
        print(f"      排除ST后剩余 {len(codes)} 只")
    else:
        print("      未获取到ST列表，将不对ST进行过滤（结果可能包含ST股，请人工复核）")

    # 3. 扫描选股
    print(f"\n[3/5] 开始日线N型扫描（min_score={cfg.screener.min_score}）...")
    results = []
    errors = []
    start_time = time.time()

    for i, (code, market) in enumerate(codes):
        if i % 200 == 0 or i == len(codes) - 1:
            elapsed = time.time() - start_time
            print(f"  [{i+1}/{len(codes)}] 已扫描 {i} 只，发现 {len(results)} 个信号，耗时 {elapsed:.1f}s")
        try:
            res = scan_stock(code, market, cfg)
            if res:
                results.append(res)
        except Exception as e:
            errors.append((code, str(e)))

    print(f"\n[4/5] 扫描完成：发现信号 {len(results)} 只，失败 {len(errors)} 只")

    if not results:
        print("\n  当前未选出符合条件的股票")
        return

    # 4. 排序并输出
    results_df = pd.DataFrame(results)
    results_df = results_df.sort_values('score', ascending=False).reset_index(drop=True)

    # 尝试补充名称
    results_df['name'] = ''
    if not args.offline:
        try:
            import akshare as ak
            names = ak.stock_zh_a_spot_em()
            if '代码' in names.columns and '名称' in names.columns:
                name_map = dict(zip(names['代码'].astype(str).str.strip(), names['名称']))
                results_df['name'] = results_df['code'].map(name_map)
        except Exception:
            pass

    # 5. 保存结果
    output_csv = args.output if args.output else f"tdx_n_wave_screen_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    results_df.to_csv(output_csv, index=False, encoding='utf-8-sig')

    # 6. 打印列表
    print("\n[5/5] 选股结果列表")
    print("=" * 120)
    display_cols = ['code', 'name', 'market', 'date', 'close', 'score', 'grade',
                    'phase', 'buy_signal', 'rsi', 'vol_ratio', 'ma_bullish', 'entry_price', 'stop_loss']
    display_df = results_df[display_cols].copy()
    display_df['buy_signal'] = display_df['buy_signal'].apply(lambda x: '[买入]' if x else '否')
    display_df['ma_bullish'] = display_df['ma_bullish'].apply(lambda x: '是' if x else '否')

    top_n = args.top if args.top > 0 else len(display_df)
    print(display_df.head(top_n).to_string(index=False))
    print("=" * 120)

    # 统计
    buy_count = results_df['buy_signal'].sum()
    print(f"\n统计：")
    print(f"  总扫描：{len(codes)} 只")
    print(f"  入选信号：{len(results_df)} 只")
    print(f"  买入信号（N型突破）：{buy_count} 只")
    print(f"  S级(>=80)：{len(results_df[results_df['score'] >= 80])} 只")
    print(f"  A级(65-79)：{len(results_df[(results_df['score'] >= 65) & (results_df['score'] < 80)])} 只")
    print(f"  B级(50-64)：{len(results_df[(results_df['score'] >= 50) & (results_df['score'] < 65)])} 只")
    print(f"\n  结果已保存：{output_csv}")
    if not st_codes:
        print("  注意：本次未进行ST过滤，结果中可能包含ST股，请人工复核")
    print("=" * 120)


if __name__ == "__main__":
    main()
