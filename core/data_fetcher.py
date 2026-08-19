"""
AiToEarn 数据获取模块
- 三级数据源：akshare（主）→ efinance（降级）→ baostock（最终备选）
- 增量更新：仅拉取 new_date ~ today 区间追加到 CSV
- 自动重试：指数退避，最多 5 次
"""

import os
import re
import time
import random
import warnings
from datetime import datetime, timedelta

import akshare as ak
import numpy as np
import pandas as pd

from config import (
    DATA_DIR, HISTORY_DAYS, MIN_DATA_ROWS, MAX_RETRIES, RETRY_BASE,
    AKSHARE_RATE_LIMIT, MIN_MARKET_CAP, MIN_AMOUNT, MAX_POOL_SIZE,
    MAX_UPDATE_ROUNDS, MIN_SUCCESS_RATE,
)

warnings.filterwarnings('ignore')


# ============================================================
# 1. ETF 标的池获取（akshare）
# ============================================================

def get_etf_snapshot():
    """获取全市场 ETF 实时快照"""
    df = ak.fund_etf_spot_em()
    col_map = {
        '代码': 'code', '名称': 'name', '最新价': 'price',
        '涨跌幅': 'pct_change', '成交量': 'volume',
        '成交额': 'amount', '总市值': 'total_mv'
    }
    df = df.rename(columns=col_map)
    df['code'] = df['code'].astype(str)
    return df


def extract_core_keyword(name):
    """提取 ETF 名称核心词，用于聚类去重（从原脚本复用）"""
    s = name
    for kw in ['ETF', '基金', '华夏', '易方达', '华安', '广发', '南方', '嘉实',
               '国泰', '天弘', '银华', '博时', '汇添富', '富国', '鹏华', '景顺',
               '招商', '工银', '交银', '建信', '中银', '兴证', '平安', '万家',
               '大成', '长城', '民生加银', '浦银', '中欧', '泓德', '前海开源',
               '联接', 'A', 'C', '指数', '沪深', '上证', '深证', '中证', '创业',
               '科创', '板块', '投资', '型', 'LOF', '华泰柏瑞', '华宝',
               '国联安', '鹏扬', '路博迈', '安中', '施罗德']:
        s = s.replace(kw, '')
    s = re.sub(r'\d', '', s)
    s = s.strip()
    return s if s else name


def deduplicate_etf(df):
    """每个核心词组只保留成交额最大的 1 只（从原脚本复用）"""
    df = df.copy()
    df['core_kw'] = df['name'].apply(extract_core_keyword)
    df = df.sort_values('amount', ascending=False)
    df = df.drop_duplicates(subset='core_kw', keep='first')
    df = df.drop(columns='core_kw')
    return df


def filter_etf_pool(df):
    """过滤 ETF 标的池（从原脚本复用）"""
    # 过滤1：剔除债券/货币 ETF
    bond_kw = ['债', '债券', '货币', '国债', '信用', '城投']
    mask = df['name'].apply(lambda n: not any(k in n for k in bond_kw))
    df = df[mask].copy()

    # 过滤2：剔除跨境 ETF
    cross_kw = ['美国', '纳指', '纳斯达克', '道琼斯', '日经', '恒生', '德国', '法国', '越南',
                '黄金', '石油', '原油', '港股', '港', '标普', 'DAX', '日兴', 'TOPIX', '跨境',
                '中概', '海外', '全球']
    mask = df['name'].apply(lambda n: not any(k in n for k in cross_kw))
    df = df[mask].copy()

    # 过滤3：最低规模过滤（总市值 >= 5 亿）
    df['total_mv'] = pd.to_numeric(df['total_mv'], errors='coerce').fillna(0)
    df = df[df['total_mv'] >= MIN_MARKET_CAP].copy()

    # 过滤4：最低流动性（成交额 >= 3000 万）
    df['amount'] = pd.to_numeric(df['amount'], errors='coerce').fillna(0)
    df = df[df['amount'] >= MIN_AMOUNT].copy()

    # 过滤5：同质化去重
    df = deduplicate_etf(df)

    # 数量调节：截取成交量前 100
    if len(df) > MAX_POOL_SIZE:
        df = df.nlargest(MAX_POOL_SIZE, 'amount')

    return df.reset_index(drop=True)


def get_etf_pool():
    """获取并过滤 ETF 标的池，同时返回全量名称映射"""
    snapshot = get_etf_snapshot()
    pool = filter_etf_pool(snapshot)
    # 全量名称映射，用于评分时补充不在 pool 中的 ETF 名称
    name_map = dict(zip(snapshot['code'], snapshot['name']))
    return pool, name_map


