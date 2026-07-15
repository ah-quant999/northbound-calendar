#!/usr/bin/env python3
"""
北向资金日历自动更新脚本 — GitHub Actions 纯 Python 版

完全不依赖 CodeActSDK / pydantic，只用标准库 + requests。
数据来源：东方财富龙虎榜官方 API
  - 龙虎榜买入营业部明细：RPT_BILLBOARD_DAILYDETAILSBUY
  - 龙虎榜卖出营业部明细：RPT_BILLBOARD_DAILYDETAILSSELL
  - 龙虎榜个股明细：RPT_DAILYBILLBOARD_DETAILSNEW（用于股票名称映射）

筛选逻辑：
  营业部名称含 "沪股通专用" 或 "深股通专用" 的记录，按股票聚合后取净买卖 TOP5。

用法：
  python3 update_northbound_gha.py --date 2026-07-14 --html 北向资金日历.html
  python3 update_northbound_gha.py --date 2026-07-14 --html 北向资金日历.html --dry-run
"""

import argparse
import os
import re
import sys
import time
from datetime import datetime
from typing import List, Dict, Optional

import requests

# ========== 配置区 ==========

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

# 港股独立休市日（北向通道关闭）
HK_HOLIDAYS_2026 = {
    "2026-07-01",  # 香港回归纪念日
}

# 北向席位匹配关键词
NORTHBOUND_KEYWORDS = ("沪股通专用", "深股通专用")


# ========== 工具函数 ==========

def _safe_num(v) -> float:
    if v is None:
        return 0.0
    try:
        return float(v)
    except (ValueError, TypeError):
        return 0.0


def format_amount(amount_wan: float) -> str:
    """格式化金额：万/亿"""
    if abs(amount_wan) >= 10000:
        return f"{amount_wan / 10000:.2f}亿"
    else:
        return f"{amount_wan:.0f}万"


def is_a_stock_holiday(date_str: str) -> bool:
    return date_str in A_STOCK_HOLIDAYS_2026


def is_hk_holiday(date_str: str) -> bool:
    return date_str in HK_HOLIDAYS_2026


def is_trading_day(date_str: str) -> bool:
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    if dt.weekday() >= 5:
        return False
    if is_a_stock_holiday(date_str):
        return False
    return True


def is_northbound_open(date_str: str) -> bool:
    """北向通道是否开放（A 股开盘且非港股休市日）"""
    if not is_trading_day(date_str):
        return False
    if is_hk_holiday(date_str):
        return False
    return True


# ========== 东财API数据获取 ==========

def fetch_eastmoney_api(report_name: str, filter_expr: str,
                        sort_columns: str, sort_types: str = "-1",
                        page_size: int = 200, max_pages: int = 10,
                        retries: int = 3) -> List[Dict]:
    """调用东方财富数据中心API，自动翻页，带重试"""
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
            print(f"  ⚠️  API请求失败 (第{attempt+1}次): {e}")
            if attempt < retries - 1:
                time.sleep(2 * (attempt + 1))
            else:
                raise
    return all_data


# ========== 北向资金数据提取 ==========

def get_stock_name_map(date_str: str) -> Dict[str, str]:
    """从龙虎榜个股明细获取股票代码->名称映射"""
    print(f"  📡 [股票名] 调用 {REPORT_DAILY_DETAILS} ...")
    raw_data = fetch_eastmoney_api(
        REPORT_DAILY_DETAILS,
        filter_expr=f"(TRADE_DATE='{date_str}')",
        sort_columns="BILLBOARD_NET_AMT,TRADE_DATE,SECURITY_CODE",
        sort_types="-1,-1,1",
        page_size=200, max_pages=5,
    )
    name_map = {}
    for item in raw_data:
        code = item.get("SECURITY_CODE", "")
        name = item.get("SECURITY_NAME_ABBR", "")
        if code and name:
            name_map[code] = name
    print(f"    股票名映射: {len(name_map)} 只")
    return name_map


