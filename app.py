#!/usr/bin/env python3
"""
AiToEarn Flask Web 应用启动入口
启动方式: python app.py
"""
import sys
import os

# 确保项目根目录在 path 中
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import create_app
from db.models import init_db
from core.scheduler import start_scheduler
from config import FLASK_HOST, FLASK_PORT, FLASK_DEBUG

# 初始化数据库
print("[启动] 初始化数据库...")
init_db()

# 创建 Flask 应用
app = create_app()

# 启动调度器
print("[启动] 启动定时调度...")
start_scheduler()

print(f"""
╔══════════════════════════════════════════════╗
║     AiToEarn ETF 板块轮动仪表盘             ║
║                                              ║
║  本地地址: http://{FLASK_HOST}:{FLASK_PORT}              ║
║  仪表盘:   http://{FLASK_HOST}:{FLASK_PORT}/             ║
║                                              ║
║  定时任务: 每个交易日 14:30 + 15:30         ║
║  手动刷新: 点击页面 🔄 按钮                 ║
╚══════════════════════════════════════════════╝
""")

if __name__ == '__main__':
    host = os.environ.get('HOST', FLASK_HOST)
    port = int(os.environ.get('PORT', FLASK_PORT))
    app.run(host=host, port=port, debug=FLASK_DEBUG)