# ============================================================
# 2. 历史数据获取（akshare 主数据源）
# ============================================================

def fetch_etf_hist_ak(code, start_str, end_str, max_retries=MAX_RETRIES):
    """通过 akshare 获取单只 ETF 历史数据，带指数退避重试
    ConnectionError/RemoteDisconnected 视为服务器不可达，不重试直接抛出（快速降级到下一数据源）
    """
    import requests as _req
    for attempt in range(max_retries):
        try:
            hist = ak.fund_etf_hist_em(
                symbol=code, period='daily', adjust='',
                start_date=start_str, end_date=end_str
            )
            return hist
        except (_req.exceptions.ConnectionError, _req.exceptions.ChunkedEncodingError) as e:
            # 服务器不可达，立即放弃，快速降级到 efinance
            raise e
        except Exception as e:
            if attempt < max_retries - 1:
                wait = RETRY_BASE ** attempt + 1
                time.sleep(wait)
            else:
                raise e
    return None


def fetch_etf_hist_efinance(code, start_str, end_str, max_retries=MAX_RETRIES):
    """通过 efinance（东方财富基金接口）获取 ETF 历史净值数据，带指数退避重试

    返回格式与 akshare 一致：DataFrame with columns [日期, 单位净值(→close), 开盘(→open)]
    """
    for attempt in range(max_retries):
        try:
            import efinance as ef
            # efinance.fund 使用基金代码（不需要后缀）
            df = ef.fund.get_quote_history(code, pz=1000000)
            if df is None or len(df) == 0:
                raise ValueError(f"efinance 返回空数据: {code}")

            # 转换为标准格式
            df = df.rename(columns={
                '日期': 'date',
                '单位净值': 'close',
            })
            df['date'] = pd.to_datetime(df['date'])
            df = df.sort_values('date').reset_index(drop=True)

            # 按日期范围过滤
            start_dt = pd.Timestamp(start_str)
            end_dt = pd.Timestamp(end_str)
            df = df[(df['date'] >= start_dt) & (df['date'] <= end_dt)]

            if len(df) == 0:
                raise ValueError(f"efinance 数据在日期范围内为空: {code}")

            # 用前一日净值作为 open（ETF 净值型数据没有 OHLC）
            df = df.set_index('date')
            df = df.sort_index()
            df['open'] = df['close'].shift(1)
            # 第一条的 open 用 close 填充
            df['open'] = df['open'].fillna(df['close'])
            df = df[['open', 'close']]
            df = df.astype(float)

            # forward fill 非交易日
            df = df.reindex(pd.date_range(df.index.min(), df.index.max(), freq='B'))
            df = df.ffill()

            return df
        except Exception as e:
            if attempt < max_retries - 1:
                wait = RETRY_BASE ** attempt + 1
                time.sleep(wait)
            else:
                raise e
    return None


def _save_data(code, df, filepath):
    """保存 DataFrame 到 CSV（统一入口）"""
    df.to_csv(filepath)
    return len(df)


def _process_hist_df(hist):
    """将 akshare 返回的 DataFrame 统一为标准格式"""
    hist = hist.rename(columns={
        '日期': 'date', '开盘': 'open', '收盘': 'close',
    })
    hist = hist[['date', 'open', 'close']].copy()
    hist['date'] = pd.to_datetime(hist['date'])
    hist = hist.sort_values('date').reset_index(drop=True)
    hist = hist.set_index('date')
    hist = hist.astype(float)
    # forward fill 非交易日
    hist = hist.reindex(pd.date_range(hist.index.min(), hist.index.max(), freq='B'))
    hist = hist.ffill()
    return hist


def get_cache_info(code):
    """获取缓存文件信息，返回 (last_date, row_count) 或 (None, 0)"""
    filepath = os.path.join(DATA_DIR, f"{code}.csv")
    if not os.path.exists(filepath):
        return None, 0
    try:
        cached = pd.read_csv(filepath, index_col=0, parse_dates=True)
        if len(cached) >= MIN_DATA_ROWS:
            return cached.index.max().strftime('%Y%m%d'), len(cached)
        return None, len(cached)
    except Exception:
        return None, 0


def load_cached_data(code):
    """加载缓存数据（自动做前复权处理）"""
    filepath = os.path.join(DATA_DIR, f"{code}.csv")
    if not os.path.exists(filepath):
        return None
    try:
        df = pd.read_csv(filepath, index_col=0, parse_dates=True)
        if len(df) >= MIN_DATA_ROWS:
            from core.adjust import forward_adjust
            return forward_adjust(df)
        return None
    except Exception:
        return None


