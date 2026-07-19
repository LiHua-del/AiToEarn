"""
AiToEarn 回测引擎
从 etf_rotation_backtest.py 提取 run_backtest 逻辑，保持完全一致
"""

import os
import time
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from config import DATA_DIR, WEIGHTS, N_TOP, TRADE_COST, DEVIATION_WEIGHTS
from core.data_fetcher import load_cached_data, get_etf_pool, extract_core_keyword
from core.indicators import calc_ma60, calc_ma5, calc_20d_gain, calc_up_days, calc_all_scores, calc_deviation_rate, classify_sector
from core.risk_control import LayeredStopLossManager
from db.models import EquityCurveModel, EquityCurveV2Model, TradeHistoryModel, get_conn


# ============================================================
# 数据加载
# ============================================================

def load_etf_data(codes):
    """加载所有 ETF 历史数据（与原始脚本完全一致）"""
    data = {}
    for code in codes:
        df = load_cached_data(code)
        if df is not None and len(df) >= 65:
            data[code] = df
    return data


def get_sector(etf_pool, code):
    """从标的池获取 ETF 的板块分类"""
    if etf_pool is not None:
        row = etf_pool[etf_pool['code'] == code]
        if len(row) > 0:
            name = row.iloc[0]['name']
            return classify_sector(name)
    return extract_core_keyword(code)


# ============================================================
# 统计计算
# ============================================================

def calc_stats(equity, turnover, total_reb, late_entry, n_top):
    """计算回测统计指标（与原脚本完全一致）"""
    rets = equity.pct_change().dropna()
    cum_ret = equity.iloc[-1] / equity.iloc[0] - 1
    n_days = len(equity)
    ann_ret = (1 + cum_ret) ** (252 / max(n_days, 1)) - 1

    peak = equity.cummax()
    dd = (equity - peak) / peak
    max_dd = dd.min()

    rf_daily = 0.03 / 252
    excess = rets - rf_daily
    sharpe = np.sqrt(252) * excess.mean() / excess.std() if excess.std() > 0 else 0

    win_rate = (rets > 0).sum() / len(rets) if len(rets) > 0 else 0
    late_entry_rate = late_entry / (total_reb * n_top) if total_reb * n_top > 0 else 0

    return {
        'cum_ret': round(cum_ret, 4),
        'ann_ret': round(ann_ret, 4),
        'max_dd': round(max_dd, 4),
        'sharpe': round(sharpe, 4),
        'win_rate': round(win_rate, 4),
        'turnover': int(turnover),
        'late_entry_rate': round(late_entry_rate, 4),
    }


# ============================================================
# 回测核心（与原脚本完全一致的逻辑）
# ============================================================

