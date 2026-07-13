"""
ETF 强势分析程序
参考 top40_daily_strength.py 的五维评分思路，
针对 A股场内 ETF 进行日线级别强势分析，输出 Top10 强势 ETF 及简报。
报告包含：板块分布、量价时空分析、RSI 顶部风险提示。
"""
import akshare as ak
import pandas as pd
import numpy as np
import warnings
import time
from datetime import datetime, timedelta
from collections import Counter

warnings.filterwarnings('ignore')

# 主流 ETF 池（代码、名称、板块分类）
ETF_UNIVERSE = [
    # 宽基指数
    {"code": "510050", "name": "上证50ETF", "sector": "宽基指数"},
    {"code": "510300", "name": "沪深300ETF", "sector": "宽基指数"},
    {"code": "510500", "name": "中证500ETF", "sector": "宽基指数"},
    {"code": "512100", "name": "中证1000ETF", "sector": "宽基指数"},
    {"code": "588000", "name": "科创50ETF", "sector": "宽基指数"},
    {"code": "588200", "name": "科创芯片ETF", "sector": "宽基指数"},
    {"code": "159915", "name": "创业板ETF", "sector": "宽基指数"},
    {"code": "159949", "name": "创业板50ETF", "sector": "宽基指数"},
    {"code": "159901", "name": "深证100ETF", "sector": "宽基指数"},
    {"code": "510180", "name": "上证180ETF", "sector": "宽基指数"},
    {"code": "159819", "name": "人工智能ETF", "sector": "科技/TMT"},

    # 科技 / TMT
    {"code": "512480", "name": "半导体ETF", "sector": "科技/TMT"},
    {"code": "512760", "name": "芯片ETF", "sector": "科技/TMT"},
    {"code": "159995", "name": "芯片ETF", "sector": "科技/TMT"},
    {"code": "515000", "name": "科技ETF", "sector": "科技/TMT"},
    {"code": "515050", "name": "5GETF", "sector": "科技/TMT"},
    {"code": "159870", "name": "云计算ETF", "sector": "科技/TMT"},
    {"code": "516510", "name": "云计算ETF", "sector": "科技/TMT"},
    {"code": "516000", "name": "机器人ETF", "sector": "科技/TMT"},
    {"code": "159892", "name": "恒生科技ETF", "sector": "科技/TMT"},
    {"code": "513130", "name": "恒生科技ETF", "sector": "科技/TMT"},

    # 新能源 / 高端制造
    {"code": "516160", "name": "新能源ETF", "sector": "新能源/制造"},
    {"code": "515030", "name": "新能源车ETF", "sector": "新能源/制造"},
    {"code": "515790", "name": "光伏ETF", "sector": "新能源/制造"},
    {"code": "159857", "name": "光伏ETF", "sector": "新能源/制造"},
    {"code": "159928", "name": "消费ETF", "sector": "新能源/制造"},
    {"code": "159967", "name": "创业板200ETF", "sector": "新能源/制造"},

    # 消费 / 医药
    {"code": "512690", "name": "酒ETF", "sector": "消费/医药"},
    {"code": "512170", "name": "医疗ETF", "sector": "消费/医药"},
    {"code": "512010", "name": "医药ETF", "sector": "消费/医药"},
    {"code": "515170", "name": "食品饮料ETF", "sector": "消费/医药"},
    {"code": "159928", "name": "消费ETF", "sector": "消费/医药"},
    {"code": "512980", "name": "传媒ETF", "sector": "消费/医药"},

    # 金融 / 周期
    {"code": "512880", "name": "证券ETF", "sector": "金融/周期"},
    {"code": "512000", "name": "券商ETF", "sector": "金融/周期"},
    {"code": "512800", "name": "银行ETF", "sector": "金融/周期"},
    {"code": "512200", "name": "地产ETF", "sector": "金融/周期"},
    {"code": "510880", "name": "红利ETF", "sector": "金融/周期"},
    {"code": "515220", "name": "煤炭ETF", "sector": "金融/周期"},
    {"code": "510170", "name": "商品ETF", "sector": "金融/周期"},
    {"code": "159870", "name": "钢铁ETF", "sector": "金融/周期"},

    # 商品 / 跨境
    {"code": "518880", "name": "黄金ETF", "sector": "商品/跨境"},
    {"code": "513100", "name": "纳指ETF", "sector": "商品/跨境"},
    {"code": "159941", "name": "纳指ETF", "sector": "商品/跨境"},
    {"code": "159920", "name": "恒生ETF", "sector": "商品/跨境"},
    {"code": "513050", "name": "中概互联ETF", "sector": "商品/跨境"},

    # 债券 / 货币（货币ETF波动极小，后续在排名中排除）
    {"code": "511010", "name": "国债ETF", "sector": "债券/货币"},
    {"code": "511880", "name": "银华日利", "sector": "货币ETF"},
    {"code": "511990", "name": "华宝添益", "sector": "货币ETF"},
]

