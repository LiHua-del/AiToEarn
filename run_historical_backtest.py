#!/usr/bin/env python3
"""下载隔离的五年行情、审计异常并生成历史回测结果。"""
import argparse

from core.historical_backtest import generate


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true", help="忽略 history_data 缓存并重新下载")
    args = parser.parse_args()
    result = generate(refresh=args.refresh)
    print("\n历史回测完成")
    print(f"区间: {result['period']['start']} ~ {result['period']['end']}")
    print(f"可用 ETF: {result['quality']['usable']}/{result['quality']['requested']}")
    for scheme, payload in result["schemes"].items():
        summary = payload["summary"]
        print(f"方案{scheme}: ¥{summary['final_asset']:,.2f}  累计 {summary['cumulative_return_pct']:+.2f}%  最大回撤 {summary['max_drawdown_pct']:.2f}%")
