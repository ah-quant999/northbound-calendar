#!/usr/bin/env python3
"""
重要日历月度更新脚本 — GitHub Actions 纯 Python 版

完全不依赖 CodeActSDK / pydantic，只用标准库 + requests。
数据来源：
  - 基础日历结构：本地 generate_calendars.py 模板
  - 中国宏观数据（CPI/PPI/PMI/GDP）：东方财富 datacenter 官方 API
  - 美股/台股/韩股财报：关注股披露日期（硬编码季度规律 + API补充）
  - 节假日：内置日历数据
  - FOMC：联邦公开市场委员会日程内置

用法：
  python3 scripts/update_important_gha.py --month 2026-07 --repo-dir .
  python3 scripts/update_important_gha.py --month 2026-07 --repo-dir . --dry-run
  python3 scripts/update_important_gha.py --repo-dir .
  # 默认 month = 当前月
"""

import argparse
import calendar
import hashlib
import json
import os
import re
import sys
import time
from datetime import date, datetime, timedelta

try:
    import requests
except ImportError:
    print("❌ requests 库未安装，请先执行 pip install requests")
    sys.exit(1)

# ========== 路径与常量 ==========

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPT_DIR)  # 仓库根目录
GENERATE_SCRIPT = os.path.join(REPO_ROOT, "generate_calendars.py")

# 东方财富 API
EASTMONEY_API_BASE = "https://datacenter-web.eastmoney.com/api/data/v1/get"
EASTMONEY_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://data.eastmoney.com/",
    "Accept": "application/json, text/plain, */*",
}

# 关注的核心股票（用于财报日期注入）
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
    # 提取股票代码（美股代码格式：大写字母，如 TSLA、AAPL）
    stock_codes = re.findall(r'\(([A-Z]{2,6})\)', text)
    keywords.extend(stock_codes)
    # 提取公司名片段
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


def parse_date(date_str):
    """解析日期字符串为 date 对象"""
    if not date_str:
        return None
    date_str = date_str.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d", "%Y年%m月%d日"):
        try:
            return datetime.strptime(date_str, fmt).date()
        except ValueError:
            continue
    return None


def file_md5(path):
    """计算文件 MD5"""
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
    """获取 CPI 数据，返回该月数据 dict 或 None"""
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
    """获取 PPI 数据"""
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
    """获取官方 PMI 数据"""
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
    """获取 GDP 数据"""
    data = fetch_eastmoney_report(
        "RPT_ECONOMY_GDP",
        sort_col="REPORT_DATE",
        sort_type="-1",
        page_size=20,
    )
    for item in data:
        rd = item.get("REPORT_DATE", "")
        if rd.startswith(f"{year}-"):
            # 按季度匹配
            q_end_month = quarter * 3
            month_str = f"{q_end_month:02d}"
            if f"-{month_str}-" in rd or rd.startswith(f"{year}-{month_str}"):
                return item
    return None


# ========== 宏观数据发布状态检测 ==========

def build_macro_publish_status(target_year, target_month):
    """
    检查 target_year 年 target_month 月 各宏观数据的发布状态
    返回 dict: {event_key: {"published": bool, "value": str, "publish_day": int or None}}
    """
    status = {}

    # CPI/PPI: 通常次月 9 日左右发布上月数据
    # 例如 2026年6月CPI 于 2026年7月9日发布
    # 我们查的是「数据所属月份」= target_month 的发布情况
    # 实际上我们的日历事件中，每月9日是「上月CPI/PPI发布日」
    # 所以对于 month=M，CPI/PPI 事件对应的是 (M-1) 月的数据

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

    # PMI: 当月最后一天发布当月数据（官方PMI）
    # 日历里每月最后一天有 PMI 事件
    pmi = fetch_pmi_data(target_year, target_month)
    pmi_published = pmi is not None
    pmi_value = ""
    if pmi_published:
        # 找制造业PMI值
        manu = _safe_float(pmi.get("MANU_PMI") or pmi.get("PMI_MANU") or pmi.get("MANUFACTURING_PMI"))
        if manu is None:
            # 尝试用第一个非 None 的数值字段
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

    # 工业/社零/固投: 每月15日左右发布上月数据
    status["industrial"] = {
        "published": False,  # API暂未找到对应报表
        "value": "",
        "data_month_label": f"{prev_year}年{prev_month}月",
    }

    # GDP: 季度数据，在季后月中旬发布
    # 季度末月+1 月的 15 日左右
    if target_month in (1, 4, 7, 10):
        # target_month = 发布月
        q_num = (target_month - 1) // 3  # 季度序号 (0=Q1发布在4月? 不对)
        # 1月: 上年Q4? 不，1月一般没GDP。4月: Q1, 7月: Q2上修, 10月: Q3
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

