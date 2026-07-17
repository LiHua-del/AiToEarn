"""V2 回测换仓历史 API"""
from datetime import datetime, timedelta
from flask import Blueprint, jsonify, request
from db.models import TradeHistoryModel, EquityCurveV2Model, ScoreHistoryModel
from core.data_fetcher import get_latest_trade_day, is_trade_day
from core.indicators import select_top_n
from core.data_fetcher import load_cached_data

trade_bp = Blueprint('trade', __name__)


@trade_bp.route('/trade/history')
def get_trade_history():
    """获取换仓历史记录"""
    scheme = request.args.get('scheme', 'B').upper()
    limit = int(request.args.get('limit', 50))

    rows = TradeHistoryModel.get_history(scheme=scheme, limit=limit)

    # 按日期分组
    history_by_date = {}
    for r in rows:
        d = r['date']
        if d not in history_by_date:
            history_by_date[d] = {'date': d, 'buys': [], 'sells': [], 'holds': []}
        item = {
            'code': r['etf_code'],
            'name': r['etf_name'],
            'price': r['price'],
            'shares': r['shares'],
            'amount': r['amount'],
        }
        if r['action'] == 'buy':
            history_by_date[d]['buys'].append(item)
        elif r['action'] == 'sell':
            history_by_date[d]['sells'].append(item)
        elif r['action'] == 'hold':
            history_by_date[d]['holds'].append(item)

    # 按日期倒序
    result = sorted(history_by_date.values(), key=lambda x: x['date'], reverse=True)

    # 追加当日账户总资产（从 equity_curve_v2 取）
    date_set = sorted(set(r['date'] for r in rows), reverse=True)
    asset_map = {}
    if date_set:
        v2_rows = EquityCurveV2Model.get_all(scheme=scheme)
        for r in v2_rows:
            asset_map[r['date']] = r['total_asset']

    for item in result:
        item['total_asset'] = asset_map.get(item['date'])

    return jsonify({
        'scheme': scheme,
        'count': len(result),
        'history': result,
    })


@trade_bp.route('/trade/current')
def get_current_holdings():
    """获取当前持仓（最新一次换仓后的状态）"""
    scheme = request.args.get('scheme', 'B').upper()

    holdings, latest_date = TradeHistoryModel.get_current_holdings_with_price(scheme=scheme)

    # 计算浮盈浮亏%：基于成本价（上次换仓价）与当前最新价
    for h in holdings:
        cost_price = h.get('price', 0)  # 换仓时的成交价即成本价
        current_price = _get_latest_price(h['etf_code'])
        h['cost_price'] = cost_price
        h['current_price'] = current_price
        if cost_price and cost_price > 0 and current_price and current_price > 0:
            h['pnl_pct'] = round((current_price / cost_price - 1) * 100, 2)
        else:
            h['pnl_pct'] = 0

    # 获取最新评分排行，判断哪些是新进入的
    scores, score_date = ScoreHistoryModel.get_latest(session='final', scheme=scheme, limit=15)
    current_top3_codes = set()
    if scores:
        top3 = select_top_n(scores, n=3)
        current_top3_codes = {s['code'] for s in top3}

    holding_codes = {h['etf_code'] for h in holdings}

    # 判断持仓状态
    for h in holdings:
        code = h['etf_code']
        if code in current_top3_codes:
            h['status'] = 'hold'  # 继续持有
        else:
            h['status'] = 'sell'  # 本周卖出

    # 新进入的（在 top3 但不在当前持仓中）
    new_codes = current_top3_codes - holding_codes
    new_entries = []
    for s in scores:
        if s['code'] in new_codes:
            new_entries.append({
                'code': s['code'],
                'name': s.get('name', s['code']),
                'status': 'new',
                'score': s.get(f'score_{scheme.lower()}', 0),
            })

    # 获取换仓信息
    # 下次换仓日：下周最后一个交易日
    next_rebalance = _get_next_rebalance_day()

    return jsonify({
        'scheme': scheme,
        'date': latest_date,
        'holdings': holdings,
        'new_entries': new_entries,
        'next_rebalance': next_rebalance,
    })


@trade_bp.route('/trade/next')
def get_next_rebalance():
    """获取下次换仓日期"""
    return jsonify(_get_next_rebalance_day())


def _get_next_rebalance_day():
    """计算下次换仓日期（本周五未到则显示本周五，周六/周日跳到下周五）"""
    now = datetime.now()

    # 计算本周五
    days_since_monday = now.weekday()  # 0=周一 ... 4=周五 ... 6=周日
    this_friday = now + timedelta(days=(4 - days_since_monday))

    # 如果今天是周五，判断是否已过收盘（15:30）
    if days_since_monday == 4 and now.hour >= 16:
        # 周五已收盘，换仓日是下周五
        next_friday = this_friday + timedelta(days=7)
    elif days_since_monday >= 5:
        # 周六/周日，换仓日是下周五
        next_friday = this_friday + timedelta(days=7)
    else:
        # 周一~周四，或周五收盘前，换仓日是本周五
        next_friday = this_friday

    # 使用交易日历确认（处理节假日）
    try:
        latest_trade_day = get_latest_trade_day(next_friday)
        # 如果当日不是交易日，往前找到当周最后一个交易日
        next_rebalance_date = latest_trade_day
    except Exception:
        next_rebalance_date = next_friday.strftime('%Y-%m-%d')

    # 计算距今天数
    try:
        target = datetime.strptime(next_rebalance_date, '%Y-%m-%d')
        days_away = (target - now).days
    except Exception:
        days_away = 0

    weekday_names = ['一', '二', '三', '四', '五', '六', '日']

    return {
        'date': next_rebalance_date,
        'weekday': '周' + weekday_names[datetime.strptime(next_rebalance_date, '%Y-%m-%d').weekday()] if next_rebalance_date else '',
        'days_away': days_away,
    }


def _get_latest_price(etf_code):
    """从缓存数据获取 ETF 最新收盘价"""
    try:
        df = load_cached_data(etf_code)
        if df is not None and len(df) > 0:
            return round(float(df['close'].iloc[-1]), 4)
    except Exception:
        pass
    return None
