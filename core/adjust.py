"""
前复权处理模块
检测 ETF 分红除权导致的净值跳变，做前复权处理
保持最新价格不变，往前所有除权点之前的价格乘以调整因子
"""

import pandas as pd
import numpy as np

# 除权检测阈值：单日跌幅超过此值认为是除权
EX_DIV_THRESHOLD = -0.20


def forward_adjust(df, threshold=EX_DIV_THRESHOLD):
    """
    对 DataFrame 做前复权处理

    检测单日跌幅 < threshold 的除权跳变，累积调整因子后乘以 open/close。
    不修改原始 DataFrame，返回调整后的副本。

    前复权逻辑：
    - 对每个除权日，ratio = close_after / close_before
    - 除权日之前所有行的 adj_factor *= ratio
    - 多次除权从后往前累积

    参数:
        df: DataFrame，需包含 'open' 和 'close' 列
        threshold: 除权检测阈值，默认 -0.20（单日跌幅超20%）

    返回:
        DataFrame，前复权后的数据
    """
    if df is None or len(df) < 2:
        return df

    result = df.copy()

    # 计算日收益率
    pct = result['close'].pct_change()

    # 检测除权日
    ex_div_mask = pct < threshold
    ex_div_dates = result.index[ex_div_mask].tolist()

    if not ex_div_dates:
        return result  # 无除权跳变，直接返回

    # 构建调整因子序列（初始全为1）
    adj_factor = pd.Series(1.0, index=result.index)

    for ex_date in ex_div_dates:
        loc = result.index.get_loc(ex_date)
        if loc == 0:
            continue  # 第一天无法计算前日收盘价
        prev_close = result.iloc[loc - 1]['close']
        cur_close = result.iloc[loc]['close']

        if prev_close <= 0:
            continue

        ratio = cur_close / prev_close
        # 除权日之前所有行的调整因子乘以 ratio
        adj_factor.iloc[:loc] *= ratio

    # 应用前复权
    result['close'] = result['close'] * adj_factor
    result['open'] = result['open'] * adj_factor

    return result
