#!/usr/bin/env python3
"""
重要日历月度更新脚本 — GitHub Actions 增量更新版

核心原则：
  1. 绝对不重新生成页面，只在现有母版 HTML 上做增量数据更新
  2. 绝不修改 HTML 结构、布局、样式、颜色、顶栏导航
  3. 只更新日历格子里的数据（事件、数值、✅标记、更新时间）
  4. 用正则精确匹配 <td onclick="showDayDetail(N)"> 块，逐格替换 data-events 和 event-list

数据来源：
  - 中国宏观数据（CPI/PPI/PMI/GDP）：东方财富 datacenter 官方 API
  - 美股/台股/韩股财报：关注股季度规律
  - FOMC：2026-2027 日程内置，季度会议标注 SEP/点阵图
  - 节假日/交割等：模板已有，不改动

用法：
  python3 scripts/update_important_gha.py --month 2026-07 --repo-dir .
  python3 scripts/update_important_gha.py --month 2026-07 --repo-dir . --dry-run
  python3 scripts/update_important_gha.py --repo-dir .
"""

import argparse
import calendar
import hashlib
import json
import os
import re
import sys
import time
from datetime import date, datetime

try:
    import requests
except ImportError:
    print("❌ requests 库未安装，请先执行 pip install requests")
    sys.exit(1)

# ========== 路径与常量 ==========

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPT_DIR)

# 东方财富 API
EASTMONEY_API_BASE = "https://datacenter-web.eastmoney.com/api/data/v1/get"
EASTMONEY_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://data.eastmoney.com/",
    "Accept": "application/json, text/plain, */*",
}

# 关注的核心股票（用于财报事件构建）
US_EARNINGS_WATCH = {
    "TSLA": "特斯拉",
    "GOOGL": "谷歌",
    "MSFT": "微软",
    "AAPL": "苹果",
    "AMZN": "亚马逊",
    "META": "Meta",
    "NVDA": "英伟达",
    "MU": "美光",
    "JPM": "摩根大通",
    "MS": "摩根士丹利",
    "BLK": "贝莱德",
    "NFLX": "奈飞",
    "PEP": "百事",
    "DAL": "达美航空",
    "SPGI": "标普全球",
    "ADP": "ADP",
}

TW_EARNINGS_WATCH = {
    "TSM": "台积电",
}

KR_EARNINGS_WATCH = {
    "三星电子": "三星电子",
    "SK海力士": "SK海力士",
}

# FOMC 2026-2027 日程（截至已知）
FOMC_SCHEDULE = {
    (2026, 1): (27, 28),
    (2026, 3): (17, 18),
    (2026, 4): (29, 30),
    (2026, 6): (17, 18),
    (2026, 7): (29, 30),
    (2026, 9): (16, 17),
    (2026, 11): (4, 5),
    (2026, 12): (15, 16),
    (2027, 1): (26, 27),
    (2027, 3): (16, 17),
    (2027, 5): (4, 5),
    (2027, 6): (15, 16),
    (2027, 7): (27, 28),
    (2027, 9): (21, 22),
    (2027, 11): (2, 3),
    (2027, 12): (14, 15),
}
# 含点阵图/SEP 的季度会议（3/6/9/12月）
FOMC_SEP_MONTHS = {3, 6, 9, 12}


# ========== 工具函数 ==========

def _safe_float(v):
    if v is None:
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def _extract_dup_keywords(cls, text):
    """从事件文本中提取用于去重的关键词"""
    keywords = []
    stock_codes = re.findall(r'\(([A-Z]{2,6})\)', text)
    keywords.extend(stock_codes)
    if cls == 'us-stock':
        for name in US_EARNINGS_WATCH.values():
            if name in text:
                keywords.append(name)
    if cls == 'kr-stock':
        for name in KR_EARNINGS_WATCH.keys():
            if name in text:
                keywords.append(name)
    if cls == 'tw-stock':
        if '台积电' in text or 'TSMC' in text:
            keywords.extend(['台积电', 'TSMC'])
    return keywords


def file_md5(path):
    if not os.path.isfile(path):
        return None
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


# ========== 东方财富 API ==========