# 去重（同一代码只保留一条）
seen = set()
ETF_UNIVERSE = [e for e in ETF_UNIVERSE if not (e['code'] in seen or seen.add(e['code']))]


def get_etf_daily(code, days=120, max_retries=3):
    """从新浪获取单只 ETF 日线数据，带重试"""
    prefix = 'sh' if code.startswith('5') else 'sz'
    symbol = f"{prefix}{code}"

    for attempt in range(max_retries):
        try:
            df = ak.fund_etf_hist_sina(symbol=symbol)
            if df is None or df.empty:
                time.sleep(0.5)
                continue

            # 列名标准化：新浪返回可能是 7 列或 8 列
            df.columns = [c.strip() for c in df.columns]
            if len(df.columns) == 8:
                # date, prevclose, open, high, low, close, volume, amount
                df = df[['date', 'open', 'high', 'low', 'close', 'volume', 'amount']]
            elif len(df.columns) == 7:
                # date, open, high, low, close, volume, amount
                pass
            else:
                # 按位置取前7列
                df = df.iloc[:, :7]
                df.columns = ['date', 'open', 'high', 'low', 'close', 'volume', 'amount']

            for col in ['open', 'high', 'low', 'close', 'volume', 'amount']:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors='coerce')
            df['date'] = pd.to_datetime(df['date'])
            df = df.sort_values('date').reset_index(drop=True)

            # 取最近 days 天
            cutoff = datetime.now() - timedelta(days=days)
            df = df[df['date'] >= cutoff].copy()
            if len(df) < 30:
                return None
            return df

        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(1.0 + attempt * 0.5)
            else:
                return None
    return None


def add_indicators(df):
    """添加均线、RSI、量能指标"""
    df = df.copy()
    df['ma5'] = df['close'].rolling(5).mean()
    df['ma10'] = df['close'].rolling(10).mean()
    df['ma20'] = df['close'].rolling(20).mean()
    df['ma60'] = df['close'].rolling(60).mean()
    df['vol_ma20'] = df['volume'].rolling(20).mean()
    df['vol_ratio'] = df['volume'] / df['vol_ma20']

    # RSI(14)
    delta = df['close'].diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    df['rsi'] = 100 - (100 / (1 + rs))

    # 涨跌幅
    df['ret_5d'] = df['close'].pct_change(5)
    df['ret_10d'] = df['close'].pct_change(10)
    df['ret_20d'] = df['close'].pct_change(20)
    return df


def score_trend_strength(df):
    """趋势强度 (30分)"""
    if len(df) < 30:
        return 10, "数据不足"
    last = df.iloc[-1]
    score = 0
    details = []

    # 均线多头排列
    if last['ma5'] > last['ma10'] > last['ma20']:
        score += 12
        details.append("均线多头排列")
    elif last['ma5'] > last['ma10']:
        score += 6
        details.append("短期均线多头")
    else:
        details.append("均线空头或粘合")

    # 价格在各均线上方
    if last['close'] > last['ma5']:
        score += 6
        details.append("收盘站上5日线")
    if last['close'] > last['ma20']:
        score += 6
        details.append("收盘站上20日线")
    if last['close'] > last['ma60']:
        score += 6
        details.append("收盘站上60日线")

    desc = "，".join(details) if details else "趋势一般"
    return min(score, 30), desc