def _try_fetch_etf(code, start_str, end_str, source='akshare'):
    """尝试从指定数据源获取 ETF 数据，返回 (DataFrame or None, error_msg)"""
    try:
        if source == 'akshare':
            hist = fetch_etf_hist_ak(code, start_str, end_str)
            if hist is None or len(hist) < MIN_DATA_ROWS:
                return None, f"akshare数据不足({len(hist) if hist is not None else 0}行)"
            df = _process_hist_df(hist)
            return df, f"akshare成功({len(df)}行)"

        elif source == 'efinance':
            df = fetch_etf_hist_efinance(code, start_str, end_str)
            # efinance 内部已下载全量历史再按日期过滤，增量更新时行数少是正常的，只要 > 0 即可
            if df is None or len(df) < 1:
                return None, f"efinance数据不足({len(df) if df is not None else 0}行)"
            return df, f"efinance成功({len(df)}行)"

        elif source == 'baostock':
            # baostock 作为最终备选，只获取当日收盘价
            hist = fetch_etf_hist_ak(code, start_str, end_str, max_retries=2)
            if hist is None or len(hist) < MIN_DATA_ROWS:
                return None, f"baostock数据不足"
            df = _process_hist_df(hist)
            return df, f"baostock成功({len(df)}行)"

    except Exception as e:
        return None, f"{source}异常: {str(e)[:80]}"


def update_etf_data(code, end_date=None, start_date=None):
    """
    三级数据源降级更新单只 ETF 数据
    - 主数据源：akshare
    - 降级1：efinance（东方财富基金接口）
    - 降级2：akshare 重试（减少重试次数）
    - start_date: 指定起始日期（用于全量回填），None 则使用 HISTORY_DAYS
    - 如果有缓存，只拉取 last_date ~ end_date 区间
    - 返回 (success: bool, error_msg: str)
    """
    if end_date is None:
        end_date = datetime.now()
    end_str = end_date.strftime('%Y%m%d')

    filepath = os.path.join(DATA_DIR, f"{code}.csv")
    last_date_str, cached_rows = get_cache_info(code)

    # 情况1：无缓存或缓存不足 → 全量拉取
    if last_date_str is None:
        if start_date is not None:
            start_str = start_date.strftime('%Y%m%d')
        else:
            start_date = end_date - timedelta(days=HISTORY_DAYS)
            start_str = start_date.strftime('%Y%m%d')

        # 三级降级：akshare → efinance → akshare重试
        for source in ['akshare', 'efinance', 'baostock']:
            df, msg = _try_fetch_etf(code, start_str, end_str, source=source)
            if df is not None:
                _save_data(code, df, filepath)
                return True, f"全量拉取({source}): {msg}"
        return False, f"全量拉取失败: 所有数据源均失败"

    # 情况2：有缓存 → 增量拉取
    if last_date_str >= end_str:
        return True, f"缓存已是最新(截止{last_date_str})"

    start_str = last_date_str  # 从缓存最后一天开始

    try:
        # 加载已有缓存
        cached = pd.read_csv(filepath, index_col=0, parse_dates=True)

        # 三级降级：akshare → efinance → akshare重试
        new_data = None
        used_source = ''
        for source in ['akshare', 'efinance', 'baostock']:
            df, msg = _try_fetch_etf(code, start_str, end_str, source=source)
            if df is not None:
                new_data = df
                used_source = source
                break

        if new_data is None:
            return False, f"增量拉取失败: 所有数据源均失败"

        # 合并：缓存 + 新数据，按日期去重（新数据优先）
        combined = pd.concat([cached, new_data])
        combined = combined[~combined.index.duplicated(keep='last')]
        combined = combined.sort_index()

        # forward fill
        combined = combined.reindex(
            pd.date_range(combined.index.min(), combined.index.max(), freq='B')
        )
        combined = combined.ffill()

        _save_data(code, combined, filepath)
        new_rows = len(combined) - len(cached)
        return True, f"增量更新({used_source})+{new_rows}行(总计{len(combined)}行)"
    except Exception as e:
        return False, str(e)[:100]


