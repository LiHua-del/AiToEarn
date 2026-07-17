"""ETF 排行 API"""
from flask import Blueprint, jsonify, request
from db.models import ScoreHistoryModel

ranking_bp = Blueprint('ranking', __name__)


@ranking_bp.route('/ranking')
def get_ranking():
    scheme = request.args.get('scheme', 'B').upper()
    session = request.args.get('session', 'final')
    limit = int(request.args.get('limit', 0))

    scores, latest_date = ScoreHistoryModel.get_latest(session=session, scheme=scheme, limit=limit)

    return jsonify({
        'date': latest_date,
        'session': session,
        'scheme': scheme,
        'count': len(scores),
        'scores': scores,
    })


@ranking_bp.route('/ranking/history')
def get_ranking_history():
    code = request.args.get('code')
    session = request.args.get('session', 'final')
    days = int(request.args.get('days', 90))

    if not code:
        return jsonify({'error': '缺少 code 参数'}), 400

    history = ScoreHistoryModel.get_history(code, session=session, days=days)
    return jsonify({
        'code': code,
        'count': len(history),
        'history': history,
    })


@ranking_bp.route('/ranking/top')
def get_top_holdings():
    """获取 Top 8（用于持仓推荐）"""
    scheme = request.args.get('scheme', 'B').upper()
    session = request.args.get('session', 'final')

    scores, latest_date = ScoreHistoryModel.get_latest(session=session, scheme=scheme, limit=8)

    # 板块去重（前端也会做，这里做一层保障）
    seen = set()
    top = []
    for s in scores:
        sector = s.get('sector', '')
        if sector not in seen:
            seen.add(sector)
            top.append(s)
        if len(top) >= 8:
            break

    return jsonify({
        'date': latest_date,
        'session': session,
        'scheme': scheme,
        'top': top,
    })