def score_breakout_quality(df):
    """突破质量 (25分)"""
    if len(df) < 30:
        return 8, "数据不足"
    last = df.iloc[-1]
    high20 = df.tail(20)['high'].max()
    high60 = df.tail(60)['high'].max()
    low20 = df.tail(20)['low'].min()
    score = 0
    details = []

    # 创20日新高
    if last['close'] >= high20 * 0.995:
        score += 10
        details.append("接近/创20日新高")
        # 创60日新高
        if last['close'] >= high60 * 0.99:
            score += 8
            details.append("创60日新高")
    else:
        dist = (high20 - last['close']) / (high20 - low20) if (high20 - low20) > 0 else 1
        if dist < 0.1:
            score += 5
            details.append("临近20日高点")
        else:
            details.append("未接近20日高点")

    # 突破时量能
    if last['vol_ratio'] > 1.5:
        score += 7
        details.append("量能显著放大")
    elif last['vol_ratio'] > 1.0:
        score += 4
        details.append("量能温和放大")

    desc = "，".join(details) if details else "突破信号一般"
    return min(score, 25), desc


def score_volume(df):
    """量能配合 (20分)"""
    if len(df) < 20:
        return 5, "数据不足"
    last = df.iloc[-1]
    recent5 = df.tail(5)
    avg_vol_20 = df.tail(20)['volume'].mean()
    score = 0
    details = []

    # 当前量能
    if last['vol_ratio'] >= 1.5:
        score += 10
        details.append("当日量能充沛(≥1.5倍)")
    elif last['vol_ratio'] >= 1.0:
        score += 6
        details.append("量能正常")
    else:
        score += 2
        details.append("量能萎缩")

    # 近5日量能趋势
    vol_trend_up = (recent5['volume'].iloc[-1] > recent5['volume'].iloc[0])
    if vol_trend_up and last['close'] > df.iloc[-6]['close']:
        score += 6
        details.append("量价齐升")
    elif vol_trend_up:
        score += 3
        details.append("量升价平")

    # 缩量回调加分（若近5日有回调）
    recent_low = recent5['low'].min()
    if last['close'] > recent_low * 1.03 and recent5['volume'].mean() < avg_vol_20:
        score += 4
        details.append("缩量回调后企稳")

    desc = "，".join(details) if details else "量能一般"
    return min(score, 20), desc


def score_relative_strength(df):
    """相对强度/时空 (15分)"""
    if len(df) < 30:
        return 5, "数据不足"
    last = df.iloc[-1]
    ret5 = last['ret_5d']
    ret10 = last['ret_10d']
    ret20 = last['ret_20d']
    score = 0
    details = []

    if pd.notna(ret5) and ret5 > 0.05:
        score += 5
        details.append(f"近5日强势(+{ret5*100:.1f}%)")
    elif pd.notna(ret5) and ret5 > 0:
        score += 2
        details.append(f"近5日上涨(+{ret5*100:.1f}%)")

    if pd.notna(ret10) and ret10 > 0.08:
        score += 5
        details.append(f"近10日强势(+{ret10*100:.1f}%)")
    elif pd.notna(ret10) and ret10 > 0:
        score += 2
        details.append(f"近10日上涨(+{ret10*100:.1f}%)")

    if pd.notna(ret20) and ret20 > 0.10:
        score += 5
        details.append(f"近20日强势(+{ret20*100:.1f}%)")
    elif pd.notna(ret20) and ret20 > 0:
        score += 2
        details.append(f"近20日上涨(+{ret20*100:.1f}%)")

    desc = "，".join(details) if details else "近期涨幅一般"
    return min(score, 15), desc


