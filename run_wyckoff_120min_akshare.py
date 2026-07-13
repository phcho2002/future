#!/usr/bin/env python3
"""
使用akshare数据源运行 future_2 (威科夫) 系统，2H周期
"""
import os, sys, time
sys.path.insert(0, 'future_2')

import pandas as pd
import numpy as np
import akshare as ak

# 数据库
DB_PATH = "D:/work_ai/futures_data.db"

def get_top40():
    """获取top40品种"""
    import sqlite3
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT symbol, name, exchange FROM futures_top40 ORDER BY 排名")
    rows = [(r[0], r[1], r[2]) for r in cur.fetchall()]
    conn.close()
    return rows[:40]

def fetch_120min_kline(symbol: str, exchange: str, length: int = 300):
    """
    获取120分钟K线
    akshare不支持120分钟周期,从60分钟合成

    合成规则:
        open  = 第1根60分钟的开盘价
        close = 第2根60分钟的收盘价
        high  = max(两根的最高价)
        low   = min(两根的最低价)
        volume= 求和
    """
    try:
        # 获取60分钟数据，需要 2*length 根
        raw = ak.futures_zh_minute_sina(symbol=symbol, period="60")
        if raw is None or len(raw) < 2:
            return None
        
        # 解析
        df = raw.copy()
        # 不同的akshare版本列名可能不同
        col_map = {}
        cn_map = {'日期':'datetime','时间':'datetime','开盘':'open','最高':'high',
                  '最低':'low','收盘':'close','成交量':'volume'}
        for c in df.columns:
            if c in cn_map:
                col_map[c] = cn_map[c]
        if col_map:
            df = df.rename(columns=col_map)
        
        if 'datetime' not in df.columns and 'day' in df.columns:
            df['datetime'] = pd.to_datetime(df['day'])
        elif 'datetime' not in df.columns:
            df['datetime'] = pd.to_datetime(df.iloc[:, 0], errors='coerce')
        else:
            df['datetime'] = pd.to_datetime(df['datetime'], errors='coerce')
        
        # 确保数值
        for col in ('open','high','low','close','volume'):
            if col not in df.columns:
                raise ValueError(f"Missing column: {col}")
            df[col] = pd.to_numeric(df[col], errors='coerce')
        
        df = df.dropna(subset=['datetime','open','high','low','close'])
        df = df.sort_values('datetime').reset_index(drop=True)
        
        # 合成120分钟
        bars_120 = []
        for i in range(0, len(df) - 1, 2):
            k1 = df.iloc[i]
            k2 = df.iloc[i + 1]
            bars_120.append({
                'datetime': k1['datetime'],
                'open': k1['open'],
                'high': max(k1['high'], k2['high']),
                'low': min(k1['low'], k2['low']),
                'close': k2['close'],
                'volume': k1['volume'] + k2['volume'],
            })
        
        result = pd.DataFrame(bars_120)
        if len(result) > length:
            result = result.tail(length).reset_index(drop=True)
        
        return result
    except Exception as e:
        print(f"    获取 {symbol} 120min 失败: {e}")
        return None

def run_wyckoff_analysis(df: pd.DataFrame, symbol: str, name: str):
    """简化的威科夫分析 - 用价格行为判断Spring/Upthrust"""
    if df is None or len(df) < 20:
        return None
    
    # 用最近20根K线分析
    recent = df.tail(30).copy()
    
    # 简单支撑/阻力
    support = recent['low'].tail(10).min()
    resistance = recent['high'].tail(10).max()
    
    # 最新价格
    last = df.iloc[-1]
    price = last['close']
    
    # RSI 简单计算
    delta = df['close'].diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rs = gain / (loss + 1e-8)
    rsi = (100 - 100 / (1 + rs)).iloc[-1]
    
    # Spring: 价格突破支撑后收回 (最低到了支撑以下又收回)
    recent_low = recent['low'].min()
    has_spring = recent_low < support * 1.02 and price > support
    
    # Upthrust: 价格突破阻力后回落
    recent_high = recent['high'].max()
    has_upthrust = recent_high > resistance * 0.98 and price < resistance
    
    # 判断信号
    signal = None
    if has_spring and rsi < 40:
        signal = {
            'side': 'long',
            'entry': price,
            'stop': recent_low,
            'target': price + 1.5 * (price - recent_low),
            'reason': f'Spring/弹簧效应 RSI={rsi:.1f} 支撑={support:.2f}',
            'phase': 'Phase_B',
            'rsi': rsi,
        }
    elif has_upthrust and rsi > 60:
        signal = {
            'side': 'short',
            'entry': price,
            'stop': recent_high,
            'target': price - 1.5 * (recent_high - price),
            'reason': f'Upthrust/上冲回落 RSI={rsi:.1f} 阻力={resistance:.2f}',
            'phase': 'Phase_B',
            'rsi': rsi,
        }
    
    return signal

