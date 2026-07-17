#!/usr/bin/env python3
"""
AiToEarn 每日增量更新脚本
串联：数据拉取 → 质量校验 → 评分计算 → 回测更新 → 持久化

用法：
    python daily_update.py                    # 完整收盘后更新
    python daily_update.py --intraday         # 盘中更新
    python daily_update.py --date 2026-06-24  # 指定日期
"""

import argparse
import os
import sys
import time
from datetime import datetime

import pandas as pd

# 确保项目根目录在 path 中
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import DATA_DIR
from db.models import init_db, DataQualityLogModel, ScoreHistoryModel
from core.data_fetcher import (
    get_etf_pool, update_all_etf_data, load_cached_data,
    get_latest_trade_day, get_today_str, is_trade_day,
)
from core.validator import validate_all_etfs, generate_quality_report
from core.indicators import score_all_etfs
from core.backtest import update_backtest_results, update_backtest_v2


def run_update(session='final', target_date=None):
    """
    执行一次完整更新流程
    session: 'intraday' | 'final'
    target_date: datetime 或 None（默认今天）
    返回结果 dict
    """
    if target_date is None:
        target_date = datetime.now()

    # 判断是否为交易日
    latest_trade_day = get_latest_trade_day(target_date)
    if not is_trade_day(target_date) and session == 'final':
        print(f"[跳过] {target_date.strftime('%Y-%m-%d')} 非交易日，最近交易日: {latest_trade_day}")
        return {'status': 'skip', 'reason': '非交易日', 'latest_trade_day': latest_trade_day}

    session_label = {'intraday': '盘中', 'final': '收盘后'}.get(session, session)

    print(f"=" * 60)
    print(f"  AiToEarn 每日更新 — {session_label}模式")
    print(f"  日期: {target_date.strftime('%Y-%m-%d')}  最近交易日: {latest_trade_day}")
    print(f"=" * 60)

    # 1. 获取标的池
    print("\n[1/4] 获取 ETF 标的池...")
    try:
        pool, full_name_map = get_etf_pool()
        print(f"  标的池: {len(pool)} 只 ETF")
    except Exception as e:
        print(f"  获取标的池失败: {e}")
        return {'status': 'error', 'step': 'get_pool', 'error': str(e)}

    # 2. 增量更新数据
    print(f"\n[2/4] 增量更新 ETF 历史数据...")
    success, fail, failed_codes = update_all_etf_data(pool, target_date)
    print(f"  成功: {success}, 失败: {fail}")
    if failed_codes:
        for f in failed_codes[:5]:
            print(f"    ✗ {f['code']} {f['name']}: {f['error'][:60]}")
        if len(failed_codes) > 5:
            print(f"    ... 及其他 {len(failed_codes) - 5} 只")

    # 3. 数据质量校验
    print(f"\n[3/4] 数据质量校验...")
    valid_codes = []
    for f in os.listdir(DATA_DIR):
        if f.endswith('.csv') and not f.startswith('.'):
            code = f.replace('.csv', '')
            if os.path.getsize(os.path.join(DATA_DIR, f)) > 100:
                valid_codes.append(code)

    validation_result = validate_all_etfs(valid_codes, latest_trade_day)
    print(f"  总计: {validation_result['total']} | 通过: {validation_result['passed']} | "
          f"警告: {validation_result['warning']} | 错误: {validation_result['error']}")

    # 交叉校验（仅收盘后）
    cross_check_result = None
    if session == 'final':
        from core.data_fetcher import get_etf_close_baostock, cross_check_close
        sample_codes = valid_codes[:10]  # 抽查前 10 只
        if sample_codes:
            print(f"  双源交叉校验 (抽查 {len(sample_codes)} 只)...")
            ak_results = []
            bs_results = {}
            for code in sample_codes:
                df = load_cached_data(code)
                if df is not None and len(df) > 0:
                    close_val = float(df.iloc[-1]['close'])
                    ak_results.append({'code': code, 'close': close_val})
                    bs_close = get_etf_close_baostock(code, latest_trade_day)
                    if bs_close is not None:
                        bs_results[code] = bs_close

            from core.validator import validate_cross_check
            cross_check_result = validate_cross_check(ak_results, bs_results)
            print(f"  交叉校验: {cross_check_result['passed']} 通过 / {cross_check_result['failed']} 失败")
            # 打印失败详情
            for d in cross_check_result.get('details', []):
                if d.get('passed') is False:
                    print(f"    ✗ {d['code']}: ak={d['ak_close']} bs={d['bs_close']} {d.get('message','')}")
                elif d.get('passed') is None:
                    print(f"    ⚠ {d['code']}: {d.get('message','')}")

    # 写入质量日志
    generate_quality_report(
        source='akshare', session=session,
        total_etfs=len(valid_codes),
        success_count=success, failed_count=fail,
        validation_result=validation_result,
        cross_check_result=cross_check_result,
    )

    # 4. 评分 + 回测
    print(f"\n[4/4] 计算评分 & 更新回测...")

    # 加载数据
    data = {}
    for code in valid_codes:
        df = load_cached_data(code)
        if df is not None:
            data[code] = df

    # 评分
    target_score_date = pd.Timestamp(latest_trade_day) if session == 'final' else pd.Timestamp(target_date.strftime('%Y-%m-%d'))
    scores = score_all_etfs(pool, valid_codes, data, target_score_date, extra_name_map=full_name_map)
    print(f"  有效评分: {len(scores)} 只 ETF")

    # 保存评分到数据库
    if scores:
        score_date = latest_trade_day if session == 'final' else target_date.strftime('%Y-%m-%d')
        ScoreHistoryModel.save_batch(score_date, session, scores)
        print(f"  评分已保存到数据库 (date={score_date}, session={session})")
        # 打印 Top 5
        print(f"\n  Top 5 (方案B):")
        for i, s in enumerate(scores[:5]):
            print(f"    {s['rank_b']}. {s['code']} {s['name']} "
                  f"板块={s['sector']} 评分={s['score_b']} "
                  f"涨幅={s['gain_20d']}% 涨天={s['up_days_20d']}")

    # 回测更新（仅收盘后做完整回测）
    if session == 'final':
        print(f"\n  更新回测净值曲线...")
        try:
            stats = update_backtest_results()
            print(f"  回测更新完成:")
            for s, st in stats.items():
                print(f"    方案{s}: 累计{st['cum_ret']:.2%} 年化{st['ann_ret']:.2%} "
                      f"夏普{st['sharpe']:.2f} 最大回撤{st['max_dd']:.2%}")
        except Exception as e:
            print(f"  回测更新失败: {e}")

        # V2 回测（30万本金 Top3）
        print(f"\n  更新V2回测（30万本金 Top3）...")
        try:
            stats_v2 = update_backtest_v2(extra_name_map=full_name_map)
            print(f"  V2回测更新完成:")
            for s, st in stats_v2.items():
                print(f"    方案{s}: 总资产¥{st['total_asset']:,.2f} 累计{st['cum_return']:.2f}% "
                      f"夏普{st['sharpe']:.2f} 最大回撤{st['max_dd']:.2f}%")
        except Exception as e:
            print(f"  V2回测更新失败: {e}")

    print(f"\n{'=' * 60}")
    print(f"  更新完成!")
    print(f"{'=' * 60}")

    return {
        'status': 'ok',
        'session': session,
        'date': target_date.strftime('%Y-%m-%d'),
        'pool_size': len(pool),
        'data_success': success,
        'data_fail': fail,
        'scores_count': len(scores),
        'validation': {
            'total': validation_result['total'],
            'passed': validation_result['passed'],
            'warning': validation_result['warning'],
            'error': validation_result['error'],
        },
    }


# ============================================================
# 命令行入口
# ============================================================

def main():
    parser = argparse.ArgumentParser(description='AiToEarn 每日增量更新')
    parser.add_argument('--intraday', action='store_true', help='盘中更新模式')
    parser.add_argument('--date', type=str, help='指定日期 YYYY-MM-DD')
    args = parser.parse_args()

    # 初始化数据库
    print("初始化数据库...")
    init_db()

    session = 'intraday' if args.intraday else 'final'
    target_date = None
    if args.date:
        target_date = datetime.strptime(args.date, '%Y-%m-%d')

    result = run_update(session=session, target_date=target_date)
    return 0 if result['status'] == 'ok' else 1


if __name__ == '__main__':
    sys.exit(main())