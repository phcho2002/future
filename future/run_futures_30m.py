"""
Al Brooks Three Push Reversal — 期货30分钟级别信号扫描
=====================================================
从 futures_data.db 读取品种列表 → akshare 获取30分钟K线
→ 运行三推反转量化系统 → 输出多空信号

用法:
    python run_futures_30m.py              # 扫描全部品种
    python run_futures_30m.py --top 20     # 仅扫描前20个品种
"""

import sys
import os
import sqlite3
import time
import warnings
from datetime import datetime
from typing import List, Dict, Optional, Tuple

import numpy as np
import pandas as pd

warnings.filterwarnings('ignore')

# ---- 接入全系统统一行情入口 future_data（xtquant 后端 + TTL 缓存）----
from pathlib import Path
_SCRIPT_DIR = Path(__file__).resolve().parent
_WORK_AI = _SCRIPT_DIR.parent
sys.path.insert(0, str(_WORK_AI))
try:
    from future_data import get_klines as _tq_get_klines
    _HAS_FUTURE_DATA = True
except Exception:  # noqa: BLE001
    _HAS_FUTURE_DATA = False

# Add parent to path for direct execution
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from albrooks_reversal import ReversalEngine, EngineConfig
from albrooks_reversal.engine import ReversalSignal

# ============================================================================
# 1. 品种列表构建 (从 SQLite + 合约映射)
# ============================================================================

# 合约乘数映射 (用于数据验证)
MULTIPLIER_MAP = {
    "V": 5, "P": 10, "B": 10, "M": 10, "I": 100,
    "JD": 5, "L": 5, "PP": 5, "FB": 10, "Y": 10,
    "C": 10, "A": 10, "J": 100, "JM": 60, "CS": 10,
    "EG": 10, "RR": 10, "EB": 5, "PG": 20, "LH": 16,
    "LG": 90, "BZ": 5,
    "TA": 5, "OI": 10, "RS": 10, "RM": 10, "WH": 20,
    "JR": 20, "SR": 10, "CF": 5, "RI": 20, "MA": 10,
    "FG": 20, "LR": 20, "SF": 5, "SM": 5, "CY": 5,
    "AP": 10, "CJ": 5, "UR": 20, "SA": 20, "PF": 5,
    "PK": 5, "SH": 5, "PX": 5, "PR": 5, "PL": 5,
    "FU": 10, "AL": 5, "RU": 10, "ZN": 5, "CU": 5,
    "AU": 1000, "RB": 10, "PB": 5, "AG": 15, "BU": 10,
    "HC": 10, "SN": 1, "NI": 1, "SP": 10, "SS": 5,
    "AO": 20, "BR": 5, "AD": 25, "OP": 10,
    "SC": 1000, "NR": 10, "LU": 10, "BC": 5, "EC": 50,
    "IF": 300, "IH": 300, "IC": 200, "IM": 200,
    "TS": 20000, "TF": 10000, "T": 10000, "TL": 10000,
    "SI": 5, "LC": 1, "PS": 3, "PT": 1, "PD": 1,
}

# 具体合约映射 (2026年6月有效合约)
# 从 hourly_analysis_top40 表中提取的映射
SPECIFIC_CONTRACTS_DB = {
    'czce': ['TA2609','OI2609','RS2609','RM2609','ZC2212','WH2303','JR2301',
             'SR2609','CF2609','MA2609','FG2609','SF2607','SM2609','CY2609',
             'AP2610','CJ2609','UR2609','SA2609','PF2608','PK2610','SH2607',
             'PX2609','PR2609','PL2609'],
    'dce': ['V2609','P2609','B2607','M2609','I2609','JD2608','L2609','PP2609',
            'FB2608','BB2607','Y2609','C2607','A2607','J2609','JM2609','CS2607',
            'EG2609','RR2607','EB2607','PG2607','LH2609','LG2607','BZ2607'],
    'shfe': ['FU2609','SC2607','AL2607','RU2609','ZN2607','CU2607','AU2608',
             'RB2610','WR2607','PB2607','AG2608','BU2609','HC2610','SN2607',
             'NI2607','SP2609','NR2608','SS2607','LU2607','BC2607','AO2609',
             'BR2607','EC2607','AD2608','OP2608'],
    'cffex': ['IF2606','TF2609','IH2606','IC2606','TS2609','IM2606'],
    'gfex': ['SI2609','LC2609','PS2609','PT2608','PD2608'],
}


