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
from core.risk_control import calc_deviation_on_date, LayeredStopLossManager
from db.models import RiskControlLogModel


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
    print("\n[1/5] 获取 ETF 标的池...")
    try:
        pool, full_name_map = get_etf_pool()
        print(f"  标的池: {len(pool)} 只 ETF")
    except Exception as e:
        print(f"  获取标的池失败: {e}")
        return {'status': 'error', 'step': 'get_pool', 'error': str(e)}

    # 2. 增量更新数据
    print(f"\n[2/5] 增量更新 ETF 历史数据...")
    success, fail, failed_codes = update_all_etf_data(pool, target_date)
    print(f"  成功: {success}, 失败: {fail}")
    if failed_codes:
        for f in failed_codes[:5]:
            print(f"    ✗ {f['code']} {f['name']}: {f['error'][:60]}")
        if len(failed_codes) > 5:
            print(f"    ... 及其他 {len(failed_codes) - 5} 只")

    # 3. 数据质量校验
    print(f"\n[3/5] 数据质量校验...")
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

    # 4. 评分 + 回测 + 风控
    print(f"\n[4/5] 计算评分 & 更新回测...")

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
            final_key = 'score_final_b'
            print(f"    {s['rank_b']}. {s['code']} {s['name']} "
                  f"板块={s['sector']} 原始评分={s['score_b']} 最终评分={s.get(final_key, s['score_b'])} "
                  f"涨幅={s['gain_20d']}% 涨天={s['up_days_20d']} "
                  f"乖离率={s.get('deviation_20d', 'N/A')}")

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

    # 5. 风控检查
    print(f"\n[5/5] 风控检查...")
    if session == 'final' and scores:
        # 对齐数据（复用评分时的 closes）
        from core.indicators import calc_ma5, calc_ma60
        all_dates = sorted(set().union(*[set(df.index) for df in data.values()]))
        date_index = pd.DatetimeIndex(all_dates)
        closes_check = pd.DataFrame(index=date_index)
        for code, df in data.items():
            closes_check[code] = df['close'].reindex(date_index).ffill()
        ma5_check = closes_check.apply(calc_ma5)
        ma60_check = closes_check.apply(calc_ma60)

        score_date_str = latest_trade_day if session == 'final' else target_date.strftime('%Y-%m-%d')
        check_date = pd.Timestamp(score_date_str)

        for scheme in ['A', 'B', 'C']:
            # 获取当前持仓（从 trade_history）
            from db.models import TradeHistoryModel
            current_holdings, _ = TradeHistoryModel.get_current_holdings_with_price(scheme)

            if not current_holdings:
                # 无持仓，记录正常
                RiskControlLogModel.add(
                    date=score_date_str, scheme=scheme,
                    layer='portfolio', action='normal',
                    details={'msg': '无持仓'}
                )
                continue

            # 构建持仓 dict 供风控检查
            holdings_dict = {}
            price_data = {}
            ma5_data = {}
            for h in current_holdings:
                code = h['etf_code']
                holdings_dict[code] = {
                    'shares': h['shares'],
                    'cost_price': h['price'],
                    'name': h['etf_name'],
                }
                if code in closes_check.columns and check_date in closes_check.index:
                    price_data[code] = float(closes_check.loc[check_date, code])
                if code in ma5_check.columns and check_date in ma5_check.index:
                    m5 = ma5_check.loc[check_date, code]
                    if pd.notna(m5):
                        ma5_data[code] = float(m5)

            # 计算总资产
            total_asset = sum(
                holdings_dict[c]['shares'] * price_data.get(c, holdings_dict[c]['cost_price'])
                for c in holdings_dict
            )

            # 运行风控检查
            mgr = LayeredStopLossManager()
            risk_result = mgr.run_daily_check(
                holdings_dict, price_data, ma5_data, total_asset,
                date=score_date_str
            )

            # 记录日志
            if risk_result['final_action'] != 'normal':
                # 个股层
                for code, ind_res in risk_result['individual_results'].items():
                    if ind_res['triggered']:
                        RiskControlLogModel.add(
                            date=score_date_str, scheme=scheme,
                            layer='individual', action='reduce',
                            etf_code=code,
                            details={
                                'below_days': ind_res['below_days'],
                                'reduce_to': ind_res['reduce_to'],
                                'buffer_pct': ind_res['buffer_pct'],
                            }
                        )
                        print(f"  ⚠ [{scheme}] 个股止损: {code} 连续{ind_res['below_days']}日低于MA5 → 减仓至{ind_res['reduce_to']*100:.0f}%")

                # 组合层
                if risk_result['portfolio_result']['triggered']:
                    RiskControlLogModel.add(
                        date=score_date_str, scheme=scheme,
                        layer='portfolio', action='liquidate',
                        details={
                            'drawdown': risk_result['portfolio_result']['drawdown'],
                            'peak_nav': risk_result['portfolio_result']['peak_nav'],
                            'current_nav': risk_result['portfolio_result']['current_nav'],
                        }
                    )
                    print(f"  🚨 [{scheme}] 组合止损: 回撤{risk_result['portfolio_result']['drawdown']*100:.1f}% 超过阈值 → 清仓")
            else:
                RiskControlLogModel.add(
                    date=score_date_str, scheme=scheme,
                    layer='portfolio', action='normal',
                    details={'msg': '风控检查正常'}
                )
                print(f"  ✓ [{scheme}] 风控检查正常")

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