def fetch_eastmoney_report(report_name, columns="ALL", page_size=50,
                           page_num=1, filter_str=None, sort_col=None,
                           sort_type="-1", retries=3):
    """调用东方财富 datacenter API"""
    params = {
        "pageSize": str(page_size),
        "pageNumber": str(page_num),
        "reportName": report_name,
        "columns": columns,
        "source": "WEB",
        "client": "WEB",
    }
    if filter_str:
        params["filter"] = filter_str
    if sort_col:
        params["sortColumns"] = sort_col
        params["sortTypes"] = sort_type

    for attempt in range(retries):
        try:
            r = requests.get(
                EASTMONEY_API_BASE,
                params=params,
                headers=EASTMONEY_HEADERS,
                timeout=15,
            )
            r.raise_for_status()
            data = r.json()
            if data.get("success") and data.get("result"):
                return data["result"].get("data", [])
            return []
        except Exception as e:
            print(f"  ⚠️  API 请求失败 ({attempt + 1}/{retries}): {e}")
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
    return []


def fetch_cpi_data(year, month):
    target = f"{year}-{month:02d}-01"
    data = fetch_eastmoney_report(
        "RPT_ECONOMY_CPI",
        sort_col="REPORT_DATE",
        sort_type="-1",
        page_size=24,
    )
    for item in data:
        rd = item.get("REPORT_DATE", "")
        if rd.startswith(target[:7]):
            return item
    return None


def fetch_ppi_data(year, month):
    target = f"{year}-{month:02d}-01"
    data = fetch_eastmoney_report(
        "RPT_ECONOMY_PPI",
        sort_col="REPORT_DATE",
        sort_type="-1",
        page_size=24,
    )
    for item in data:
        rd = item.get("REPORT_DATE", "")
        if rd.startswith(target[:7]):
            return item
    return None


def fetch_pmi_data(year, month):
    target = f"{year}-{month:02d}-01"
    data = fetch_eastmoney_report(
        "RPT_ECONOMY_PMI",
        sort_col="REPORT_DATE",
        sort_type="-1",
        page_size=24,
    )
    for item in data:
        rd = item.get("REPORT_DATE", "")
        if rd.startswith(target[:7]):
            return item
    return None


def fetch_gdp_data(year, quarter):
    data = fetch_eastmoney_report(
        "RPT_ECONOMY_GDP",
        sort_col="REPORT_DATE",
        sort_type="-1",
        page_size=20,
    )
    for item in data:
        rd = item.get("REPORT_DATE", "")
        if rd.startswith(f"{year}-"):
            q_end_month = quarter * 3
            month_str = f"{q_end_month:02d}"
            if f"-{month_str}-" in rd or rd.startswith(f"{year}-{month_str}"):
                return item
    return None


# ========== 宏观数据发布状态检测 ==========

