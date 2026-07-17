"""
AiToEarn 数据质量校验模块
校验规则：
1. 最新价与昨收偏差 ≤ 10%（A股涨跌停限制）
2. 价格连续性：相邻两天跳空 > 15% 报警
3. 字段完整性：date/open/close 不可缺失
4. 数据新鲜度：最新日期是否等于最近交易日
5. 双源交叉校验：akshare vs baostock 偏差 ≤ 1%
"""

import json
from datetime import datetime

import numpy as np
import pandas as pd

from config import DATA_DIR, MIN_DATA_ROWS, CROSS_CHECK_THRESHOLD
from db.models import DataQualityLogModel


# ============================================================
# 单只 ETF 数据校验
# ============================================================

def validate_single_etf(code, df, latest_trade_date=None):
    """校验单只 ETF 数据质量
    返回: list of error dicts，每个 {type, severity, message}
    severity: 'error' | 'warning' | 'info'
    """
    errors = []

    if df is None or len(df) == 0:
        errors.append({'type': 'no_data', 'severity': 'error', 'message': f'{code}: 无数据'})
        return errors

    # 1. 字段完整性
    required_cols = ['open', 'close']
    missing = [c for c in required_cols if c not in df.columns]
    if missing:
        errors.append({
            'type': 'missing_columns',
            'severity': 'error',
            'message': f'{code}: 缺失字段 {missing}'
        })
        return errors  # 后续校验无意义

    # 检查是否有 NaN
    if df['close'].isna().any():
        na_count = df['close'].isna().sum()
        errors.append({
            'type': 'nan_values',
            'severity': 'warning',
            'message': f'{code}: 存在 {na_count} 个 NaN 值'
        })

    # 2. 数据新鲜度
    if latest_trade_date:
        last_date = df.index.max().strftime('%Y-%m-%d')
        if last_date < latest_trade_date:
            errors.append({
                'type': 'stale_data',
                'severity': 'warning',
                'message': f'{code}: 最新数据日期 {last_date}，最近交易日 {latest_trade_date}'
            })

    # 3. 最新价涨跌停校验（日涨跌幅 ≤ 10%）
    if len(df) >= 2:
        latest_close = df['close'].iloc[-1]
        prev_close = df['close'].iloc[-2]
        if prev_close > 0:
            pct_change = abs(latest_close - prev_close) / prev_close
            if pct_change > 0.10:
                errors.append({
                    'type': 'price_spike',
                    'severity': 'error',
                    'message': f'{code}: 最新价日涨跌幅 {pct_change:.2%} 超过 10% 限制'
                })

    # 4. 价格连续性（相邻两天跳空 > 15%）
    if len(df) >= 5:
        closes = df['close'].values
        for j in range(1, len(closes)):
            if closes[j-1] > 0:
                gap = abs(closes[j] - closes[j-1]) / closes[j-1]
                if gap > 0.15:
                    date_str = df.index[j].strftime('%Y-%m-%d')
                    errors.append({
                        'type': 'price_gap',
                        'severity': 'warning',
                        'message': f'{code}: {date_str} 价格跳空 {gap:.2%}'
                    })
                    break  # 只报一次

    # 5. 总行数校验
    if len(df) < MIN_DATA_ROWS:
        errors.append({
            'type': 'insufficient_data',
            'severity': 'warning',
            'message': f'{code}: 仅 {len(df)} 行数据（最低 {MIN_DATA_ROWS}）'
        })

    return errors


# ============================================================
# 批量校验
# ============================================================

def validate_all_etfs(codes, latest_trade_date=None):
    """批量校验所有 ETF 数据
    返回: {
        'total': int,
        'passed': int,         # 无 error 级别的 ETF 数
        'warning': int,         # 仅有 warning 的 ETF 数
        'error': int,           # 有 error 的 ETF 数
        'errors': [{'code': ..., 'name': ..., 'errors': [...]}],
    }
    """
    import os

    all_errors = []
    passed = 0
    warning_only = 0
    has_error = 0

    for code in codes:
        filepath = os.path.join(DATA_DIR, f"{code}.csv")
        df = None
        if os.path.exists(filepath):
            try:
                df = pd.read_csv(filepath, index_col=0, parse_dates=True)
            except Exception:
                pass

        etf_errors = validate_single_etf(code, df, latest_trade_date)

        if not etf_errors:
            passed += 1
        elif any(e['severity'] == 'error' for e in etf_errors):
            has_error += 1
        else:
            warning_only += 1

        if etf_errors:
            all_errors.append({
                'code': code,
                'errors': etf_errors,
            })

    return {
        'total': len(codes),
        'passed': passed,
        'warning': warning_only,
        'error': has_error,
        'errors': all_errors,
    }


# ============================================================
# 双源交叉校验（汇总）
# ============================================================

def validate_cross_check(ak_results, bs_results, threshold=CROSS_CHECK_THRESHOLD):
    """
    汇总双源交叉校验结果
    ak_results: list of {code, name, close}
    bs_results: dict code -> close (from baostock)
    返回: {passed, failed, total, details: [{code, ak, bs, deviation, passed}]}
    """
    details = []
    passed = 0
    failed = 0

    for item in ak_results:
        code = item['code']
        ak_close = item['close']
        bs_close = bs_results.get(code)

        if bs_close is None:
            details.append({
                'code': code, 'ak_close': ak_close, 'bs_close': None,
                'deviation': None, 'passed': None, 'message': 'baostock 数据不可用'
            })
            continue

        if ak_close == 0:
            failed += 1
            details.append({
                'code': code, 'ak_close': ak_close, 'bs_close': bs_close,
                'deviation': None, 'passed': False, 'message': 'akshare 价格为 0'
            })
            continue

        deviation = abs(ak_close - bs_close) / ak_close
        is_passed = deviation <= threshold
        if is_passed:
            passed += 1
        else:
            failed += 1

        details.append({
            'code': code,
            'ak_close': round(ak_close, 4),
            'bs_close': round(bs_close, 4),
            'deviation': round(deviation, 6),
            'passed': is_passed,
            'message': f"偏差 {deviation:.4%}" + (" ✓" if is_passed else " ✗超标"),
        })

    return {
        'passed': passed,
        'failed': failed,
        'total': passed + failed,
        'details': details,
    }


# ============================================================
# 综合质量报告（写入数据库）
# ============================================================

def generate_quality_report(source, session, total_etfs, success_count, failed_count, validation_result, cross_check_result=None):
    """
    生成数据质量报告并写入数据库
    validation_result: 批量校验返回值
    cross_check_result: 交叉校验返回值（可选）
    """
    validation_errors = []
    if validation_result:
        for item in validation_result.get('errors', []):
            validation_errors.append({
                'code': item['code'],
                'errors': [e['message'] for e in item['errors']]
            })

    cross_ok = 1
    if cross_check_result:
        cross_ok = 1 if cross_check_result.get('failed', 0) == 0 else 0
        # 追加交叉校验错误
        for d in cross_check_result.get('details', []):
            if not d.get('passed', True):
                validation_errors.append({
                    'code': d['code'],
                    'errors': [d.get('message', '交叉校验未通过')]
                })

    DataQualityLogModel.add(
        source=source,
        session=session,
        total_etfs=total_etfs,
        success_count=success_count,
        failed_count=failed_count,
        validation_errors=validation_errors if validation_errors else None,
        cross_check_passed=cross_ok,
    )

    return {
        'source': source,
        'session': session,
        'total_etfs': total_etfs,
        'success': success_count,
        'failed': failed_count,
        'validation_errors': len(validation_errors),
        'cross_check_passed': cross_ok,
    }