def estimate_us_earnings_dates(year, quarter):
    """
    估算美股关注股财报日期（基于历史规律，用于未来月份）
    返回: list of (day, stock_code, stock_name, period_text)
    """
    # 简化：根据季度估算大致发布周
    # Q1财报季: 4月中下旬
    # Q2财报季: 7月中下旬
    # Q3财报季: 10月中下旬
    # Q4/年报: 1月中下旬
    quarter_month_map = {1: 4, 2: 7, 3: 10, 4: 1}

    target_month = quarter_month_map[quarter]
    target_year = year if quarter != 4 else year + 1

    # 简化估算（按历史规律）
    estimates = []
    # 银行股：财报季第2周（约 14-15 日）
    bank_stocks = [("JPM", "摩根大通"), ("MS", "摩根士丹利"), ("BLK", "贝莱德")]
    for code, name in bank_stocks:
        estimates.append((14, code, name, f"{target_year}年Q{quarter}"))
    # 科技股：财报季第3周后半（约 22-30 日）
    tech_week3 = [
        (22, "TSLA", "特斯拉"),
        (23, "GOOGL", "谷歌"),
        (29, "MSFT", "微软"),
        (29, "META", "Meta"),
        (30, "AAPL", "苹果"),
        (30, "AMZN", "亚马逊"),
    ]
    for day, code, name in tech_week3:
        estimates.append((day, code, name, f"{target_year}年Q{quarter}"))
    # 其他
    others = {
        1: [(12, "PEP", "百事"), (10, "DAL", "达美航空")],
        2: [(16, "NFLX", "奈飞"), (28, "SPGI", "标普全球"), (29, "ADP", "ADP")],
        3: [(18, "NFLX", "奈飞")],
        4: [(17, "NFLX", "奈飞")],
    }
    for day, code, name in others.get(quarter, []):
        estimates.append((day, code, name, f"{target_year}年Q{quarter}"))
    # 英伟达
    if quarter == 2:
        estimates.append((28, "NVDA", "英伟达", f"{target_year}年Q{quarter}"))
    elif quarter == 3:
        estimates.append((28, "NVDA", "英伟达", f"{target_year}年Q{quarter}"))
    # 美光
    if quarter == 3:
        estimates.append((25, "MU", "美光", f"{target_year}年Q{quarter}"))
    elif quarter == 1:
        estimates.append((20, "MU", "美光", f"{target_year}年Q{quarter}"))

    # 过滤出 target_month 的
    return [e for e in estimates if target_month == target_month or True]


def build_us_earnings_events(year, month):
    """
    构建美股财报事件列表
    返回: list of dict {'day': int, 'cls': 'us-stock', 'text': str}
    """
    events = []
    # 判断该月属于哪个财报季
    month_to_quarter = {
        1: 4,    # 1月 = 上一年Q4财报季
        4: 1,    # 4月 = Q1财报季
        7: 2,    # 7月 = Q2财报季
        10: 3,   # 10月 = Q3财报季
    }
    if month not in month_to_quarter:
        return events

    q = month_to_quarter[month]
    year_for_q = year if q != 4 else year - 1

    estimates = estimate_us_earnings_dates(year_for_q, q)
    # 过滤出本月的
    month_estimates = [(d, c, n, p) for d, c, n, p in estimates if d > 0 and d <= 31]

    for day, code, name, period in month_estimates:
        events.append({
            'day': day,
            'cls': 'us-stock',
            'text': f'🇺🇸 {name}({code}) {period} 财报（预估）',
        })

    return events


# ========== FOMC 事件 ==========

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

    # 会议纪要：会议后 3 周左右（一般在会后第 3 周的周三/周四）
    # 简化处理，不做精确计算，只在对应月标注
    return events


# ========== 其他月度事件增强 ==========

