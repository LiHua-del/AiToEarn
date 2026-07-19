"""
AiToEarn 数据库操作封装
使用 sqlite3 原生接口，提供 ORM 风格函数
"""
import json
import sqlite3
import os
from datetime import datetime

from config import DB_PATH


def get_conn():
    """获取数据库连接"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """初始化数据库：执行 schema.sql"""
    schema_path = os.path.join(os.path.dirname(__file__), 'schema.sql')
    conn = get_conn()
    try:
        with open(schema_path, 'r', encoding='utf-8') as f:
            conn.executescript(f.read())
        conn.commit()
    finally:
        conn.close()


# ============================================================
# TradeModel — 个人交易记录
# ============================================================
class TradeModel:
    @staticmethod
    def add(etf_code, direction, quantity, price, amount, trade_date, notes='', etf_name=''):
        """记录一笔买卖"""
        conn = get_conn()
        try:
            conn.execute(
                """INSERT INTO trades (etf_code, etf_name, direction, quantity, price, amount, trade_date, notes)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (etf_code, etf_name, direction, quantity, price, amount, trade_date, notes)
            )
            conn.commit()
            trade_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            return trade_id
        finally:
            conn.close()

    @staticmethod
    def get_all(etf_code=None, direction=None, start_date=None, end_date=None):
        """获取交易记录，支持筛选"""
        conn = get_conn()
        try:
            conditions = []
            params = []
            if etf_code:
                conditions.append("etf_code = ?")
                params.append(etf_code)
            if direction:
                conditions.append("direction = ?")
                params.append(direction)
            if start_date:
                conditions.append("trade_date >= ?")
                params.append(start_date)
            if end_date:
                conditions.append("trade_date <= ?")
                params.append(end_date)

            where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
            rows = conn.execute(
                f"SELECT * FROM trades {where} ORDER BY trade_date DESC, id DESC", params
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    @staticmethod
    def get_by_id(trade_id):
        conn = get_conn()
        try:
            row = conn.execute("SELECT * FROM trades WHERE id = ?", (trade_id,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    @staticmethod
    def update(trade_id, **kwargs):
        """更新交易记录字段"""
        allowed = ['etf_code', 'etf_name', 'direction', 'quantity', 'price', 'amount', 'trade_date', 'notes']
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [trade_id]
        conn = get_conn()
        try:
            conn.execute(f"UPDATE trades SET {set_clause} WHERE id = ?", values)
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def delete(trade_id):
        conn = get_conn()
        try:
            conn.execute("DELETE FROM trades WHERE id = ?", (trade_id,))
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def get_all_codes():
        """获取所有交易过的 ETF 代码"""
        conn = get_conn()
        try:
            rows = conn.execute(
                "SELECT DISTINCT etf_code, etf_name FROM trades ORDER BY etf_code"
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()


# ============================================================
# HoldingModel — 当前持仓
# ============================================================
class HoldingModel:
    @staticmethod
    def recalculate():
        """根据 trades 表重新计算所有持仓（先进先出）"""
        conn = get_conn()
        try:
            # 获取所有交易按时间排序
            trades = conn.execute(
                "SELECT * FROM trades ORDER BY trade_date ASC, id ASC"
            ).fetchall()

            # 按 ETF 分组计算
            holdings = {}  # code -> {quantity, total_cost}
            for t in trades:
                code = t['etf_code']
                name = t['etf_name']
                if code not in holdings:
                    holdings[code] = {'etf_name': name, 'quantity': 0, 'total_cost': 0}

                if t['direction'] == 'buy':
                    holdings[code]['quantity'] += t['quantity']
                    holdings[code]['total_cost'] += t['amount']
                elif t['direction'] == 'sell':
                    # 按比例减少
                    if holdings[code]['quantity'] > 0:
                        sell_ratio = min(t['quantity'] / holdings[code]['quantity'], 1.0)
                        holdings[code]['quantity'] -= t['quantity']
                        holdings[code]['total_cost'] -= holdings[code]['total_cost'] * sell_ratio

                    if holdings[code]['quantity'] <= 0.001:
                        holdings[code] = {'etf_name': name, 'quantity': 0, 'total_cost': 0}

            # 写入 holdings 表
            conn.execute("DELETE FROM holdings")
            for code, h in holdings.items():
                if h['quantity'] > 0.001:
                    avg_cost = h['total_cost'] / h['quantity'] if h['quantity'] > 0 else 0
                    conn.execute(
                        """INSERT OR REPLACE INTO holdings (etf_code, etf_name, total_quantity, avg_cost, total_cost)
                           VALUES (?, ?, ?, ?, ?)""",
                        (code, h['etf_name'], round(h['quantity'], 4), round(avg_cost, 4), round(h['total_cost'], 2))
                    )
            conn.commit()
            return holdings
        finally:
            conn.close()

    @staticmethod
    def get_all():
        """获取当前持仓"""
        conn = get_conn()
        try:
            rows = conn.execute("SELECT * FROM holdings WHERE total_quantity > 0 ORDER BY total_cost DESC").fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    @staticmethod
    def get_summary():
        """持仓汇总"""
        holdings = HoldingModel.get_all()
        total_cost = sum(h['total_cost'] for h in holdings)
        return {
            'count': len(holdings),
            'total_cost': round(total_cost, 2),
            'holdings': holdings,
        }


# ============================================================
# DailyPnlModel — 每日净值
# ============================================================
class DailyPnlModel:
    @staticmethod
    def snapshot(date, total_value, total_cost):
        """记录当日市值快照"""
        pnl = total_value - total_cost
        pnl_pct = round(pnl / total_cost * 100, 2) if total_cost > 0 else 0
        conn = get_conn()
        try:
            conn.execute(
                """INSERT OR REPLACE INTO daily_pnl (date, total_value, total_cost, pnl, pnl_pct)
                   VALUES (?, ?, ?, ?, ?)""",
                (date, round(total_value, 2), round(total_cost, 2), round(pnl, 2), pnl_pct)
            )
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def get_all(start_date=None, end_date=None):
        """获取净值历史"""
        conn = get_conn()
        try:
            if start_date and end_date:
                rows = conn.execute(
                    "SELECT * FROM daily_pnl WHERE date >= ? AND date <= ? ORDER BY date",
                    (start_date, end_date)
                ).fetchall()
            elif start_date:
                rows = conn.execute(
                    "SELECT * FROM daily_pnl WHERE date >= ? ORDER BY date", (start_date,)
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM daily_pnl ORDER BY date").fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()


# ============================================================
# DataQualityLogModel — 数据质量日志
# ============================================================
class DataQualityLogModel:
    @staticmethod
    def add(source, session, total_etfs, success_count, failed_count, validation_errors=None, cross_check_passed=1):
        conn = get_conn()
        try:
            errors_json = json.dumps(validation_errors or [], ensure_ascii=False)
            conn.execute(
                """INSERT INTO data_quality_log (source, session, total_etfs, success_count, failed_count,
                   validation_errors, cross_check_passed) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (source, session, total_etfs, success_count, failed_count, errors_json, cross_check_passed)
            )
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def get_latest(limit=5):
        conn = get_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM data_quality_log ORDER BY check_time DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    @staticmethod
    def get_latest_by_source():
        """每个数据源取最新一条"""
        conn = get_conn()
        try:
            rows = conn.execute(
                """SELECT * FROM data_quality_log WHERE id IN
                   (SELECT MAX(id) FROM data_quality_log GROUP BY source)"""
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()


# ============================================================
# ScoreHistoryModel — 评分历史
# ============================================================
class ScoreHistoryModel:
    @staticmethod
    def save_batch(date, session, scores):
        """批量保存评分数据
        scores: list of dict with keys: code, name, sector, gain_20d, up_days_20d,
                score_a, score_b, score_c, deviation_20d, deviation_median,
                deviation_penalty, score_final_a, score_final_b, score_final_c,
                above_ma60, rank_a, rank_b, rank_c
        """
        conn = get_conn()
        try:
            # 先删除当天同 session 的旧数据
            conn.execute(
                "DELETE FROM score_history WHERE date = ? AND session = ?", (date, session)
            )
            for s in scores:
                conn.execute(
                    """INSERT INTO score_history
                       (date, session, code, name, sector, gain_20d, up_days_20d,
                        score_a, score_b, score_c,
                        deviation_20d, deviation_median, deviation_penalty,
                        score_final_a, score_final_b, score_final_c,
                        above_ma60, rank_a, rank_b, rank_c)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (date, session,
                     s['code'], s.get('name', ''), s.get('sector', ''),
                     s.get('gain_20d', 0), s.get('up_days_20d', 0),
                     s.get('score_a', 0), s.get('score_b', 0), s.get('score_c', 0),
                     s.get('deviation_20d'), s.get('deviation_median'), s.get('deviation_penalty', 0),
                     s.get('score_final_a', 0), s.get('score_final_b', 0), s.get('score_final_c', 0),
                     s.get('above_ma60', 0),
                     s.get('rank_a', 0), s.get('rank_b', 0), s.get('rank_c', 0))
                )
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def get_latest(session='final', scheme='B', limit=0):
        """获取最新评分排行，limit=0 表示返回全部"""
        conn = get_conn()
        try:
            row = conn.execute(
                "SELECT date FROM score_history WHERE session = ? ORDER BY date DESC LIMIT 1",
                (session,)
            ).fetchone()
            if not row:
                return [], None
            latest_date = row[0]
            rank_col = f'rank_{scheme.lower()}'
            score_col = f'score_{scheme.lower()}'
            if limit > 0:
                rows = conn.execute(
                    f"""SELECT * FROM score_history
                        WHERE date = ? AND session = ?
                        ORDER BY {rank_col} ASC LIMIT ?""",
                    (latest_date, session, limit)
                ).fetchall()
            else:
                rows = conn.execute(
                    f"""SELECT * FROM score_history
                        WHERE date = ? AND session = ?
                        ORDER BY {rank_col} ASC""",
                    (latest_date, session)
                ).fetchall()
            return [dict(r) for r in rows], latest_date
        finally:
            conn.close()

    @staticmethod
    def get_history(code, session='final', days=90):
        """获取某只 ETF 的评分历史"""
        conn = get_conn()
        try:
            rows = conn.execute(
                """SELECT date, score_a, score_b, score_c, rank_a, rank_b, rank_c, gain_20d, up_days_20d
                   FROM score_history
                   WHERE code = ? AND session = ?
                   ORDER BY date DESC LIMIT ?""",
                (code, session, days)
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    @staticmethod
    def get_weekly_scores(session='final'):
        """获取各周评分汇总（用于热力图），每周取周五的数据"""
        conn = get_conn()
        try:
            rows = conn.execute(
                """SELECT date, code, name, sector, score_a, score_b, score_c, rank_a, rank_b, rank_c
                   FROM score_history WHERE session = ?
                   ORDER BY date DESC, rank_b ASC""",
                (session,)
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()


# ============================================================
# EquityCurveModel — 回测净值曲线
# ============================================================
class EquityCurveModel:
    @staticmethod
    def save_batch(records):
        """批量保存净值数据
        records: list of (date, scheme, nav)
        """
        conn = get_conn()
        try:
            conn.executemany(
                "INSERT OR REPLACE INTO equity_curve (date, scheme, nav) VALUES (?, ?, ?)",
                records
            )
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def get_all(scheme=None):
        conn = get_conn()
        try:
            if scheme:
                rows = conn.execute(
                    "SELECT date, nav FROM equity_curve WHERE scheme = ? ORDER BY date", (scheme,)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT date, scheme, nav FROM equity_curve ORDER BY date, scheme"
                ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    @staticmethod
    def get_latest_date():
        conn = get_conn()
        try:
            row = conn.execute("SELECT MAX(date) FROM equity_curve").fetchone()
            return row[0] if row else None
        finally:
            conn.close()


# ============================================================
# EquityCurveV2Model — V2 回测净值曲线（30万本金 Top3）
# ============================================================
class EquityCurveV2Model:
    @staticmethod
    def save_batch(records):
        """批量保存 V2 净值数据
        records: list of (date, scheme, total_asset, benchmark_asset)
        """
        conn = get_conn()
        try:
            conn.executemany(
                """INSERT OR REPLACE INTO equity_curve_v2
                   (date, scheme, total_asset, benchmark_asset) VALUES (?, ?, ?, ?)""",
                records
            )
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def get_all(scheme=None, start_date=None):
        conn = get_conn()
        try:
            if scheme and start_date:
                rows = conn.execute(
                    "SELECT date, scheme, total_asset, benchmark_asset FROM equity_curve_v2 WHERE scheme = ? AND date >= ? ORDER BY date",
                    (scheme, start_date)
                ).fetchall()
            elif scheme:
                rows = conn.execute(
                    "SELECT date, scheme, total_asset, benchmark_asset FROM equity_curve_v2 WHERE scheme = ? ORDER BY date",
                    (scheme,)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT date, scheme, total_asset, benchmark_asset FROM equity_curve_v2 ORDER BY date, scheme"
                ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    @staticmethod
    def get_latest_date():
        conn = get_conn()
        try:
            row = conn.execute("SELECT MAX(date) FROM equity_curve_v2").fetchone()
            return row[0] if row else None
        finally:
            conn.close()

    @staticmethod
    def get_stats(scheme='B'):
        """获取 V2 回测统计指标"""
        conn = get_conn()
        try:
            rows = conn.execute(
                "SELECT date, total_asset, benchmark_asset FROM equity_curve_v2 WHERE scheme = ? ORDER BY date",
                (scheme,)
            ).fetchall()
            if not rows:
                return None

            first = rows[0]
            last = rows[-1]
            total_asset_list = [r['total_asset'] for r in rows]
            benchmark_list = [r['benchmark_asset'] for r in rows]

            # 累计收益
            cum_ret = (last['total_asset'] / first['total_asset'] - 1) * 100

            # 最大回撤
            peak = total_asset_list[0]
            max_dd = 0
            for v in total_asset_list:
                if v > peak:
                    peak = v
                dd = (v - peak) / peak
                if dd < max_dd:
                    max_dd = dd

            # 夏普比率（基于日收益率）
            import numpy as np
            daily_rets = np.diff(total_asset_list) / total_asset_list[:-1]
            rf_daily = 0.03 / 252
            excess = daily_rets - rf_daily
            sharpe = (np.sqrt(252) * excess.mean() / excess.std()) if excess.std() > 0 else 0

            return {
                'total_asset': round(last['total_asset'], 2),
                'cum_return': round(cum_ret, 2),
                'cum_pnl': round(last['total_asset'] - first['total_asset'], 2),
                'max_dd': round(max_dd * 100, 2),
                'sharpe': round(float(sharpe), 2),
                'latest_date': last['date'],
                'benchmark_asset': round(last['benchmark_asset'], 2),
            }
        finally:
            conn.close()


# ============================================================
# TradeHistoryModel — V2 回测换仓历史
# ============================================================
class TradeHistoryModel:
    @staticmethod
    def save_batch(records):
        """批量保存换仓记录
        records: list of (date, action, etf_code, etf_name, price, shares, amount, scheme)
        """
        conn = get_conn()
        try:
            conn.executemany(
                """INSERT INTO trade_history
                   (date, action, etf_code, etf_name, price, shares, amount, scheme)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                records
            )
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def get_history(scheme='B', limit=50):
        """获取换仓历史（按日期倒序）"""
        conn = get_conn()
        try:
            rows = conn.execute(
                """SELECT * FROM trade_history WHERE scheme = ?
                   ORDER BY date DESC, id DESC LIMIT ?""",
                (scheme, limit)
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    @staticmethod
    def get_current_holdings(scheme='B'):
        """获取当前持仓（最新一次换仓后的 hold/buy 记录）"""
        conn = get_conn()
        try:
            # 获取最新换仓日期
            row = conn.execute(
                "SELECT MAX(date) FROM trade_history WHERE scheme = ?", (scheme,)
            ).fetchone()
            if not row or not row[0]:
                return []
            latest_date = row[0]
            rows = conn.execute(
                """SELECT * FROM trade_history WHERE scheme = ? AND date = ?
                   AND action IN ('buy', 'hold') ORDER BY id""",
                (scheme, latest_date)
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    @staticmethod
    def clear_scheme(scheme):
        """清除某方案的所有记录（重新回测前用）"""
        conn = get_conn()
        try:
            conn.execute("DELETE FROM trade_history WHERE scheme = ?", (scheme,))
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def get_current_holdings_with_price(scheme='B'):
        """获取当前持仓（含实时价格和浮盈）"""
        conn = get_conn()
        try:
            row = conn.execute(
                "SELECT MAX(date) FROM trade_history WHERE scheme = ?", (scheme,)
            ).fetchone()
            if not row or not row[0]:
                return [], None
            latest_date = row[0]
            rows = conn.execute(
                """SELECT * FROM trade_history WHERE scheme = ? AND date = ?
                   AND action IN ('buy', 'hold') ORDER BY id""",
                (scheme, latest_date)
            ).fetchall()
            return [dict(r) for r in rows], latest_date
        finally:
            conn.close()


# ============================================================
# RiskControlLogModel — 风控日志
# ============================================================
class RiskControlLogModel:
    @staticmethod
    def add(date, scheme, layer, action, etf_code='', details=''):
        """记录风控事件"""
        conn = get_conn()
        try:
            details_json = json.dumps(details, ensure_ascii=False) if isinstance(details, dict) else details
            conn.execute(
                """INSERT INTO risk_control_log
                   (date, scheme, layer, action, etf_code, details)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (date, scheme, layer, action, etf_code, details_json)
            )
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def get_latest(scheme='B', limit=20):
        """获取最近的风控事件"""
        conn = get_conn()
        try:
            rows = conn.execute(
                """SELECT * FROM risk_control_log WHERE scheme = ?
                   ORDER BY date DESC, id DESC LIMIT ?""",
                (scheme, limit)
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    @staticmethod
    def get_by_date(date, scheme='B'):
        """获取某日的风控事件"""
        conn = get_conn()
        try:
            rows = conn.execute(
                """SELECT * FROM risk_control_log WHERE date = ? AND scheme = ?
                   ORDER BY id""",
                (date, scheme)
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()