def build_macro_publish_status(target_year, target_month):
    """
    检查 target_year 年 target_month 月 各宏观数据的发布状态
    返回 dict: {event_key: {"published": bool, "value": str, ...}}
    """
    status = {}

    # 上月数据
    prev_month = target_month - 1
    prev_year = target_year
    if prev_month < 1:
        prev_month = 12
        prev_year -= 1

    # CPI
    cpi = fetch_cpi_data(prev_year, prev_month)
    cpi_published = cpi is not None
    cpi_value = ""
    if cpi_published:
        same = _safe_float(cpi.get("NATIONAL_SAME"))
        seq = _safe_float(cpi.get("NATIONAL_SEQUENTIAL"))
        parts = []
        if same is not None:
            parts.append(f"同比{same:+.1f}%")
        if seq is not None:
            parts.append(f"环比{seq:+.1f}%")
        cpi_value = "，".join(parts)
    status["cpi_ppi"] = {
        "published": cpi_published,
        "value": cpi_value,
        "data_month_label": f"{prev_year}年{prev_month}月",
    }

    # PPI
    ppi = fetch_ppi_data(prev_year, prev_month)
    ppi_published = ppi is not None
    ppi_value = ""
    if ppi_published:
        same = _safe_float(ppi.get("NATIONAL_SAME"))
        seq = _safe_float(ppi.get("NATIONAL_SEQUENTIAL"))
        parts = []
        if same is not None:
            parts.append(f"同比{same:+.1f}%")
        if seq is not None:
            parts.append(f"环比{seq:+.1f}%")
        ppi_value = "，".join(parts)
    status["ppi_only"] = {
        "published": ppi_published,
        "value": ppi_value,
        "data_month_label": f"{prev_year}年{prev_month}月",
    }

    # PMI
    pmi = fetch_pmi_data(target_year, target_month)
    pmi_published = pmi is not None
    pmi_value = ""
    if pmi_published:
        manu = _safe_float(pmi.get("MANU_PMI") or pmi.get("PMI_MANU") or pmi.get("MANUFACTURING_PMI"))
        if manu is None:
            for key in ["PMI", "MANU_PMI", "SERVICE_PMI", "NATIONAL_PMI"]:
                v = _safe_float(pmi.get(key))
                if v is not None:
                    manu = v
                    break
        if manu is not None:
            pmi_value = f"制造业{manu:.1f}%"
    status["pmi"] = {
        "published": pmi_published,
        "value": pmi_value,
    }

    # 工业/社零/固投
    status["industrial"] = {
        "published": False,
        "value": "",
        "data_month_label": f"{prev_year}年{prev_month}月",
    }

    # GDP: 季度数据，4/7/10月发布
    if target_month in (4, 7, 10):
        quarter_map = {4: 1, 7: 2, 10: 3}
        q = quarter_map.get(target_month)
        if q:
            gdp = fetch_gdp_data(target_year, q)
            gdp_published = gdp is not None
            gdp_value = ""
            if gdp_published:
                yoy = _safe_float(gdp.get("GDP_YOY") or gdp.get("YOY") or gdp.get("GDP_SAME"))
                if yoy is not None:
                    gdp_value = f"同比{yoy:+.1f}%"
            status["gdp"] = {
                "published": gdp_published,
                "value": gdp_value,
                "quarter_label": f"{target_year}年Q{q}",
            }

    return status


# ========== 财报日期（关注股） ==========

def build_us_earnings_events(year, month):
    """构建美股财报事件列表（基于季度规律，用于未来月份预估）"""
    events = []
    month_to_quarter = {
        1: 4,
        4: 1,
        7: 2,
        10: 3,
    }
    if month not in month_to_quarter:
        return events

    q = month_to_quarter[month]
    year_for_q = year if q != 4 else year - 1

    # 简化估算（按历史规律，仅作占位用，未来会被 API 覆盖）
    estimates = []
    bank_stocks = [("JPM", "摩根大通"), ("MS", "摩根士丹利"), ("BLK", "贝莱德")]
    for code, name in bank_stocks:
        estimates.append((14, code, name, f"{year_for_q}年Q{q}"))
    tech_week3 = [
        (22, "TSLA", "特斯拉"),
        (23, "GOOGL", "谷歌"),
        (29, "MSFT", "微软"),
        (29, "META", "Meta"),
        (30, "AAPL", "苹果"),
        (30, "AMZN", "亚马逊"),
    ]
    for day, code, name in tech_week3:
        estimates.append((day, code, name, f"{year_for_q}年Q{q}"))
    others = {
        1: [(12, "PEP", "百事"), (10, "DAL", "达美航空")],
        2: [(16, "NFLX", "奈飞"), (28, "SPGI", "标普全球"), (29, "ADP", "ADP")],
        3: [(18, "NFLX", "奈飞")],
        4: [(17, "NFLX", "奈飞")],
    }
    for day, code, name in others.get(q, []):
        estimates.append((day, code, name, f"{year_for_q}年Q{q}"))
    if q == 2:
        estimates.append((28, "NVDA", "英伟达", f"{year_for_q}年Q{q}"))
    elif q == 3:
        estimates.append((28, "NVDA", "英伟达", f"{year_for_q}年Q{q}"))
    if q == 3:
        estimates.append((25, "MU", "美光", f"{year_for_q}年Q{q}"))
    elif q == 1:
        estimates.append((20, "MU", "美光", f"{year_for_q}年Q{q}"))

    for day, code, name, period in estimates:
        events.append({
            'day': day,
            'cls': 'us-stock',
            'text': f'🇺🇸 {name}({code}) {period} 财报（预估）',
        })

    return events


