"""
N型主升浪交易系统 — 可视化
==============================
使用 mplfinance + matplotlib 绘制K线图、N型结构标注、信号标记。
"""

import numpy as np
import pandas as pd
from typing import Optional, List
from pathlib import Path

from config import Config
from n_pattern import NPatternResult


def plot_analysis(
    df: pd.DataFrame,
    cfg: Config,
    title: str = 'N型主升浪分析',
    save_path: Optional[str] = None,
    show_signals: bool = True,
    show_n_pattern: bool = True,
    show_ma: bool = True,
    show_volume: bool = True,
    show_indicators: bool = True,
) -> None:
    """
    绘制完整的N型分析图表

    包含:
        - K线主图（含均线、N型标注、买卖信号）
        - 成交量副图（含均量线、放量标记）
        - RSI副图
        - MACD副图
        - 信号评分柱状图
    """
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    import mplfinance as mpf

    # 用 mplfinance 的方式需要特别处理。这里使用 matplotlib 原生实现，
    # 以获得完全的控制力来标注N型结构。

    # 使用 mplfinance 的 make_addplot 做多面板
    df_plot = df.copy()

    # 确保索引是 DatetimeIndex
    if not isinstance(df_plot.index, pd.DatetimeIndex):
        df_plot.index = pd.to_datetime(df_plot.index)

    # ================================================================
    # 构建额外面板（addplots）
    # ================================================================
    addplots = []

    # 均线
    if show_ma and 'ema_s' in df_plot.columns:
        addplots.append(mpf.make_addplot(
            df_plot['ema_s'], color='#2196F3', width=0.8, label='EMA10'))
        addplots.append(mpf.make_addplot(
            df_plot['ema_m'], color='#FF9800', width=0.8, label='EMA20'))
        addplots.append(mpf.make_addplot(
            df_plot['ema_l'], color='#9C27B0', width=1.2, label='EMA50'))

    # N型关键价位（水平虚线）
    if show_n_pattern and 'n_l1' in df_plot.columns:
        l1_vals = df_plot['n_l1'].dropna()
        h1_vals = df_plot['n_h1'].dropna()
        l2_vals = df_plot['n_l2'].dropna()

        if len(l1_vals) > 0:
            last_l1 = l1_vals.iloc[-1]
            addplots.append(mpf.make_addplot(
                pd.Series(last_l1, index=df_plot.index),
                color='#4CAF50', width=1.0, linestyle='--', label='L1'))
        if len(h1_vals) > 0:
            last_h1 = h1_vals.iloc[-1]
            addplots.append(mpf.make_addplot(
                pd.Series(last_h1, index=df_plot.index),
                color='#FFD740', width=1.2, linestyle='--', label='H1'))
        if len(l2_vals) > 0:
            last_l2 = l2_vals.iloc[-1]
            addplots.append(mpf.make_addplot(
                pd.Series(last_l2, index=df_plot.index),
                color='#40C4FF', width=1.0, linestyle='--', label='L2'))

    # 买入信号标记
    if show_signals and 'buy_signal' in df_plot.columns:
        buy_mask = df_plot['buy_signal'] == True
        if buy_mask.any():
            buy_prices = df_plot.loc[buy_mask, 'close'] * 0.97
            buy_prices = buy_prices.reindex(df_plot.index)
            addplots.append(mpf.make_addplot(
                buy_prices, type='scatter', marker='^',
                color='#00E676', s=120, label='Buy'))

    # RSI 面板
    if show_indicators and 'rsi' in df_plot.columns:
        rsi_panel = mpf.make_addplot(
            df_plot['rsi'], panel=2, color='#9C27B0', width=1.0,
            ylabel='RSI', label='RSI')

        # RSI 参考线
        rsi_70 = mpf.make_addplot(
            pd.Series(70, index=df_plot.index), panel=2,
            color='red', width=0.5, linestyle='--', alpha=0.5)
        rsi_40 = mpf.make_addplot(
            pd.Series(40, index=df_plot.index), panel=2,
            color='green', width=0.5, linestyle='--', alpha=0.5)

        addplots.extend([rsi_panel, rsi_70, rsi_40])

    # MACD 面板
    if show_indicators and 'macd_line' in df_plot.columns:
        macd_line_plot = mpf.make_addplot(
            df_plot['macd_line'], panel=3, color='#2196F3', width=1.0,
            ylabel='MACD', label='MACD')
        macd_sig_plot = mpf.make_addplot(
            df_plot['macd_sig'], panel=3, color='#FF9800', width=0.8,
            label='Signal')
        # MACD 柱状图
        colors = ['#4CAF50' if v >= 0 else '#F44336'
                  for v in df_plot['macd_hist'].fillna(0)]
        macd_hist_plot = mpf.make_addplot(
            df_plot['macd_hist'], panel=3, type='bar', color=colors,
            alpha=0.6, label='Hist')

        addplots.extend([macd_line_plot, macd_sig_plot, macd_hist_plot])

    # 信号评分面板
    if show_indicators and 'signal_score' in df_plot.columns:
        score_colors = ['#00E676' if s >= 80 else '#4CAF50' if s >= 65
                        else '#FFAB40' if s >= 50 else '#FF5252'
                        for s in df_plot['signal_score'].fillna(0)]
        score_plot = mpf.make_addplot(
            df_plot['signal_score'], panel=4, type='bar', color=score_colors,
            alpha=0.7, ylabel='Score', label='Score')
        addplots.append(score_plot)

    # ================================================================
    # 风格配置
    # ================================================================
    mc = mpf.make_marketcolors(
        up='#ef5350', down='#26a69a',
        edge='inherit', wick='inherit', volume='inherit',
        alpha=1.0
    )
    s = mpf.make_mpf_style(
        marketcolors=mc,
        gridstyle=':', gridcolor='#37474F',
        facecolor='#1A1A2E', figcolor='#1A1A2E',
        y_on_right=True,
    )

    # 面板比例
    panel_ratios = [3]
    if show_volume:
        panel_ratios.append(1)
    if show_indicators:
        panel_ratios.extend([1, 1, 0.8])

    # ================================================================
    # 绘制
    # ================================================================
    fig, axes = mpf.plot(
        df_plot,
        type='candle',
        style=s,
        addplot=addplots,
        volume=show_volume,
        title=title,
        returnfig=True,
        figsize=(16, 10),
        panel_ratios=panel_ratios,
        datetime_format='%Y-%m',
        xrotation=30,
        warn_too_much_data=len(df_plot) + 100,
    )

    # ================================================================
    # N型结构标注（在主图上）
    # ================================================================
    if show_n_pattern and axes is not None:
        ax_main = axes[0]

        # 找到N型结构的三个关键点并标注
        if 'n_l1' in df_plot.columns and 'n_h1' in df_plot.columns:
            l1_vals = df_plot['n_l1'].dropna()
            h1_vals = df_plot['n_h1'].dropna()
            l2_vals = df_plot['n_l2'].dropna() if 'n_l2' in df_plot.columns else pd.Series()

            if len(l1_vals) > 0:
                last_l1_idx = l1_vals.index[-1]
                last_l1 = l1_vals.iloc[-1]
                ax_main.annotate(f'L1\n{last_l1:.2f}',
                                 xy=(last_l1_idx, last_l1),
                                 xytext=(0, -25), textcoords='offset points',
                                 fontsize=8, color='#4CAF50', weight='bold',
                                 ha='center',
                                 arrowprops=dict(arrowstyle='->', color='#4CAF50', alpha=0.6))

            if len(h1_vals) > 0:
                last_h1_idx = h1_vals.index[-1]
                last_h1 = h1_vals.iloc[-1]
                ax_main.annotate(f'H1\n{last_h1:.2f}',
                                 xy=(last_h1_idx, last_h1),
                                 xytext=(0, 15), textcoords='offset points',
                                 fontsize=8, color='#FFD740', weight='bold',
                                 ha='center',
                                 arrowprops=dict(arrowstyle='->', color='#FFD740', alpha=0.6))

            if len(l2_vals) > 0:
                last_l2_idx = l2_vals.index[-1]
                last_l2 = l2_vals.iloc[-1]
                ax_main.annotate(f'L2\n{last_l2:.2f}',
                                 xy=(last_l2_idx, last_l2),
                                 xytext=(0, -25), textcoords='offset points',
                                 fontsize=8, color='#40C4FF', weight='bold',
                                 ha='center',
                                 arrowprops=dict(arrowstyle='->', color='#40C4FF', alpha=0.6))

    # ================================================================
    # 保存或显示
    # ================================================================
    if save_path:
        Path(save_path).parent.mkdir(parents=True, exist_ok=True)
        plt.savefig(save_path, dpi=150, bbox_inches='tight',
                    facecolor='#1A1A2E', edgecolor='none')
        print(f'图表已保存至: {save_path}')
    else:
        plt.show()

    plt.close('all')