def score_rsi(df):
    """RSI健康度 (10分)，同时识别超买"""
    if len(df) < 14:
        return 3, "数据不足"
    last = df.iloc[-1]
    rsi = last['rsi']
    if pd.isna(rsi):
        return 3, "RSI计算失败"

    if 50 <= rsi <= 70:
        return 10, f"RSI健康({rsi:.1f})"
    elif 40 <= rsi < 50:
        return 7, f"RSI中性({rsi:.1f})"
    elif 70 < rsi <= 80:
        return 5, f"RSI偏高({rsi:.1f})，注意追高风险"
    elif rsi > 80:
        return 2, f"RSI超买({rsi:.1f})，顶部风险加大"
    else:
        return 4, f"RSI偏弱({rsi:.1f})"


def analyze_etf(etf_info):
    """分析单只 ETF"""
    code = etf_info['code']
    df = get_etf_daily(code)
    if df is None or len(df) < 30:
        return None

    df = add_indicators(df)
    last = df.iloc[-1]

    s1, d1 = score_trend_strength(df)
    s2, d2 = score_breakout_quality(df)
    s3, d3 = score_volume(df)
    s4, d4 = score_relative_strength(df)
    s5, d5 = score_rsi(df)

    total = s1 + s2 + s3 + s4 + s5

    # 判定等级
    if total >= 80:
        grade = "S级 极强"
    elif total >= 68:
        grade = "A级 强势"
    elif total >= 56:
        grade = "B级 偏强"
    elif total >= 44:
        grade = "C级 中性"
    elif total >= 32:
        grade = "D级 偏弱"
    else:
        grade = "E级 弱势"

    return {
        'code': code,
        'name': etf_info['name'],
        'sector': etf_info['sector'],
        'latest_date': last['date'].strftime('%Y-%m-%d'),
        'close': round(last['close'], 3),
        'change_5d': round(last['ret_5d'] * 100, 2) if pd.notna(last['ret_5d']) else None,
        'change_10d': round(last['ret_10d'] * 100, 2) if pd.notna(last['ret_10d']) else None,
        'change_20d': round(last['ret_20d'] * 100, 2) if pd.notna(last['ret_20d']) else None,
        'rsi': round(last['rsi'], 1) if pd.notna(last['rsi']) else None,
        'vol_ratio': round(last['vol_ratio'], 2) if pd.notna(last['vol_ratio']) else None,
        'ma_status': "多头排列" if last['ma5'] > last['ma10'] > last['ma20'] else "非多头",
        'trend_score': s1,
        'breakout_score': s2,
        'volume_score': s3,
        'momentum_score': s4,
        'rsi_score': s5,
        'total_score': total,
        'grade': grade,
        'trend_desc': d1,
        'breakout_desc': d2,
        'volume_desc': d3,
        'momentum_desc': d4,
        'rsi_desc': d5,
    }