def get_northbound_dept_data(date_str: str) -> List[Dict]:
    """获取龙虎榜营业部明细中所有北向席位记录（买卖两侧合并去重）"""
    all_rows = []
    for rpt in [REPORT_BUY_DEPT, REPORT_SELL_DEPT]:
        print(f"  📡 [{rpt}] 调用 ...")
        raw_data = fetch_eastmoney_api(
            rpt,
            filter_expr=f"(TRADE_DATE='{date_str}')",
            sort_columns="TRADE_DATE,SECURITY_CODE",
            sort_types="-1,1",
            page_size=200, max_pages=10,
        )
        print(f"    原始记录数: {len(raw_data)}")
        all_rows.extend(raw_data)

    # 筛选北向席位
    northbound = [
        r for r in all_rows
        if any(kw in r.get("OPERATEDEPT_NAME", "") for kw in NORTHBOUND_KEYWORDS)
    ]
    print(f"    北向席位记录数: {len(northbound)}")

    # 去重：同股票+同席位+同TRADE_ID视为同一条（买卖两侧各出一次会重复）
    seen = set()
    unique = []
    for r in northbound:
        key = (r.get("SECURITY_CODE", ""), r.get("OPERATEDEPT_NAME", ""), r.get("TRADE_ID", ""))
        if key in seen:
            continue
        seen.add(key)
        unique.append(r)
    print(f"    去重后记录数: {len(unique)}")

    return unique


def aggregate_northbound(rows: List[Dict], name_map: Dict[str, str]) -> Dict:
    """
    按股票聚合北向席位数据。
    返回: {
        "stocks": [{"code", "name", "buy_wan", "sell_wan", "net_wan"}, ...],
        "total_net_wan": float,
    }
    """
    stock_map = {}
    for r in rows:
        code = r.get("SECURITY_CODE", "")
        if not code:
            continue
        buy = _safe_num(r.get("BUY"))
        sell = _safe_num(r.get("SELL"))
        # NET 字段有些记录可能为 None，用 buy - sell 兜底
        net = _safe_num(r.get("NET"))
        if net == 0 and (buy != 0 or sell != 0):
            net = buy - sell

        if code not in stock_map:
            stock_map[code] = {
                "code": code,
                "name": name_map.get(code, code),
                "buy": 0.0,
                "sell": 0.0,
                "net": 0.0,
            }
        stock_map[code]["buy"] += buy
        stock_map[code]["sell"] += sell
        stock_map[code]["net"] += net

    stocks = list(stock_map.values())
    for s in stocks:
        s["buy_wan"] = round(s["buy"] / 10000.0, 2)
        s["sell_wan"] = round(s["sell"] / 10000.0, 2)
        s["net_wan"] = round(s["net"] / 10000.0, 2)

    total_net_wan = round(sum(s["net_wan"] for s in stocks), 2)

    return {"stocks": stocks, "total_net_wan": total_net_wan}


def build_daily_data(date_str: str) -> Dict:
    """
    构建单日北向资金数据。
    返回: {
        "date": str,
        "total_inflow_wan": float or None,  # 正数=净流入，负数=净流出
        "top_buy": [{"name", "code", "amount"}, ...],  # 万元
        "top_sell": [{"name", "code", "amount"}, ...],  # 万元（正数）
        "data_source": str,
        "hk_holiday": bool,
    }
    """
    print(f"📊 正在获取 {date_str} 的龙虎榜北向席位数据...")

    name_map = get_stock_name_map(date_str)
    dept_rows = get_northbound_dept_data(date_str)

    if not dept_rows:
        # 没有北向席位数据（可能是港股休市或当天龙虎榜没有北向）
        return {
            "date": date_str,
            "total_inflow_wan": None,
            "top_buy": [],
            "top_sell": [],
            "data_source": "东方财富龙虎榜官方API",
            "hk_holiday": is_hk_holiday(date_str),
        }

    agg = aggregate_northbound(dept_rows, name_map)
    stocks = agg["stocks"]

    buy_sorted = sorted(stocks, key=lambda x: x["net_wan"], reverse=True)
    sell_sorted = sorted(stocks, key=lambda x: x["net_wan"])

    top_buy = [
        {"name": s["name"], "code": s["code"], "amount": s["net_wan"]}
        for s in buy_sorted[:5]
        if s["net_wan"] > 0
    ]
    top_sell = [
        {"name": s["name"], "code": s["code"], "amount": abs(s["net_wan"])}
        for s in sell_sorted[:5]
        if s["net_wan"] < 0
    ]

    print(f"\n    总净流入: {format_amount(agg['total_net_wan'])}")
    print(f"    净买入TOP5: {[(s['name'], s['amount']) for s in top_buy]}")
    print(f"    净卖出TOP5: {[(s['name'], s['amount']) for s in top_sell]}")

    return {
        "date": date_str,
        "total_inflow_wan": agg["total_net_wan"],
        "top_buy": top_buy,
        "top_sell": top_sell,
        "data_source": "东方财富龙虎榜官方API",
        "hk_holiday": False,
    }


