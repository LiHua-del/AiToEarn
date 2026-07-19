"""
AiToEarn 风险控制模块
- 个股层MA5止损（连续跌破 → 减仓）
- 组合层回撤止损（区间高点回撤 → 清仓）
- 乖离率评分惩罚（第三因子）
"""

import numpy as np
import pandas as pd

from config import (
    MA5_BUFFER_PCT, MA5_REDUCE_TO_PCT, MA5_CONSECUTIVE_DAYS,
    PORTFOLIO_STOP_THRESHOLD, REENTRY_MODE,
    DEVIATION_WEIGHTS, N_TOP_V2,
)


# ============================================================
# 个股层：MA5 止损
# ============================================================

class IndividualMA5Stop:
    """
    个股层 MA5 止损逻辑：
    - 连续 N 日收盘价低于 MA5 × (1 - buffer) 时触发
    - 触发动作：将持仓减至 reduce_to_pct 比例
    """

    def __init__(self, buffer_pct=MA5_BUFFER_PCT,
                 reduce_to_pct=MA5_REDUCE_TO_PCT,
                 consecutive_days=MA5_CONSECUTIVE_DAYS):
        self.buffer_pct = buffer_pct
        self.reduce_to_pct = reduce_to_pct
        self.consecutive_days = consecutive_days
        # 追踪每只持仓低于 MA5 的连续天数
        self.below_ma5_days = {}  # code -> int
        # 追踪每只持仓是否已触发过止损（同一持仓周期内只触发一次）
        self.triggered_codes = set()  # code

    def reset(self, holding_codes):
        """换仓日重置追踪状态"""
        # 保留仍在持仓中的记录，清除已卖出的
        new_tracker = {}
        for code in holding_codes:
            new_tracker[code] = self.below_ma5_days.get(code, 0)
        self.below_ma5_days = new_tracker
        # 换仓后清空已触发标记（新持仓周期）
        self.triggered_codes = set()

    def check(self, code, price, ma5):
        """
        检查单只 ETF 是否触发 MA5 止损
        返回: {'triggered': bool, 'action': str, 'reduce_to': float|None,
               'below_days': int, 'buffer_pct': float}
        - price: 当日收盘价
        - ma5: 当日 MA5 值（可为 NaN）
        """
        result = {
            'triggered': False,
            'action': 'hold',
            'reduce_to': None,
            'below_days': 0,
            'buffer_pct': self.buffer_pct,
        }

        if pd.isna(ma5) or ma5 <= 0:
            # MA5 不可用，不触发
            self.below_ma5_days[code] = 0
            return result

        threshold = ma5 * (1 - self.buffer_pct)
        below = price < threshold

        if below:
            self.below_ma5_days[code] = self.below_ma5_days.get(code, 0) + 1
        else:
            self.below_ma5_days[code] = 0

        result['below_days'] = self.below_ma5_days[code]

        if self.below_ma5_days[code] >= self.consecutive_days and code not in self.triggered_codes:
            result['triggered'] = True
            result['action'] = 'reduce'
            result['reduce_to'] = self.reduce_to_pct
            # 标记已触发，同一持仓周期内不再重复触发
            self.triggered_codes.add(code)

        return result


# ============================================================
# 组合层：回撤止损
# ============================================================

class PortfolioTrailingStop:
    """
    组合层回撤止损逻辑：
    - 追踪区间内账户净值最高点（period_peak）
    - 当前净值较 period_peak 回撤超过 threshold 时触发清仓
    - 触发后进入保守再入场模式（conservative reentry）
    """

    def __init__(self, stop_threshold=PORTFOLIO_STOP_THRESHOLD,
                 reentry_mode=REENTRY_MODE):
        self.stop_threshold = stop_threshold
        self.reentry_mode = reentry_mode
        self.period_peak_nav = None  # 区间最高净值
        self.triggered = False       # 是否已触发
        self.trigger_date = None     # 触发日期

    def reset_period(self):
        """换仓日重置区间高点（新持仓开始追踪），同时重置触发状态"""
        self.period_peak_nav = None
        self.triggered = False
        self.trigger_date = None

    def reset_all(self):
        """完全重置（新回测方案开始时）"""
        self.period_peak_nav = None
        self.triggered = False
        self.trigger_date = None

    def check(self, total_asset, date=None):
        """
        检查组合是否触发回撤止损
        返回: {'triggered': bool, 'action': str,
               'drawdown': float, 'peak_nav': float|None,
               'current_nav': float}
        """
        result = {
            'triggered': False,
            'action': 'normal',
            'drawdown': 0.0,
            'peak_nav': self.period_peak_nav,
            'current_nav': total_asset,
        }

        # 更新区间高点
        if self.period_peak_nav is None or total_asset > self.period_peak_nav:
            self.period_peak_nav = total_asset

        result['peak_nav'] = self.period_peak_nav

        # 计算回撤
        if self.period_peak_nav > 0:
            drawdown = (total_asset - self.period_peak_nav) / self.period_peak_nav
            result['drawdown'] = round(drawdown, 4)

            if drawdown <= -self.stop_threshold and not self.triggered:
                result['triggered'] = True
                result['action'] = 'full_liquidate'
                self.triggered = True
                self.trigger_date = date

        return result

    def should_reentry(self, scores, top_n=N_TOP_V2):
        """
        保守再入场判断：
        - 仅当 Top N 评分均大于 0（整体趋势向上）时才允许重新入场
        """
        if not self.triggered:
            return True  # 未触发过，正常入场

        if self.reentry_mode == 'conservative':
            # 保守模式：Top N 评分均为正才入场
            if scores and len(scores) >= top_n:
                return all(s['score'] > 0 for s in scores[:top_n])
            return False

        # 非保守模式直接允许
        return True


