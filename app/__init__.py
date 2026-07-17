"""
Flask 应用工厂
"""
from flask import Flask
import os
import sys

# 确保项目根目录在 path 中
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import FLASK_HOST, FLASK_PORT, FLASK_DEBUG


def create_app():
    app = Flask(__name__)
    app.config['SECRET_KEY'] = 'aitorearn-secret-key-' + os.urandom(12).hex()

    # 注册路由
    from app.routes.dashboard import dashboard_bp
    from app.routes.ranking import ranking_bp
    from app.routes.equity import equity_bp
    from app.routes.refresh import refresh_bp
    from app.routes.trade_history import trade_bp

    app.register_blueprint(dashboard_bp)
    app.register_blueprint(ranking_bp, url_prefix='/api')
    app.register_blueprint(equity_bp, url_prefix='/api')
    app.register_blueprint(refresh_bp, url_prefix='/api')
    app.register_blueprint(trade_bp, url_prefix='/api')

    return app