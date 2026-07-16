#!/usr/bin/env python3
"""
机游共振日历自动更新脚本 — GitHub Actions 纯 Python 版

完全不依赖 CodeActSDK / pydantic，只用标准库 + requests。
功能与 update_jiyou_resonance_calendar.py 完全一致，仅负责更新指定 HTML 中指定日期的数据。

数据来源（东方财富官方API）：
  - 机构买卖：RPT_ORGANIZATION_TRADE_DETAILS
  - 龙虎榜个股明细：RPT_DAILYBILLBOARD_DETAILSNEW

共振逻辑：
  机构净买入TOP5  ∩  游资净买入TOP20  =  机游共振

用法：
  python3 update_jiyou_resonance_gha.py --date 2026-07-14 --html 机游共振日历.html
  python3 update_jiyou_resonance_gha.py --date 2026-07-14 --html 机游共振日历.html --dry-run
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime
from typing import Optional, List, Dict

import requests

# ========== 配置区 ==========

EASTMONEY_API_BASE = "https://datacenter-web.eastmoney.com/api/data/v1/get"
EASTMONEY_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://data.eastmoney.com/",
    "Accept": "application/json, text/plain, */*",
}

REPORT_INSTITUTION = "RPT_ORGANIZATION_TRADE_DETAILS"
REPORT_DAILY_DETAILS = "RPT_DAILYBILLBOARD_DETAILSNEW"
REPORT_BUY_DETAILS = "RPT_BILLBOARD_DAILYDETAILSBUY"
REPORT_SELL_DETAILS = "RPT_BILLBOARD_DAILYDETAILSSELL"

# 游资数据排除的席位类型（北向+机构，确保游资口径纯净）
YOUZI_EXCLUDE_DEPT_KEYWORDS = ["机构专用", "沪股通专用", "深股通专用"]

# 共振参数
RESONANCE_YOUZI_TOP_N = 20
YOUZI_DISPLAY_TOP_N = 5

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


def is_trading_day(date_str: str) -> bool:
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    if dt.weekday() >= 5:
        return False
    if is_a_stock_holiday(date_str):
        return False
    return True


# ========== 东财API数据获取 ==========

def fetch_eastmoney_api(report_name: str, filter_expr: str,
                        sort_columns: str, sort_types: str = "-1",
                        page_size: int = 200, max_pages: int = 5,
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


def get_institution_data(date_str: str) -> Dict[str, List[Dict]]:
    """获取机构买卖数据（按股票聚合）"""
    print(f"  📡 [机构] 调用 {REPORT_INSTITUTION} ...")
    raw_data = fetch_eastmoney_api(
        REPORT_INSTITUTION,
        filter_expr=f"(TRADE_DATE='{date_str}')",
        sort_columns="NET_BUY_AMT,TRADE_DATE,SECURITY_CODE",
        sort_types="-1,-1,1",
        page_size=200, max_pages=5,
    )
    print(f"    原始记录数: {len(raw_data)}")

    stock_map = {}
    for item in raw_data:
        code = item.get("SECURITY_CODE", "")
        name = item.get("SECURITY_NAME_ABBR", "")
        net_buy = _safe_num(item.get("NET_BUY_AMT"))
        buy_amt = _safe_num(item.get("BUY_AMT"))
        sell_amt = _safe_num(item.get("SELL_AMT"))
        buy_times = int(_safe_num(item.get("BUY_TIMES")))
        sell_times = int(_safe_num(item.get("SELL_TIMES")))

        if code not in stock_map:
            stock_map[code] = {
                "code": code, "name": name,
                "net_buy": 0.0, "buy_amt": 0.0, "sell_amt": 0.0,
                "buy_count": 0, "sell_count": 0,
            }
        stock_map[code]["net_buy"] += net_buy
        stock_map[code]["buy_amt"] += buy_amt
        stock_map[code]["sell_amt"] += sell_amt
        stock_map[code]["buy_count"] = max(stock_map[code]["buy_count"], buy_times)
        stock_map[code]["sell_count"] = max(stock_map[code]["sell_count"], sell_times)

    stocks = list(stock_map.values())
    for s in stocks:
        s["net_buy_wan"] = s["net_buy"] / 10000.0
        s["buy_wan"] = s["buy_amt"] / 10000.0
        s["sell_wan"] = s["sell_amt"] / 10000.0

    buy_sorted = sorted(stocks, key=lambda x: x["net_buy"], reverse=True)
    sell_sorted = sorted(stocks, key=lambda x: x["net_buy"])

    print(f"    去重后股票数: {len(stocks)}")
    print(f"    机构净买入TOP5: {[(s['name'], round(s['net_buy_wan'],2)) for s in buy_sorted[:5]]}")
    print(f"    机构净卖出TOP5: {[(s['name'], round(s['net_buy_wan'],2)) for s in sell_sorted[:5]]}")

    return {"buy_sorted": buy_sorted, "sell_sorted": sell_sorted}


def get_youzi_stock_data(date_str: str) -> Dict[str, List[Dict]]:
    """
    获取游资净买卖数据（龙虎榜营业部明细口径，剔除机构专用/北向席位）

    数据来源：
      - RPT_BILLBOARD_DAILYDETAILSBUY（买入营业部明细）
      - RPT_BILLBOARD_DAILYDETAILSSELL（卖出营业部明细）

    处理逻辑：
      1. 分别获取所有营业部买入/卖出明细
      2. 剔除机构专用、沪股通专用、深股通专用席位
      3. 按股票代码聚合净买卖金额
      4. 得到纯净游资口径的净买入/净卖出排名
    """
    print(f"  📡 [游资] 调用营业部买卖明细（剔除机构+北向）...")

    # 1. 获取名称映射（从个股明细接口拿股票名称）
    daily_details = fetch_eastmoney_api(
        REPORT_DAILY_DETAILS,
        filter_expr=f"(TRADE_DATE='{date_str}')",
        sort_columns="BILLBOARD_NET_AMT,TRADE_DATE,SECURITY_CODE",
        sort_types="-1,-1,1",
        page_size=200, max_pages=3,
    )
    name_map = {}
    for item in daily_details:
        code = item.get("SECURITY_CODE", "")
        name = item.get("SECURITY_NAME_ABBR", "")
        if code and name:
            name_map[code] = name

    filter_expr = f"(TRADE_DATE='{date_str}')"

    # 2. 获取买入营业部明细
    buy_raw = fetch_eastmoney_api(
        REPORT_BUY_DETAILS,
        filter_expr=filter_expr,
        sort_columns="TRADE_DATE",
        sort_types="-1",
        page_size=500, max_pages=5,
    )
    print(f"    买入明细原始记录数: {len(buy_raw)}")

    # 3. 获取卖出营业部明细
    sell_raw = fetch_eastmoney_api(
        REPORT_SELL_DETAILS,
        filter_expr=filter_expr,
        sort_columns="TRADE_DATE",
        sort_types="-1",
        page_size=500, max_pages=5,
    )
    print(f"    卖出明细原始记录数: {len(sell_raw)}")

    # 4. 按股票聚合，剔除机构/北向席位
    stock_map = {}

    def _agg_item(item, is_buy):
        code = item.get("SECURITY_CODE", "")
        dept = item.get("OPERATEDEPT_NAME", "")
        if not code:
            return
        # 剔除机构/北向席位
        for kw in YOUZI_EXCLUDE_DEPT_KEYWORDS:
            if kw in dept:
                return
        if code not in stock_map:
            stock_map[code] = {
                "code": code,
                "name": name_map.get(code, code),
                "net_buy": 0.0,
                "buy_amt": 0.0,
                "sell_amt": 0.0,
            }
        if is_buy:
            stock_map[code]["buy_amt"] += _safe_num(item.get("BUY"))
            stock_map[code]["net_buy"] += _safe_num(item.get("BUY"))
        else:
            stock_map[code]["sell_amt"] += _safe_num(item.get("SELL"))
            stock_map[code]["net_buy"] -= _safe_num(item.get("SELL"))

    for item in buy_raw:
        _agg_item(item, is_buy=True)
    for item in sell_raw:
        _agg_item(item, is_buy=False)

    stocks = list(stock_map.values())
    for s in stocks:
        s["net_buy_wan"] = s["net_buy"] / 10000.0
        s["buy_wan"] = s["buy_amt"] / 10000.0
        s["sell_wan"] = s["sell_amt"] / 10000.0

    buy_sorted = sorted(stocks, key=lambda x: x["net_buy"], reverse=True)
    sell_sorted = sorted(stocks, key=lambda x: x["net_buy"])

    print(f"    去重后股票数: {len(stocks)}")
    print(f"    游资净买入TOP10: {[(s['name'], round(s['net_buy_wan'],2)) for s in buy_sorted[:10]]}")

    return {"buy_sorted": buy_sorted, "sell_sorted": sell_sorted}


# ========== 共振计算 ==========

def compute_resonance(inst_top5: List[Dict], youzi_data: Dict) -> List[Dict]:
    """
    共振：机构净买入TOP5 ∩ 游资净买入TOP20
    返回: [{"stock_name": ..., "inst_amount": ..., "youzi_amount": ...}, ...]
    按机构排名顺序
    """
    inst_top5 = inst_top5[:5]
    inst_codes = {s["code"] for s in inst_top5}

    youzi_top_n = youzi_data["buy_sorted"][:RESONANCE_YOUZI_TOP_N]
    youzi_codes = {s["code"] for s in youzi_top_n}
    youzi_code_to_amount = {s["code"]: s["net_buy_wan"] for s in youzi_top_n}

    overlap = inst_codes & youzi_codes

    resonance = []
    for s in inst_top5:
        if s["code"] in overlap:
            resonance.append({
                "stock_name": s["name"],
                "inst_amount": round(s["net_buy_wan"], 2),
                "youzi_amount": round(youzi_code_to_amount.get(s["code"], 0.0), 2),
            })

    print(f"    共振股票数: {len(resonance)}")
    for r in resonance:
        print(f"      - {r['stock_name']}: 机构+{r['inst_amount']:.0f}万, 游资+{r['youzi_amount']:.0f}万")
    return resonance


# ========== 构建单日数据 ==========

def build_daily_data(date_str: str) -> Dict:
    """构建单日完整数据（返回dict，不依赖pydantic）"""
    print(f"📊 正在获取 {date_str} 的龙虎榜数据...")

    inst_data = get_institution_data(date_str)
    youzi_data = get_youzi_stock_data(date_str)

    # 基本完整性检查
    if not inst_data["buy_sorted"]:
        raise ValueError(f"{date_str} 机构数据为空，无法继续")

    inst_top5 = [
        {"name": s["name"], "code": s["code"], "amount": round(s["net_buy_wan"], 2)}
        for s in inst_data["buy_sorted"][:5]
        if s["net_buy"] > 0
    ]
    inst_sell_top5 = [
        {"name": s["name"], "code": s["code"], "amount": round(s["net_buy_wan"], 2)}
        for s in inst_data["sell_sorted"][:5]
        if s["net_buy"] < 0
    ]
    youzi_buy_top5 = [
        {"name": s["name"], "code": s["code"], "amount": round(s["net_buy_wan"], 2)}
        for s in youzi_data["buy_sorted"][:YOUZI_DISPLAY_TOP_N]
        if s["net_buy"] > 0
    ]
    youzi_sell_top5 = [
        {"name": s["name"], "code": s["code"], "amount": round(s["net_buy_wan"], 2)}
        for s in youzi_data["sell_sorted"][:YOUZI_DISPLAY_TOP_N]
        if s["net_buy"] < 0
    ]
    resonance = compute_resonance(inst_data["buy_sorted"][:5], youzi_data)

    return {
        "date": date_str,
        "institution_top5": inst_top5,
        "institution_sell_top5": inst_sell_top5,
        "resonance": resonance,
        "youzi_buy_top5": youzi_buy_top5,
        "youzi_sell_top5": youzi_sell_top5,
        "data_source": "东方财富龙虎榜官方API",
    }


# ========== HTML构建 ==========

def build_day_cell_html(data: Dict) -> str:
    """构建日期单元格HTML（与原版完全一致的结构和class）"""
    dt = datetime.strptime(data["date"], "%Y-%m-%d")
    day = dt.day
    inst_top5 = data["institution_top5"]
    inst_sell_top5 = data["institution_sell_top5"]
    resonance = data["resonance"]
    youzi_buy_top5 = data["youzi_buy_top5"]
    youzi_sell_top5 = data["youzi_sell_top5"]

    has_resonance = len(resonance) > 0
    has_data = bool(inst_top5 or resonance)

    if not has_data:
        return f'''                <div class="day-cell">
                    <div class="day-header"><span class="day-number">{day}</span></div>
                    <div class="empty-content">--</div>
                </div>'''

    resonance_names = {r["stock_name"] for r in resonance}

    lines = []
    lines.append('                <div class="day-cell">')

    # day-header
    if has_resonance:
        lines.append(
            f'                    <div class="day-header"><span class="day-number">{day}</span>'
            f'<span class="amount resonance-tag">★共振</span></div>'
        )
    else:
        lines.append(
            f'                    <div class="day-header"><span class="day-number">{day}</span></div>'
        )

    lines.append('                    <div class="stock-list">')

    # 1. 机构净买入TOP5
    if inst_top5:
        lines.append('                        <div class="section-title">▲ 机构净买入TOP5</div>')
        lines.append('                        <div class="stock-row">')
        for stock in inst_top5[:5]:
            amount_str = f"+{format_amount(stock['amount'])}"
            if stock["name"] in resonance_names:
                lines.append(
                    f'                            <span class="stock-item">'
                    f'<span class="stock-icon resonance">★</span>'
                    f'<span class="stock-name">{stock["name"]}</span>'
                    f'<span class="stock-amount resonance-amount">{amount_str}</span>'
                    f'</span>'
                )
            else:
                lines.append(
                    f'                            <span class="stock-item">'
                    f'<span class="stock-icon up">▲</span>'
                    f'<span class="stock-name">{stock["name"]}</span>'
                    f'<span class="stock-amount up">{amount_str}</span>'
                    f'</span>'
                )
        lines.append('                        </div>')

    # 2. 机构净卖出TOP5
    if inst_sell_top5:
        lines.append('                        <div class="section-title sell-title">▼ 机构净卖出TOP5</div>')
        lines.append('                        <div class="stock-row">')
        for stock in inst_sell_top5[:5]:
            amount_str = format_amount(stock["amount"])
            lines.append(
                f'                            <span class="stock-item">'
                f'<span class="stock-icon down">▼</span>'
                f'<span class="stock-name">{stock["name"]}</span>'
                f'<span class="stock-amount down">{amount_str}</span>'
                f'</span>'
            )
        lines.append('                        </div>')

    # 3. 机游共振
    if resonance:
        lines.append('                        <div class="section-title resonance-title">★ 机游共振</div>')
        lines.append('                        <div class="stock-row">')
        for res in resonance:
            lines.append(
                f'                            <span class="stock-item">'
                f'<span class="stock-icon resonance">★</span>'
                f'<span class="stock-name">{res["stock_name"]}</span>'
                f'<span class="stock-amount resonance-amount">+{format_amount(res["youzi_amount"])}</span>'
                f'</span>'
            )
        lines.append('                        </div>')

    # 4. 游资净买入TOP5
    if youzi_buy_top5:
        lines.append('                        <div class="section-title youzi-title">▲ 游资净买入TOP5</div>')
        lines.append('                        <div class="stock-row">')
        for stock in youzi_buy_top5[:YOUZI_DISPLAY_TOP_N]:
            amount_str = f"+{format_amount(stock['amount'])}"
            lines.append(
                f'                            <span class="stock-item">'
                f'<span class="stock-icon up">▲</span>'
                f'<span class="stock-name">{stock["name"]}</span>'
                f'<span class="stock-amount up">{amount_str}</span>'
                f'</span>'
            )
        lines.append('                        </div>')

    # 5. 游资净卖出TOP5
    if youzi_sell_top5:
        lines.append('                        <div class="section-title youzi-sell-title">▼ 游资净卖出TOP5</div>')
        lines.append('                        <div class="stock-row">')
        for stock in youzi_sell_top5[:YOUZI_DISPLAY_TOP_N]:
            amount_str = format_amount(stock["amount"])
            lines.append(
                f'                            <span class="stock-item">'
                f'<span class="stock-icon down">▼</span>'
                f'<span class="stock-name">{stock["name"]}</span>'
                f'<span class="stock-amount down">{amount_str}</span>'
                f'</span>'
            )
        lines.append('                        </div>')

    lines.append('                    </div>')
    lines.append('                </div>')
    return "\n".join(lines)


# ========== HTML更新 ==========

def update_html(html_path: str, data: Dict) -> bool:
    """更新HTML文件中指定日期的数据"""
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
    has_resonance = len(data["resonance"]) > 0
    has_data = bool(data["institution_top5"] or data["resonance"])

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
    td_pattern = re.compile(
        rf'(<td[^>]*>)\s*<div class="day-cell">((?!</td>).)*?'
        rf'<span class="day-number">\s*{day}\s*</span>'
        rf'((?!</td>).)*?</div>\s*</div>\s*</td>',
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

    # 共振 class
    if has_resonance and has_data:
        if 'class="' in td_open:
            if 'has-resonance' not in td_open:
                td_open = td_open.replace('class="', 'class="has-resonance ')
        else:
            td_open = td_open.rstrip('>') + ' class="has-resonance">'
    # 如果没有共振但之前有，移除 class（可选，这里保留避免影响其他逻辑）

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
        description="机游共振日历更新脚本 (GHA纯Python版)"
    )
    parser.add_argument("--date", required=True, help="目标日期 (YYYY-MM-DD)")
    parser.add_argument("--html", required=True, help="HTML文件路径")
    parser.add_argument("--dry-run", action="store_true", help="只抓取不写入")
    args = parser.parse_args()

    print("=" * 60)
    print("🚀 机游共振日历更新 (GHA纯Python版)")
    print(f"📅 目标日期: {args.date}")
    print(f"📄 HTML文件: {args.html}")
    print(f"🔬 共振逻辑: 机构净买入TOP5 ∩ 游资净买入TOP{RESONANCE_YOUZI_TOP_N}")
    print("=" * 60)

    # 交易日检查
    if not is_trading_day(args.date):
        print(f"📅 {args.date} 是非交易日（周末或法定假日），无需更新")
        sys.exit(0)

    try:
        daily_data = build_daily_data(args.date)

        print(f"\n✅ 获取到数据:")
        print(f"   机构净买入TOP5: {len(daily_data['institution_top5'])} 只")
        print(f"   机构净卖出TOP5: {len(daily_data['institution_sell_top5'])} 只")
        print(f"   游资净买入TOP5: {len(daily_data['youzi_buy_top5'])} 只")
        print(f"   游资净卖出TOP5: {len(daily_data['youzi_sell_top5'])} 只")
        print(f"   机游共振: {len(daily_data['resonance'])} 个")

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