def main():
    print("=" * 70)
    print("  future_2 (威科夫) 系统 — 2H周期 (akshare数据源)")
    print("=" * 70)
    
    symbols = get_top40()
    print(f"\n扫描 {len(symbols)} 个品种 ...")
    
    signals = []
    errors = []
    
    for idx, (sym, name, ex) in enumerate(symbols, 1):
        print(f"[{idx:2d}/{len(symbols)}] {sym:6s} {name:10s} ... ", end="", flush=True)
        
        try:
            df = fetch_120min_kline(sym, ex, length=200)
            if df is None or df.empty:
                print("无数据")
                continue
            
            sig = run_wyckoff_analysis(df, sym, name)
            if sig:
                side_str = "做多" if sig['side'] == 'long' else "做空"
                print(f"信号! {side_str} 入场={sig['entry']:.2f} 止损={sig['stop']:.2f}")
                signals.append({
                    'symbol': sym,
                    'name': name,
                    'exchange': ex,
                    'side': sig['side'],
                    'entry_price': sig['entry'],
                    'stop_loss': sig['stop'],
                    'target_price': sig['target'],
                    'rsi': sig['rsi'],
                    'phase': sig['phase'],
                    'reason': sig['reason'],
                })
            else:
                rsi = df['close'].diff().where(df['close'].diff()>0,0).rolling(14).mean()
                print(f"无信号 价格={df.iloc[-1]['close']:.2f}")
        except Exception as e:
            print(f"错误: {e}")
            errors.append((sym, str(e)))
        
        # 限速
        time.sleep(0.5)
    
    # 输出
    print("\n" + "=" * 70)
    print(f"  扫描完成 | 信号: {len(signals)} | 错误: {len(errors)}")
    print("=" * 70)
    
    if signals:
        print("\n┌──────────────────────────────────────────────────────────────────────┐")
        print("│                威科夫量价分析 — 交易信号列表 (2H)                      │")
        print("└──────────────────────────────────────────────────────────────────────┘")
        for i, s in enumerate(signals, 1):
            side_cn = "做多" if s['side'] == 'long' else "做空"
            print(f"\n  ├── 信号 #{i} ─────────────────────────────────────────────")
            print(f"  │ {s['symbol']:6s} {s['name']:10s} ({s['exchange']})")
            print(f"  │ 方向: {side_cn}")
            print(f"  │ 阶段: {s['phase']}")
            print(f"  │ 入场: {s['entry_price']:>10.2f}  止损: {s['stop_loss']:>10.2f}")
            print(f"  │ 目标: {s['target_price']:>10.2f}  RSI: {s['rsi']:>8.1f}")
            print(f"  │ 逻辑: {s['reason']}")
            print(f"  └──────────────────────────────────────────────────────────")
    
    if errors:
        print(f"\n错误: {len(errors)}")
        for sym, err in errors[:5]:
            print(f"  {sym}: {err}")
    
    # 输出CSV
    if signals:
        ts = time.strftime("%Y%m%d_%H%M%S")
        out = pd.DataFrame(signals)
        outfile = f"D:/work_ai/future_2/wyckoff_signals_120min_{ts}.csv"
        out.to_csv(outfile, index=False)
        print(f"\n已保存: {outfile}")

if __name__ == "__main__":
    main()
