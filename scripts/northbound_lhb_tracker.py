#!/usr/bin/env python3
"""
沪股通/深股通席位龙虎榜跟踪

从龙虎榜买卖明细中提取北向席位（沪股通专用、深股通专用），
按日汇总净买卖金额，生成跟踪页面。

数据来源：东方财富龙虎榜营业部买卖明细
"""

import json
import os
import sys
import time
from datetime import datetime, timedelta
from typing import List, Dict

import requests

EASTMONEY_API_BASE = "https://datacenter-web.eastmoney.com/api/data/v1/get"
EASTMONEY_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://data.eastmoney.com/",
    "Accept": "application/json, text/plain, */*",
}

REPORT_BUY_DETAILS = "RPT_BILLBOARD_DAILYDETAILSBUY"
REPORT_SELL_DETAILS = "RPT_BILLBOARD_DAILYDETAILSSELL"
REPORT_DAILY_DETAILS = "RPT_DAILYBILLBOARD_DETAILSNEW"

# 北向席位关键词
NORTHBOUND_DEPT_KEYWORDS = ["沪股通专用", "深股通专用", "陆股通专用"]

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


def _safe_num(v) -> float:
    if v is None:
        return 0.0
    try:
        return float(v)
    except (ValueError, TypeError):
        return 0.0


def is_trading_day(date_str: str) -> bool:
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    if dt.weekday() >= 5:
        return False
    if date_str in A_STOCK_HOLIDAYS_2026:
        return False
    return True


