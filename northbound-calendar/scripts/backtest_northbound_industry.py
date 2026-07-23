#!/usr/bin/env python3
"""
北向行业胜率回测

逻辑：
  每日按行业聚合北向净买入（龙虎榜北向席位口径），
  取当日净买入TOP行业，计算持有T+N后的行业平均收益率
  （用行业内上榜个股的平均涨跌幅代表行业收益）

数据源：
  - 北向：龙虎榜北向席位（沪股通+深股通）
  - 行情：腾讯前复权日K
"""

import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Tuple

import requests

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from update_northbound_gha import (  # noqa: E402
    fetch_eastmoney_api,
    get_northbound_dept_data,
    aggregate_northbound,
    get_stock_name_map,
    is_trading_day,
    format_amount,
    A_STOCK_HOLIDAYS_2026,
)
from stock_industry import load_cache, get_industry  # noqa: E402

HOLD_PERIODS = [5, 10, 20, 30, 60, 90]
START_DATE = "2026-01-01"
END_DATE = "2026-07-21"
MIN_STOCK_PER_INDUSTRY = 3  # 行业内至少3只上榜股才算有效信号
CACHE_DIR = "/tmp/nb_ind_backtest_cache"

os.makedirs(CACHE_DIR, exist_ok=True)


def get_recent_trading_days(start: str, end: str) -> List[str]:
    days = []
    dt = datetime.strptime(start, "%Y-%m-%d")
    end_dt = datetime.strptime(end, "%Y-%m-%d")
    while dt <= end_dt:
        ds = dt.strftime("%Y-%m-%d")
        if is_trading_day(ds):
            days.append(ds)
        dt += timedelta(days=1)
    return days


def shift_trading_day(date_str: str, n: int) -> str:
    """往后推n个交易日"""
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    count = 0
    while count < n:
        dt += timedelta(days=1)
        ds = dt.strftime("%Y-%m-%d")
        if is_trading_day(ds):
            count += 1
    return dt.strftime("%Y-%m-%d")


def fetch_kline_tencent(code: str, days: int = 250) -> List[Dict]:
    """腾讯前复权日K"""
    cache_file = f"{CACHE_DIR}/kline_{code}.json"
    if os.path.exists(cache_file):
        with open(cache_file) as f:
            return json.load(f)

    # 腾讯接口用sz/sh前缀
    if code.startswith("6") or code.startswith("9"):
        prefix = "sh"
    else:
        prefix = "sz"

    url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
    params = {
        "param": f"{prefix}{code},day,,,{days},qfq",
    }
    try:
        r = requests.get(url, params=params, timeout=10)
        data = r.json()
        klines = data.get("data", {}).get(f"{prefix}{code}", {}).get("qfqday", [])
        if not klines:
            klines = data.get("data", {}).get(f"{prefix}{code}", {}).get("day", [])
        result = []
        for k in klines:
            result.append({
                "date": k[0],
                "open": float(k[1]),
                "close": float(k[2]),
                "high": float(k[3]),
                "low": float(k[4]),
                "volume": float(k[5]) if len(k) > 5 else 0,
            })
        with open(cache_file, "w") as f:
            json.dump(result, f)
        return result
    except Exception as e:
        print(f"    K线获取失败 {code}: {e}")
        return []


def get_close_on_date(klines: List[Dict], date_str: str) -> float:
    """获取指定日期收盘价，找不到往前找最近的"""
    for k in klines:
        if k["date"] == date_str:
            return k["close"]
    # 往前找最近的交易日
    date_dt = datetime.strptime(date_str, "%Y-%m-%d")
    for k in reversed(klines):
        k_dt = datetime.strptime(k["date"], "%Y-%m-%d")
        if k_dt <= date_dt:
            return k["close"]
    return 0.0


def get_daily_northbound_by_industry(date_str: str, industry_cache: Dict) -> Dict[str, List[Dict]]:
    """
    获取指定日期北向数据，按行业分组
    返回: {行业名: [{code, name, net_wan, ...}, ...]}
    """
    cache_file = f"{CACHE_DIR}/nb_{date_str}.json"
    if os.path.exists(cache_file):
        with open(cache_file) as f:
            return json.load(f)

    try:
        name_map = get_stock_name_map(date_str)
        rows = get_northbound_dept_data(date_str)
        agg = aggregate_northbound(rows, name_map)
    except Exception as e:
        print(f"  ⚠️  {date_str} 北向数据获取失败: {e}")
        return {}

    # 按行业分组
    industry_map = {}
    for s in agg["stocks"]:
        if s["net_wan"] <= 0:  # 只看净买入
            continue
        ind = get_industry(s["code"], industry_cache) or "未分类"
        if ind not in industry_map:
            industry_map[ind] = []
        industry_map[ind].append(s)

    with open(cache_file, "w") as f:
        json.dump(industry_map, f)
    return industry_map


def calc_industry_return(industry_stocks: List[Dict], buy_date: str,
                         hold_days: int, klines_cache: Dict) -> Tuple[float, int]:
    """
    计算行业持有N天后的平均收益率
    返回: (平均收益率%, 有效股票数)
    """
    sell_date = shift_trading_day(buy_date, hold_days)
    returns = []

    for s in industry_stocks:
        code = s["code"]
        if code not in klines_cache:
            klines_cache[code] = fetch_kline_tencent(code)
        kl = klines_cache[code]
        if not kl:
            continue
        buy_close = get_close_on_date(kl, buy_date)
        sell_close = get_close_on_date(kl, sell_date)
        if buy_close > 0 and sell_close > 0:
            ret = (sell_close - buy_close) / buy_close * 100
            returns.append(ret)

    if len(returns) < MIN_STOCK_PER_INDUSTRY:
        return 0.0, 0

    avg_ret = sum(returns) / len(returns)
    return avg_ret, len(returns)