def build_report(top10, equity_results, money_results):
    """生成简短分析报告"""
    lines = []
    lines.append("=" * 80)
    lines.append("  ETF 强势分析报告")
    lines.append("  " + datetime.now().strftime("%Y-%m-%d %H:%M"))
    lines.append("=" * 80)
    lines.append("")

    # Top10 列表（已排除货币ETF）
    lines.append("【Top 10 强势 ETF】（已排除货币ETF，因其波动极小）")
    lines.append(f"{'排名':>4} {'代码':>8} {'名称':<14} {'板块':<12} {'最新价':>8} {'总分':>5} {'等级':<10} {'RSI':>6} {'5日%':>7} {'20日%':>8}")
    lines.append("-" * 100)
    for i, row in enumerate(top10, 1):
        lines.append(
            f"{i:>4} {row['code']:>8} {row['name']:<14} {row['sector']:<12} "
            f"{row['close']:>8.3f} {row['total_score']:>5} {row['grade']:<10} "
            f"{row['rsi'] if row['rsi'] else '-':>6} {row['change_5d'] if row['change_5d'] else '-':>7} {row['change_20d'] if row['change_20d'] else '-':>8}"
        )
    lines.append("")

    # 货币基金单独提示
    if not money_results.empty:
        lines.append("【货币基金提示】")
        lines.append("  以下货币基金因波动极小，不参与强势排名，仅作现金管理参考：")
        for _, row in money_results.iterrows():
            lines.append(f"    - {row['name']}({row['code']}): 最新价={row['close']:.3f}, 得分={row['total_score']}, RSI={row['rsi']}")
        lines.append("")

    # 板块分析
    sector_counter = Counter([r['sector'] for r in equity_results if r['total_score'] >= 55])
    lines.append("【板块分析】")
    if sector_counter:
        lines.append("强势ETF（总分≥55）的板块分布：")
        for sector, count in sector_counter.most_common():
            lines.append(f"  - {sector}: {count} 只")
    else:
        lines.append("当前无明显强势板块。")

    # 找出强势板块中得分最高的
    top_sector = sector_counter.most_common(1)[0][0] if sector_counter else None
    if top_sector:
        sector_etfs = [r for r in equity_results if r['sector'] == top_sector and r['total_score'] >= 55]
        avg_score = np.mean([r['total_score'] for r in sector_etfs])
        lines.append(f"  最强板块：{top_sector}，平均得分 {avg_score:.1f}，代表品种：" +
                     "、".join([f"{r['name']}({r['code']})" for r in sector_etfs[:3]]))
    lines.append("")

    # 量价时空分析
    lines.append("【量价时空分析】")
    # 量价齐升
    vol_price_up = [r for r in top10 if '量升' in r['volume_desc'] or '量价齐升' in r['volume_desc']]
    # 突破新高
    breakout = [r for r in top10 if '新高' in r['breakout_desc']]
    # 趋势多头
    trend_bull = [r for r in top10 if r['ma_status'] == '多头排列']

    lines.append(f"  量价齐升：{len(vol_price_up)} 只" + ("(" + ", ".join([r['name'] for r in vol_price_up[:3]]) + ")" if vol_price_up else ""))
    lines.append(f"  突破新高：{len(breakout)} 只" + ("(" + ", ".join([r['name'] for r in breakout[:3]]) + ")" if breakout else ""))
    lines.append(f"  均线多头：{len(trend_bull)} 只" + ("(" + ", ".join([r['name'] for r in trend_bull[:3]]) + ")" if trend_bull else ""))

    # 近期时间维度
    avg_5d = np.mean([r['change_5d'] for r in top10 if r['change_5d'] is not None])
    avg_20d = np.mean([r['change_20d'] for r in top10 if r['change_20d'] is not None])
    lines.append(f"  Top10 近5日平均涨幅：{avg_5d:.2f}%")
    lines.append(f"  Top10 近20日平均涨幅：{avg_20d:.2f}%")
    lines.append("")

    # RSI 顶部风险提示
    lines.append("【RSI 顶部风险提示】")
    overbought = [r for r in equity_results if r['rsi'] and r['rsi'] > 75]
    high_rsi = [r for r in equity_results if r['rsi'] and 70 < r['rsi'] <= 75]

    if overbought:
        lines.append(f"  ⚠️ RSI 超买区（>75）品种：")
        for r in sorted(overbought, key=lambda x: x['rsi'], reverse=True):
            lines.append(f"    - {r['name']}({r['code']}): RSI={r['rsi']:.1f}，总分={r['total_score']}，注意追高风险/短期回调")
    else:
        lines.append("  暂无 RSI>75 的极端超买品种。")

    if high_rsi:
        lines.append(f"  ⚡ RSI 偏高区（70-75）品种：")
        for r in sorted(high_rsi, key=lambda x: x['rsi'], reverse=True)[:5]:
            lines.append(f"    - {r['name']}({r['code']}): RSI={r['rsi']:.1f}，建议关注量能是否跟上，若缩量冲高需警惕")
    else:
        lines.append("  暂无 RSI 在70-75之间的品种。")

    # 如果Top10中有RSI超买的，特别标注
    top_overbought = [r for r in top10 if r['rsi'] and r['rsi'] > 75]
    if top_overbought:
        lines.append("")
        lines.append("  【重点提醒】Top10 中存在 RSI 超买品种，虽然趋势仍强，但短期顶部风险加大，建议分批减仓或设置移动止盈。")
        for r in top_overbought:
            lines.append(f"    - {r['name']}({r['code']}): RSI={r['rsi']:.1f}")
    lines.append("")

    # 操作建议
    lines.append("【综合操作建议】")
    lines.append("  1. 当前强势ETF以趋势跟随为主，可关注回踩5日/10日均线的机会。")
    lines.append("  2. 对RSI>75的品种，不宜追高，等待缩量回调或放量突破确认。")
    lines.append("  3. 板块上可优先配置强势板块中的龙头ETF，避免分散到弱势板块。")
    lines.append("  4. 严格止损：若收盘跌破20日均线或关键支撑位，考虑减仓。")
    lines.append("")

    lines.append("=" * 80)
    lines.append("  免责声明：本报告仅基于历史行情的技术分析，不构成投资建议。")
    lines.append("  ETF交易存在市场风险，请独立判断并控制仓位。")
    lines.append("=" * 80)

    return "\n".join(lines)