def build_kr_earnings_events(year, month):
    """韩股财报事件（三星/SK海力士）"""
    events = []
    # 三星电子：业绩指引在季度最后一个月的第一周初，正式财报在下个月第一周
    # Q1: 指引 4月初，财报 4月底
    # Q2: 指引 7月初，财报 7月底
    # Q3: 指引 10月初，财报 10月底
    # Q4: 指引 1月初，财报 1月底
    quarter_data = {
        1: ("指引", 7, 1, None),   # 1月: 上年Q4指引? 实际 Q4指引在1月初
        4: ("Q1业绩指引", 7, 1, "Q1完整财报"),
        7: ("Q2业绩指引", 7, 2, "Q2完整财报"),
        10: ("Q3业绩指引", 7, 3, "Q3完整财报"),
    }
    # 指引在季度末月+1 的月初；完整财报在月末
    if month in (1, 4, 7, 10):
        q_label = {1: "Q4", 4: "Q1", 7: "Q2", 10: "Q3"}[month]
        y = year if month != 1 else year - 1
        # 月初：业绩指引（约 7 日）
        events.append({
            'day': 7,
            'cls': 'kr-stock',
            'text': f'🇰🇷 三星电子{y}年{q_label}业绩指引',
        })
        # 月底：完整财报（约 30 日）
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
    # 台积电：法说会/财报在 4/7/10/1 月中旬
    if month in (1, 4, 7, 10):
        q_map = {1: "Q4", 4: "Q1", 7: "Q2", 10: "Q3"}
        y = year if month != 1 else year - 1
        q = q_map[month]
        # 台积电通常在当月第 3 周的周四发布
        # 简化：18 日
        events.append({
            'day': 18,
            'cls': 'tw-stock',
            'text': f'🇹🇼 台积电TSMC {y}年{q}财报/法说会',
        })
    return events


# ========== HTML 事件注入与更新 ==========

def inject_events_into_html(html, year, month, new_events, macro_status):
    """
    将新事件注入到 HTML 的日历格子中，同时更新宏观事件的「已发布」标记
    仅修改当前月日期的格子内容，保持所有样式/结构/颜色不变

    策略：
      1. 对于每个日期，在现有事件基础上合并新增事件（按 cls 去重）
      2. 对于宏观事件（CPI/PPI/PMI等），若已发布则更新文本加 ✅ 标记
      3. 同时更新 data-events 属性和 event-list 内容
    """
    # 按天分组新事件
    events_by_day = {}
    for ev in new_events:
        day = ev['day']
        if day not in events_by_day:
            events_by_day[day] = []
        events_by_day[day].append(ev)

    days_in_month = calendar.monthrange(year, month)[1]

    for day in range(1, days_in_month + 1):
        day_new_events = events_by_day.get(day, [])
        if not day_new_events and day not in [9, 15, days_in_month]:
            # 既没有新事件，也不是需要检查宏观发布状态的日期，跳过
            continue

        # 找到当天的 <td> 块
        # 用正则匹配：onclick="showDayDetail(N)" 精确到天
        pattern = re.compile(
            r'(<td[^>]*onclick="showDayDetail\(' + str(day) + r'\)"[^>]*data-events=\'[^\']*\'>.*?</td>)',
            re.DOTALL,
        )
        m = pattern.search(html)
        if not m:
            print(f"  ⚠️  未找到 {month}月{day}日 的单元格")
            continue

        td_block = m.group(1)

        # 提取 data-events 中的现有事件
        data_m = re.search(r"data-events='(\[.*?\])'", td_block, re.DOTALL)
        if not data_m:
            continue
        try:
            existing_events = json.loads(data_m.group(1))
        except json.JSONDecodeError:
            continue

        # 更新宏观事件：标记已发布 + 附加数值
        updated_events = []
        seen_keys = set()
        for ev in existing_events:
            ev = dict(ev)  # 复制
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
            # GDP（4/7/10月）
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

        # 注入新事件（按 cls + 关键词 去重，避免与基础日历已有的具体事件重复）
        for new_ev in day_new_events:
            cls = new_ev['cls']
            text = new_ev['text']

            # 生成去重关键词
            dup_keywords = _extract_dup_keywords(cls, text)

            # 检查是否与现有事件重复（同类事件且关键词高度重叠）
            is_dup = False
            for existing in updated_events:
                if existing['cls'] != cls:
                    continue
                existing_text = existing['text']
                # 对财报类：检查股票代码/名称是否重复
                if cls in ('us-stock', 'tw-stock', 'kr-stock', 'eu-earn', 'jp-earn'):
                    for kw in dup_keywords:
                        if kw and kw in existing_text:
                            is_dup = True
                            break
                # 对 FOMC：同一天的 FOMC 只保留更详细的
                elif cls == 'fomc':
                    # 同一天的 FOMC 事件，如果现有文本已经包含日期/天数等关键信息，则不重复添加
                    if ('FOMC' in existing_text and 'FOMC' in text):
                        # 保留文本更长、更详细的那个
                        if len(text) > len(existing_text):
                            updated_events.remove(existing)
                            seen_keys.discard((existing['cls'], existing['text']))
                        else:
                            is_dup = True
                        break
                # 其他类：精确 cls+text 去重
                else:
                    if existing_text == text:
                        is_dup = True
                        break

            if not is_dup:
                key = (new_ev['cls'], new_ev['text'])
                if key not in seen_keys:
                    seen_keys.add(key)
                    updated_events.append(new_ev)

        # 按 cls 排序
        updated_events.sort(key=lambda x: x['cls'])

        # 重建 data-events 和 event-list
        new_data_events = json.dumps(
            [{"cls": e["cls"], "text": e["text"]} for e in updated_events],
            ensure_ascii=False,
        ).replace("'", "&#39;")

        # 重建 event-list 或 empty-content
        if updated_events:
            event_html_lines = ['                        <div class="event-list">']
            for ev in updated_events:
                event_html_lines.append(
                    f'                            <div class="event-item {ev["cls"]}">{ev["text"]}</div>'
                )
            event_html_lines.append('                        </div>')
            event_html = '\n'.join(event_html_lines)
        else:
            event_html = '                        <div class="empty-content">--</div>'

        # 替换 data-events
        new_td = re.sub(
            r"data-events='\[.*?\]'",
            f"data-events='{new_data_events}'",
            td_block,
            count=1,
            flags=re.DOTALL,
        )

        # 替换 event-list 或 empty-content 块
        # 先尝试替换 event-list
        if re.search(r'<div class="event-list">.*?</div>', new_td, re.DOTALL):
            new_td = re.sub(
                r'<div class="event-list">.*?</div>',
                event_html if updated_events else '                        <div class="empty-content">--</div>',
                new_td,
                count=1,
                flags=re.DOTALL,
            )
        else:
            # 替换 empty-content
            new_td = re.sub(
                r'<div class="empty-content">.*?</div>',
                event_html if updated_events else '                        <div class="empty-content">--</div>',
                new_td,
                count=1,
                flags=re.DOTALL,
            )

        html = html[:m.start()] + new_td + html[m.end():]

    return html