def build_fomc_events(year, month):
    """构建 FOMC 事件"""
    events = []
    key = (year, month)
    if key not in FOMC_SCHEDULE:
        return events

    day1, day2 = FOMC_SCHEDULE[key]
    has_sep = month in FOMC_SEP_MONTHS

    events.append({
        'day': day1,
        'cls': 'fomc',
        'text': f'🇺🇸 FOMC议息会议第1天（{month}/{day1}）',
    })

    sep_text = '+点阵图/SEP ' if has_sep else ''
    events.append({
        'day': day2,
        'cls': 'fomc',
        'text': f'🇺🇸 FOMC利率决议 {sep_text}北京时间{month}/{day2}凌晨',
    })

    return events


def build_kr_earnings_events(year, month):
    """韩股财报事件"""
    events = []
    if month in (1, 4, 7, 10):
        q_label = {1: "Q4", 4: "Q1", 7: "Q2", 10: "Q3"}[month]
        y = year if month != 1 else year - 1
        events.append({
            'day': 7,
            'cls': 'kr-stock',
            'text': f'🇰🇷 三星电子{y}年{q_label}业绩指引',
        })
        last_day = calendar.monthrange(year, month)[1]
        events.append({
            'day': last_day,
            'cls': 'kr-stock',
            'text': f'🇰🇷 三星电子{y}年{q_label}完整财报',
        })
    return events


def build_tw_earnings_events(year, month):
    """台股财报事件（台积电）"""
    events = []
    if month in (1, 4, 7, 10):
        q_map = {1: "Q4", 4: "Q1", 7: "Q2", 10: "Q3"}
        y = year if month != 1 else year - 1
        q = q_map[month]
        events.append({
            'day': 18,
            'cls': 'tw-stock',
            'text': f'🇹🇼 台积电TSMC {y}年{q}财报/法说会',
        })
    return events


# ========== HTML 增量更新核心 ==========

def _get_indent(event_item_html):
    """从事件行提取缩进字符串"""
    m = re.match(r'^(\s*)', event_item_html)
    return m.group(1) if m else ''


def update_day_cell_in_html(html, day, updated_events, day_indent_info):
    """
    精确更新指定日期的单元格事件：
    - 替换 data-events='[...]'
    - 替换 <div class="event-list">...</div> 内容（或替换 empty-content）
    保持 td 标签、class、day-header、缩进完全不变。

    从后往前替换（大 day 号先替换），避免字符串长度变化导致后续匹配错位。
    """
    # 匹配当天 td 完整块（从 <td onclick="showDayDetail(N)" ...> 到 </td>）
    # 注意：用非贪婪匹配，只匹配到第一个 </td>
    pattern = re.compile(
        r'(<td[^>]*onclick="showDayDetail\(' + str(day) + r'\)"[^>]*data-events=\')([^\']*)(\'[^>]*>\s*<div class="day-cell">\s*<div class="day-header">.*?</div>\s*)(<div class="(?:event-list|empty-content)">.*?</div>)(\s*</div>\s*</td>)',
        re.DOTALL,
    )
    m = pattern.search(html)
    if not m:
        return html, False

    td_before_data = m.group(1)   # <td ... data-events='
    old_data_events = m.group(2)  # [...] 内的 JSON
    td_middle = m.group(3)        # '> 到 <div class="event-list|empty-content"> 之前的内容
    old_events_block = m.group(4)  # 整个 event-list 或 empty-content div
    td_after = m.group(5)         # 结束部分

    # 构建新的 data-events JSON
    new_data_json = json.dumps(
        [{"cls": e["cls"], "text": e["text"]} for e in updated_events],
        ensure_ascii=False,
    )

    # 从原块中精确提取缩进格式
    # list_indent: event-list/empty-content 外层缩进
    list_indent_match = re.search(r'^(\s*)<div class="(?:event-list|empty-content)">', old_events_block, re.MULTILINE)
    list_indent = list_indent_match.group(1) if list_indent_match else '                        '

    # item_indent: 每个 event-item 的缩进（含前导换行）
    item_indent_match = re.search(r'(\n\s*)<div class="event-item ', old_events_block)
    item_indent_full = item_indent_match.group(1) if item_indent_match else '\n                            '
    # 去掉开头的换行，保留纯空格缩进
    item_inner_indent = item_indent_full.lstrip('\n')
    # 结尾 </div> 的缩进（取空行后紧跟 </div> 的格式）
    closing_match = re.search(r'(\n\s*)</div>\s*$', old_events_block)
    closing_indent = closing_match.group(1) if closing_match else '\n' + list_indent

    if updated_events:
        parts = [f'{list_indent}<div class="event-list">']
        for ev in updated_events:
            parts.append(f'\n{item_inner_indent}<div class="event-item {ev["cls"]}">{ev["text"]}</div>')
        parts.append(f'{closing_indent}</div>')
        new_events_block = ''.join(parts)
    else:
        new_events_block = f'{list_indent}<div class="empty-content">--</div>'

    new_td = td_before_data + new_data_json + td_middle + new_events_block + td_after

    return html[:m.start()] + new_td + html[m.end():], True