# ========== HTML构建 ==========

def build_day_cell_html(data: Dict) -> str:
    """
    构建日期单元格的 HTML。
    data: build_daily_data 的返回值
    """
    dt = datetime.strptime(data["date"], "%Y-%m-%d")
    day = dt.day

    total_inflow = data.get("total_inflow_wan")
    top_buy = data.get("top_buy", [])
    top_sell = data.get("top_sell", [])
    hk_holiday = data.get("hk_holiday", False)

    has_data = (total_inflow is not None) or top_buy or top_sell

    if not has_data:
        if hk_holiday:
            return f'''                <div class="day-cell">
                    <div class="day-header"><span class="day-number">{day}</span></div>
                    <div class="empty-content">
                        <div class="amount holiday">北向通道关闭</div>
                        <div style="font-size:10px;color:#6e7681;margin-top:4px;">港股休市·A股正常交易</div>
                    </div>
                </div>'''
        else:
            return f'''                <div class="day-cell">
                    <div class="day-header"><span class="day-number">{day}</span></div>
                    <div class="empty-content">--</div>
                </div>'''

    # 总净流入显示
    if total_inflow is not None:
        if total_inflow >= 0:
            inflow_class = "inflow"
            inflow_text = f"净流入+{format_amount(total_inflow)}"
        else:
            inflow_class = "outflow"
            inflow_text = f"净流出{format_amount(total_inflow)}"
    else:
        inflow_class = "inflow"
        inflow_text = "数据已更新"

    lines = []
    lines.append('                <div class="day-cell">')
    lines.append(
        f'                    <div class="day-header"><span class="day-number">{day}</span>'
        f'<span class="amount {inflow_class}">{inflow_text}</span></div>'
    )
    lines.append('                    <div class="stock-list">')

    if top_buy:
        lines.append('                        <div class="section-title">▲ 净买入TOP5</div>')
        for stock in top_buy[:5]:
            amount_str = f"+{format_amount(stock['amount'])}"
            lines.append(
                f'                        <div class="stock-item">'
                f'<span class="stock-icon up">▲</span>'
                f'<span class="stock-name">{stock["name"]}</span>'
                f'<span class="stock-amount up">{amount_str}</span>'
                f'</div>'
            )

    if top_sell:
        lines.append('                        <div class="section-title">▼ 净卖出TOP5</div>')
        for stock in top_sell[:5]:
            amount_str = f"-{format_amount(stock['amount'])}"
            lines.append(
                f'                        <div class="stock-item">'
                f'<span class="stock-icon down">▼</span>'
                f'<span class="stock-name">{stock["name"]}</span>'
                f'<span class="stock-amount down">{amount_str}</span>'
                f'</div>'
            )

    lines.append('                    </div>')
    lines.append('                </div>')
    return "\n".join(lines)


# ========== HTML更新 ==========