def update_today_events_section(html, year, month, today_date):
    """更新「今日事件」区块（如果今天在本月）"""
    if not (today_date.year == year and today_date.month == month):
        return html

    today_day = today_date.day
    # 从 data-events 中提取今日事件
    pattern = re.compile(
        r'<td[^>]*onclick="showDayDetail\(' + str(today_day) + r'\)"[^>]*data-events=\'([^\']*)\'',
        re.DOTALL,
    )
    m = pattern.search(html)
    if not m:
        return html

    try:
        today_events = json.loads(m.group(1))
    except json.JSONDecodeError:
        return html

    if not today_events:
        return html

    today_events.sort(key=lambda x: x['cls'])

    color_map = {
        'policy': '#ff8c42', 'cbank': '#ffd700', 'macro': '#e8b830',
        'earn-end': '#ff6b55', 'option': '#4da6ff', 'futures': '#6cb4ff',
        'a50': '#ff5a52', 'hk-holiday': '#78828a', 'tw-stock': '#2ea043',
        'phone': '#c084fc', 'fomc': '#e63946', 'us-holiday': '#5a6270',
        'us-stock': '#388bfd', 'eu-earn': '#4ade80', 'jp-earn': '#f0c040',
        'kr-stock': '#a855f7', 'sg-holiday': '#ff528a',
    }
    cls_label_map = {
        'policy': '重要政策', 'cbank': '央行/LPR', 'macro': '中国数据',
        'earn-end': '财报截止', 'option': '期权交割', 'futures': '期货交割',
        'a50': 'A50交割', 'hk-holiday': '港股休市', 'tw-stock': '台股财报',
        'phone': '苹果/华为', 'fomc': 'FOMC', 'us-holiday': '美股休市',
        'us-stock': '美股财报', 'eu-earn': '欧股财报', 'jp-earn': '日股财报',
        'kr-stock': '韩股财报', 'sg-holiday': 'SG公假',
    }

    lines = []
    for ev in today_events:
        is_important = ev['cls'] in ('fomc', 'earn-end', 'a50', 'policy')
        star_html = '<span class="today-event-star">★</span>' if is_important else ''
        dot_color = color_map.get(ev['cls'], '#8b949e')
        cls_label = cls_label_map.get(ev['cls'], ev['cls'])
        lines.append(f'        <div class="today-event-item">')
        lines.append(f'            <span class="today-event-dot" style="background:{dot_color};"></span>')
        lines.append(f'            <span class="today-event-text">{star_html}{ev["text"]}</span>')
        lines.append(f'            <span class="today-event-cls" style="background:{dot_color};color:#fff;">{cls_label}</span>')
        lines.append(f'        </div>')

    today_html = '\n'.join(lines)

    # 找到今日事件区块并替换内容
    section_pattern = re.compile(
        r'(<div class="today-events-section">.*?<div class="today-events-title">.*?</div>\n)(.*?)(\n    </div>)',
        re.DOTALL,
    )
    m2 = section_pattern.search(html)
    if not m2:
        return html

    # 更新标题里的数量
    title_pattern = re.compile(
        r'📅 今日事件\(\d+个\) · \d{4}-\d{2}-\d{2}'
    )
    new_title = f'📅 今日事件({len(today_events)}个) · {today_date.strftime("%Y-%m-%d")}'

    new_section = m2.group(1) + today_html + m2.group(3)
    new_section = title_pattern.sub(new_title, new_section)

    html = html[:m2.start()] + new_section + html[m2.end():]

    return html