def main():
    print("=" * 60)
    print("北向行业胜率回测")
    print("=" * 60)

    industry_cache = load_cache()
    print(f"行业映射: {len(industry_cache)} 只股票")

    trading_days = get_recent_trading_days(START_DATE, END_DATE)
    print(f"回测区间: {START_DATE} ~ {END_DATE}, 共 {len(trading_days)} 个交易日")

    klines_cache = {}

    # 按周期收集结果
    results = {p: {"returns": [], "win_count": 0, "total": 0,
                   "industry_wins": {}, "industry_total": {}}
               for p in HOLD_PERIODS}

    # 每个行业每个周期的收益记录
    industry_period_returns = {}  # {行业: {周期: [收益列表]}}

    for i, date_str in enumerate(trading_days):
        print(f"\n[{i+1}/{len(trading_days)}] {date_str}")
        industry_map = get_daily_northbound_by_industry(date_str, industry_cache)
        if not industry_map:
            continue

        # 计算每个行业的总净买入，筛选符合条件的行业
        valid_industries = []
        for ind, stocks in industry_map.items():
            if ind == "未分类":
                continue
            if len(stocks) < MIN_STOCK_PER_INDUSTRY:
                continue
            total_net = sum(s["net_wan"] for s in stocks)
            valid_industries.append((ind, total_net, stocks))

        # 按净买入排序，取TOP10
        valid_industries.sort(key=lambda x: x[1], reverse=True)
        top_industries = valid_industries[:10]

        if not top_industries:
            continue

        print(f"  有效行业: {len(valid_industries)} 个, TOP10: {[x[0] for x in top_industries]}")

        for period in HOLD_PERIODS:
            for ind_name, total_net, stocks in top_industries:
                avg_ret, n_valid = calc_industry_return(stocks, date_str, period, klines_cache)
                if n_valid == 0:
                    continue

                results[period]["returns"].append(avg_ret)
                results[period]["total"] += 1
                if avg_ret > 0:
                    results[period]["win_count"] += 1

                if ind_name not in industry_period_returns:
                    industry_period_returns[ind_name] = {p: [] for p in HOLD_PERIODS}
                industry_period_returns[ind_name][period].append(avg_ret)

        # 每10天保存一次缓存
        if (i + 1) % 10 == 0:
            time.sleep(0.1)

    # 汇总统计
    print("\n" + "=" * 60)
    print("回测结果汇总")
    print("=" * 60)

    overall = {}
    for period in HOLD_PERIODS:
        r = results[period]
        if r["total"] == 0:
            continue
        rets = r["returns"]
        avg_ret = sum(rets) / len(rets)
        win_rate = r["win_count"] / r["total"] * 100

        # 盈亏比
        wins = [x for x in rets if x > 0]
        losses = [abs(x) for x in rets if x < 0]
        avg_win = sum(wins) / len(wins) if wins else 0
        avg_loss = sum(losses) / len(losses) if losses else 1
        pl_ratio = avg_win / avg_loss if avg_loss > 0 else 0

        median_ret = sorted(rets)[len(rets) // 2]

        overall[str(period)] = {
            "sample_count": r["total"],
            "win_rate": round(win_rate, 2),
            "avg_return_pct": round(avg_ret, 2),
            "median_return_pct": round(median_ret, 2),
            "profit_loss_ratio": round(pl_ratio, 2),
            "max_return_pct": round(max(rets), 2),
            "min_return_pct": round(min(rets), 2),
        }

        print(f"T+{period}: 样本{r['total']}, 胜率{win_rate:.2f}%, "
              f"平均收益{avg_ret:+.2f}%, 盈亏比{pl_ratio:.2f}")

    # 分行业统计（样本≥10）
    by_industry = {}
    for ind, periods in industry_period_returns.items():
        ind_data = {}
        valid = False
        for period, rets in periods.items():
            if len(rets) < 3:
                continue
            avg_ret = sum(rets) / len(rets)
            win_count = sum(1 for r in rets if r > 0)
            win_rate = win_count / len(rets) * 100

            wins = [x for x in rets if x > 0]
            losses = [abs(x) for x in rets if x < 0]
            avg_win = sum(wins) / len(wins) if wins else 0
            avg_loss = sum(losses) / len(losses) if losses else 1
            pl_ratio = avg_win / avg_loss if avg_loss > 0 else 0

            ind_data[str(period)] = {
                "sample_count": len(rets),
                "win_rate": round(win_rate, 2),
                "avg_return_pct": round(avg_ret, 2),
                "profit_loss_ratio": round(pl_ratio, 2),
            }
            if len(rets) >= 10:
                valid = True
        if valid:
            by_industry[ind] = ind_data

    print(f"\n有统计意义的行业数（样本≥10）: {len(by_industry)}")

    result = {
        "overall": overall,
        "by_industry": by_industry,
        "config": {
            "start_date": START_DATE,
            "end_date": END_DATE,
            "min_stock_per_industry": MIN_STOCK_PER_INDUSTRY,
            "hold_periods": HOLD_PERIODS,
            "top_n_industries": 10,
        },
        "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

    output_path = "data/northbound_industry_backtest.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"\n✅ 结果已保存: {output_path}")


if __name__ == "__main__":
    main()
