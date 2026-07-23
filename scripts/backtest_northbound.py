#!/usr/bin/env python3
"""
北向资金中长线回测脚本

策略：
  每日筛选北向净买入≥门槛（默认1000万）的个股，以当日收盘价买入，
  分别持有 T+5/T+10/T+20/T+30/T+60/T+90 个交易日后卖出，
  统计各周期的胜率、平均收益率、盈亏比。

统计维度：
  1. 总体统计
  2. 分行业统计（样本≥5才展示）
  3. 分净买入档位统计

数据源：
  - 北向明细：东方财富龙虎榜北向席位 API
  - 股价K线：腾讯前复权日K API
  - 行业分类：本地 stock_industry.json

约束：纯 Python + 标准库 + requests
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple

import requests

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "data"))
OUTPUT_DIR = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "data"))

# ========== 常量配置 ==========

EASTMONEY_API_BASE = "https://datacenter-web.eastmoney.com/api/data/v1/get"
EASTMONEY_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://data.eastmoney.com/",
    "Accept": "application/json, text/plain, */*",
}

REPORT_BUY_DEPT = "RPT_BILLBOARD_DAILYDETAILSBUY"
REPORT_SELL_DEPT = "RPT_BILLBOARD_DAILYDETAILSSELL"
REPORT_DAILY_DETAILS = "RPT_DAILYBILLBOARD_DETAILSNEW"

NORTHBOUND_KEYWORDS = ("沪股通专用", "深股通专用")

# A股2026法定假日
A_STOCK_HOLIDAYS_2026 = {
    "2026-01-01", "2026-01-02", "2026-01-03",
    "2026-02-16", "2026-02-17", "2026-02-18", "2026-02-19", "2026-02-20",
    "2026-02-23",
    "2026-04-06",
    "2026-05-01", "2026-05-04", "2026-05-05",
    "2026-06-19",
    "2026-09-25",
    "2026-10-01", "2026-10-02", "2026-10-05", "2026-10-06", "2026-10-07",
}

HK_HOLIDAYS_2026 = {
    "2026-07-01",
}

# 腾讯K线接口
KLINE_API_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"

# 净买入档位（万元）
NET_BUY_BUCKETS = [
    (1000, 3000, "1000-3000万"),
    (3000, 5000, "3000-5000万"),
    (5000, 10000, "5000万-1亿"),
    (10000, float("inf"), "1亿以上"),
]


# ========== 工具函数 ==========

def _safe_num(v) -> float:
    if v is None:
        return 0.0
    try:
        return float(v)
    except (ValueError, TypeError):
        return 0.0


def log_info(msg: str) -> None:
    print(f"[INFO] {msg}")


def log_warn(msg: str) -> None:
    print(f"[WARN] {msg}", file=sys.stderr)


def log_error(msg: str) -> None:
    print(f"[ERROR] {msg}", file=sys.stderr)


def is_trading_day(date_str: str) -> bool:
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    if dt.weekday() >= 5:
        return False
    if date_str in A_STOCK_HOLIDAYS_2026:
        return False
    return True


def is_northbound_open(date_str: str) -> bool:
    if not is_trading_day(date_str):
        return False
    if date_str in HK_HOLIDAYS_2026:
        return False
    return True


def gen_trading_days(start: str, end: str) -> List[str]:
    days = []
    cur = datetime.strptime(start, "%Y-%m-%d")
    end_dt = datetime.strptime(end, "%Y-%m-%d")
    while cur <= end_dt:
        ds = cur.strftime("%Y-%m-%d")
        if is_trading_day(ds):
            days.append(ds)
        cur += timedelta(days=1)
    return days


def shift_trading_day(date_str: str, n: int, trading_day_list: List[str]) -> Optional[str]:
    """
    从 date_str 开始往后数 n 个交易日（不含当日）。
    trading_day_list 为升序的交易日列表。
    """
    try:
        idx = trading_day_list.index(date_str)
    except ValueError:
        return None
    target = idx + n
    if target >= len(trading_day_list):
        return None
    return trading_day_list[target]


def code_to_gtimg_prefix(code: str) -> str:
    if not code:
        return ""
    code = code.strip()
    if code.startswith("6") or code.startswith("9"):
        return "sh" + code
    elif code.startswith("0") or code.startswith("3") or code.startswith("2"):
        return "sz" + code
    elif code.startswith("4") or code.startswith("8"):
        return "bj" + code
    return "sh" + code


# ========== 东财API ==========

def fetch_eastmoney_api(report_name: str, filter_expr: str,
                        sort_columns: str, sort_types: str = "-1",
                        page_size: int = 200, max_pages: int = 10,
                        retries: int = 3) -> List[Dict]:
    all_data = []
    for attempt in range(retries):
        try:
            for page in range(1, max_pages + 1):
                params = {
                    "sortColumns": sort_columns,
                    "sortTypes": sort_types,
                    "pageSize": str(page_size),
                    "pageNumber": str(page),
                    "reportName": report_name,
                    "columns": "ALL",
                    "source": "WEB",
                    "client": "WEB",
                    "filter": filter_expr,
                }
                resp = requests.get(
                    EASTMONEY_API_BASE, params=params,
                    headers=EASTMONEY_HEADERS, timeout=15,
                )
                resp.raise_for_status()
                result = resp.json()
                if not result.get("success") or not result.get("result"):
                    break
                data = result["result"].get("data", [])
                if not data:
                    break
                all_data.extend(data)
                count = result["result"].get("count", 0)
                if page * page_size >= count:
                    break
            return all_data
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(1 + attempt)
            else:
                log_warn(f"东财API请求失败({report_name}): {e}")
    return all_data


def get_stock_name_map(date_str: str) -> Dict[str, str]:
    name_map = {}
    rows = fetch_eastmoney_api(
        REPORT_DAILY_DETAILS,
        filter_expr=f'(TRADE_DATE=\'{date_str}\')',
        sort_columns="SECURITY_CODE",
        sort_types="1",
        page_size=500, max_pages=5,
    )
    for r in rows:
        code = r.get("SECURITY_CODE", "")
        name = r.get("SECURITY_NAME_ABBR", "")
        if code:
            name_map[code] = name
    return name_map


def get_northbound_dept_data(date_str: str) -> List[Dict]:
    all_rows = []
    for rpt in [REPORT_BUY_DEPT, REPORT_SELL_DEPT]:
        raw_data = fetch_eastmoney_api(
            rpt,
            filter_expr=f"(TRADE_DATE='{date_str}')",
            sort_columns="TRADE_DATE,SECURITY_CODE",
            sort_types="-1,1",
            page_size=200, max_pages=10,
        )
        all_rows.extend(raw_data)

    # 筛选北向席位
    northbound = [
        r for r in all_rows
        if any(kw in r.get("OPERATEDEPT_NAME", "") for kw in NORTHBOUND_KEYWORDS)
    ]

    # 去重：同股票+同席位+同TRADE_ID
    seen = set()
    unique = []
    for r in northbound:
        key = (r.get("SECURITY_CODE", ""), r.get("OPERATEDEPT_NAME", ""), r.get("TRADE_ID", ""))
        if key in seen:
            continue
        seen.add(key)
        unique.append(r)
    return unique


def aggregate_northbound(rows: List[Dict], name_map: Dict[str, str]) -> Dict:
    stock_map = {}
    total_net = 0.0
    for r in rows:
        code = r.get("SECURITY_CODE", "")
        if not code:
            continue
        # BUY/SELL 字段单位为元，转换为万元
        buy = _safe_num(r.get("BUY")) / 10000.0
        sell = _safe_num(r.get("SELL")) / 10000.0
        net = _safe_num(r.get("NET")) / 10000.0
        if net == 0 and (buy != 0 or sell != 0):
            net = buy - sell
        if code not in stock_map:
            stock_map[code] = {
                "code": code,
                "name": name_map.get(code, ""),
                "buy_wan": 0.0,
                "sell_wan": 0.0,
                "net_wan": 0.0,
            }
        stock_map[code]["buy_wan"] += buy
        stock_map[code]["sell_wan"] += sell
        stock_map[code]["net_wan"] += net
        total_net += net

    stocks = sorted(stock_map.values(), key=lambda x: x["net_wan"], reverse=True)
    for s in stocks:
        s["buy_wan"] = round(s["buy_wan"], 2)
        s["sell_wan"] = round(s["sell_wan"], 2)
        s["net_wan"] = round(s["net_wan"], 2)

    return {"stocks": stocks, "total_net_wan": round(total_net, 2)}


def get_northbound_daily(date_str: str) -> Dict:
    if not is_northbound_open(date_str):
        return {"date": date_str, "stocks": [], "total_net_wan": 0.0}
    name_map = get_stock_name_map(date_str)
    dept_rows = get_northbound_dept_data(date_str)
    if not dept_rows:
        return {"date": date_str, "stocks": [], "total_net_wan": 0.0}
    agg = aggregate_northbound(dept_rows, name_map)
    return {
        "date": date_str,
        "stocks": agg["stocks"],
        "total_net_wan": agg["total_net_wan"],
    }


# ========== K线数据（腾讯前复权） ==========

def fetch_kline(code: str, count: int = 250) -> List[Dict]:
    """获取某只股票前复权日K线，返回按日期升序"""
    gtimg_code = code_to_gtimg_prefix(code)
    count = max(count, 120)
    params = {"param": f"{gtimg_code},day,,,{count},qfq"}
    try:
        r = requests.get(
            KLINE_API_URL, params=params, timeout=10,
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"},
        )
        r.raise_for_status()
        data = r.json()
        if data.get("code") != 0 or not data.get("data"):
            return []
        stock_data = list(data["data"].values())[0]
        kline_data = stock_data.get("qfqday") or stock_data.get("day") or []
        if not kline_data:
            return []
        klines = []
        prev_close = None
        for row in kline_data:
            if len(row) < 6:
                continue
            date = row[0]
            open_p = _safe_num(row[1])
            close_p = _safe_num(row[2])
            high_p = _safe_num(row[3])
            low_p = _safe_num(row[4])
            volume = _safe_num(row[5])
            change_pct = 0.0
            if prev_close and prev_close > 0:
                change_pct = (close_p - prev_close) / prev_close * 100.0
            klines.append({
                "date": date, "open": open_p, "close": close_p,
                "high": high_p, "low": low_p,
                "volume": volume, "change_pct": round(change_pct, 2),
            })
            prev_close = close_p
        return klines
    except Exception as e:
        log_warn(f"K线获取失败 {code}: {e}")
        return []


# ========== 行业数据 ==========

def load_industry_map(path: str) -> Dict[str, str]:
    if not os.path.isfile(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        return {k: v for k, v in data.items()}
    return {}


def get_bucket_label(net_wan: float) -> str:
    for lo, hi, label in NET_BUY_BUCKETS:
        if lo <= net_wan < hi:
            return label
    return ""


# ========== 核心回测逻辑 ==========

def collect_signals(nb_data: Dict[str, Dict], threshold_wan: float,
                    industry_map: Dict[str, str]) -> List[Dict]:
    """
    从北向数据中收集所有满足净买入门槛的信号。
    返回: [{date, code, name, net_wan, industry}]
    """
    signals = []
    for date_str, day_data in nb_data.items():
        for s in day_data.get("stocks", []):
            net = s.get("net_wan", 0.0)
            if net >= threshold_wan:
                code = s["code"]
                signals.append({
                    "date": date_str,
                    "code": code,
                    "name": s.get("name", ""),
                    "net_wan": net,
                    "industry": industry_map.get(code, "未分类"),
                })
    return signals


def compute_stats(returns: List[float]) -> Dict:
    """根据收益率列表计算胜率、平均收益率、盈亏比、样本数"""
    n = len(returns)
    if n == 0:
        return {
            "sample_count": 0,
            "win_rate": 0.0,
            "avg_return_pct": 0.0,
            "profit_loss_ratio": 0.0,
            "median_return_pct": 0.0,
            "max_return_pct": 0.0,
            "min_return_pct": 0.0,
        }
    sorted_rets = sorted(returns)
    wins = [r for r in returns if r > 0]
    losses = [r for r in returns if r < 0]
    win_rate = len(wins) / n * 100.0
    avg_ret = sum(returns) / n
    median_ret = sorted_rets[n // 2] if n % 2 == 1 else (sorted_rets[n // 2 - 1] + sorted_rets[n // 2]) / 2
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = abs(sum(losses) / len(losses)) if losses else 0.0
    pl_ratio = avg_win / avg_loss if avg_loss > 0 else float("inf") if avg_win > 0 else 0.0
    if pl_ratio == float("inf"):
        pl_ratio = 999.0  # 用一个大数表示无穷，方便JSON序列化

    return {
        "sample_count": n,
        "win_rate": round(win_rate, 2),
        "avg_return_pct": round(avg_ret, 2),
        "profit_loss_ratio": round(pl_ratio, 2),
        "median_return_pct": round(median_ret, 2),
        "max_return_pct": round(max(returns), 2),
        "min_return_pct": round(min(returns), 2),
    }


def run_backtest(signals: List[Dict], hold_periods: List[int],
                 trading_days: List[str]) -> Dict:
    """
    执行回测：为每个信号计算各持有周期的收益率，并按维度汇总。
    返回完整回测结果字典。
    """
    # 收集所有需要K线的股票
    all_codes = list({s["code"] for s in signals})
    log_info(f"需要获取 {len(all_codes)} 只股票的K线数据 ...")

    # 缓存K线 {code: {date: close_price}}
    kline_cache: Dict[str, Dict[str, float]] = {}
    failed_codes = []
    for i, code in enumerate(all_codes):
        if i > 0 and i % 20 == 0:
            log_info(f"  K线进度: {i}/{len(all_codes)}")
        klines = fetch_kline(code, count=250)
        if not klines:
            failed_codes.append(code)
            continue
        kline_cache[code] = {k["date"]: k["close"] for k in klines}
        time.sleep(0.05)

    log_info(f"  K线获取完成: 成功 {len(kline_cache)} 只，失败 {len(failed_codes)} 只")
    if failed_codes:
        log_warn(f"  失败列表(前10): {failed_codes[:10]}")

    # 计算每个信号各周期的收益率
    # signal_returns: {code_date: {period: return_pct}}
    signal_returns: List[Dict] = []
    skipped = 0
    for sig in signals:
        date_str = sig["date"]
        code = sig["code"]
        close_map = kline_cache.get(code)
        if not close_map or date_str not in close_map:
            skipped += 1
            continue
        entry_price = close_map[date_str]
        if entry_price <= 0:
            skipped += 1
            continue

        period_returns = {}
        for p in hold_periods:
            exit_date = shift_trading_day(date_str, p, trading_days)
            if not exit_date or exit_date not in close_map:
                period_returns[p] = None
                continue
            exit_price = close_map[exit_date]
            ret_pct = (exit_price - entry_price) / entry_price * 100.0
            period_returns[p] = round(ret_pct, 2)

        signal_returns.append({
            **sig,
            "entry_price": round(entry_price, 2),
            "returns": period_returns,
        })

    log_info(f"有效信号数: {len(signal_returns)} (跳过 {skipped} 个)")

    # ========== 统计维度1：总体 ==========
    overall_stats = {}
    for p in hold_periods:
        rets = [s["returns"][p] for s in signal_returns
                if s["returns"].get(p) is not None]
        overall_stats[str(p)] = compute_stats(rets)

    # ========== 统计维度2：分行业 ==========
    industry_stats: Dict[str, Dict] = {}
    industries = set(s["industry"] for s in signal_returns)
    for ind in industries:
        ind_signals = [s for s in signal_returns if s["industry"] == ind]
        ind_stat = {}
        for p in hold_periods:
            rets = [s["returns"][p] for s in ind_signals
                    if s["returns"].get(p) is not None]
            ind_stat[str(p)] = compute_stats(rets)
        # 以样本数最多的周期作为判断是否展示的依据，这里以最短周期(T+5)为准
        min_samples = min(
            (ind_stat[str(p)]["sample_count"] for p in hold_periods),
            default=0,
        )
        # 至少有一个周期样本≥5才展示
        has_enough = any(ind_stat[str(p)]["sample_count"] >= 5 for p in hold_periods)
        if has_enough:
            industry_stats[ind] = ind_stat

    # ========== 统计维度3：分净买入档位 ==========
    bucket_stats: Dict[str, Dict] = {}
    for _, _, label in NET_BUY_BUCKETS:
        bucket_signals = [s for s in signal_returns if get_bucket_label(s["net_wan"]) == label]
        bucket_stat = {}
        for p in hold_periods:
            rets = [s["returns"][p] for s in bucket_signals
                    if s["returns"].get(p) is not None]
            bucket_stat[str(p)] = compute_stats(rets)
        bucket_stats[label] = bucket_stat

    return {
        "overall": overall_stats,
        "by_industry": industry_stats,
        "by_net_buy_bucket": bucket_stats,
        "signal_count": len(signal_returns),
        "skipped_signals": skipped,
        "failed_codes": failed_codes,
    }


# ========== 打印摘要 ==========

def print_summary(result: Dict, hold_periods: List[int]) -> None:
    print()
    print("=" * 80)
    print("北向资金中长线回测 — 统计摘要")
    print("=" * 80)

    print("\n【总体统计】")
    header = f"{'周期':>8s} | {'样本数':>6s} | {'胜率%':>7s} | {'平均收益%':>9s} | {'盈亏比':>7s}"
    print(header)
    print("-" * len(header))
    for p in hold_periods:
        s = result["overall"].get(str(p), {})
        pl_str = f"{s['profit_loss_ratio']:.2f}" if s.get("profit_loss_ratio", 0) < 999 else "∞"
        print(f"{'T+'+str(p):>8s} | {s['sample_count']:>6d} | {s['win_rate']:>7.2f} | "
              f"{s['avg_return_pct']:>+9.2f} | {pl_str:>7s}")

    print("\n【分行业统计】（按T+5胜率排序，仅展示样本≥5）")
    industries_sorted = sorted(
        result["by_industry"].items(),
        key=lambda x: x[1].get(str(hold_periods[0]), {}).get("avg_return_pct", 0),
        reverse=True,
    )
    hdr = f"{'行业':<18s} | {'周期':>6s} | {'样本':>5s} | {'胜率%':>7s} | {'平均收益%':>9s}"
    print(hdr)
    print("-" * len(hdr))
    for ind, stats in industries_sorted:
        # 只展示T+5和T+20作为代表
        for p in [hold_periods[0], hold_periods[min(2, len(hold_periods)-1)]]:
            s = stats.get(str(p), {})
            if s.get("sample_count", 0) < 5:
                continue
            label = ind if p == hold_periods[0] else ""
            print(f"{label:<18s} | {'T+'+str(p):>6s} | {s['sample_count']:>5d} | "
                  f"{s['win_rate']:>7.2f} | {s['avg_return_pct']:>+9.2f}")

    print("\n【分净买入档位统计】")
    print(hdr)
    print("-" * len(hdr))
    for label in [b[2] for b in NET_BUY_BUCKETS]:
        stats = result["by_net_buy_bucket"].get(label, {})
        for p in [hold_periods[0], hold_periods[min(2, len(hold_periods)-1)]]:
            s = stats.get(str(p), {})
            if s.get("sample_count", 0) == 0:
                continue
            lab = label if p == hold_periods[0] else ""
            print(f"{lab:<18s} | {'T+'+str(p):>6s} | {s['sample_count']:>5d} | "
                  f"{s['win_rate']:>7.2f} | {s['avg_return_pct']:>+9.2f}")

    print()
    print(f"有效信号总数: {result.get('signal_count', 0)}")
    print(f"跳过信号数: {result.get('skipped_signals', 0)}")
    print("=" * 80)


# ========== 主函数 ==========

def main():
    parser = argparse.ArgumentParser(description="北向资金中长线回测")
    parser.add_argument("--start", default="2026-01-01", help="回测起始日期")
    parser.add_argument("--end", default="2026-07-21", help="回测结束日期")
    parser.add_argument("--threshold", type=float, default=1000.0,
                        help="北向净买入门槛（万元），默认1000")
    parser.add_argument("--periods", default="5,10,20,30,60,90",
                        help="持有周期（交易日），逗号分隔，默认5,10,20,30,60,90")
    parser.add_argument("--industry-file", default=os.path.join(DATA_DIR, "stock_industry.json"),
                        help="行业分类JSON文件路径")
    parser.add_argument("--output", default=os.path.join(OUTPUT_DIR, "northbound_backtest.json"),
                        help="输出结果JSON文件路径")
    args = parser.parse_args()

    hold_periods = [int(x.strip()) for x in args.periods.split(",") if x.strip()]
    hold_periods.sort()

    print(f"[参数] 回测区间: {args.start} ~ {args.end}")
    print(f"[参数] 净买入门槛: {args.threshold:.0f} 万元")
    print(f"[参数] 持有周期: {', '.join('T+'+str(p) for p in hold_periods)}")
    print(f"[参数] 行业文件: {args.industry_file}")
    print(f"[参数] 输出文件: {args.output}")

    # 生成交易日列表（要覆盖到 end 后最长持有周期，用于计算退出日期）
    max_period = max(hold_periods)
    # 计算扩展后的结束日期（自然日多留一些缓冲）
    end_dt = datetime.strptime(args.end, "%Y-%m-%d")
    extended_end = (end_dt + timedelta(days=max_period * 2 + 30)).strftime("%Y-%m-%d")
    trading_days = gen_trading_days(args.start, extended_end)
    # 北向数据只抓到 end
    nb_trading_days = [d for d in trading_days if d <= args.end]
    log_info(f"回测区间交易日数: {len(nb_trading_days)}，扩展后总交易日数: {len(trading_days)}")

    # 加载行业数据
    industry_map = load_industry_map(args.industry_file)
    log_info(f"行业映射加载完成，共 {len(industry_map)} 只股票")

    # 获取北向数据
    log_info(f"开始获取 {len(nb_trading_days)} 天的北向数据 ...")
    nb_data: Dict[str, Dict] = {}
    for i, ds in enumerate(nb_trading_days):
        if i > 0 and i % 10 == 0:
            log_info(f"  北向进度: {i}/{len(nb_trading_days)}")
        try:
            day_data = get_northbound_daily(ds)
            nb_data[ds] = day_data
        except Exception as e:
            log_error(f"获取 {ds} 北向数据失败: {e}")
            nb_data[ds] = {"date": ds, "stocks": [], "total_net_wan": 0.0}
        time.sleep(0.1)

    # 收集信号
    signals = collect_signals(nb_data, args.threshold, industry_map)
    log_info(f"满足门槛的信号总数: {len(signals)}")

    if not signals:
        log_warn("没有满足条件的信号，回测无法进行")
        result = {
            "config": {
                "start_date": args.start,
                "end_date": args.end,
                "threshold_wan": args.threshold,
                "hold_periods": hold_periods,
            },
            "overall": {},
            "by_industry": {},
            "by_net_buy_bucket": {},
            "signal_count": 0,
            "skipped_signals": 0,
            "failed_codes": [],
        }
    else:
        # 执行回测
        result = run_backtest(signals, hold_periods, trading_days)
        result["config"] = {
            "start_date": args.start,
            "end_date": args.end,
            "threshold_wan": args.threshold,
            "hold_periods": hold_periods,
            "signal_total": len(signals),
        }

    # 写输出
    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    log_info(f"回测结果已写入: {args.output}")

    # 打印摘要
    if signals:
        print_summary(result, hold_periods)


if __name__ == "__main__":
    main()
