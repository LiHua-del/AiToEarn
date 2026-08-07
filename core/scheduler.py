"""
AiToEarn 定时调度器
- 每个交易日 14:30 盘中 + 15:30 收盘后自动更新
- 启动时检测数据是否落后最新交易日，落后则后台自动补充
"""

import threading
import time
from datetime import datetime

import schedule

from core.data_fetcher import is_trade_day, get_latest_trade_day
from daily_update import run_update


class Scheduler:
    """定时调度器，作为独立线程运行"""

    def __init__(self):
        self.running = False
        self.thread = None

    def start(self):
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        print("[调度器] 已启动 — 每个交易日 14:30 盘中 + 15:30 收盘后")

    def stop(self):
        self.running = False
        print("[调度器] 已停止")

    def _run(self):
        schedule.every().day.at("14:30").do(self._intraday_job)
        schedule.every().day.at("15:30").do(self._close_job)

        while self.running:
            schedule.run_pending()
            time.sleep(30)

    def _intraday_job(self):
        if not is_trade_day():
            return
        print(f"\n{'='*40}\n  [调度] 14:30 盘中自动拉取\n{'='*40}")
        try:
            result = run_update(session='intraday')
            print(f"  [调度] 盘中拉取完成: {result.get('status')}")
        except Exception as e:
            print(f"  [调度] 盘中拉取失败: {e}")

    def _close_job(self):
        if not is_trade_day():
            return
        print(f"\n{'='*40}\n  [调度] 15:30 收盘后自动拉取\n{'='*40}")
        try:
            result = run_update(session='final')
            print(f"  [调度] 收盘后拉取完成: {result.get('status')}")
        except Exception as e:
            print(f"  [调度] 收盘后拉取失败: {e}")


# ============================================================
# 启动时数据落后检测
# ============================================================

def _check_and_update_on_startup():
    """启动时检测评分日期是否落后最新交易日，落后则在后台触发一次 final 更新"""
    try:
        from db.models import ScoreHistoryModel
        latest_trade_day = get_latest_trade_day()
        _, last_score_date = ScoreHistoryModel.get_latest(session='final', scheme='B')

        if last_score_date is None or last_score_date < latest_trade_day:
            gap = '无历史数据' if last_score_date is None else f'{last_score_date} → 需更新至 {latest_trade_day}'
            print(f"[启动检测] 数据落后 ({gap})，后台触发补充更新...")
            threading.Thread(target=_startup_update_job, daemon=True).start()
        else:
            print(f"[启动检测] 数据已是最新 ({last_score_date})")
    except Exception as e:
        print(f"[启动检测] 检测失败: {e}")


def _startup_update_job():
    """启动补充更新（后台线程）"""
    try:
        result = run_update(session='final')
        print(f"[启动更新] 完成: {result.get('status')} 评分{result.get('scores_count', 0)}只")
    except Exception as e:
        print(f"[启动更新] 失败: {e}")


# ============================================================
# 全局实例
# ============================================================

_scheduler = Scheduler()


def start_scheduler():
    """启动调度器，并触发启动时数据检测"""
    _scheduler.start()
    _check_and_update_on_startup()


def stop_scheduler():
    _scheduler.stop()
