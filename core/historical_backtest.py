"""五年历史回测与数据质量审计。

数据与日常仪表盘隔离：
- 主行情：新浪 ETF 日线 OHLC（akshare.fund_etf_hist_sina）
- 分红：新浪 ETF 累计分红（差分得到单次现金分红）
- 份额折算：仅处理隔夜跳空超过 30% 且日内波动正常的事件
- 输出：app/data/history_backtest.json；原始缓存 history_data/（git ignore）
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from datetime import datetime

import akshare as ak
import numpy as np
import pandas as pd

from config import (
    BASE_DIR, DEVIATION_WEIGHTS, MA5_BUFFER_PCT, MA5_CONSECUTIVE_DAYS,
    MA5_REDUCE_TO_PCT, PORTFOLIO_STOP_THRESHOLD, SECTOR_KEYWORDS,
    TRADE_COST, WEIGHTS,
)
from core.data_fetcher import extract_core_keyword, get_etf_pool


INITIAL_CAPITAL = 300_000.0
TOP_N = 3
HISTORY_START = "2020-10-01"  # 2021-01-01 前保留 MA60 预热窗口
BACKTEST_START = "2021-01-01"
DATA_DIR = os.path.join(BASE_DIR, "history_data")
RESULT_PATH = os.path.join(BASE_DIR, "app", "data", "history_backtest.json")


def _symbol(code: str) -> str:
    return ("sh" if code.startswith(("5", "6")) else "sz") + code


def _sector(name: str) -> str:
    for sector, keywords in SECTOR_KEYWORDS.items():
        if any(keyword in name for keyword in keywords):
            return sector
    return extract_core_keyword(name)


def _download_one(code: str, name: str, refresh: bool = False) -> tuple[pd.DataFrame, list[dict]]:
    os.makedirs(DATA_DIR, exist_ok=True)
    path = os.path.join(DATA_DIR, f"{code}.csv")
    dividend_path = os.path.join(DATA_DIR, f"{code}.dividend.csv")

    if not refresh and os.path.exists(path):
        raw = pd.read_csv(path, parse_dates=["date"])
    else:
        raw = ak.fund_etf_hist_sina(symbol=_symbol(code))
        if raw is None or raw.empty:
            raise ValueError("新浪日线为空")
        raw["date"] = pd.to_datetime(raw["date"])
        raw.to_csv(path, index=False)
        time.sleep(0.12)

    if not refresh and os.path.exists(dividend_path):
        dividends = pd.read_csv(dividend_path, parse_dates=["date"])
    else:
        try:
            dividends = ak.fund_etf_dividend_sina(symbol=_symbol(code))
            if dividends is None or dividends.empty:
                dividends = pd.DataFrame(columns=["date", "cumulative"])
            else:
                dividends = dividends.rename(columns={"日期": "date", "累计分红": "cumulative"})
                dividends["date"] = pd.to_datetime(dividends["date"])
            dividends.to_csv(dividend_path, index=False)
        except Exception:
            dividends = pd.DataFrame(columns=["date", "cumulative"])

    raw["date"] = pd.to_datetime(raw["date"])
    raw = raw.sort_values("date").drop_duplicates("date", keep="last")
    raw = raw[raw["date"] >= pd.Timestamp(HISTORY_START)].copy()
    for col in ["open", "high", "low", "close", "volume", "amount"]:
        if col in raw:
            raw[col] = pd.to_numeric(raw[col], errors="coerce")
    raw = raw.dropna(subset=["open", "close"])

    events: list[dict] = []
    # OHLC 基础约束。
    bad_ohlc = raw[(raw["high"] < raw[["open", "close"]].max(axis=1)) |
                   (raw["low"] > raw[["open", "close"]].min(axis=1))]
    for _, row in bad_ohlc.iterrows():
        events.append({"code": code, "name": name, "date": row["date"].strftime("%Y-%m-%d"),
                       "type": "invalid_ohlc", "action": "excluded", "move_pct": None})
    if not bad_ohlc.empty:
        raw = raw.drop(index=bad_ohlc.index)

    raw = raw.set_index("date").sort_index()
    adjusted = raw.copy()

    # 现金分红：累计分红差分得到单次分红，构造总回报前复权序列。
    if not dividends.empty:
        dividends = dividends.sort_values("date")
        dividends["cash"] = dividends["cumulative"].diff().fillna(dividends["cumulative"])
        for _, item in dividends.iterrows():
            ex_date = pd.Timestamp(item["date"])
            cash = float(item["cash"])
            if cash <= 0 or ex_date not in adjusted.index:
                continue
            loc = adjusted.index.get_loc(ex_date)
            if loc == 0:
                continue
            prev_close = float(adjusted.iloc[loc - 1]["close"])
            factor = max((prev_close - cash) / prev_close, 0.01)
            adjusted.iloc[:loc, adjusted.columns.get_indexer(["open", "high", "low", "close"])] *= factor
            events.append({"code": code, "name": name, "date": ex_date.strftime("%Y-%m-%d"),
                           "type": "cash_dividend", "action": "adjusted", "move_pct": round(cash / prev_close * 100, 2),
                           "detail": f"每份分红约 {cash:.4f} 元"})

    # 份额折算/数据量纲切换：A 股 ETF 正常隔夜涨跌不应超过 30%。
    # 用开盘/昨收比率调整历史段，避免把当天真实日内涨跌吞进调整因子。
    prev_close = adjusted["close"].shift(1)
    gap = adjusted["open"] / prev_close - 1
    intraday = adjusted["close"] / adjusted["open"] - 1
    candidates = adjusted.index[(gap.abs() > 0.30) & (intraday.abs() < 0.20)]
    for event_date in candidates:
        loc = adjusted.index.get_loc(event_date)
        if loc == 0:
            continue
        factor = float(adjusted.loc[event_date, "open"] / adjusted.iloc[loc - 1]["close"])
        adjusted.iloc[:loc, adjusted.columns.get_indexer(["open", "high", "low", "close"])] *= factor
        events.append({"code": code, "name": name, "date": event_date.strftime("%Y-%m-%d"),
                       "type": "share_split_or_scale_change", "action": "adjusted",
                       "move_pct": round((factor - 1) * 100, 2),
                       "detail": f"隔夜比例 {factor:.6f}，按开盘/昨收修复"})

    # 调整后仍超过 15% 的日变化作为真实极端行情保留，并显式披露。
    returns = adjusted["close"].pct_change()
    for event_date, value in returns[returns.abs() >= 0.15].items():
        events.append({"code": code, "name": name, "date": event_date.strftime("%Y-%m-%d"),
                       "type": "large_market_move", "action": "kept",
                       "move_pct": round(float(value) * 100, 2),
                       "detail": "复权后仍为大幅行情，保留并进入回测"})

    adjusted["name"] = name
    adjusted["code"] = code
    return adjusted, events


def prepare_history(refresh: bool = False) -> tuple[dict[str, pd.DataFrame], dict, list[dict]]:
    pool, name_map = get_etf_pool()
    data: dict[str, pd.DataFrame] = {}
    events: list[dict] = []
    failures: list[dict] = []
    for i, row in pool.iterrows():
        code, name = str(row["code"]), str(row["name"])
        try:
            frame, frame_events = _download_one(code, name, refresh=refresh)
            if len(frame) >= 60:
                data[code] = frame
                events.extend(frame_events)
            else:
                failures.append({"code": code, "name": name, "reason": f"仅 {len(frame)} 行"})
        except Exception as exc:
            failures.append({"code": code, "name": name, "reason": str(exc)[:160]})
        print(f"[{i + 1:3d}/{len(pool)}] {code} {name}: {'OK' if code in data else 'FAIL'}")

    latest_dates = [frame.index.max() for frame in data.values()]
    quality = {
        "requested": int(len(pool)),
        "usable": len(data),
        "failed": len(failures),
        "failures": failures,
        "common_latest_date": min(latest_dates).strftime("%Y-%m-%d") if latest_dates else None,
        "max_latest_date": max(latest_dates).strftime("%Y-%m-%d") if latest_dates else None,
        "full_five_year_coverage": sum(frame.index.min() <= pd.Timestamp(BACKTEST_START) for frame in data.values()),
        "corporate_actions_adjusted": sum(e["type"] in {"cash_dividend", "share_split_or_scale_change"} for e in events),
        "large_moves_kept": sum(e["type"] == "large_market_move" for e in events),
        "source": "新浪财经 ETF 日线 OHLC + 累计分红",
        "limitations": [
            "标的池使用当前可交易 ETF，存在幸存者偏差",
            "缺少历史每日规模与流动性快照，无法还原每个历史时点的完整准入池",
            "尾盘信号使用当日收盘价近似成交，实际成交可能存在滑点",
        ],
    }
    return data, quality, events


def _score_on_date(closes: pd.DataFrame, opens: pd.DataFrame, ma60: pd.DataFrame,
                   active: pd.DataFrame, date: pd.Timestamp, scheme: str,
                   names: dict[str, str]) -> list[dict]:
    w = WEIGHTS[scheme]
    candidates = []
    deviations = []
    for code in closes.columns:
        if not bool(active.at[date, code]):
            continue
        series = closes[code].loc[:date].dropna()
        open_series = opens[code].loc[:date].dropna()
        if len(series) < 60 or len(open_series) < 20:
            continue
        price = float(series.iloc[-1])
        ma = ma60.at[date, code]
        if pd.notna(ma) and price <= ma:
            continue
        gain = (price / float(series.iloc[-21]) - 1) * 100
        up_days = int((series.iloc[-20:].values > open_series.reindex(series.index).iloc[-20:].values).sum())
        dev = (price / float(series.iloc[-20:].mean()) - 1) * 100
        base = w["w1"] * gain + w["w2"] * up_days
        item = {"code": code, "name": names.get(code, code), "sector": _sector(names.get(code, code)),
                "price": price, "score": base, "deviation": dev}
        candidates.append(item)
        deviations.append(dev)
    median = float(np.median(deviations)) if deviations else 0.0
    for item in candidates:
        item["score"] -= DEVIATION_WEIGHTS[scheme] * max(0.0, item["deviation"] - median)
    sector_best = {}
    for item in candidates:
        if item["sector"] not in sector_best or item["score"] > sector_best[item["sector"]]["score"]:
            sector_best[item["sector"]] = item
    return sorted(sector_best.values(), key=lambda x: x["score"], reverse=True)[:TOP_N]


def _annual_stats(curve: pd.Series, initial_capital: float = INITIAL_CAPITAL) -> list[dict]:
    records = []
    previous_year_end = float(initial_capital)
    for year, values in curve.groupby(curve.index.year):
        if values.empty:
            continue
        start_asset = previous_year_end
        end_asset = float(values.iloc[-1])
        # 年度回撤从上一年期末（本年期初）开始计算，包含跨年首日变化。
        with_start = pd.concat([pd.Series([start_asset]), values.reset_index(drop=True)], ignore_index=True)
        peak = with_start.cummax()
        max_dd = float(((with_start - peak) / peak).min() * 100)
        records.append({"year": int(year), "start_asset": round(start_asset, 2),
                        "end_asset": round(end_asset, 2),
                        "return_pct": round((end_asset / start_asset - 1) * 100, 2),
                        "max_drawdown_pct": round(max_dd, 2),
                        "is_partial": bool(year == datetime.now().year)})
        previous_year_end = end_asset
    return records


def run_five_year_backtest(data: dict[str, pd.DataFrame], quality: dict,
                           events: list[dict], backtest_start: str = BACKTEST_START) -> dict:
    all_dates = sorted(set().union(*(set(frame.index) for frame in data.values())))
    index = pd.DatetimeIndex(all_dates)
    end_date = pd.Timestamp(quality["common_latest_date"])
    index = index[(index >= pd.Timestamp(HISTORY_START)) & (index <= end_date)]
    names = {code: str(frame["name"].iloc[-1]) for code, frame in data.items()}

    close_raw = pd.DataFrame({code: frame["close"].reindex(index) for code, frame in data.items()}, index=index)
    open_raw = pd.DataFrame({code: frame["open"].reindex(index) for code, frame in data.items()}, index=index)
    active = close_raw.notna()
    closes = close_raw.ffill()
    opens = open_raw.ffill()
    ma60 = closes.rolling(60, min_periods=60).mean()
    dates = index[index >= pd.Timestamp(backtest_start)]
    week_last = pd.Series(dates, index=dates).groupby([dates.isocalendar().year, dates.isocalendar().week]).last().tolist()
    week_last = set(pd.Timestamp(x) for x in week_last)

    schemes = {}
    for scheme in ["A", "B", "C"]:
        cash = INITIAL_CAPITAL
        holdings: dict[str, dict] = {}
        below_ma5: dict[str, int] = {}
        period_peak = INITIAL_CAPITAL
        portfolio_stopped = False
        curve = []
        trades = 0

        def asset(date):
            return cash + sum(h["shares"] * float(closes.at[date, code]) for code, h in holdings.items())

        for date in dates:
            total = asset(date)
            period_peak = max(period_peak, total)

            # 每日分层风控。
            if holdings and total / period_peak - 1 <= -PORTFOLIO_STOP_THRESHOLD:
                for code, holding in list(holdings.items()):
                    proceeds = holding["shares"] * float(closes.at[date, code]) * (1 - TRADE_COST)
                    cash += proceeds
                    trades += 1
                holdings.clear()
                portfolio_stopped = True
                total = cash
            elif holdings:
                for code, holding in list(holdings.items()):
                    series = closes[code].loc[:date].dropna()
                    if len(series) < 5:
                        continue
                    ma5 = float(series.iloc[-5:].mean())
                    below_ma5[code] = below_ma5.get(code, 0) + 1 if float(series.iloc[-1]) < ma5 * (1 - MA5_BUFFER_PCT) else 0
                    if below_ma5[code] >= MA5_CONSECUTIVE_DAYS and not holding.get("reduced"):
                        sell_shares = holding["shares"] * (1 - MA5_REDUCE_TO_PCT)
                        cash += sell_shares * float(series.iloc[-1]) * (1 - TRADE_COST)
                        holding["shares"] -= sell_shares
                        holding["reduced"] = True
                        trades += 1
                total = asset(date)

            if date in week_last:
                top3 = _score_on_date(closes, opens, ma60, active, date, scheme, names)
                if portfolio_stopped and (len(top3) < TOP_N or not all(x["score"] > 0 for x in top3)):
                    top3 = []
                if top3:
                    targets = {x["code"]: x for x in top3}
                    total = asset(date)
                    # 为买入侧手续费预留现金，禁止回测因手续费形成隐性杠杆。
                    target_value = total / (len(targets) * (1 + TRADE_COST))
                    for code in list(holdings):
                        if code not in targets:
                            cash += holdings[code]["shares"] * float(closes.at[date, code]) * (1 - TRADE_COST)
                            del holdings[code]
                            trades += 1
                    for code, item in targets.items():
                        price = float(closes.at[date, code])
                        old_shares = holdings.get(code, {}).get("shares", 0.0)
                        target_shares = target_value / price
                        diff = target_shares - old_shares
                        if diff > 0:
                            cash -= diff * price * (1 + TRADE_COST)
                        elif diff < 0:
                            cash += (-diff) * price * (1 - TRADE_COST)
                        if abs(diff) > 1e-8:
                            trades += 1
                        holdings[code] = {"shares": target_shares, "reduced": False}
                    below_ma5 = {code: 0 for code in holdings}
                    period_peak = asset(date)
                    portfolio_stopped = False

            curve.append((date, asset(date)))

        series = pd.Series(dict(curve)).sort_index()
        peak = series.cummax()
        max_dd = float(((series - peak) / peak).min() * 100)
        daily = series.pct_change().dropna()
        sharpe = float(np.sqrt(252) * (daily.mean() - 0.03 / 252) / daily.std()) if daily.std() else 0.0
        schemes[scheme] = {
            "dates": [d.strftime("%Y-%m-%d") for d in series.index],
            "assets": [round(float(v), 2) for v in series.values],
            "annual": _annual_stats(series),
            "summary": {"initial_asset": INITIAL_CAPITAL, "final_asset": round(float(series.iloc[-1]), 2),
                        "cumulative_return_pct": round((float(series.iloc[-1]) / INITIAL_CAPITAL - 1) * 100, 2),
                        "max_drawdown_pct": round(max_dd, 2), "sharpe": round(sharpe, 2),
                        "trade_count": trades},
        }

    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "period": {"start": backtest_start, "end": quality["common_latest_date"]},
        "initial_capital": INITIAL_CAPITAL,
        "methodology": {"top_n": TOP_N, "rebalance": "每周最后一个交易日尾盘",
                        "compounding": "30万元一次投入，跨年度连续复利",
                        "trade_cost_each_side": TRADE_COST,
                        "risk_control": "MA5个股减仓 + 8%组合回撤清仓",
                        "price_basis": "新浪交易所ETF OHLC，经现金分红和份额折算前复权"},
        "quality": quality,
        "anomalies": sorted(events, key=lambda x: (x["date"], x["code"]), reverse=True),
        "schemes": schemes,
    }


def generate(refresh: bool = False) -> dict:
    data, quality, events = prepare_history(refresh=refresh)
    result = run_five_year_backtest(data, quality, events)
    ytd_start = f"{pd.Timestamp(quality['common_latest_date']).year}-01-01"
    ytd_result = run_five_year_backtest(data, quality, events, backtest_start=ytd_start)
    result["audited_ytd"] = {
        "period": ytd_result["period"],
        "schemes": ytd_result["schemes"],
    }
    os.makedirs(os.path.dirname(RESULT_PATH), exist_ok=True)
    with open(RESULT_PATH, "w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
    return result