def inject_events_into_html(html, year, month, new_events, macro_status):
    """
    增量更新 HTML 中的日历格子事件：
    1. 读取每个日期现有的 data-events
    2. 更新宏观事件（加 ✅ 和数值）
    3. 合并新增事件（按去重规则）
    4. 精确替换该天的 data-events 和 event-list 块

    只修改事件数据，绝不改动任何其他 DOM 结构。
    """
    # 按天分组新事件
    events_by_day = {}
    for ev in new_events:
        day = ev['day']
        if day not in events_by_day:
            events_by_day[day] = []
        events_by_day[day].append(ev)

    days_in_month = calendar.monthrange(year, month)[1]

    # 收集需要更新的日期（有新增事件 + 需要检查宏观发布的日期）
    days_to_update = set(events_by_day.keys())
    # 每月固定需要检查的宏观日期
    days_to_update.add(9)    # CPI/PPI
    days_to_update.add(15)   # 工业/社零/固投
    days_to_update.add(days_in_month)  # PMI
    # GDP 发布月（月中）
    if month in (4, 7, 10):
        days_to_update.add(15)

    changed_days = []

    # 从后往前替换，避免字符串长度变化导致匹配偏移
    sorted_days = sorted(days_to_update, reverse=True)

    for day in sorted_days:
        if day < 1 or day > days_in_month:
            continue

        # 先提取当天现有事件
        pattern = re.compile(
            r'<td[^>]*onclick="showDayDetail\(' + str(day) + r'\)"[^>]*data-events=\'([^\']*)\'',
            re.DOTALL,
        )
        m = pattern.search(html)
        if not m:
            continue

        try:
            existing_events = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue

        # ---- 更新宏观事件：标记已发布 + 附加数值 ----
        updated_events = []
        seen_keys = set()
        for ev in existing_events:
            ev = dict(ev)
            cls = ev.get('cls', '')
            text = ev.get('text', '')

            # CPI/PPI 事件（每月9日）
            if day == 9 and cls == 'macro' and ('CPI' in text or 'PPI' in text):
                ms = macro_status.get('cpi_ppi', {})
                if ms.get('published'):
                    val = ms.get('value', '')
                    month_label = ms.get('data_month_label', '')
                    suffix = f" ✅ {month_label}已发布"
                    if val:
                        suffix += f"（{val}）"
                    if '✅' not in text:
                        ev['text'] = text + suffix
            # 工业/社零/固投（每月15日）
            elif day == 15 and cls == 'macro' and ('工业' in text or '社零' in text or '固投' in text):
                ms = macro_status.get('industrial', {})
                if ms.get('published'):
                    val = ms.get('value', '')
                    month_label = ms.get('data_month_label', '')
                    suffix = f" ✅ {month_label}已发布"
                    if val:
                        suffix += f"（{val}）"
                    if '✅' not in text:
                        ev['text'] = text + suffix
            # PMI（每月最后一天）
            elif day == days_in_month and cls == 'macro' and 'PMI' in text:
                ms = macro_status.get('pmi', {})
                if ms.get('published'):
                    val = ms.get('value', '')
                    suffix = " ✅ 已发布"
                    if val:
                        suffix += f"（{val}）"
                    if '✅' not in text:
                        ev['text'] = text + suffix
            # GDP
            elif cls == 'macro' and 'GDP' in text:
                ms = macro_status.get('gdp', {})
                if ms.get('published'):
                    val = ms.get('value', '')
                    suffix = " ✅ 已发布"
                    if val:
                        suffix += f"（{val}）"
                    if '✅' not in text:
                        ev['text'] = text + suffix

            key = (ev['cls'], ev['text'])
            if key not in seen_keys:
                seen_keys.add(key)
                updated_events.append(ev)

        # ---- 注入新事件（去重） ----
        day_new_events = events_by_day.get(day, [])
        for new_ev in day_new_events:
            cls = new_ev['cls']
            text = new_ev['text']
            dup_keywords = _extract_dup_keywords(cls, text)

            is_dup = False
            for existing in updated_events:
                if existing['cls'] != cls:
                    continue
                existing_text = existing['text']
                if cls in ('us-stock', 'tw-stock', 'kr-stock', 'eu-earn', 'jp-earn'):
                    for kw in dup_keywords:
                        if kw and kw in existing_text:
                            is_dup = True
                            break
                elif cls == 'fomc':
                    if 'FOMC' in existing_text and 'FOMC' in text:
                        if len(text) > len(existing_text):
                            updated_events.remove(existing)
                            seen_keys.discard((existing['cls'], existing['text']))
                        else:
                            is_dup = True
                        break
                else:
                    if existing_text == text:
                        is_dup = True
                        break
                if is_dup:
                    break

            if not is_dup:
                key = (new_ev['cls'], new_ev['text'])
                if key not in seen_keys:
                    seen_keys.add(key)
                    updated_events.append(new_ev)

        # 按 cls 排序
        updated_events.sort(key=lambda x: x['cls'])

        # 判断是否有变化
        old_json = json.dumps([{"cls": e["cls"], "text": e["text"]} for e in existing_events],
                              ensure_ascii=False, sort_keys=True)
        new_json = json.dumps([{"cls": e["cls"], "text": e["text"]} for e in updated_events],
                              ensure_ascii=False, sort_keys=True)
        if old_json == new_json:
            continue

        # 执行替换
        html, ok = update_day_cell_in_html(html, day, updated_events, None)
        if ok:
            changed_days.append(day)

    return html, changed_days