def plot_simple(df: pd.DataFrame, title: str = 'N型分析',
                save_path: Optional[str] = None) -> None:
    """
    简化版图表（快速查看，只需 matplotlib）

    适合没有 mplfinance 的环境。
    """
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates

    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index)

    fig, axes = plt.subplots(3, 1, figsize=(14, 10),
                              gridspec_kw={'height_ratios': [3, 1, 1]},
                              facecolor='white')
    fig.suptitle(title, fontsize=14, fontweight='bold')

    ax1, ax2, ax3 = axes

    # --- 主图：价格 + 均线 + N型标注 ---
    ax1.plot(df.index, df['close'], color='black', linewidth=1.5, label='Close')
    if 'ema_s' in df.columns:
        ax1.plot(df.index, df['ema_s'], color='#2196F3', linewidth=0.8, alpha=0.7, label='EMA10')
    if 'ema_m' in df.columns:
        ax1.plot(df.index, df['ema_m'], color='#FF9800', linewidth=0.8, alpha=0.7, label='EMA20')
    if 'ema_l' in df.columns:
        ax1.plot(df.index, df['ema_l'], color='#9C27B0', linewidth=1.0, alpha=0.7, label='EMA50')

    # N型关键点
    if 'n_l1' in df.columns:
        l1 = df['n_l1'].dropna()
        if len(l1) > 0:
            ax1.scatter(l1.index[-1], l1.iloc[-1], c='green', s=80, zorder=5, marker='s')
            ax1.annotate('L1', (l1.index[-1], l1.iloc[-1]),
                         textcoords='offset points', xytext=(0, -15), ha='center', color='green')
    if 'n_h1' in df.columns:
        h1 = df['n_h1'].dropna()
        if len(h1) > 0:
            ax1.scatter(h1.index[-1], h1.iloc[-1], c='orange', s=80, zorder=5, marker='s')
            ax1.annotate('H1', (h1.index[-1], h1.iloc[-1]),
                         textcoords='offset points', xytext=(0, 10), ha='center', color='orange')
    if 'n_l2' in df.columns:
        l2 = df['n_l2'].dropna()
        if len(l2) > 0:
            ax1.scatter(l2.index[-1], l2.iloc[-1], c='blue', s=80, zorder=5, marker='s')
            ax1.annotate('L2', (l2.index[-1], l2.iloc[-1]),
                         textcoords='offset points', xytext=(0, -15), ha='center', color='blue')

    # 买入信号
    if 'buy_signal' in df.columns:
        buy_mask = df['buy_signal'] == True
        if buy_mask.any():
            ax1.scatter(df.index[buy_mask], df.loc[buy_mask, 'close'] * 0.96,
                        c='lime', marker='^', s=150, zorder=10, alpha=0.9,
                        edgecolors='darkgreen', linewidths=1)

    ax1.set_ylabel('Price')
    ax1.legend(loc='upper left', fontsize=7, ncol=3)
    ax1.grid(True, alpha=0.3)
    ax1.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))

    # --- 成交量 ---
    if 'volume' in df.columns:
        colors = ['red' if df['close'].iloc[i] >= df['open'].iloc[i] else 'green'
                  for i in range(len(df))]
        ax2.bar(df.index, df['volume'] / 1e6, color=colors, alpha=0.7, width=2)
        if 'vol_ma' in df.columns:
            ax2.plot(df.index, df['vol_ma'] / 1e6, color='orange', linewidth=1, alpha=0.8)
    ax2.set_ylabel('Vol (M)')
    ax2.grid(True, alpha=0.3)
    ax2.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))

    # --- 信号评分 ---
    if 'signal_score' in df.columns:
        score_colors = ['#00E676' if s >= 80 else '#4CAF50' if s >= 65
                        else '#FFAB40' if s >= 50 else '#FF5252'
                        for s in df['signal_score'].fillna(0)]
        ax3.bar(df.index, df['signal_score'], color=score_colors, alpha=0.8, width=2)
        ax3.axhline(y=80, color='lime', linestyle='--', alpha=0.5, linewidth=0.8)
        ax3.axhline(y=65, color='green', linestyle='--', alpha=0.5, linewidth=0.8)
        ax3.axhline(y=50, color='orange', linestyle='--', alpha=0.5, linewidth=0.8)
    ax3.set_ylabel('Score')
    ax3.set_ylim(0, 105)
    ax3.grid(True, alpha=0.3)
    ax3.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))

    plt.tight_layout()

    if save_path:
        Path(save_path).parent.mkdir(parents=True, exist_ok=True)
        plt.savefig(save_path, dpi=120, bbox_inches='tight', facecolor='white')
        print(f'图表已保存至: {save_path}')
    else:
        plt.show()

    plt.close('all')
