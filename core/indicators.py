"""
AiToEarn 策略指标计算
从 etf_rotation_backtest.py 提取，保持与原脚本逻辑完全一致
"""

import numpy as np
import pandas as pd

from config import WEIGHTS, N_TOP


# ============================================================
# 基础指标
# ============================================================

def calc_ma60(prices):
    """60 日移动均线"""
    return prices.rolling(window=60, min_periods=60).mean()


def calc_20d_gain(prices):
    """20 日涨幅（%），使用 shift(21) 即当天 vs 21天前"""
    return (prices / prices.shift(21) - 1) * 100


def calc_up_days(closes, opens):
    """近 20 日上涨天数"""
    up = (closes > opens).astype(int)
    return up.rolling(window=20, min_periods=20).sum()


def calc_ma60_pct_above(price, ma60):
    """价格高于 MA60 的百分比"""
    if pd.isna(price) or pd.isna(ma60) or ma60 == 0:
        return np.nan
    return (price / ma60 - 1) * 100


# ============================================================
# 评分计算
# ============================================================

def calc_score(gain, up_days, scheme='B'):
    """
    计算 ETF 综合评分
    - scheme: 'A'(0.7/0.3), 'B'(0.6/0.4), 'C'(0.5/0.5)
    """
    w = WEIGHTS.get(scheme, WEIGHTS['B'])
    return w['w1'] * gain + w['w2'] * up_days


def calc_all_scores(gain, up_days):
    """
    同时计算三套方案的评分
    返回 (score_a, score_b, score_c)
    """
    score_a = WEIGHTS['A']['w1'] * gain + WEIGHTS['A']['w2'] * up_days
    score_b = WEIGHTS['B']['w1'] * gain + WEIGHTS['B']['w2'] * up_days
    score_c = WEIGHTS['C']['w1'] * gain + WEIGHTS['C']['w2'] * up_days
    return score_a, score_b, score_c


# ============================================================
# 板块分类
# ============================================================

from core.data_fetcher import extract_core_keyword


def classify_sector(name):
    """
    根据 ETF 名称归类板块
    从 sector_history.json 生成时的逻辑推断而来
    """
    from config import SECTOR_KEYWORDS

    for sector, keywords in SECTOR_KEYWORDS.items():
        for kw in keywords:
            if kw in name:
                return sector
    # 默认用提取的核心词
    return extract_core_keyword(name)


def get_etf_name_map(etf_pool):
    """构建 code -> name 的映射"""
    if etf_pool is None:
        return {}
    return dict(zip(etf_pool['code'], etf_pool['name']))


# ============================================================
# 批量评分（最新一天，用于仪表盘排行）
# ============================================================

def score_all_etfs(etf_pool, valid_codes, data, target_date=None, extra_name_map=None):
    """
    对给定日期的所有有效 ETF 进行评分
    返回: list of dict，按方案B评分降序排列
         [{code, name, sector, gain_20d, up_days_20d, score_a, score_b, score_c,
           above_ma60, rank_a, rank_b, rank_c}]
    extra_name_map: 额外的 code->name 映射（如全量 ETF 快照），补充 pool 中不存在的 ETF
    """
    if not data:
        return []

    # 对齐日期
    all_dates = sorted(set().union(*[set(df.index) for df in data.values()]))
    date_index = pd.DatetimeIndex(all_dates)

    closes = pd.DataFrame(index=date_index)
    opens = pd.DataFrame(index=date_index)
    for code, df in data.items():
        closes[code] = df['close'].reindex(date_index).ffill()
        opens[code] = df['open'].reindex(date_index).ffill()

    ma60 = closes.apply(calc_ma60)

    if target_date is None:
        target_date = date_index[-1]

    if target_date not in date_index:
        # 找最近的小于 target_date 的日期
        candidates = date_index[date_index <= target_date]
        if len(candidates) == 0:
            return []
        target_date = candidates[-1]

    name_map = get_etf_name_map(etf_pool)
    # 用全量名称映射补充 pool 中不存在的 ETF
    if extra_name_map:
        for code, name in extra_name_map.items():
            if code not in name_map:
                name_map[code] = name

    score_results = []
    for code in closes.columns:
        try:
            close_series = closes[code].loc[:target_date].dropna()
            open_series = opens[code].loc[:target_date].dropna()

            if len(close_series) < 25 or len(open_series) < 25:
                continue

            price_now = closes.loc[target_date, code]
            ma60_now = ma60.loc[target_date, code] if target_date in ma60.index else np.nan

            if pd.isna(price_now):
                continue

            gain = calc_20d_gain(close_series).iloc[-1]
            up = calc_up_days(close_series, open_series).iloc[-1]

            if pd.isna(gain) or pd.isna(up):
                continue

            above_ma60 = 1 if (not pd.isna(ma60_now) and price_now > ma60_now) else 0
            score_a, score_b, score_c = calc_all_scores(gain, up)
            name = name_map.get(code, code)
            sector = classify_sector(name)

            score_results.append({
                'code': code,
                'name': name,
                'sector': sector,
                'price': round(price_now, 4),
                'ma60': round(ma60_now, 4) if not pd.isna(ma60_now) else None,
                'gain_20d': round(gain, 2),
                'up_days_20d': int(up),
                'score_a': round(score_a, 2),
                'score_b': round(score_b, 2),
                'score_c': round(score_c, 2),
                'above_ma60': above_ma60,
            })
        except Exception:
            continue

    # 排序赋排名
    for rank_key, score_key in [('rank_a', 'score_a'), ('rank_b', 'score_b'), ('rank_c', 'score_c')]:
        sorted_results = sorted(score_results, key=lambda x: x[score_key], reverse=True)
        for rank, item in enumerate(sorted_results, 1):
            item[rank_key] = rank

    # 最终按方案B排序返回
    score_results.sort(key=lambda x: x['score_b'], reverse=True)
    return score_results


# ============================================================
# Top N 筛选（板块去重）
# ============================================================

def select_top_n(scores, n=N_TOP):
    """
    从评分列表中选出 Top N（板块去重：每个板块最多1只）
    返回 top_n 列表
    """
    seen_sectors = set()
    top = []
    for item in scores:
        sector = item.get('sector', '')
        if sector not in seen_sectors:
            seen_sectors.add(sector)
            top.append(item)
        if len(top) >= n:
            break
    return top


def get_holdings_score_summary(scores, scheme='B'):
    """获取持仓摘要（支持前端显示）"""
    score_key = f'score_{scheme.lower()}'
    rank_key = f'rank_{scheme.lower()}'

    top = select_top_n(
        sorted(scores, key=lambda x: x[score_key], reverse=True)
    )

    return {
        'date': scores[0].get('date') if scores else '',
        'scheme': scheme,
        'top': top,
        'late_entry_count': sum(1 for t in top if t.get('up_days_20d', 0) < 8),
        'avg_gain': round(np.mean([t.get('gain_20d', 0) for t in top]), 2) if top else 0,
        'avg_up_days': round(np.mean([t.get('up_days_20d', 0) for t in top]), 1) if top else 0,
    }