def update_update_time(html, now_dt):
    """更新「本次更新时间」"""
    new_text = f'本次更新时间: {now_dt.strftime("%Y-%m-%d %H:%M")}'
    new_html, n = re.subn(
        r'本次更新时间: \d{4}-\d{2}-\d{2} \d{2}:\d{2}',
        new_text,
        html,
    )
    return new_html, n > 0


# ========== 主流程 ==========

def update_month_calendar(year, month, repo_dir, dry_run=False):
    """增量更新单月日历"""
    print(f"\n{'=' * 60}")
    print(f"📅 增量更新 {year}年{month}月 重要日历")
    print(f"{'=' * 60}")

    filename = f"重要日历_{year}{month:02d}.html"
    filepath = os.path.join(repo_dir, filename)

    if not os.path.isfile(filepath):
        print(f"❌ 文件不存在: {filepath}")
        return False

    today = date.today()
    now_dt = datetime.now()

    # 1. 读取现有 HTML 母版
    print("1/4 读取现有 HTML 母版...")
    old_md5 = file_md5(filepath)
    with open(filepath, "r", encoding="utf-8") as f:
        html = f.read()
    print(f"   文件: {filename}（{len(html)} 字符）")

    # 2. 获取宏观数据发布状态
    print("2/4 获取宏观数据发布状态...")
    macro_status = build_macro_publish_status(year, month)
    print(f"   CPI/PPI: {'已发布' if macro_status['cpi_ppi']['published'] else '未发布'}  "
          f"{macro_status['cpi_ppi'].get('value', '')}")
    print(f"   PMI: {'已发布' if macro_status['pmi']['published'] else '未发布'}  "
          f"{macro_status['pmi'].get('value', '')}")
    if 'gdp' in macro_status:
        print(f"   GDP: {'已发布' if macro_status['gdp']['published'] else '未发布'}  "
              f"{macro_status['gdp'].get('value', '')}")

    # 3. 构建增强事件列表
    print("3/4 构建增强事件...")
    extra_events = []
    extra_events.extend(build_us_earnings_events(year, month))
    extra_events.extend(build_fomc_events(year, month))
    extra_events.extend(build_kr_earnings_events(year, month))
    extra_events.extend(build_tw_earnings_events(year, month))
    print(f"   美股财报事件: {sum(1 for e in extra_events if e['cls']=='us-stock')} 个")
    print(f"   FOMC事件: {sum(1 for e in extra_events if e['cls']=='fomc')} 个")
    print(f"   韩股财报事件: {sum(1 for e in extra_events if e['cls']=='kr-stock')} 个")
    print(f"   台股财报事件: {sum(1 for e in extra_events if e['cls']=='tw-stock')} 个")

    # 4. 增量更新 HTML
    print("4/4 增量更新 HTML（仅修改事件数据）...")
    updated_html, changed_days = inject_events_into_html(html, year, month, extra_events, macro_status)
    updated_html, time_changed = update_update_time(updated_html, now_dt)

    new_md5 = hashlib.md5(updated_html.encode("utf-8")).hexdigest()
    changed = old_md5 != new_md5

    if changed_days:
        print(f"   有变化的日期: {sorted(changed_days)}")
    else:
        print(f"   事件数据无变化")
    if time_changed:
        print(f"   已更新「本次更新时间」")

    if dry_run:
        print(f"\n🔍 [DRY-RUN] 不写入文件")
        print(f"   原长度: {len(html)}  新长度: {len(updated_html)}")
        print(f"   状态: {'有变化' if changed else '无变化'}")
        return changed

    # 写回文件
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(updated_html)

    if changed:
        print(f"✅ {filename} 已更新（有变化）")
    else:
        print(f"ℹ️  {filename} 无变化")

    # 同步更新默认页（重要日历.html）—— 如果本月是当前月
    if today.year == year and today.month == month:
        default_path = os.path.join(repo_dir, "重要日历.html")
        default_old_md5 = file_md5(default_path)
        with open(default_path, "w", encoding="utf-8") as f:
            f.write(updated_html)
        default_new_md5 = file_md5(default_path)
        if default_old_md5 != default_new_md5:
            print(f"✅ 重要日历.html（默认页）已同步更新")
        else:
            print(f"ℹ️  重要日历.html（默认页）无变化")

    return changed


