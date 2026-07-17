"""手动刷新 API"""
import threading
from flask import Blueprint, jsonify, request
from datetime import datetime

from core.data_fetcher import is_trade_day, is_trading_hours, is_after_close, get_latest_trade_day
from daily_update import run_update

refresh_bp = Blueprint('refresh', __name__)

# 刷新锁（防止并发刷新）
refresh_lock = threading.Lock()
_refresh_status = {'running': False, 'last_result': None, 'last_time': None}


@refresh_bp.route('/refresh', methods=['POST'])
def trigger_refresh():
    """手动触发刷新"""
    global _refresh_status

    if _refresh_status['running']:
        return jsonify({
            'status': 'busy',
            'message': '正在刷新，请稍后再试...',
            'last_time': _refresh_status['last_time'],
        })

    with refresh_lock:
        _refresh_status['running'] = True
        try:
            now = datetime.now()

            # 判断是否交易日
            if not is_trade_day(now):
                latest = get_latest_trade_day(now)
                _refresh_status['running'] = False
                _refresh_status['last_time'] = now.strftime('%Y-%m-%d %H:%M:%S')
                return jsonify({
                    'status': 'skip',
                    'message': '今日非交易日，最近交易日为 ' + latest,
                    'latest_trade_day': latest,
                })

            # 判断时段
            if is_trading_hours() or (not is_after_close()):
                session = 'intraday'
                session_label = '盘中'
            else:
                session = 'final'
                session_label = '收盘后'

            # 执行更新
            result = run_update(session=session, target_date=now)

            _refresh_status['running'] = False
            _refresh_status['last_result'] = result
            _refresh_status['last_time'] = now.strftime('%Y-%m-%d %H:%M:%S')

            return jsonify({
                'status': 'ok',
                'message': session_label + '刷新完成',
                'session': session,
                'result': {
                    'pool_size': result.get('pool_size', 0),
                    'data_success': result.get('data_success', 0),
                    'data_fail': result.get('data_fail', 0),
                    'scores_count': result.get('scores_count', 0),
                },
            })
        except Exception as e:
            _refresh_status['running'] = False
            return jsonify({
                'status': 'error',
                'message': '刷新失败: ' + str(e)[:200],
            })


@refresh_bp.route('/refresh/status')
def get_refresh_status():
    """获取刷新状态"""
    return jsonify({
        'running': _refresh_status['running'],
        'last_time': _refresh_status['last_time'],
        'is_trade_day': is_trade_day(),
        'is_trading_hours': is_trading_hours(),
        'last_result_summary': {
            'pool_size': _refresh_status['last_result'].get('pool_size', 0),
            'scores_count': _refresh_status['last_result'].get('scores_count', 0),
        } if _refresh_status['last_result'] else None,
    })