def run_backtest(etf_pool, valid_codes, scheme='B'):
    """
    运行回测（含乖离率评分惩罚 — 三因子模型）
    参数: etf_pool=标的池 DataFrame, valid_codes=有效代码列表, scheme='A'/'B'/'C'
    返回: {
        'equity_series': pd.Series,      # 每日净值
        'weekly_holdings': list[dict],    # 每周持仓明细
        'stats': dict,                    # 统计指标
        'label': str,                     # 方案标签
    }
    """
    w = WEIGHTS.get(scheme, WEIGHTS['B'])
    w1, w2_val = w['w1'], w['w2']
    w3 = DEVIATION_WEIGHTS.get(scheme, DEVIATION_WEIGHTS['B'])
    label = w['label']

    data = load_etf_data(valid_codes)
    if not data:
        return None

    # 对齐日期索引
    all_dates = sorted(set().union(*[set(df.index) for df in data.values()]))
    date_index = pd.DatetimeIndex(all_dates)

    closes = pd.DataFrame(index=date_index)
    opens = pd.DataFrame(index=date_index)
    for code, df in data.items():
        closes[code] = df['close'].reindex(date_index).ffill()
        opens[code] = df['open'].reindex(date_index).ffill()

    ma60 = closes.apply(calc_ma60)

    # 起始日期
    valid_start = ma60.dropna(how='all').index[0] if len(ma60.dropna(how='all')) > 0 else date_index[60]
    start_idx = date_index.get_loc(valid_start) if valid_start in date_index else 60

    trading_dates = date_index[start_idx:]
    fridays = trading_dates[trading_dates.weekday == 4]
    mondays = trading_dates[trading_dates.weekday == 0]

    # 周五-周一配对
    rebalance_pairs = []
    for fri in fridays:
        next_mondays = mondays[mondays > fri]
        if len(next_mondays) > 0:
            rebalance_pairs.append((fri, next_mondays[0]))

    if not rebalance_pairs:
        return None

    n_top = N_TOP
    equity = 1.0
    equity_curve = []
    equity_dates = []
    holdings = {}
    weekly_holdings_list = []
    turnover_count = 0
    late_entry_count = 0
    total_rebalance = 0

    prev_date = trading_dates[0]

    for date in trading_dates:
        # 检查是否为换仓日
        exec_pair = None
        for (fri, mon) in rebalance_pairs:
            if mon == date:
                exec_pair = (fri, mon)
                break

        if exec_pair:
            fri, mon = exec_pair
            total_rebalance += 1

            if fri in closes.index:
                fri_closes = closes.loc[fri]
                fri_opens = opens.loc[fri]
                fri_ma60 = ma60.loc[fri] if fri in ma60.index else None

                # 第一遍：收集基础评分 + 乖离率
                raw_scores = {}  # code -> (gain, up, deviation_20d)
                for code in closes.columns:
                    try:
                        price_now = fri_closes[code]
                        ma60_now = fri_ma60[code] if fri_ma60 is not None else np.nan

                        if pd.isna(price_now) or pd.isna(ma60_now) or price_now <= ma60_now:
                            continue

                        code_closes = closes[code].loc[:fri].dropna()
                        code_opens = opens[code].loc[:fri].dropna()

                        if len(code_closes) < 25 or len(code_opens) < 25:
                            continue

                        gain = calc_20d_gain(code_closes).iloc[-1]
                        up = calc_up_days(code_closes, code_opens).iloc[-1]

                        if pd.isna(gain) or pd.isna(up):
                            continue

                        # 计算乖离率
                        dev = None
                        if len(code_closes) >= 20:
                            dev_series = calc_deviation_rate(code_closes, ma_period=20)
                            if pd.notna(dev_series.iloc[-1]):
                                dev = float(dev_series.iloc[-1])

                        raw_scores[code] = (gain, up, dev)
                    except Exception:
                        continue

                # 第二遍：计算中位数并应用乖离率惩罚
                if raw_scores:
                    dev_values = [v[2] for v in raw_scores.values() if v[2] is not None]
                    median_dev = float(np.median(dev_values)) if dev_values else 0

                    # 计算最终评分
                    scored_items = {}  # code -> (gain, up, final_score)
                    for code, (gain, up, dev) in raw_scores.items():
                        base_score = w1 * gain + w2_val * up
                        if dev is not None and dev > median_dev:
                            penalty = w3 * (dev - median_dev)
                        else:
                            penalty = 0
                        final_score = base_score - penalty
                        scored_items[code] = (gain, up, final_score)

                    # 按板块分组取最佳（用 final_score）
                    sector_best = {}
                    for code, (gain, up, final_score) in scored_items.items():
                        sector = get_sector(etf_pool, code)
                        if sector not in sector_best or final_score > sector_best[sector][2]:
                            sector_best[sector] = (code, gain, up, final_score)

                    candidates = list(sector_best.values())
                    candidates.sort(key=lambda x: x[3], reverse=True)
                    top = candidates[:n_top]

                    new_holdings = {item[0]: 1.0 / n_top for item in top}

                    # 末段入场统计
                    for item in top:
                        if item[2] < 8:
                            late_entry_count += 1

                    # 换手计数
                    old_codes = set(holdings.keys())
                    new_codes = set(new_holdings.keys())
                    turnover_count += len(old_codes.symmetric_difference(new_codes))

                    holdings = new_holdings

                    # 记录持仓明细
                    holding_record = {
                        'date': mon.strftime('%Y-%m-%d'),
                        'week_fri': fri.strftime('%Y-%m-%d'),
                    }
                    for j, (code, gain, up, final_score) in enumerate(top):
                        name_row = etf_pool[etf_pool['code'] == code] if etf_pool is not None else None
                        name = name_row.iloc[0]['name'] if (name_row is not None and len(name_row) > 0) else code
                        holding_record[f'top{j+1}_code'] = code
                        holding_record[f'top{j+1}_name'] = name
                        holding_record[f'top{j+1}_gain'] = round(gain, 2)
                        holding_record[f'top{j+1}_updays'] = int(up)
                    weekly_holdings_list.append(holding_record)

        # 每日净值计算
        daily_ret = 0.0
        for code, weight in holdings.items():
            if code in closes.columns and date in closes.index:
                try:
                    p_today = closes.loc[date, code]
                    p_prev = closes.loc[prev_date, code] if prev_date in closes.index else p_today
                    if pd.notna(p_today) and pd.notna(p_prev) and p_prev > 0:
                        daily_ret += weight * (p_today / p_prev - 1)
                except Exception:
                    pass

        equity *= (1 + daily_ret)
        equity_curve.append(equity)
        equity_dates.append(date)
        prev_date = date

    equity_series = pd.Series(equity_curve, index=equity_dates)
    stats = calc_stats(equity_series, turnover_count, total_rebalance, late_entry_count, n_top)

    return {
        'equity_series': equity_series,
        'weekly_holdings': weekly_holdings_list,
        'stats': stats,
        'label': label,
        'scheme': scheme,
    }