# ============================================================
# 分层止损管理器
# ============================================================

class LayeredStopLossManager:
    """
    分层止损管理器：
    - 个股层：MA5 止损 → 触发时减仓
    - 组合层：回撤止损 → 触发时清仓
    - 组合层优先级高于个股层
    """

    def __init__(self, buffer_pct=MA5_BUFFER_PCT,
                 reduce_to_pct=MA5_REDUCE_TO_PCT,
                 consecutive_days=MA5_CONSECUTIVE_DAYS,
                 stop_threshold=PORTFOLIO_STOP_THRESHOLD,
                 reentry_mode=REENTRY_MODE):
        self.individual_stop = IndividualMA5Stop(
            buffer_pct=buffer_pct,
            reduce_to_pct=reduce_to_pct,
            consecutive_days=consecutive_days,
        )
        self.portfolio_stop = PortfolioTrailingStop(
            stop_threshold=stop_threshold,
            reentry_mode=reentry_mode,
        )

    def on_rebalance(self, holding_codes):
        """换仓日调用：重置追踪状态"""
        self.individual_stop.reset(holding_codes)
        self.portfolio_stop.reset_period()

    def run_daily_check(self, holdings, price_data, ma5_data, total_asset, date=None):
        """
        每日风控检查

        参数:
            holdings: dict {code: {'shares': float, 'cost_price': float, 'name': str}}
            price_data: dict {code: float} 当日收盘价
            ma5_data: dict {code: float} 当日 MA5 值
            total_asset: float 当前账户总资产
            date: str 日期（用于日志）

        返回: {
            'individual_results': {code: check_result},
            'portfolio_result': check_result,
            'final_action': 'normal' | 'partial_reduce' | 'full_liquidate',
            'reduce_codes': {code: reduce_to_pct},  # 需要减仓的个股
            'liquidate': bool,  # 是否需要全部清仓
        }
        """
        # 1. 组合层检查（优先级更高）
        portfolio_result = self.portfolio_stop.check(total_asset, date)

        # 2. 个股层检查
        individual_results = {}
        reduce_codes = {}

        for code in holdings:
            price = price_data.get(code)
            ma5 = ma5_data.get(code)

            if price is None:
                continue

            ind_result = self.individual_stop.check(code, price, ma5)
            individual_results[code] = ind_result

            if ind_result['triggered']:
                reduce_codes[code] = ind_result['reduce_to']

        # 3. 综合决策
        if portfolio_result['triggered']:
            final_action = 'full_liquidate'
        elif reduce_codes:
            final_action = 'partial_reduce'
        else:
            final_action = 'normal'

        return {
            'individual_results': individual_results,
            'portfolio_result': portfolio_result,
            'final_action': final_action,
            'reduce_codes': reduce_codes,
            'liquidate': portfolio_result['triggered'],
        }


# ============================================================
# 乖离率计算
# ============================================================

def calc_deviation_rate(close_series, ma_period=20):
    """
    计算乖离率 = (close - MA) / MA × 100

    参数:
        close_series: pd.Series 收盘价序列
        ma_period: int 均线周期，默认 20

    返回:
        pd.Series 乖离率序列
    """
    ma = close_series.rolling(window=ma_period, min_periods=ma_period).mean()
    deviation = (close_series - ma) / ma * 100
    return deviation


