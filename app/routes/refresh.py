"""手动刷新 API（异步：后台线程执行，立即返回）"""
import threading
from flask import Blueprint, jsonify
from datetime import datetime

from core.data_fetcher import is_trade_day, is_trading_hours, is_after_close, get_latest_trade_day
from daily_update import run_update

refresh_bp = Blueprint('refresh', __name__)

_refresh_lock = threading.Lock()
_refresh_status = {'running': False, 'last_result': None, 'last_time': None}


@refresh_bp.route('/refresh', methods=['POST'])
def trigger_refresh():
    """手动触发刷新 — 立即返回，后台线程执行更新"""
    global _refresh_status

    with _refresh_lock:
        if _refresh_status['running']:
            return jsonify({
                'status': 'busy',
                'message': '正在刷新中，请稍候...',
                'last_time': _refresh_status['last_time'],
            })

        now = datetime.now()

        # 判断是否交易日
        if not is_trade_day(now):
            latest = get_latest_trade_day(now)
            return jsonify({
                'status': 'skip',
                'message': '今日非交易日，最近交易日为 ' + latest,
                'latest_trade_day': latest,
            })

        # 判断时段：交易时间内用盘中模式，收盘后用完整模式（含回测）
        if is_trading_hours():
            session = 'intraday'
            session_label = '盘中'
        else:
            session = 'final'
            session_label = '收盘后'

        _refresh_status['running'] = True
        _refresh_status['last_time'] = now.strftime('%Y-%m-%d %H:%M:%S')

    def _run():
        try:
            result = run_update(session=session, target_date=now)
            _refresh_status['last_result'] = result
        except Exception as e:
            _refresh_status['last_result'] = {'status': 'error', 'error': str(e)[:200]}
        finally:
            _refresh_status['running'] = False
            _refresh_status['last_time'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    threading.Thread(target=_run, daemon=True).start()

    return jsonify({
        'status': 'started',
        'message': f'{session_label}刷新已在后台启动，页面将在完成后自动更新...',
        'session': session,
    })


@refresh_bp.route('/refresh/status')
def get_refresh_status():
    """获取刷新状态（前端轮询用）"""
    last = _refresh_status['last_result'] or {}
    return jsonify({
        'running': _refresh_status['running'],
        'last_time': _refresh_status['last_time'],
        'is_trade_day': is_trade_day(),
        'is_trading_hours': is_trading_hours(),
        'last_result_summary': {
            'status': last.get('status'),
            'pool_size': last.get('pool_size', 0),
            'scores_count': last.get('scores_count', 0),
        } if last else None,
    })