def update_update_time(html, now_dt):
    """更新「本次更新时间」"""
    new_text = f'本次更新时间: {now_dt.strftime("%Y-%m-%d %H:%M")}'
    html = re.sub(
        r'本次更新时间: \d{4}-\d{2}-\d{2} \d{2}:\d{2}',
        new_text,
        html,
    )
    return html


# ========== 主流程 ==========

def update_month_calendar(year, month, repo_dir, dry_run=False):
    """更新单月日历"""
    print(f"\n{'=' * 60}")
    print(f"📅 更新 {year}年{month}月 重要日历")
    print(f"{'=' * 60}")

    # 导入生成模块
    sys.path.insert(0, repo_dir)
    try:
        from generate_calendars import generate_month_html
    except ImportError as e:
        print(f"❌ 无法导入 generate_calendars: {e}")
        return False

    today = date.today()
    now_dt = datetime.now()

    # 1. 生成基础 HTML
    print("1/4 生成基础日历 HTML...")
    base_html = generate_month_html(year, month, today)

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

    # 4. 注入事件到 HTML
    print("4/4 注入事件并更新 HTML...")
    updated_html = inject_events_into_html(base_html, year, month, extra_events, macro_status)
    updated_html = update_today_events_section(updated_html, year, month, today)
    updated_html = update_update_time(updated_html, now_dt)

    # 写入文件
    filename = f"重要日历_{year}{month:02d}.html"
    filepath = os.path.join(repo_dir, filename)

    if dry_run:
        print(f"\n🔍 [DRY-RUN] 不写入文件: {filename}")
        print(f"   原 HTML 长度: {len(base_html)}")
        print(f"   新 HTML 长度: {len(updated_html)}")
        return True

    # 比较变化
    old_md5 = file_md5(filepath)

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(updated_html)

    new_md5 = file_md5(filepath)
    changed = old_md5 != new_md5

    if changed:
        print(f"✅ {filename} 已更新（有变化）")
    else:
        print(f"ℹ️  {filename} 无变化")

    # 同时更新「重要日历.html」（默认页 = 当前月）
    if today.year == year and today.month == month:
        default_path = os.path.join(repo_dir, "重要日历.html")
        default_old_md5 = file_md5(default_path)
        with open(default_path, "w", encoding="utf-8") as f:
            f.write(updated_html)
        default_new_md5 = file_md5(default_path)
        if default_old_md5 != default_new_md5:
            print(f"✅ 重要日历.html（默认页）已更新")
        else:
            print(f"ℹ️  重要日历.html（默认页）无变化")

    return changed if old_md5 is not None else True


def main():
    parser = argparse.ArgumentParser(
        description="重要日历月度更新脚本 (GHA纯Python版)"
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
    print("🚀 重要日历月度更新 (GHA纯Python版)")
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