def main():
    print("=" * 80)
    print("  ETF 强势分析程序")
    print("  " + datetime.now().strftime("%Y-%m-%d %H:%M"))
    print("=" * 80)
    print(f"\nETF 池共 {len(ETF_UNIVERSE)} 只，开始获取日线数据...\n")

    results = []
    errors = []

    for i, etf in enumerate(ETF_UNIVERSE):
        print(f"  [{i+1}/{len(ETF_UNIVERSE)}] {etf['code']} {etf['name']} ({etf['sector']}) ...", end=' ', flush=True)
        try:
            res = analyze_etf(etf)
            if res:
                results.append(res)
                print(f"总分={res['total_score']} {res['grade']} RSI={res['rsi']}")
            else:
                errors.append((etf['code'], "数据不足"))
                print("数据不足")
        except Exception as e:
            errors.append((etf['code'], str(e)))
            print(f"错误: {e}")
        time.sleep(0.8)

    if not results:
        print("\n无有效分析结果，退出。")
        return

    # 排序（排除货币ETF，因其波动极小，强势排名无实战意义）
    results_df = pd.DataFrame(results)
    equity_results = results_df[results_df['sector'] != '货币ETF'].copy()
    equity_results = equity_results.sort_values('total_score', ascending=False).reset_index(drop=True)
    top10 = equity_results.head(10).to_dict('records')

    # 货币ETF单独列出
    money_results = results_df[results_df['sector'] == '货币ETF'].copy()
    money_results = money_results.sort_values('total_score', ascending=False).reset_index(drop=True)

    # 生成报告
    report = build_report(top10, equity_results.to_dict('records'), money_results)
    report_path = r"d:\work_ai\etf_strength_report_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".txt"
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(report)

    # 保存完整结果CSV
    csv_path = r"d:\work_ai\etf_strength_all_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".csv"
    results_df.to_csv(csv_path, index=False, encoding='utf-8-sig')

    # 终端输出摘要
    print("\n" + "=" * 80)
    print("  分析完成")
    print("=" * 80)
    print(f"  成功分析: {len(results)} 只")
    print(f"  失败: {len(errors)} 只")
    print(f"  完整报告: {report_path}")
    print(f"  全量CSV: {csv_path}")
    print("\n  Top10 强势 ETF:")
    for i, row in enumerate(top10, 1):
        print(f"    {i}. {row['code']} {row['name']} ({row['sector']})  得分:{row['total_score']}  RSI:{row['rsi']}")
    print("=" * 80)


if __name__ == "__main__":
    main()