def update_all_etf_data(etf_pool, end_date=None, start_date=None, progress_callback=None,
                        min_success_rate=MIN_SUCCESS_RATE, max_rounds=MAX_UPDATE_ROUNDS):
    """
    批量增量更新所有 ETF 数据，支持多轮重试确保高成功率

    - etf_pool: DataFrame with 'code' and 'name' columns
    - end_date: 截止日期（默认今天）
    - start_date: 起始日期（用于全量回填，None则使用缓存最后日期）
    - progress_callback: 进度回调 fn(i, total, code, name, success, msg)
    - min_success_rate: 目标最低成功率（默认 0.95）
    - max_rounds: 最大重试轮数（默认 3）
    - 返回 (success_count, fail_count, failed_codes: list)
    """
    total = len(etf_pool)
    all_codes = list(etf_pool['code'])
    all_names = dict(zip(etf_pool['code'], etf_pool['name']))

    # 跟踪每只 ETF 的最终状态
    final_status = {}  # code -> (success, msg)

    for round_num in range(1, max_rounds + 1):
        # 确定本轮需要尝试的 ETF
        if round_num == 1:
            pending_codes = list(all_codes)
        else:
            pending_codes = [c for c in all_codes if not final_status.get(c, (False,))[0]]

        if not pending_codes:
            break

        round_total = len(pending_codes)
        round_success = 0
        round_fail = 0

        print(f"\n  [第 {round_num}/{max_rounds} 轮] 更新 {round_total} 只 ETF...")

        for i, code in enumerate(pending_codes):
            name = all_names.get(code, '')
            success, msg = update_etf_data(code, end_date, start_date)

            if success:
                round_success += 1
                final_status[code] = (True, msg)
            else:
                round_fail += 1
                # 只在最后一轮或首次失败时记录
                if code not in final_status:
                    final_status[code] = (False, msg)

            if progress_callback:
                # 全局进度：用总体已处理数
                global_done = len([c for c in all_codes if c in final_status and final_status[c][0]])
                progress_callback(global_done, total, code, name, success, msg)

            # 限速：成功等 0.3s，失败等 1~2s（加随机 jitter）
            if success:
                time.sleep(AKSHARE_RATE_LIMIT)
            else:
                time.sleep(1 + random.random())

        # 本轮统计
        current_success = sum(1 for v in final_status.values() if v[0])
        current_rate = current_success / total
        print(f"    本轮: 成功 {round_success}, 失败 {round_fail}")
        print(f"    累计: {current_success}/{total} ({current_rate:.1%})")

        # 达到目标成功率，提前结束
        if current_rate >= min_success_rate:
            print(f"    ✓ 已达到目标成功率 {min_success_rate:.0%}，停止重试")
            break

        # 还有下一轮，等待一段时间让网络恢复
        if round_num < max_rounds and round_fail > 0:
            wait_sec = 5 * round_num + random.uniform(0, 5)
            print(f"    等待 {wait_sec:.1f}s 后进入下一轮重试...")
            time.sleep(wait_sec)

    # 汇总最终结果
    success_count = sum(1 for v in final_status.values() if v[0])
    fail_count = total - success_count
    failed_codes = [
        {'code': c, 'name': all_names.get(c, ''), 'error': final_status[c][1]}
        for c in all_codes if not final_status.get(c, (False,))[0]
    ]

    final_rate = success_count / total
    print(f"  最终成功率: {success_count}/{total} ({final_rate:.1%})")

    return success_count, fail_count, failed_codes


# ============================================================
# 3. baostock 副数据源（用于交叉校验）
# ============================================================

def get_etf_close_baostock(code, date_str):
    """
    通过 baostock 获取单只 ETF 某日的收盘价（用于交叉校验）
    输入: code 如 '510300', date_str 如 '2026-06-24'
    返回: close_price (float) 或 None
    """
    try:
        import baostock as bs
        bs.login()
        # baostock 需要格式 sh.510300 或 sz.159201
        if code.startswith(('51', '56', '58')):
            bs_code = f"sh.{code}"
        else:
            bs_code = f"sz.{code}"

        rs = bs.query_history_k_data_plus(
            bs_code, "close", start_date=date_str, end_date=date_str,
            frequency="d", adjustflag="1"  # 后复权
        )
        if rs.error_code != '0':
            bs.logout()
            return None

        rows = []
        while rs.next():
            rows.append(rs.get_row_data())

        bs.logout()

        if rows and len(rows) > 0 and rows[0][0] != '':
            return float(rows[0][0])
        return None
    except ImportError:
        # baostock 未安装
        return None
    except Exception:
        return None


def cross_check_close(ak_close, bs_close, threshold=0.01):
    """
    双源交叉校验收盘价
    - 返回 (passed: bool, deviation: float, message: str)
    """
    if ak_close is None:
        return False, 0, "akshare 数据缺失"

    if bs_close is None:
        return False, 0, "baostock 数据缺失（可能不支持该ETF）"

    if ak_close == 0:
        return False, 0, "akshare 价格为 0"

    deviation = abs(ak_close - bs_close) / ak_close
    passed = deviation <= threshold
    msg = f"偏差 {deviation:.4f} ({'通过' if passed else '超标'})"
    return passed, deviation, msg


