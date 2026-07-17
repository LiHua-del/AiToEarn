"""
AiToEarn 定时调度器
使用 schedule 库，在 Flask 应用线程中运行
每个交易日两次触发：14:30 盘中 + 15:30 收盘后
"""

import threading
import time
from datetime import datetime

import schedule

from core.data_fetcher import is_trade_day
from daily_update import run_update


class Scheduler:
    """定时调度器，作为独立线程运行"""

    def __init__(self):
        self.running = False
        self.thread = None

    def start(self):
        """启动调度线程"""
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        print("[调度器] 已启动 — 每个交易日 14:30 盘中 + 15:30 收盘后")

    def stop(self):
        """停止调度器"""
        self.running = False
        print("[调度器] 已停止")

    def _run(self):
        """调度主循环"""
        # 注册定时任务
        schedule.every().day.at("14:30").do(self._intraday_job)
        schedule.every().day.at("15:30").do(self._close_job)

        while self.running:
            schedule.run_pending()
            time.sleep(30)  # 每30秒检查一次

    def _intraday_job(self):
        """14:30 盘中拉取"""
        if self._should_skip():
            return
        print(f"\n{'='*40}")
        print(f"  [调度] 14:30 盘中自动拉取")
        print(f"{'='*40}")
        try:
            result = run_update(session='intraday')
            print(f"  [调度] 盘中拉取完成: {result.get('status')}")
        except Exception as e:
            print(f"  [调度] 盘中拉取失败: {e}")

    def _close_job(self):
        """15:30 收盘后拉取"""
        if self._should_skip():
            return
        print(f"\n{'='*40}")
        print(f"  [调度] 15:30 收盘后自动拉取")
        print(f"{'='*40}")
        try:
            result = run_update(session='final')
            print(f"  [调度] 收盘后拉取完成: {result.get('status')}")
        except Exception as e:
            print(f"  [调度] 收盘后拉取失败: {e}")

    def _should_skip(self):
        """检查是否应该跳过（非交易日）"""
        if not is_trade_day():
            print(f"[调度] {datetime.now().strftime('%Y-%m-%d %H:%M')} 非交易日，跳过")
            return True
        return False


# 全局调度器实例
_scheduler = Scheduler()


def start_scheduler():
    """启动全局调度器"""
    _scheduler.start()


def stop_scheduler():
    """停止全局调度器"""
    _scheduler.stop()