def update_html(html_path: str, data: Dict) -> bool:
    """更新HTML文件中的指定日期数据"""
    try:
        with open(html_path, "r", encoding="utf-8") as f:
            html = f.read()
    except Exception as e:
        print(f"❌ 读取HTML文件失败: {e}")
        return False

    dt = datetime.strptime(data["date"], "%Y-%m-%d")
    day = dt.day
    month = dt.month
    new_cell_html = build_day_cell_html(data)

    # 更新更新时间
    now = datetime.now()
    update_time_str = now.strftime("%Y-%m-%d %H:%M")
    html = re.sub(
        r'本次更新时间：\d{4}-\d{2}-\d{2} \d{2}:\d{2}',
        f'本次更新时间：{update_time_str}',
        html,
    )
    html = re.sub(
        r'生成日期：\d{4}-\d{2}-\d{2}',
        f'生成日期：{now.strftime("%Y-%m-%d")}',
        html,
    )

    # 定位月份区域
    month_section_pattern = rf'<div class="month-section[^"]*" id="month-{month}"'
    month_section_match = re.search(month_section_pattern, html)
    if not month_section_match:
        print(f"⚠️  未找到月份 {month} 的区域")
        return False

    section_start = month_section_match.start()
    next_section = re.search(r'<div class="month-section', html[section_start + 1:])
    section_end = section_start + 1 + next_section.start() if next_section else len(html)
    section_html = html[section_start:section_end]

    # 匹配目标日期单元格
    # 结构：<td ...> <div class="day-cell"> ... <span class="day-number">N</span> ... </div> </td>
    td_pattern = re.compile(
        rf'(<td[^>]*>)\s*<div class="day-cell">((?!</td>).)*?'
        rf'<span class="day-number[^"]*">\s*{day}\s*</span>'
        rf'((?!</td>).)*?</div>\s*</td>',
        re.DOTALL,
    )
    all_matches = list(td_pattern.finditer(section_html))

    # 回退：月末日期可能出现在下月第一周
    if not all_matches:
        print(f"⚠️  在 month-{month} 中未找到 {day}日，尝试回退到 month-{month+1}...")
        fallback_pattern = rf'<div class="month-section[^"]*" id="month-{month+1}"'
        fallback_match = re.search(fallback_pattern, html)
        if fallback_match:
            fb_start = fallback_match.start()
            fb_next = re.search(r'<div class="month-section', html[fb_start + 1:])
            fb_end = fb_start + 1 + fb_next.start() if fb_next else len(html)
            fb_section_html = html[fb_start:fb_end]
            all_matches = list(td_pattern.finditer(fb_section_html))
            if all_matches:
                section_start = fb_start
                section_html = fb_section_html
                print(f"✅ 在 month-{month+1} 中找到 {day}日")

    if not all_matches:
        print(f"❌ 未找到 {month}月{day}日 的匹配单元格")
        return False

    target_match = all_matches[0]
    td_open = target_match.group(1)

    # 日期注释校验（防止张冠李戴）
    td_abs_start = section_start + target_match.start()
    pre_context = html[max(0, td_abs_start - 300):td_abs_start]
    comment_m = re.search(r'!--\s*(\d+)/(\d+)\s*([一二三四五六日天]+)\s*--', pre_context)
    if comment_m:
        cm_month = int(comment_m.group(1))
        cm_day = int(comment_m.group(2))
        if cm_month != month or cm_day != day:
            print(f"❌ 日期注释不匹配！注释={cm_month}/{cm_day}，写入={month}/{day}")
            return False

    abs_start = section_start + target_match.start()
    abs_end = section_start + target_match.end()
    new_td = f'{td_open}\n{new_cell_html}\n                    </td>'
    html = html[:abs_start] + new_td + html[abs_end:]
    print(f"✅ 更新了 {month}月{day}日 的单元格")

    try:
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"✅ HTML文件已更新: {html_path}")
        return True
    except Exception as e:
        print(f"❌ 写入HTML文件失败: {e}")
        return False


# ========== 主函数 ==========

def main():
    parser = argparse.ArgumentParser(
        description="北向资金日历更新脚本 (GHA纯Python版)"
    )
    parser.add_argument("--date", required=True, help="目标日期 (YYYY-MM-DD)")
    parser.add_argument("--html", required=True, help="HTML文件路径")
    parser.add_argument("--dry-run", action="store_true", help="只抓取不写入")
    args = parser.parse_args()

    print("=" * 60)
    print("🚀 北向资金日历更新 (GHA纯Python版)")
    print(f"📅 目标日期: {args.date}")
    print(f"📄 HTML文件: {args.html}")
    print(f"📊 数据来源: 东方财富龙虎榜官方API (北向席位)")
    print("=" * 60)

    # 交易日检查
    if not is_trading_day(args.date):
        print(f"📅 {args.date} 是非交易日（周末或法定假日），无需更新")
        sys.exit(0)

    # 北向通道检查（港股休市）
    if is_hk_holiday(args.date):
        print(f"🏛️  {args.date} 港股休市，北向通道关闭")
        if not args.dry_run:
            # 仍然更新HTML显示"北向通道关闭"
            daily_data = build_daily_data(args.date)
            update_html(args.html, daily_data)
        sys.exit(0)

    try:
        daily_data = build_daily_data(args.date)

        print(f"\n✅ 获取到数据:")
        total_str = format_amount(daily_data["total_inflow_wan"]) if daily_data["total_inflow_wan"] is not None else "无"
        print(f"   总净流入: {total_str}")
        print(f"   净买入TOP5: {len(daily_data['top_buy'])} 只")
        print(f"   净卖出TOP5: {len(daily_data['top_sell'])} 只")

        if args.dry_run:
            print("\n🔍 [DRY-RUN] 只抓取不写入，任务成功")
            sys.exit(0)

        if not update_html(args.html, daily_data):
            print("❌ HTML更新失败")
            sys.exit(1)

        print("🎉 更新完成")
        sys.exit(0)

    except Exception as e:
        print(f"❌ 执行失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