def get_symbol_prefix(sym: str) -> Optional[str]:
    """提取品种前缀"""
    if len(sym) >= 2 and sym[:2] in MULTIPLIER_MAP:
        return sym[:2]
    if sym[:1] in MULTIPLIER_MAP:
        return sym[:1]
    return None


def get_contract_from_symbol(symbol: str, exchange: str) -> Optional[str]:
    """从连续合约symbol匹配到具体合约代码"""
    specs = SPECIFIC_CONTRACTS_DB.get(exchange, [])
    for plen in [2, 1]:
        prefix = symbol[:plen].upper()
        for s in specs:
            if s.startswith(prefix) and len(s) > plen:
                return s
    return None


def load_instruments(db_path: str, top_n: Optional[int] = None) -> List[Dict]:
    """
    从SQLite数据库加载品种列表。
    优先使用 hourly_analysis_top40 (已有合约映射)，
    回退到 futures_all 表。
    """
    conn = sqlite3.connect(db_path)

    # 尝试从 hourly_analysis_top40 读取 (包含合约映射)
    try:
        df = pd.read_sql_query(
            "SELECT symbol, contract, name, exchange FROM hourly_analysis_top40",
            conn
        )
        if not df.empty:
            conn.close()
            instruments = df.to_dict('records')
            if top_n:
                instruments = instruments[:top_n]
            return instruments
    except Exception:
        pass

    # 回退: 从 futures_all 读取 + 手动映射合约
    try:
        df = pd.read_sql_query(
            "SELECT symbol, name, exchange FROM futures_all ORDER BY 沉淀资金_最新 DESC",
            conn
        )
        conn.close()

        instruments = []
        for _, row in df.iterrows():
            contract = get_contract_from_symbol(row['symbol'], row['exchange'])
            if contract:
                instruments.append({
                    'symbol': row['symbol'],
                    'name': row['name'],
                    'exchange': row['exchange'],
                    'contract': contract,
                })
        if top_n:
            instruments = instruments[:top_n]
        return instruments
    except Exception as e:
        conn.close()
        raise e


# ============================================================================
# 2. 数据获取 & 系统运行
# ============================================================================

def fetch_30min_data(contract: str, exchange: Optional[str] = None) -> Optional[pd.DataFrame]:
    """
    获取30分钟K线数据。

    优先走统一入口 future_data（xtquant 后端，可取深历史 + TTL 缓存）；
    失败回退到 akshare futures_zh_minute_sina。

    返回 DataFrame，包含 'datetime', 'open', 'high', 'low', 'close', 'volume' 列。
    返回 None 表示获取失败。

    注意：Albrooks 引擎只用 OHLC 四列数组（high/low/close/open），不要 volume/datetime。
    """
    # ---- 主路径：统一入口 future_data（xtquant）----
    if _HAS_FUTURE_DATA and exchange is not None:
        try:
            df = _tq_get_klines(contract, exchange, period="30", length=2000)
            if df is not None and not df.empty:
                return df
        except Exception:
            pass  # xtquant 失败回退 akshare

    # ---- 回退路径：akshare ----
    import akshare as ak

    try:
        df = ak.futures_zh_minute_sina(symbol=contract, period='30')
        if df is None or df.empty:
            return None
        return df
    except Exception:
        return None


