"""收益曲线 API"""
import plotly.graph_objs as go
import plotly.utils
import json
from flask import Blueprint, jsonify, request
from db.models import EquityCurveModel, EquityCurveV2Model

equity_bp = Blueprint('equity', __name__)


@equity_bp.route('/equity/data')
def get_equity_data():
    """返回净值序列原始数据（JSON）"""
    scheme = request.args.get('scheme')
    rows = EquityCurveModel.get_all(scheme=scheme)

    # 按 scheme 分组
    curves = {}
    for r in rows:
        s = r['scheme']
        if s not in curves:
            curves[s] = {'dates': [], 'navs': []}
        curves[s]['dates'].append(r['date'])
        curves[s]['navs'].append(r['nav'])

    return jsonify(curves)


@equity_bp.route('/equity/chart')
def get_equity_chart():
    """返回 Plotly 图表 JSON"""
    rows = EquityCurveModel.get_all()
    if not rows:
        return jsonify({'error': '暂无回测数据'}), 404

    # 按 scheme 分组
    curves = {}
    for r in rows:
        s = r['scheme']
        if s not in curves:
            curves[s] = {'dates': [], 'navs': []}
        curves[s]['dates'].append(r['date'])
        curves[s]['navs'].append(r['nav'])

    color_map = {'A': '#e74c3c', 'B': '#3498db', 'C': '#2ecc71', 'benchmark': '#95a5a6'}
    name_map = {'A': '方案A（激进）', 'B': '方案B（平衡）', 'C': '方案C（稳健）', 'benchmark': '沪深300基准'}

    fig = go.Figure()
    for scheme in ['A', 'B', 'C', 'benchmark']:
        if scheme not in curves:
            continue
        c = curves[scheme]
        fig.add_trace(go.Scatter(
            x=c['dates'],
            y=c['navs'],
            mode='lines',
            name=name_map.get(scheme, scheme),
            line={'color': color_map.get(scheme, '#95a5a6'), 'width': 2},
            hovertemplate='%{x|%Y-%m-%d}<br>净值: %{y:.4f}<extra></extra>',
        ))

    fig.update_layout(
        title='ETF 板块轮动策略 — 累计收益曲线',
        xaxis_title='日期',
        yaxis_title='净值',
        hovermode='x unified',
        template='plotly_white',
        legend=dict(orientation='h', yanchor='bottom', y=1.02, xanchor='right', x=1),
        margin=dict(l=40, r=40, t=60, b=40),
        height=450,
    )

    graph_json = json.dumps(fig, cls=plotly.utils.PlotlyJSONEncoder)
    return jsonify({'chart': graph_json})


@equity_bp.route('/equity/v2')
def get_equity_v2():
    """V2 回测数据接口（30万本金 Top3）"""
    scheme = request.args.get('scheme', 'B').upper()
    range_param = request.args.get('range', 'all').lower()

    # 计算起止日期
    start_date = None
    if range_param == 'ytd':
        start_date = f'{datetime.now().year}-01-01'
    elif range_param == '3m':
        from datetime import timedelta
        start_date = (datetime.now() - timedelta(days=90)).strftime('%Y-%m-%d')

    rows = EquityCurveV2Model.get_all(scheme=scheme, start_date=start_date)

    # 统计指标
    stats = EquityCurveV2Model.get_stats(scheme=scheme)

    dates = [r['date'] for r in rows]
    total_assets = [r['total_asset'] for r in rows]
    benchmark_assets = [r['benchmark_asset'] for r in rows]

    return jsonify({
        'scheme': scheme,
        'range': range_param,
        'count': len(rows),
        'dates': dates,
        'total_assets': total_assets,
        'benchmark_assets': benchmark_assets,
        'stats': stats,
    })


from datetime import datetime