def fetch_eastmoney_api(report_name: str, filter_expr: str,
                        sort_columns: str, sort_types: str = "-1",
                        page_size: int = 500, max_pages: int = 5,
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
                    EASTMONEY_API_BASE,
                    params=params,
                    headers=EASTMONEY_HEADERS,
                    timeout=15,
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
            print(f"  API请求失败 (第{attempt+1}次): {e}")
            if attempt < retries - 1:
                time.sleep(2 * (attempt + 1))
            else:
                raise
    return all_data


def is_northbound_dept(dept_name: str) -> bool:
    if not dept_name:
        return False
    for kw in NORTHBOUND_DEPT_KEYWORDS:
        if kw in dept_name:
            return True
    return False


def get_northbound_lhb(date_str: str) -> Dict:
    """
    获取指定日期龙虎榜中的北向席位买卖数据
    返回: {date, total_buy, total_sell, net_amount, stock_count, stocks: [...]}
    """
    print(f"📡 {date_str} 抓取龙虎榜北向席位数据...")

    # 获取股票名称映射
    daily_details = fetch_eastmoney_api(
        REPORT_DAILY_DETAILS,
        filter_expr=f"(TRADE_DATE='{date_str}')",
        sort_columns="BILLBOARD_NET_AMT",
        sort_types="-1",
        page_size=200, max_pages=3,
    )
    name_map = {}
    for item in daily_details:
        code = item.get("SECURITY_CODE", "")
        name = item.get("SECURITY_NAME_ABBR", "")
        change_pct = _safe_num(item.get("CHANGE_RATE"))
        if code and name:
            name_map[code] = {"name": name, "change_pct": round(change_pct, 2)}

    filter_expr = f"(TRADE_DATE='{date_str}')"

    # 买入明细
    buy_raw = fetch_eastmoney_api(
        REPORT_BUY_DETAILS,
        filter_expr=filter_expr,
        sort_columns="TRADE_DATE",
        sort_types="-1",
        page_size=500, max_pages=5,
    )
    # 卖出明细
    sell_raw = fetch_eastmoney_api(
        REPORT_SELL_DETAILS,
        filter_expr=filter_expr,
        sort_columns="TRADE_DATE",
        sort_types="-1",
        page_size=500, max_pages=5,
    )

    print(f"  买入明细: {len(buy_raw)}条, 卖出明细: {len(sell_raw)}条")

    # 按股票+席位聚合
    # stock_map[code] = {code, name, depts: [{dept_name, buy, sell, net}]}
    stock_map = {}

    for item in buy_raw:
        dept_name = item.get("OPERATEDEPT_NAME", "") or item.get("DEPT_NAME", "")
        if not is_northbound_dept(dept_name):
            continue
        code = item.get("SECURITY_CODE", "")
        buy_amt = _safe_num(item.get("BUY_AMT") if item.get("BUY_AMT") is not None else item.get("BUY"))
        if code not in stock_map:
            info = name_map.get(code, {"name": code, "change_pct": 0})
            stock_map[code] = {
                "code": code,
                "name": info["name"],
                "change_pct": info["change_pct"],
                "buy_amt": 0.0,
                "sell_amt": 0.0,
                "depts": {},
            }
        stock_map[code]["buy_amt"] += buy_amt
        if dept_name not in stock_map[code]["depts"]:
            stock_map[code]["depts"][dept_name] = {"buy": 0.0, "sell": 0.0}
        stock_map[code]["depts"][dept_name]["buy"] += buy_amt

    for item in sell_raw:
        dept_name = item.get("OPERATEDEPT_NAME", "") or item.get("DEPT_NAME", "")
        if not is_northbound_dept(dept_name):
            continue
        code = item.get("SECURITY_CODE", "")
        sell_amt = _safe_num(item.get("SELL_AMT") if item.get("SELL_AMT") is not None else item.get("SELL"))
        if code not in stock_map:
            info = name_map.get(code, {"name": code, "change_pct": 0})
            stock_map[code] = {
                "code": code,
                "name": info["name"],
                "change_pct": info["change_pct"],
                "buy_amt": 0.0,
                "sell_amt": 0.0,
                "depts": {},
            }
        stock_map[code]["sell_amt"] += sell_amt
        if dept_name not in stock_map[code]["depts"]:
            stock_map[code]["depts"][dept_name] = {"buy": 0.0, "sell": 0.0}
        stock_map[code]["depts"][dept_name]["sell"] += sell_amt

    # 计算净值
    stocks = []
    total_buy = 0.0
    total_sell = 0.0
    for code, s in stock_map.items():
        net = s["buy_amt"] - s["sell_amt"]
        # 转万元
        s["buy_wan"] = round(s["buy_amt"] / 10000.0, 2)
        s["sell_wan"] = round(s["sell_amt"] / 10000.0, 2)
        s["net_wan"] = round(net / 10000.0, 2)
        # 席位信息转万元
        dept_list = []
        for dn, dv in s["depts"].items():
            dept_list.append({
                "name": dn,
                "buy_wan": round(dv["buy"] / 10000.0, 2),
                "sell_wan": round(dv["sell"] / 10000.0, 2),
                "net_wan": round((dv["buy"] - dv["sell"]) / 10000.0, 2),
            })
        s["dept_list"] = sorted(dept_list, key=lambda x: x["net_wan"], reverse=True)
        del s["depts"]
        del s["buy_amt"]
        del s["sell_amt"]
        stocks.append(s)
        total_buy += s["buy_wan"]
        total_sell += s["sell_wan"]

    # 按净买入排序
    stocks.sort(key=lambda x: x["net_wan"], reverse=True)

    net_total = round(total_buy - total_sell, 2)
    buy_count = sum(1 for s in stocks if s["net_wan"] > 0)
    sell_count = sum(1 for s in stocks if s["net_wan"] < 0)

    result = {
        "date": date_str,
        "stock_count": len(stocks),
        "buy_count": buy_count,
        "sell_count": sell_count,
        "total_buy_wan": round(total_buy, 2),
        "total_sell_wan": round(total_sell, 2),
        "net_wan": net_total,
        "stocks": stocks,
    }

    print(f"  北向席位上榜: {len(stocks)}只, 净买入: {net_total:.0f}万 (买{buy_count}/卖{sell_count})")
    return result


def get_recent_trading_days(end_date: str, count: int = 30) -> List[str]:
    days = []
    dt = datetime.strptime(end_date, "%Y-%m-%d")
    while len(days) < count:
        ds = dt.strftime("%Y-%m-%d")
        if is_trading_day(ds):
            days.append(ds)
        dt -= timedelta(days=1)
    return list(reversed(days))


def load_existing_data(path: str) -> Dict:
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"dates": {}, "update_time": ""}