def run_albrooks_system(df: pd.DataFrame) -> Optional[ReversalSignal]:
    """
    在30分钟K线上运行 Al Brooks 三推反转系统。

    需要至少 200 根K线才能可靠运行。
    """
    if df is None or len(df) < 100:
        return None

    high = df['high'].values.astype(np.float64)
    low = df['low'].values.astype(np.float64)
    close = df['close'].values.astype(np.float64)
    open_ = df['open'].values.astype(np.float64)

    # 配置引擎 (针对30分钟级别微调)
    config = EngineConfig(
        swing_window=4,                # 30min: 稍窄的窗口
        min_pushes=2,                  # 至少2推
        momentum_decay_threshold=0.12, # 稍低的动量衰减阈值
        divergence_lookback=20,
        model_bias=-1.8,               # 稍微积极一些
        model_calibration=4.0,
    )

    engine = ReversalEngine(config)

    try:
        signal = engine.analyze(high, low, close, open_)
        return signal
    except Exception:
        return None


def format_signal(signal: ReversalSignal) -> str:
    """格式化信号为单行报告"""
    dir_emoji = "[L] LONG" if signal.direction == 'bullish' else \
                "[S] SHORT" if signal.direction == 'bearish' else "[] NEUTRAL"
    conf_stars = {
        'very_high': '*****',
        'high': '****',
        'medium': '***',
        'low': '**',
    }.get(signal.confidence, '*')

    return (
        f"{dir_emoji:12s} | "
        f"Prob: {signal.probability:5.1f}% | "
        f"Conf: {conf_stars:6s} | "
        f"Entry: {signal.entry_price:>10.4f} | "
        f"Target: {signal.target_price:>10.4f} | "
        f"Stop: {signal.stop_loss:>10.4f} | "
        f"RR: {signal.risk_reward_ratio:.2f}:1 | "
        f"Move: {signal.expected_move_pct:.2f}% | "
        f"DD: {signal.expected_drawdown_pct:.2f}% | "
        f"S:{signal.structure_score:.2f} E:{signal.exhaustion_score:.2f} "
        f"C:{signal.candle_score:.2f} T:{signal.trend_score:.2f}"
    )


# ============================================================================
# 3. 主流程
# ============================================================================