def calc_deviation_on_date(close_df, date, ma_period=20):
    """
    计算指定日期所有 ETF 的乖离率

    参数:
        close_df: pd.DataFrame 所有 ETF 收盘价（列为 code，索引为日期）
        date: 目标日期
        ma_period: 均线周期

    返回:
        dict {code: deviation_20d}，仅包含有有效值的 ETF
    """
    deviations = {}
    for code in close_df.columns:
        try:
            series = close_df[code].loc[:date].dropna()
            if len(series) < ma_period:
                continue
            dev = calc_deviation_rate(series, ma_period)
            val = dev.iloc[-1]
            if pd.notna(val):
                deviations[code] = round(float(val), 4)
        except Exception:
            continue
    return deviations


# ============================================================
# 乖离率评分惩罚（第三因子）
# ============================================================

def apply_deviation_score_penalty(scores, deviations, scheme='B'):
    """
    应用乖离率评分惩罚

    逻辑: score_final = score_original - w3 × max(0, deviation - median)
    - 仅对乖离率高于中位数的 ETF 施加惩罚
    - 乖离率低于中位数的不受影响（合理的均值回归）
    - w3 按方案不同：A(激进, 较小惩罚) < B(平衡) < C(稳健, 较大惩罚)

    参数:
        scores: list of dict，每个含 'code' 和 'score_xxx' 字段
        deviations: dict {code: deviation_20d}
        scheme: 'A' | 'B' | 'C'

    返回:
        list of dict，在原 dict 基础上增加:
            - deviation_20d: float
            - deviation_median: float
            - deviation_penalty: float
            - score_final: float（惩罚后的评分）
    """
    w3 = DEVIATION_WEIGHTS.get(scheme, DEVIATION_WEIGHTS['B'])
    score_key = f'score_{scheme.lower()}'

    # 计算中位数（仅对有乖离率数据的 ETF）
    dev_values = []
    for s in scores:
        dev = deviations.get(s['code'])
        if dev is not None:
            dev_values.append(dev)

    if not dev_values:
        # 无乖离率数据，不加惩罚
        for s in scores:
            s['deviation_20d'] = None
            s['deviation_median'] = None
            s['deviation_penalty'] = 0.0
            s['score_final'] = s.get(score_key, 0)
        return scores

    median_dev = float(np.median(dev_values))

    for s in scores:
        dev = deviations.get(s['code'])
        s['deviation_20d'] = dev
        s['deviation_median'] = round(median_dev, 4)

        if dev is not None and dev > median_dev:
            penalty = w3 * (dev - median_dev)
            s['deviation_penalty'] = round(penalty, 4)
        else:
            s['deviation_penalty'] = 0.0

        original_score = s.get(score_key, 0)
        s['score_final'] = round(original_score - s['deviation_penalty'], 4)

    return scores


# ============================================================
# 辅助：寻找最优组合止损阈值
# ============================================================

def find_optimal_stop_threshold(scheme='B'):
    """
    分析 V2 回测净值曲线，寻找最优组合止损阈值
    通过遍历不同阈值，计算触发后的净值恢复情况

    参数:
        scheme: 方案

    返回:
        dict {threshold: {'trigger_count': int, 'avg_recovery_days': float,
                          'avoided_dd_pct': float}}
        以及推荐的 threshold
    """
    from db.models import EquityCurveV2Model

    rows = EquityCurveV2Model.get_all(scheme=scheme)
    if not rows:
        return None

    assets = [r['total_asset'] for r in rows]
    if not assets:
        return None

    results = {}
    best_threshold = PORTFOLIO_STOP_THRESHOLD
    best_score = -float('inf')

    for threshold_pct in range(3, 16):  # 3% ~ 15%
        threshold = threshold_pct / 100.0
        peak = assets[0]
        trigger_count = 0
        avoided_dd_sum = 0.0
        in_drawdown = False

        for i, v in enumerate(assets):
            if v > peak:
                peak = v
            dd = (v - peak) / peak

            if dd <= -threshold and not in_drawdown:
                trigger_count += 1
                in_drawdown = True
                avoided_dd_sum += abs(dd) - threshold

            if in_drawdown and v >= peak * (1 - threshold * 0.5):
                # 简化的恢复判断
                in_drawdown = False
                peak = v

        # 评分：触发次数合理(1-5次)且避免的回撤大
        if 1 <= trigger_count <= 5:
            score = avoided_dd_sum - trigger_count * 0.02
        else:
            score = -1.0

        results[threshold_pct] = {
            'trigger_count': trigger_count,
            'avoided_dd_pct': round(avoided_dd_sum * 100, 2),
            'score': round(score, 4),
        }

        if score > best_score:
            best_score = score
            best_threshold = threshold

    return {
        'results': results,
        'recommended_threshold': best_threshold,
    }