def main():
    parser = argparse.ArgumentParser(
        description="重要日历月度更新脚本 (GHA增量更新版)"
    )
    parser.add_argument(
        "--month",
        default=None,
        help="目标月份 (YYYY-MM，留空为当前月)",
    )
    parser.add_argument(
        "--lookahead",
        type=int,
        default=3,
        help="向前展望几个月（含本月，默认3个月）",
    )
    parser.add_argument(
        "--repo-dir",
        default=".",
        help="仓库根目录（默认当前目录）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只生成不写入",
    )
    args = parser.parse_args()

    repo_dir = os.path.abspath(args.repo_dir)
    print("=" * 60)
    print("🚀 重要日历月度更新 (GHA增量更新版)")
    print(f"📁 仓库目录: {repo_dir}")
    print(f"🔍 dry-run: {args.dry_run}")
    print("=" * 60)

    # 解析目标月
    if args.month:
        try:
            parts = args.month.split("-")
            target_year = int(parts[0])
            target_month = int(parts[1])
        except (ValueError, IndexError):
            print(f"❌ 无效的月份格式: {args.month}（应为 YYYY-MM）")
            sys.exit(1)
    else:
        today = date.today()
        target_year = today.year
        target_month = today.month

    lookahead = max(1, args.lookahead)
    print(f"📅 更新范围: {target_year}-{target_month:02d} ~ 未来{lookahead}个月")

    # 逐个更新
    any_changed = False
    total = 0
    year, month = target_year, target_month
    for i in range(lookahead):
        y = year + (month + i - 1) // 12
        m = (month + i - 1) % 12 + 1
        changed = update_month_calendar(y, m, repo_dir, args.dry_run)
        if changed:
            any_changed = True
        total += 1

    print(f"\n{'=' * 60}")
    print(f"🎉 处理完成：共 {total} 个月")

    # 输出 GHA 标记
    if any_changed and not args.dry_run:
        print("GHA_HAS_CHANGES=true")
        print(f"GHA_TARGET_MONTH={target_year}{target_month:02d}")
    else:
        print("GHA_NO_CHANGE=true")
        print("GHA_HAS_CHANGES=false")

    sys.exit(0)


if __name__ == "__main__":
    main()