def main():
    import argparse

    parser = argparse.ArgumentParser(description='Al Brooks 三推反转 - 30分钟期货信号扫描')
    parser.add_argument('--top', type=int, default=None,
                        help='仅扫描前N个品种 (默认: 全部)')
    parser.add_argument('--min-prob', type=float, default=40.0,
                        help='最低概率阈值 (默认: 40%%)')
    parser.add_argument('--db', type=str,
                        default=str(Path(__file__).resolve().parent.parent / 'futures_data.db'),
                        help='SQLite数据库路径')
    args = parser.parse_args()

    db_path = args.db
    min_prob = args.min_prob

    print()
    print("=" * 140)
    print("  Al Brooks Three Push Reversal — 期货30分钟信号扫描")
    print(f"  运行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  数据源: akshare futures_zh_minute_sina (30min)")
    print(f"  概率阈值: {min_prob:.0f}%")
    print("=" * 140)

    # 加载品种
    print("\n[1/3] 加载品种列表...")
    instruments = load_instruments(db_path, top_n=args.top)
    print(f"  共加载 {len(instruments)} 个品种")

    if not instruments:
        print("  错误: 未找到任何品种")
        return

    # 扫描
    print(f"\n[2/3] 获取30分钟数据 & 运行Al Brooks系统...")
    print(f"  {'序号':>3}  {'合约':>8}  {'品种':<12} {'K线数':>6}  {'信号':>10}  {'概率':>6}  {'RR':>6}  {'详情'}")
    print(f"  {'-'*3}  {'-'*8}  {'-'*12} {'-'*6}  {'-'*10}  {'-'*6}  {'-'*6}  {'-'*30}")

    all_signals: List[Tuple[Dict, ReversalSignal]] = []
    errors: List[Tuple[str, str, str]] = []

    for idx, inst in enumerate(instruments):
        symbol = inst.get('symbol', '?')
        contract = inst.get('contract', '?')
        name = inst.get('name', '?')
        exchange = inst.get('exchange')

        try:
            # 获取数据（优先 xtquant 统一入口，回退 akshare）
            df = fetch_30min_data(contract, exchange)

            if df is None or df.empty:
                print(f"  {idx+1:>3}  {contract:>8}  {name:<12} {'N/A':>6}  {'--':>10}  {'--':>6}  {'--':>6}  [无数据]")
                errors.append((symbol, name, "无30分钟数据"))
                time.sleep(0.2)
                continue

            n_bars = len(df)

            # 运行系统
            signal = run_albrooks_system(df)

            if signal is None:
                print(f"  {idx+1:>3}  {contract:>8}  {name:<12} {n_bars:>6}  {'--':>10}  {'--':>6}  {'--':>6}  [数据不足]")
                errors.append((symbol, name, f"K线不足({n_bars}根)"))
                time.sleep(0.15)
                continue

            # 判定信号方向
            if signal.pattern_detected and signal.probability >= min_prob:
                all_signals.append((inst, signal))

                dir_tag = "LONG" if signal.direction == 'bullish' else \
                          "SHORT" if signal.direction == 'bearish' else "NEUTRAL"

                print(f"  {idx+1:>3}  {contract:>8}  {name:<12} {n_bars:>6}  "
                      f"{dir_tag:>10}  {signal.probability:>5.1f}%  "
                      f"{signal.risk_reward_ratio:>5.2f}  "
                      f"S:{signal.structure_score:.2f} E:{signal.exhaustion_score:.2f} "
                      f"C:{signal.candle_score:.2f} T:{signal.trend_score:.2f} "
                      f"[{signal.wedge_type}]")
            else:
                dir_tag = "LONG" if signal.direction == 'bullish' else \
                          "SHORT" if signal.direction == 'bearish' else "--"
                prob_str = f"{signal.probability:.1f}%" if signal.pattern_detected else "--"
                print(f"  {idx+1:>3}  {contract:>8}  {name:<12} {n_bars:>6}  "
                      f"{dir_tag:>10}  {prob_str:>6}  "
                      f"{signal.risk_reward_ratio:>5.2f}  "
                      f"[{'有pattern' if signal.pattern_detected else '无pattern'}] prob<{min_prob:.0f}%")

            time.sleep(0.25)  # 速率控制

        except Exception as e:
            print(f"  {idx+1:>3}  {contract:>8}  {name:<12} {'ERR':>6}  {'--':>10}  {'--':>6}  {'--':>6}  [错误: {str(e)[:40]}]")
            errors.append((symbol, name, str(e)[:80]))
            time.sleep(0.5)

    # ====================================================================
    # 信号输出
    # ====================================================================
    print()
    print("=" * 140)
    print(f"  [3/3] 信号汇总")
    print(f"  扫描品种: {len(instruments)} | 成功: {len(instruments) - len(errors)} | "
          f"失败: {len(errors)} | 信号数: {len(all_signals)}")
    print("=" * 140)

    if all_signals:
        # 按概率排序
        all_signals.sort(key=lambda x: x[1].probability, reverse=True)

        # ─── 做多信号 ───
        long_signals = [(inst, sig) for inst, sig in all_signals
                        if sig.direction == 'bullish']
        if long_signals:
            print()
            print("  " + "─" * 132)
            print("  [L] 做多信号 (LONG) — Three Pushes DOWN → Bullish Reversal")
            print("  " + "─" * 132)
            print(f"  {'排名':>3} {'合约':>8} {'品种':<12} {'概率':>7} {'置信度':<10} {'入场':>10} "
                  f"{'目标':>10} {'止损':>10} {'RR':>6} {'预期涨幅':>8} {'预期回撤':>8} "
                  f"{'结构':>5} {'衰竭':>5} {'K线':>5} {'趋势':>5} {'推数':>4} {'楔形':<12}")
            print(f"  {'-'*3} {'-'*8} {'-'*12} {'-'*7} {'-'*10} {'-'*10} "
                  f"{'-'*10} {'-'*10} {'-'*6} {'-'*8} {'-'*8} "
                  f"{'-'*5} {'-'*5} {'-'*5} {'-'*5} {'-'*4} {'-'*12}")

            for i, (inst, sig) in enumerate(long_signals):
                print(f"  {i+1:>3} {inst['contract']:>8} {inst['name']:<12} "
                      f"{sig.probability:>6.1f}% {sig.confidence:<10} "
                      f"{sig.entry_price:>10.4f} {sig.target_price:>10.4f} {sig.stop_loss:>10.4f} "
                      f"{sig.risk_reward_ratio:>5.2f} "
                      f"{sig.expected_move_pct:>7.2f}% {sig.expected_drawdown_pct:>7.2f}% "
                      f"{sig.structure_score:>4.2f} {sig.exhaustion_score:>4.2f} "
                      f"{sig.candle_score:>4.2f} {sig.trend_score:>4.2f} "
                      f"{sig.push_count:>4} {sig.wedge_type:<12}")

        # ─── 做空信号 ───
        short_signals = [(inst, sig) for inst, sig in all_signals
                         if sig.direction == 'bearish']
        if short_signals:
            print()
            print("  " + "─" * 132)
            print("  [S] 做空信号 (SHORT) — Three Pushes UP → Bearish Reversal")
            print("  " + "─" * 132)
            print(f"  {'排名':>3} {'合约':>8} {'品种':<12} {'概率':>7} {'置信度':<10} {'入场':>10} "
                  f"{'目标':>10} {'止损':>10} {'RR':>6} {'预期跌幅':>8} {'预期回撤':>8} "
                  f"{'结构':>5} {'衰竭':>5} {'K线':>5} {'趋势':>5} {'推数':>4} {'楔形':<12}")
            print(f"  {'-'*3} {'-'*8} {'-'*12} {'-'*7} {'-'*10} {'-'*10} "
                  f"{'-'*10} {'-'*10} {'-'*6} {'-'*8} {'-'*8} "
                  f"{'-'*5} {'-'*5} {'-'*5} {'-'*5} {'-'*4} {'-'*12}")

            for i, (inst, sig) in enumerate(short_signals):
                print(f"  {i+1:>3} {inst['contract']:>8} {inst['name']:<12} "
                      f"{sig.probability:>6.1f}% {sig.confidence:<10} "
                      f"{sig.entry_price:>10.4f} {sig.target_price:>10.4f} {sig.stop_loss:>10.4f} "
                      f"{sig.risk_reward_ratio:>5.2f} "
                      f"{sig.expected_move_pct:>7.2f}% {sig.expected_drawdown_pct:>7.2f}% "
                      f"{sig.structure_score:>4.2f} {sig.exhaustion_score:>4.2f} "
                      f"{sig.candle_score:>4.2f} {sig.trend_score:>4.2f} "
                      f"{sig.push_count:>4} {sig.wedge_type:<12}")

        # ─── 综合统计 ───
        print()
        print("  " + "─" * 132)
        print(f"  :: 信号统计")
        print(f"     总信号: {len(all_signals)} (做多: {len(long_signals)}, 做空: {len(short_signals)})")
        if all_signals:
            probs = [s.probability for _, s in all_signals]
            print(f"     最高概率: {max(probs):.1f}%  |  最低概率: {min(probs):.1f}%  |  平均: {np.mean(probs):.1f}%")
            rrs = [s.risk_reward_ratio for _, s in all_signals]
            print(f"     最高RR: {max(rrs):.2f}:1  |  最低RR: {min(rrs):.2f}:1  |  平均RR: {np.mean(rrs):.2f}:1")
            actionable = [s for _, s in all_signals if s.is_actionable()]
            print(f"     可执行信号 (Prob≥60%, RR≥1.5): {len(actionable)}")
    else:
        print()
        print("  [!] 未检测到任何符合阈值的反转信号")
        print(f"     (当前概率阈值: {min_prob:.0f}%, 可尝试 --min-prob 30 降低阈值)")

    # ─── 错误报告 ───
    if errors:
        print()
        print(f"  [!] {len(errors)} 个品种分析失败:")
        for sym, nm, err in errors:
            print(f"    {sym} ({nm}): {err}")

    print()
    print("=" * 140)
    print("  扫描完成")
    print("=" * 140)
    print()


if __name__ == '__main__':
    main()