# ============================================================
# 基准收益（沪深300 ETF 买入持有）
# ============================================================

def run_benchmark():
    """计算沪深300 ETF (510300) 基准收益曲线"""
    code = '510300'
    df = load_cached_data(code)
    if df is None or len(df) < 60:
        return None

    df = df.iloc[60:]  # 等 MA60 可用
    closes = df['close']
    equity = closes / closes.iloc[0]
    return equity


# ============================================================
# 完整回测（三方案 + 基准）
# ============================================================

def run_full_backtest(etf_pool=None, valid_codes=None):
    """
    运行完整回测：三方案 + 基准
    返回: {
        'results': {scheme: result_dict},
        'benchmark': pd.Series,
        'all_equity': [(equity_series, label), ...],
    }
    """
    if etf_pool is None:
        etf_pool, _ = get_etf_pool()

    if valid_codes is None:
        # 从 data/ 目录自动获取有效代码
        valid_codes = []
        for f in os.listdir(DATA_DIR):
            if f.endswith('.csv') and not f.startswith('.'):
                code = f.replace('.csv', '')
                if os.path.getsize(os.path.join(DATA_DIR, f)) > 100:
                    valid_codes.append(code)

    results = {}
    for scheme in ['A', 'B', 'C']:
        result = run_backtest(etf_pool, valid_codes, scheme)
        if result:
            results[scheme] = result

    benchmark = run_benchmark()

    # 构建 equity 列表（用于前端 Plotly 绘制）
    all_equity = []
    for scheme in ['A', 'B', 'C']:
        if scheme in results:
            r = results[scheme]
            all_equity.append((r['equity_series'], r['label']))
    if benchmark is not None:
        all_equity.append((benchmark, '沪深300基准'))

    return {
        'results': results,
        'benchmark': benchmark,
        'all_equity': all_equity,
    }


# ============================================================
# 回测结果持久化
# ============================================================

def save_equity_curves(all_equity):
    """将回测净值序列保存到数据库"""
    records = []
    scheme_map = {
        '方案A（激进）': 'A',
        '方案B（平衡）': 'B',
        '方案C（稳健）': 'C',
        '沪深300基准': 'benchmark',
    }
    for equity_series, label in all_equity:
        scheme = scheme_map.get(label, label)
        for date, nav in equity_series.items():
            records.append((date.strftime('%Y-%m-%d'), scheme, round(float(nav), 6)))

    if records:
        EquityCurveModel.save_batch(records)