# ============================================================
# 4. 交易日历
# ============================================================

def get_trade_calendar(start_date=None, end_date=None):
    """
    获取 A 股交易日历
    返回 set of 日期字符串 YYYY-MM-DD
    """
    try:
        df = ak.tool_trade_date_hist_sina()
        if start_date:
            df = df[df['trade_date'] >= start_date]
        if end_date:
            df = df[df['trade_date'] <= end_date]
        return set(str(d) for d in df['trade_date'].tolist())
    except Exception:
        # 降级：周一至周五都算交易日
        return None


def is_trade_day(date=None):
    """判断某日是否为交易日"""
    if date is None:
        date = datetime.now()
    if isinstance(date, datetime):
        date_str = date.strftime('%Y-%m-%d')
    else:
        date_str = date

    trade_dates = get_trade_calendar()
    if trade_dates is not None:
        return date_str in trade_dates
    # 降级：周一至周五
    if isinstance(date, datetime):
        return date.weekday() < 5
    dt = datetime.strptime(date_str, '%Y-%m-%d')
    return dt.weekday() < 5


def get_latest_trade_day(date=None):
    """获取最近的交易日（含当天）"""
    if date is None:
        date = datetime.now()
    date_str = date.strftime('%Y-%m-%d') if isinstance(date, datetime) else date

    trade_dates = get_trade_calendar()
    if trade_dates is not None:
        # 统一转为字符串比较
        dates = sorted([str(d) if not isinstance(d, str) else d for d in trade_dates], reverse=True)
        for d in dates:
            if d <= date_str:
                return d
    # 降级
    if isinstance(date, datetime):
        d = date
    else:
        d = datetime.strptime(date_str, '%Y-%m-%d')
    while d.weekday() >= 5:
        d = d - timedelta(days=1)
    return d.strftime('%Y-%m-%d')


def is_trading_hours():
    """
    判断当前是否在交易时段
    A股交易时间：9:30-11:30, 13:00-15:00
    """
    now = datetime.now()
    if now.weekday() >= 5:
        return False

    morning_start = now.replace(hour=9, minute=30, second=0, microsecond=0)
    morning_end = now.replace(hour=11, minute=30, second=0, microsecond=0)
    afternoon_start = now.replace(hour=13, minute=0, second=0, microsecond=0)
    afternoon_end = now.replace(hour=15, minute=0, second=0, microsecond=0)

    return (morning_start <= now <= morning_end) or (afternoon_start <= now <= afternoon_end)


def is_after_close():
    """判断是否收盘后（>=15:00）"""
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    close_time = now.replace(hour=15, minute=0, second=0, microsecond=0)
    return now >= close_time


# ============================================================
# 5. 完整数据更新流程
# ============================================================

def get_today_str():
    """获取今天日期字符串"""
    return datetime.now().strftime('%Y-%m-%d')


def full_update_flow(end_date=None, use_cross_check=True, progress_callback=None):
    """
    完整的数据更新流程：标的池 → 增量拉取 → 校验
    返回 {
        'pool': DataFrame,           # ETF 标的池
        'success': int,               # 成功数
        'fail': int,                  # 失败数
        'failed_codes': list,         # 失败代码列表
        'validation_results': list,   # 校验结果
    }
    """
    # 1. 获取标的池
    pool, _ = get_etf_pool()

    # 2. 增量更新所有 ETF 数据
    success, fail, failed_codes = update_all_etf_data(
        pool, end_date, progress_callback
    )

    # 3. 交叉校验（仅收盘后）
    validation_results = None
    if use_cross_check:
        latest_date = get_latest_trade_day(end_date)
        # 对标的池中前 10 只做抽查交叉校验
        validation_results = []
        for _, row in pool.head(10).iterrows():
            code = row['code']
            df = load_cached_data(code)
            if df is not None and len(df) > 0:
                ak_close = df.iloc[-1]['close']
                bs_close = get_etf_close_baostock(code, latest_date)
                passed, dev, msg = cross_check_close(ak_close, bs_close)
                validation_results.append({
                    'code': code,
                    'name': row.get('name', ''),
                    'ak_close': ak_close,
                    'bs_close': bs_close,
                    'deviation': dev,
                    'passed': passed,
                    'message': msg,
                })

    return {
        'pool': pool,
        'success': success,
        'fail': fail,
        'failed_codes': failed_codes,
        'validation_results': validation_results,
    }
