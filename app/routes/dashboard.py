"""仪表盘首页"""
from flask import Blueprint, render_template
from datetime import datetime
from db.models import ScoreHistoryModel, EquityCurveModel, DataQualityLogModel

dashboard_bp = Blueprint('dashboard', __name__)


@dashboard_bp.route('/')
def index():
    # 获取最新评分日期
    scores_final, latest_date = ScoreHistoryModel.get_latest(session='final', limit=15)
    scores_intra, intra_date = ScoreHistoryModel.get_latest(session='intraday', limit=15)

    # 数据质量状态
    quality_logs = DataQualityLogModel.get_latest(limit=5)
    latest_quality = quality_logs[0] if quality_logs else None

    # 回测最新日期
    equity_latest = EquityCurveModel.get_latest_date()

    return render_template(
        'dashboard.html',
        latest_date=latest_date,
        intra_date=intra_date,
        quality=latest_quality,
        equity_latest=equity_latest,
        scores_final=scores_final,
        scores_intra=scores_intra,
    )