def update_backtest_results():
    """
    运行完整回测并持久化结果（用于每日更新调用）
    返回 stats dict
    """
    full = run_full_backtest()
    if full and full['all_equity']:
        save_equity_curves(full['all_equity'])

    stats_summary = {}
    for scheme in ['A', 'B', 'C']:
        if scheme in full['results']:
            stats_summary[scheme] = full['results'][scheme]['stats']

    return stats_summary


# ============================================================
# V2 回测引擎 — 30万本金 Top3 等权滚动
# ============================================================

INITIAL_CAPITAL = 300000
N_TOP_V2 = 3

from db.models import EquityCurveV2Model, TradeHistoryModel


def _get_week_last_trade_day(date_index, week_start):
    """获取某周最后一个交易日（节假日自动顺延）"""
    # week_start 是一个周一（或该周第一个交易日），找到同一周内最晚的交易日
    week_end = week_start + timedelta(days=6)  # 周日
    mask = (date_index >= week_start) & (date_index <= week_end)
    candidates = date_index[mask]
    if len(candidates) == 0:
        return None
    return candidates[-1]


def _get_year_first_trade_day(date_index, year):
    """获取某年第一个交易日"""
    mask = (date_index.year == year)
    candidates = date_index[mask]
    if len(candidates) == 0:
        return None
    return candidates[0]


def _score_etfs_on_date(closes_df, opens_df, ma60_df, etf_pool, date, scheme, extra_name_map=None):
    """
    对给定日期进行评分，返回板块去重后的 Top3
    包含乖离率评分惩罚（第三因子）
    返回: list of {code, name, score, gain, up_days, deviation_20d, price}
    """
    w = WEIGHTS.get(scheme, WEIGHTS['B'])
    w1, w2_val = w['w1'], w['w2']
    w3 = DEVIATION_WEIGHTS.get(scheme, DEVIATION_WEIGHTS['B'])

    if date not in closes_df.index:
        return []

    closes_today = closes_df.loc[date]
    ma60_today = ma60_df.loc[date] if date in ma60_df.index else None

    scores = []
    deviations = {}  # 先收集所有乖离率

    for code in closes_df.columns:
        try:
            price_now = closes_today[code]
            ma60_now = ma60_today[code] if ma60_today is not None else np.nan

            if pd.isna(price_now):
                continue
            # MA60 可用时才做过滤：价格需在 MA60 之上
            if not pd.isna(ma60_now) and price_now <= ma60_now:
                continue

            code_closes = closes_df[code].loc[:date].dropna()
            code_opens = opens_df[code].loc[:date].dropna()

            if len(code_closes) < 25 or len(code_opens) < 25:
                continue

            gain = calc_20d_gain(code_closes).iloc[-1]
            up = calc_up_days(code_closes, code_opens).iloc[-1]

            if pd.isna(gain) or pd.isna(up):
                continue

            base_score = w1 * gain + w2_val * up

            # 计算乖离率
            dev = None
            if len(code_closes) >= 20:
                dev_series = calc_deviation_rate(code_closes, ma_period=20)
                if pd.notna(dev_series.iloc[-1]):
                    dev = round(float(dev_series.iloc[-1]), 4)
                    deviations[code] = dev

            # 获取名称
            name = code
            if extra_name_map and code in extra_name_map:
                name = extra_name_map[code]
            elif etf_pool is not None:
                row = etf_pool[etf_pool['code'] == code]
                if len(row) > 0:
                    name = row.iloc[0]['name']

            # 板块去重
            sector = classify_sector(name)

            scores.append({
                'code': code,
                'name': name,
                'sector': sector,
                'score': round(base_score, 2),
                'gain': round(gain, 2),
                'up_days': int(up),
                'price': float(price_now),
                'deviation_20d': dev,
            })
        except Exception:
            continue

    # 计算乖离率中位数并应用惩罚
    dev_values = [s['deviation_20d'] for s in scores if s['deviation_20d'] is not None]
    if dev_values:
        median_dev = float(np.median(dev_values))
        for s in scores:
            dev = s['deviation_20d']
            if dev is not None and dev > median_dev:
                penalty = w3 * (dev - median_dev)
                s['score'] = round(s['score'] - penalty, 2)
                s['deviation_penalty'] = round(penalty, 4)
            else:
                s['deviation_penalty'] = 0.0

    # 板块去重：每个板块只保留评分最高的
    sector_best = {}
    for s in scores:
        if s['sector'] not in sector_best or s['score'] > sector_best[s['sector']]['score']:
            sector_best[s['sector']] = s

    # 按评分排序取 Top3
    sorted_scores = sorted(sector_best.values(), key=lambda x: x['score'], reverse=True)
    return sorted_scores[:N_TOP_V2]