def save_data(path: str, data: Dict):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def generate_html(data: Dict, output_path: str):
    """生成沪股通龙虎榜跟踪页面"""
    dates_sorted = sorted(
        [d for d, v in data["dates"].items() if v["stock_count"] > 0],
        reverse=True
    )

    # 生成概览数据（近30天每日净买卖）
    chart_dates = []
    chart_nets = []
    chart_stock_counts = []
    for d in sorted(data["dates"].keys()):
        if data["dates"][d]["stock_count"] == 0:
            continue
        chart_dates.append(d[5:])  # MM-DD
        chart_nets.append(data["dates"][d]["net_wan"])
        chart_stock_counts.append(data["dates"][d]["stock_count"])

    max_val = max((abs(x) for x in chart_nets), default=1)
    if max_val == 0:
        max_val = 1

    # 最近一天
    latest_date = dates_sorted[0] if dates_sorted else ""
    latest = data["dates"].get(latest_date, {})
    latest_stocks = latest.get("stocks", [])
    buy_stocks = [s for s in latest_stocks if s["net_wan"] > 0]
    sell_stocks = [s for s in latest_stocks if s["net_wan"] < 0]

    def fmt_amt(wan):
        if abs(wan) >= 10000:
            return f"{wan/10000:.2f}亿"
        return f"{wan:.0f}万"

    def color_cls(wan):
        return "up" if wan > 0 else "down"

    # 生成个股表格行
    def stock_rows(stocks, limit=None):
        rows = ""
        for i, s in enumerate(stocks[:limit] if limit else stocks):
            net_cls = color_cls(s["net_wan"])
            chg_cls = color_cls(s["change_pct"])
            chg_str = f"+{s['change_pct']:.2f}%" if s["change_pct"] >= 0 else f"{s['change_pct']:.2f}%"
            dept_info = "<br>".join([
                f"{d['name']}: 买{fmt_amt(d['buy_wan'])} / 卖{fmt_amt(d['sell_wan'])}"
                for d in s["dept_list"]
            ])
            rows += f"""<tr>
                <td>{i+1}</td>
                <td class="stock-name">{s['name']}<br><span class="code">{s['code']}</span></td>
                <td class="{chg_cls}">{chg_str}</td>
                <td class="up">{fmt_amt(s['buy_wan'])}</td>
                <td class="down">{fmt_amt(s['sell_wan'])}</td>
                <td class="{net_cls}">{fmt_amt(s['net_wan'])}</td>
                <td class="dept-cell">{dept_info}</td>
            </tr>"""
        return rows

    # 生成每日列表（只展示有数据的日期）
    daily_list_html = ""
    for d in dates_sorted:
        day_data = data["dates"][d]
        net_cls = color_cls(day_data["net_wan"])
        daily_list_html += f"""
        <div class="daily-card" id="day-{d}">
            <div class="daily-header">
                <span class="date-label">{d}</span>
                <span class="stock-count">{day_data['stock_count']}只上榜</span>
                <span class="buy-count up">净买入 {day_data['buy_count']}只</span>
                <span class="sell-count down">净卖出 {day_data['sell_count']}只</span>
                <span class="net-amt {net_cls}">净{fmt_amt(day_data['net_wan'])}</span>
            </div>
            <div class="daily-table-wrap">
                <table class="data-table">
                    <thead>
                        <tr><th>#</th><th>股票</th><th>涨跌幅</th><th>买入</th><th>卖出</th><th>净买入</th><th>席位明细</th></tr>
                    </thead>
                    <tbody>
                        {stock_rows(day_data['stocks'])}
                    </tbody>
                </table>
            </div>
        </div>"""

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>沪股通龙虎榜跟踪</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif; background: #0d1117; color: #e6edf3; padding: 16px; }}
.container {{ max-width: 1200px; margin: 0 auto; }}
.header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; padding-bottom: 12px; border-bottom: 1px solid #30363d; }}
.header h1 {{ font-size: 20px; font-weight: 600; }}
.update-time {{ color: #8b949e; font-size: 12px; }}
.breadcrumb {{ margin-bottom: 16px; font-size: 13px; color: #8b949e; }}
.breadcrumb a {{ color: #58a6ff; text-decoration: none; }}
.breadcrumb a:hover {{ text-decoration: underline; }}

/* 概览卡片 */
.overview {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 24px; }}
.overview-card {{ background: #161b22; border-radius: 8px; padding: 16px; border: 1px solid #30363d; }}
.overview-card .label {{ font-size: 12px; color: #8b949e; margin-bottom: 6px; }}
.overview-card .value {{ font-size: 22px; font-weight: 600; }}
.up {{ color: #f85149; }}
.down {{ color: #3fb950; }}

/* 今日重点 */
.today-section {{ background: #161b22; border-radius: 8px; padding: 16px; margin-bottom: 24px; border: 1px solid #30363d; }}
.today-section h2 {{ font-size: 16px; margin-bottom: 12px; display: flex; align-items: center; gap: 8px; }}
.today-section .sub {{ font-size: 12px; color: #8b949e; font-weight: normal; }}
.today-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }}
.today-col h3 {{ font-size: 14px; margin-bottom: 8px; color: #8b949e; }}

/* 表格 */
.data-table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
.data-table th {{ padding: 10px 8px; text-align: left; background: #21262d; color: #8b949e; font-weight: 500; border-bottom: 1px solid #30363d; white-space: nowrap; }}
.data-table td {{ padding: 8px; border-bottom: 1px solid #21262d; vertical-align: middle; }}
.data-table tbody tr:hover {{ background: #161b22; }}
.stock-name {{ font-weight: 500; }}
.code {{ color: #8b949e; font-size: 11px; }}
.dept-cell {{ font-size: 11px; color: #8b949e; line-height: 1.5; max-width: 280px; }}

/* 每日卡片 */
.daily-card {{ background: #161b22; border-radius: 8px; margin-bottom: 16px; border: 1px solid #30363d; overflow: hidden; }}
.daily-header {{ display: flex; gap: 16px; padding: 12px 16px; background: #21262d; align-items: center; flex-wrap: wrap; }}
.date-label {{ font-weight: 600; font-size: 14px; }}
.stock-count, .buy-count, .sell-count {{ font-size: 12px; color: #8b949e; }}
.net-amt {{ font-weight: 600; margin-left: auto; }}
.daily-table-wrap {{ padding: 0; overflow-x: auto; }}

/* 图表区 */
.chart-section {{ background: #161b22; border-radius: 8px; padding: 16px; margin-bottom: 24px; border: 1px solid #30363d; }}
.chart-section h2 {{ font-size: 16px; margin-bottom: 12px; }}
.chart-container {{ height: 200px; position: relative; }}
.bar-chart {{ display: flex; align-items: flex-end; height: 180px; gap: 2px; padding: 0 4px; border-bottom: 1px solid #30363d; }}
.bar-item {{ flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: flex-end; min-width: 0; }}
.bar {{ width: 100%; max-width: 20px; border-radius: 2px 2px 0 0; min-height: 2px; transition: height 0.3s; }}
.bar.up {{ background: #f85149; }}
.bar.down {{ background: #3fb950; }}
.bar-label {{ font-size: 10px; color: #8b949e; margin-top: 4px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; width: 100%; text-align: center; }}

@media (max-width: 768px) {{
    .overview {{ grid-template-columns: repeat(2, 1fr); }}
    .today-grid {{ grid-template-columns: 1fr; }}
    .dept-cell {{ max-width: 180px; font-size: 10px; }}
}}
</style>
</head>
<body>
<div class="container">
    <div class="breadcrumb">
        <a href="portal.html">← 返回首页</a>
    </div>
    <div class="header">
        <h1>📊 沪股通龙虎榜跟踪</h1>
        <div class="update-time">更新于 {data.get("update_time", "")}</div>
    </div>

    <div class="overview">
        <div class="overview-card">
            <div class="label">最新日期</div>
            <div class="value">{latest_date}</div>
        </div>
        <div class="overview-card">
            <div class="label">上榜股票数</div>
            <div class="value">{latest.get('stock_count', 0)}只</div>
        </div>
        <div class="overview-card">
            <div class="label">净买入金额</div>
            <div class="value {color_cls(latest.get('net_wan', 0))}">{fmt_amt(latest.get('net_wan', 0))}</div>
        </div>
        <div class="overview-card">
            <div class="label">买/卖 股票数</div>
            <div class="value"><span class="up">{latest.get('buy_count', 0)}</span> / <span class="down">{latest.get('sell_count', 0)}</span></div>
        </div>
    </div>

    <div class="chart-section">
        <h2>近{len(chart_dates)}日北向席位龙虎榜净买卖</h2>
        <div class="chart-container">
            <div class="bar-chart">
                {"".join(f'<div class="bar-item"><div class="bar {"up" if n>=0 else "down"}" style="height:{(abs(n)/max_val*150 + 5) if max_val > 0 else 5:.0f}px"></div><div class="bar-label">{d}</div></div>' for d, n in zip(chart_dates, chart_nets))}
            </div>
        </div>
    </div>

    <div class="today-section">
        <h2>📌 {latest_date} 龙虎榜北向席位明细 <span class="sub">共{latest.get('stock_count', 0)}只</span></h2>
        <div class="today-grid">
            <div class="today-col">
                <h3 class="up">净买入 ({len(buy_stocks)}只)</h3>
                <table class="data-table">
                    <thead><tr><th>#</th><th>股票</th><th>涨跌幅</th><th>净买入</th></tr></thead>
                    <tbody>
                        {"".join(f'<tr><td>{i+1}</td><td class="stock-name">{s["name"]}<br><span class="code">{s["code"]}</span></td><td class="{color_cls(s["change_pct"])}">{"+" if s["change_pct"]>=0 else ""}{s["change_pct"]:.2f}%</td><td class="up">{fmt_amt(s["net_wan"])}</td></tr>' for i, s in enumerate(buy_stocks[:10]))}
                    </tbody>
                </table>
            </div>
            <div class="today-col">
                <h3 class="down">净卖出 ({len(sell_stocks)}只)</h3>
                <table class="data-table">
                    <thead><tr><th>#</th><th>股票</th><th>涨跌幅</th><th>净卖出</th></tr></thead>
                    <tbody>
                        {"".join(f'<tr><td>{i+1}</td><td class="stock-name">{s["name"]}<br><span class="code">{s["code"]}</span></td><td class="{color_cls(s["change_pct"])}">{"+" if s["change_pct"]>=0 else ""}{s["change_pct"]:.2f}%</td><td class="down">{fmt_amt(abs(s["net_wan"]))}</td></tr>' for i, s in enumerate(sell_stocks[:10]))}
                    </tbody>
                </table>
            </div>
        </div>
    </div>

    <h2 style="font-size:16px;margin-bottom:12px;">📅 历史每日明细</h2>
    {daily_list_html}
</div>
</body>
</html>"""

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"✅ 页面已生成: {output_path}")


def main():
    data_path = "data/northbound_lhb.json"
    html_path = "northbound-lhb-tracker.html"

    data = load_existing_data(data_path)

    # 确定起始日期：如果已有数据，从最后一天+1开始；否则往前推30天
    if data["dates"]:
        last_date = max(data["dates"].keys())
        print(f"已有数据截止: {last_date}")
        # 从last_date的下一个交易日到今天
        today = datetime.now().strftime("%Y-%m-%d")
        # 获取从last_date之后的所有交易日
        days_to_fetch = []
        dt = datetime.strptime(last_date, "%Y-%m-%d") + timedelta(days=1)
        end_dt = datetime.now()
        while dt <= end_dt:
            ds = dt.strftime("%Y-%m-%d")
            if is_trading_day(ds):
                days_to_fetch.append(ds)
            dt += timedelta(days=1)
        # 限制最多30天
        days_to_fetch = days_to_fetch[-30:]
    else:
        print("无历史数据，从头抓取近30天...")
        today = datetime.now().strftime("%Y-%m-%d")
        days_to_fetch = get_recent_trading_days(today, 30)

    if not days_to_fetch:
        print("没有需要更新的日期")
    else:
        print(f"需要更新 {len(days_to_fetch)} 天: {days_to_fetch}")
        for d in days_to_fetch:
            try:
                day_data = get_northbound_lhb(d)
                data["dates"][d] = day_data
                time.sleep(0.5)
            except Exception as e:
                print(f"❌ {d} 抓取失败: {e}")

    data["update_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    save_data(data_path, data)
    print(f"💾 数据已保存: {data_path}")

    generate_html(data, html_path)


if __name__ == "__main__":
    main()