def run_backtest_v2(etf_pool=None, valid_codes=None, extra_name_map=None):
    """
    V2 回测：30万本金 Top3 等权滚动
    - 每周最后一个交易日换仓
    - 换仓价：当日收盘价
    - 等权分配：账户总资产 ÷ 3
    - 初始本金：300,000 元
    - 回测起始：本年度第一个交易日
    - 基准：30万买入沪深300（510300）持有不动

    返回: {
        'equity_records': [(date, scheme, total_asset, benchmark_asset), ...],
        'trade_records': [(date, action, code, name, price, shares, amount, scheme), ...],
        'stats': {scheme: {total_asset, cum_return, cum_pnl, max_dd, sharpe}},
    }
    """
    if etf_pool is None:
        etf_pool, _ = get_etf_pool()

    if valid_codes is None:
        valid_codes = []
        for f in os.listdir(DATA_DIR):
            if f.endswith('.csv') and not f.startswith('.'):
                code = f.replace('.csv', '')
                if os.path.getsize(os.path.join(DATA_DIR, f)) > 100:
                    valid_codes.append(code)

    data = load_etf_data(valid_codes)
    if not data:
        return None

    # 对齐日期
    all_dates = sorted(set().union(*[set(df.index) for df in data.values()]))
    date_index = pd.DatetimeIndex(all_dates)

    closes = pd.DataFrame(index=date_index)
    opens = pd.DataFrame(index=date_index)
    for code, df in data.items():
        closes[code] = df['close'].reindex(date_index).ffill()
        opens[code] = df['open'].reindex(date_index).ffill()

    ma60 = closes.apply(calc_ma60)
    ma5 = closes.apply(calc_ma5)

    # 基准：510300
    benchmark_data = load_cached_data('510300')
    if benchmark_data is not None:
        benchmark_data = benchmark_data.reindex(date_index).ffill()
        benchmark_closes = benchmark_data['close'] if 'close' in benchmark_data.columns else None
    else:
        benchmark_closes = None

    # 回测起始：本年度第一个交易日
    current_year = datetime.now().year
    year_start = _get_year_first_trade_day(date_index, current_year)
    if year_start is None:
        return None

    # MA60 可用的 ETF 在评分时单独判断，不延迟整体起始日

    # 筛选回测期间的交易日
    trading_dates = date_index[date_index >= year_start]

    # 计算每周最后一个交易日
    # 用 isocalendar 按周分组
    week_last_days = []
    current_week = None
    for d in trading_dates:
        week_num = d.isocalendar()[1]
        year_num = d.isocalendar()[0]
        week_key = (year_num, week_num)
        if week_key != current_week:
            if current_week is not None:
                week_last_days.append(prev_date)
            current_week = week_key
        prev_date = d
    # 别忘了最后一周
    if current_week is not None:
        week_last_days.append(prev_date)

    week_last_days = pd.DatetimeIndex(week_last_days)

    # 对三个方案分别运行
    all_equity_records = []
    all_trade_records = []
    stats_summary = {}

    for scheme in ['A', 'B', 'C']:
        # 清除旧数据
        TradeHistoryModel.clear_scheme(scheme)

        # 初始化持仓
        holdings = {}  # code -> {'shares': float, 'cost_price': float, 'name': str}
        cash = INITIAL_CAPITAL  # 账户现金
        total_asset = INITIAL_CAPITAL

        # 初始化分层止损管理器
        stop_loss_mgr = LayeredStopLossManager()

        # 获取基准初始价格
        if benchmark_closes is not None and year_start in benchmark_closes.index:
            benchmark_init_price = benchmark_closes[year_start]
            benchmark_shares = INITIAL_CAPITAL / benchmark_init_price if benchmark_init_price > 0 else 0
        else:
            benchmark_init_price = None
            benchmark_shares = 0

        equity_records_scheme = []
        trade_records_scheme = []

        # 首次建仓：在回测起始日之后的第一个换仓日
        first_rebalance = True
        prev_date = trading_dates[0]

        def _calc_market_value(holdings_dict, as_of_date):
            """计算持仓市值"""
            mv = 0
            for code, h in holdings_dict.items():
                if code in closes.columns and as_of_date in closes.index:
                    price = closes.loc[as_of_date, code]
                    if pd.notna(price):
                        mv += h['shares'] * price
                    else:
                        mv += h['shares'] * h['cost_price']
                else:
                    mv += h['shares'] * h['cost_price']
            return mv

        for date in trading_dates:
            is_rebalance_day = date in week_last_days

            if is_rebalance_day:
                # 计算当前持仓市值
                market_value = _calc_market_value(holdings, date)

                # total_asset = 持仓市值 + 现金
                total_asset = market_value + cash

                # ====== 换仓日风控检查（卖旧仓后、买新仓前） ======
                if holdings and not first_rebalance:
                    price_data = {}
                    ma5_data = {}
                    for code in holdings:
                        if code in closes.columns and date in closes.index:
                            p = closes.loc[date, code]
                            if pd.notna(p):
                                price_data[code] = float(p)
                        if code in ma5.columns and date in ma5.index:
                            m5 = ma5.loc[date, code]
                            if pd.notna(m5):
                                ma5_data[code] = float(m5)

                    risk_result = stop_loss_mgr.run_daily_check(
                        holdings, price_data, ma5_data, total_asset,
                        date=date.strftime('%Y-%m-%d')
                    )

                    if risk_result['final_action'] == 'full_liquidate':
                        # 组合止损：全部清仓，回笼现金
                        for code, h in holdings.items():
                            sell_price = price_data.get(code, h['cost_price'])
                            sell_amount = h['shares'] * sell_price
                            cash += sell_amount
                            trade_records_scheme.append((
                                date.strftime('%Y-%m-%d'), 'sell', code, h['name'],
                                round(float(sell_price), 4), round(h['shares'], 2),
                                round(sell_amount, 2), scheme
                            ))
                        holdings = {}
                        market_value = 0
                        total_asset = cash

                    elif risk_result['final_action'] == 'partial_reduce':
                        # 个股止损：减仓，回笼现金
                        for code, reduce_to in risk_result['reduce_codes'].items():
                            if code in holdings:
                                h = holdings[code]
                                target_shares = round(h['shares'] * reduce_to, 2)
                                sell_shares = round(h['shares'] - target_shares, 2)
                                sell_price = price_data.get(code, h['cost_price'])
                                sell_amount = round(sell_shares * sell_price, 2)
                                cash += sell_amount
                                trade_records_scheme.append((
                                    date.strftime('%Y-%m-%d'), 'sell', code, h['name'],
                                    round(float(sell_price), 4), sell_shares,
                                    sell_amount, scheme
                                ))
                                holdings[code] = {
                                    'shares': target_shares,
                                    'cost_price': h['cost_price'],
                                    'name': h['name'],
                                }
                        market_value = _calc_market_value(holdings, date)
                        total_asset = market_value + cash
                # ====== 换仓日风控检查结束 ======

                # 评分选股
                top3 = _score_etfs_on_date(closes, opens, ma60, etf_pool, date, scheme, extra_name_map)

                # 组合止损触发后，保守再入场判断
                if not stop_loss_mgr.portfolio_stop.should_reentry(top3):
                    top3 = []  # 不入场，保持空仓

                if top3:
                    old_codes = set(holdings.keys())
                    new_codes = set(t['code'] for t in top3)
                    target_per_etf = total_asset / len(top3)  # 按实际数量等权分配

                    # 卖出：不在新 Top3 中的，回笼现金
                    for code in old_codes - new_codes:
                        price = closes.loc[date, code] if (code in closes.columns and date in closes.index) else holdings[code]['cost_price']
                        amount = holdings[code]['shares'] * price
                        cash += amount  # 卖出回笼现金
                        trade_records_scheme.append((
                            date.strftime('%Y-%m-%d'), 'sell', code,
                            holdings[code]['name'], round(float(price), 4),
                            round(holdings[code]['shares'], 2), round(amount, 2), scheme
                        ))

                    # 新建/调整
                    new_holdings = {}
                    for t in top3:
                        code = t['code']
                        name = t['name']
                        price = t['price']  # 已从 closes 取出
                        if pd.isna(price) or price <= 0:
                            continue

                        shares = target_per_etf / price

                        if code in old_codes:
                            # 继续持有（调整仓位）
                            old_shares = holdings[code]['shares']
                            shares_diff = shares - old_shares
                            # 调仓：买入差额用现金，卖出差额回笼现金
                            cash -= shares_diff * price
                            action = 'hold'
                            # 仍记录一笔 hold 以追踪
                            trade_records_scheme.append((
                                date.strftime('%Y-%m-%d'), action, code, name,
                                round(float(price), 4), round(shares, 2),
                                round(target_per_etf, 2), scheme
                            ))
                        else:
                            # 新买入
                            cash -= target_per_etf  # 扣除买入金额
                            action = 'buy'
                            trade_records_scheme.append((
                                date.strftime('%Y-%m-%d'), action, code, name,
                                round(float(price), 4), round(shares, 2),
                                round(target_per_etf, 2), scheme
                            ))

                        new_holdings[code] = {
                            'shares': round(shares, 2),
                            'cost_price': round(float(price), 4),
                            'name': name,
                        }

                    holdings = new_holdings
                    first_rebalance = False

                    # 换仓日重置风控追踪状态
                    stop_loss_mgr.on_rebalance(list(holdings.keys()))

            # 每日计算账户市值
            if not first_rebalance:
                market_value = _calc_market_value(holdings, date)
                total_asset = market_value + cash

                # ====== 每日风控检查（非换仓日执行，换仓日在上方已检查） ======
                if holdings and not is_rebalance_day:
                    price_data = {}
                    ma5_data = {}
                    for code in holdings:
                        if code in closes.columns and date in closes.index:
                            p = closes.loc[date, code]
                            if pd.notna(p):
                                price_data[code] = float(p)
                        if code in ma5.columns and date in ma5.index:
                            m5 = ma5.loc[date, code]
                            if pd.notna(m5):
                                ma5_data[code] = float(m5)

                    risk_result = stop_loss_mgr.run_daily_check(
                        holdings, price_data, ma5_data, total_asset,
                        date=date.strftime('%Y-%m-%d')
                    )

                    if risk_result['final_action'] == 'full_liquidate':
                        # 组合止损：全部清仓，回笼现金
                        for code, h in holdings.items():
                            sell_price = price_data.get(code, h['cost_price'])
                            sell_amount = h['shares'] * sell_price
                            cash += sell_amount  # 清仓回笼现金
                            trade_records_scheme.append((
                                date.strftime('%Y-%m-%d'), 'sell', code, h['name'],
                                round(float(sell_price), 4), round(h['shares'], 2),
                                round(sell_amount, 2), scheme
                            ))
                        holdings = {}
                        market_value = 0
                        total_asset = cash  # 清仓后资产 = 现金

                    elif risk_result['final_action'] == 'partial_reduce':
                        # 个股止损：减仓，回笼现金
                        for code, reduce_to in risk_result['reduce_codes'].items():
                            if code in holdings:
                                h = holdings[code]
                                target_shares = round(h['shares'] * reduce_to, 2)
                                sell_shares = round(h['shares'] - target_shares, 2)
                                sell_price = price_data.get(code, h['cost_price'])
                                sell_amount = round(sell_shares * sell_price, 2)
                                cash += sell_amount  # 减仓回笼现金
                                trade_records_scheme.append((
                                    date.strftime('%Y-%m-%d'), 'sell', code, h['name'],
                                    round(float(sell_price), 4), sell_shares,
                                    sell_amount, scheme
                                ))
                                # 更新持仓
                                holdings[code] = {
                                    'shares': target_shares,
                                    'cost_price': h['cost_price'],
                                    'name': h['name'],
                                }
                        # 重新计算总资产
                        market_value = _calc_market_value(holdings, date)
                        total_asset = market_value + cash

                # ====== 风控检查结束 ======

            elif first_rebalance:
                total_asset = INITIAL_CAPITAL

            # 基准市值
            benchmark_asset = INITIAL_CAPITAL
            if benchmark_closes is not None and date in benchmark_closes.index and benchmark_init_price and benchmark_init_price > 0:
                benchmark_asset = benchmark_shares * benchmark_closes[date]

            equity_records_scheme.append((
                date.strftime('%Y-%m-%d'), scheme,
                round(total_asset, 2), round(benchmark_asset, 2)
            ))

            prev_date = date

        all_equity_records.extend(equity_records_scheme)
        all_trade_records.extend(trade_records_scheme)

        # 统计指标
        asset_values = [r[2] for r in equity_records_scheme]
        if asset_values:
            cum_ret = (asset_values[-1] / asset_values[0] - 1) * 100
            peak = asset_values[0]
            max_dd = 0
            for v in asset_values:
                if v > peak:
                    peak = v
                dd = (v - peak) / peak
                if dd < max_dd:
                    max_dd = dd
            daily_rets = np.diff(asset_values) / asset_values[:-1]
            rf_daily = 0.03 / 252
            excess = daily_rets - rf_daily
            sharpe = float(np.sqrt(252) * excess.mean() / excess.std()) if excess.std() > 0 else 0
            stats_summary[scheme] = {
                'total_asset': round(float(asset_values[-1]), 2),
                'cum_return': round(float(cum_ret), 2),
                'cum_pnl': round(float(asset_values[-1] - asset_values[0]), 2),
                'max_dd': round(float(max_dd * 100), 2),
                'sharpe': round(float(sharpe), 2),
                'latest_date': equity_records_scheme[-1][0] if equity_records_scheme else None,
                'benchmark_asset': round(float(equity_records_scheme[-1][3]), 2) if equity_records_scheme else INITIAL_CAPITAL,
            }

    return {
        'equity_records': all_equity_records,
        'trade_records': all_trade_records,
        'stats': stats_summary,
    }


def update_backtest_v2(extra_name_map=None):
    """
    运行 V2 回测并持久化结果（用于每日更新调用）
    返回 stats dict
    """
    result = run_backtest_v2(extra_name_map=extra_name_map)
    if result is None:
        return {}

    # 清除旧数据
    conn = get_conn()
    try:
        for scheme in ['A', 'B', 'C']:
            conn.execute("DELETE FROM equity_curve_v2 WHERE scheme = ?", (scheme,))
            conn.execute("DELETE FROM trade_history WHERE scheme = ?", (scheme,))
        conn.commit()
    finally:
        conn.close()

    # 保存净值曲线
    if result['equity_records']:
        EquityCurveV2Model.save_batch(result['equity_records'])

    # 保存换仓记录
    if result['trade_records']:
        TradeHistoryModel.save_batch(result['trade_records'])

    return result['stats']