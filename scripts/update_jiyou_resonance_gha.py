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
from datetime import datetime, timedelta
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

# 游资数据排除的席位类型（北向+机构+非营业部席位，确保游资口径纯净）
# 黑名单：匹配即排除
YOUZI_EXCLUDE_DEPT_KEYWORDS = [
    # 北向席位（含各种变体）
    "沪股通", "深股通", "陆股通", "香港中央结算",
    # 机构席位
    "机构专用",
    # 非营业部席位（龙虎榜中的股东类型，不是真实营业部）
    "自然人", "中小投资者", "其他自然人", "机构投资者",
    "个人投资者", "一般法人", "国有法人", "境内非国有法人",
    "境外法人", "境内自然人", "境外自然人", "内部职工股",
    "战略投资者", "网下配售", "公募基金", "社保基金",
    "养老金", "保险资金", "企业年金", "信托",
]

# 游资营业部白名单关键词（名称中包含任一关键词才认为是真实营业部）
YOUZI_INCLUDE_DEPT_KEYWORDS = [
    "证券", "银行", "营业部", "分公司", "有限责任公司", "股份有限公司",
    "资产管理", "投资", "证券投资", "创业投资", "股权投资",
    "高盛", "摩根", "瑞银", "中金", "中信建投", "国泰君安", "国泰海通",
    "华泰", "招商", "广发", "银河", "海通", "申万", "国信",
    "东方财富", "平安", "兴业", "光大", "方正", "中泰",
    "长江", "国金", "华西", "东吴", "浙商", "财通",
    "开源", "华鑫", "信达", "国投", "中金财富", "中国银河",
    "中信证券", "中信建投证券",
]


def _is_real_business_department(dept_name: str) -> bool:
    """判断营业部名称是否为真实营业部（黑名单优先排除，白名单二次确认）"""
    if not dept_name:
        return False
    # 黑名单优先
    for kw in YOUZI_EXCLUDE_DEPT_KEYWORDS:
        if kw in dept_name:
            return False
    # 白名单确认（至少命中一个）
    for kw in YOUZI_INCLUDE_DEPT_KEYWORDS:
        if kw in dept_name:
            return True
    return False


# 共振参数
RESONANCE_YOUZI_TOP_N = 20
YOUZI_DISPLAY_TOP_N = 5

# 每日信号精选阈值
SIGNAL_INST_ABS_THRESHOLD = 2000.0   # 机构净买卖绝对值 ≥ 2000万
SIGNAL_YOUZI_ABS_THRESHOLD = 1500.0  # 游资净买卖绝对值 ≥ 1500万
SIGNAL_NET_BUY_RATIO_THRESHOLD = 0.05  # 净买占当日成交额 > 5%

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
                "accum_amount": 0.0,
            }
        stock_map[code]["net_buy"] += net_buy
        stock_map[code]["buy_amt"] += buy_amt
        stock_map[code]["sell_amt"] += sell_amt
        stock_map[code]["buy_count"] = max(stock_map[code]["buy_count"], buy_times)
        stock_map[code]["sell_count"] = max(stock_map[code]["sell_count"], sell_times)
        # 总成交额（同一股票可能多条记录，取最大值或累加，这里取最大值）
        accum = _safe_num(item.get("ACCUM_AMOUNT"))
        if accum > stock_map[code]["accum_amount"]:
            stock_map[code]["accum_amount"] = accum

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
        # 剔除非营业部席位（黑名单+白名单双校验）
        if not _is_real_business_department(dept):
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


# ========== 每日信号精选 ==========

def compute_daily_signals(inst_data: Dict, youzi_data: Dict) -> Dict:
    """
    计算每日信号精选（4类信号）

    阈值：
      - 机构净买卖绝对值 ≥ 2000万
      - 游资净买卖绝对值 ≥ 1500万
      - 净买占当日成交额 > 5%（有数据时启用）

    4类信号（两边都达标的才入选）：
      ① 机游共振买入：机构净买>0 且 游资净买>0
      ② 机游共振卖出：机构净卖>0 且 游资净卖>0
      ③ 机构出货游资接盘：机构净卖>0 且 游资净买>0
      ④ 机构接盘游资出货：机构净买>0 且 游资净卖>0

    返回: {
        "signal_resonance_buy": [...],
        "signal_resonance_sell": [...],
        "signal_inst_sell_youzi_buy": [...],
        "signal_inst_buy_youzi_sell": [...],
    }
    每项元素: {"name":..., "code":..., "inst_net_wan":..., "youzi_net_wan":..., "net_ratio": float|None}
    """
    # 构建机构 map（含总成交额，用于计算占比）
    inst_map = {}
    all_inst_stocks = inst_data["buy_sorted"] + [
        s for s in inst_data["sell_sorted"]
        if s["code"] not in {x["code"] for x in inst_data["buy_sorted"]}
    ]
    for s in all_inst_stocks:
        inst_map[s["code"]] = s  # 包含 net_buy_wan, accum_amount 等

    # 构建游资 map
    youzi_map = {}
    all_youzi_stocks = youzi_data["buy_sorted"] + [
        s for s in youzi_data["sell_sorted"]
        if s["code"] not in {x["code"] for x in youzi_data["buy_sorted"]}
    ]
    for s in all_youzi_stocks:
        youzi_map[s["code"]] = s

    # 找交集（两边都有数据的股票）
    common_codes = set(inst_map.keys()) & set(youzi_map.keys())

    signal_resonance_buy = []      # 机游共振买入
    signal_resonance_sell = []     # 机游共振卖出
    signal_inst_sell_youzi_buy = []  # 机构出货游资接盘
    signal_inst_buy_youzi_sell = []  # 机构接盘游资出货

    for code in common_codes:
        inst = inst_map[code]
        youzi = youzi_map[code]
        inst_net = inst.get("net_buy_wan", 0.0)
        youzi_net = youzi.get("net_buy_wan", 0.0)

        # 绝对值阈值检查
        if abs(inst_net) < SIGNAL_INST_ABS_THRESHOLD:
            continue
        if abs(youzi_net) < SIGNAL_YOUZI_ABS_THRESHOLD:
            continue

        # 计算净买占比（用较大的那一方的总成交额更准确，
        # 但机构 API 里已有 ACCUM_AMOUNT，直接用机构的即可）
        accum_amount = inst.get("accum_amount", 0.0)  # 元
        net_ratio = None
        if accum_amount and accum_amount > 0:
            # 净买卖金额（绝对值之和或取较大值？用两者净买中较大者计算占比）
            # 这里定义"净买占比"为 max(|机构净买|, |游资净买|) / 当日总成交额
            max_abs_net = max(abs(inst_net), abs(youzi_net))
            net_ratio = (max_abs_net * 10000.0) / accum_amount
            # 占比阈值过滤（> 5%）
            if net_ratio <= SIGNAL_NET_BUY_RATIO_THRESHOLD:
                continue

        item = {
            "name": inst.get("name", youzi.get("name", code)),
            "code": code,
            "inst_net_wan": round(inst_net, 2),
            "youzi_net_wan": round(youzi_net, 2),
            "net_ratio": round(net_ratio * 100, 2) if net_ratio is not None else None,
        }

        if inst_net > 0 and youzi_net > 0:
            signal_resonance_buy.append(item)
        elif inst_net < 0 and youzi_net < 0:
            signal_resonance_sell.append(item)
        elif inst_net < 0 and youzi_net > 0:
            signal_inst_sell_youzi_buy.append(item)
        elif inst_net > 0 and youzi_net < 0:
            signal_inst_buy_youzi_sell.append(item)

    # 按机构净买金额排序
    signal_resonance_buy.sort(key=lambda x: x["inst_net_wan"], reverse=True)
    signal_resonance_sell.sort(key=lambda x: x["inst_net_wan"])
    signal_inst_sell_youzi_buy.sort(key=lambda x: x["inst_net_wan"])
    signal_inst_buy_youzi_sell.sort(key=lambda x: x["inst_net_wan"], reverse=True)

    result = {
        "signal_resonance_buy": signal_resonance_buy,
        "signal_resonance_sell": signal_resonance_sell,
        "signal_inst_sell_youzi_buy": signal_inst_sell_youzi_buy,
        "signal_inst_buy_youzi_sell": signal_inst_buy_youzi_sell,
    }

    print(f"    🎯 每日信号精选:")
    print(f"      ① 机游共振买入: {len(signal_resonance_buy)} 只")
    print(f"      ② 机游共振卖出: {len(signal_resonance_sell)} 只")
    print(f"      ③ 机构出货游资接盘: {len(signal_inst_sell_youzi_buy)} 只")
    print(f"      ④ 机构接盘游资出货: {len(signal_inst_buy_youzi_sell)} 只")

    return result


def build_signals_html(signals: Dict) -> str:
    """
    构建每日信号精选的隐藏 HTML 区块。
    放在 day-cell 内，日历格子中不显示，详情弹窗中渲染。
    使用 signal-group 包裹，内含 4 个 signal-section。
    """
    has_any = any(signals[k] for k in signals)
    if not has_any:
        return ''

    sections = []
    signal_defs = [
        ("signal_resonance_buy", "① 机游共振买入", "signal-buy", "#f85149"),
        ("signal_resonance_sell", "② 机游共振卖出", "signal-sell", "#3fb950"),
        ("signal_inst_sell_youzi_buy", "③ 机构出货 游资接盘", "signal-mix1", "#d29922"),
        ("signal_inst_buy_youzi_sell", "④ 机构接盘 游资出货", "signal-mix2", "#a371f7"),
    ]

    for key, title, cls, color in signal_defs:
        items = signals.get(key, [])
        if not items:
            continue
        item_lines = []
        for it in items:
            inst_str = f"+{format_amount(it['inst_net_wan'])}" if it['inst_net_wan'] > 0 else format_amount(it['inst_net_wan'])
            youzi_str = f"+{format_amount(it['youzi_net_wan'])}" if it['youzi_net_wan'] > 0 else format_amount(it['youzi_net_wan'])
            ratio_str = f" 占比{it['net_ratio']}%" if it['net_ratio'] is not None else ""
            item_lines.append(
                f'                            <div class="signal-item">\n'
                f'                                <span class="signal-name">{it["name"]}</span>\n'
                f'                                <span class="signal-meta">机构{inst_str} / 游资{youzi_str}{ratio_str}</span>\n'
                f'                            </div>'
            )
        sections.append(
            f'                        <div class="signal-section {cls}">\n'
            f'                            <div class="signal-section-title" style="color:{color};">{title}</div>\n'
            + "\n".join(item_lines) + "\n"
            + f'                        </div>'
        )

    if not sections:
        return ''

    return (
        '                    <div class="daily-signals" style="display:none;" data-signals="1">\n'
        + "\n".join(sections) + "\n"
        + '                    </div>'
    )

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

    # 每日信号精选
    daily_signals = compute_daily_signals(inst_data, youzi_data)

    return {
        "date": date_str,
        "institution_top5": inst_top5,
        "institution_sell_top5": inst_sell_top5,
        "resonance": resonance,
        "youzi_buy_top5": youzi_buy_top5,
        "youzi_sell_top5": youzi_sell_top5,
        "daily_signals": daily_signals,
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

    # 6. 每日信号精选（隐藏区块，仅详情弹窗中渲染）
    signals_html = build_signals_html(data.get("daily_signals", {}))
    if signals_html:
        lines.append(signals_html)

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


# ========== HTML 工具函数 ==========

def find_matching_div(html: str, start_idx: int) -> int:
    """
    从 start_idx（某个 <div ...> 的起始位置）开始，通过层级计数找到匹配的 </div> 结束位置（闭合标签之后的索引）。
    正确处理嵌套div。返回匹配的 </div> 的 end 位置，未找到返回 -1。
    """
    if not html[start_idx:start_idx+4].lower().startswith('<div'):
        prev_div = html.rfind('<div', 0, start_idx + 1)
        if prev_div < 0:
            return -1
        start_idx = prev_div
    depth = 0
    i = start_idx
    n = len(html)
    while i < n:
        next_open = html.find('<div', i)
        next_close = html.find('</div>', i)
        if next_close < 0:
            return -1
        if next_open >= 0 and next_open < next_close:
            depth += 1
            i = next_open + 4
        else:
            depth -= 1
            if depth == 0:
                return next_close + 6
            i = next_close + 6
    return -1


def find_week_box_by_title(html: str, week_label: str) -> tuple:
    """
    在 HTML 中根据周标题（如 "7/13-7/19"）找到所属 week-box 的起止位置和周号。
    返回 (start, end, week_num)，未找到返回 (-1, -1, 0)。
    """
    title_pattern = re.compile(
        r'<div class="week-title">\s*第(\d+)周\s*' + re.escape(week_label) + r'\s*</div>',
        re.DOTALL,
    )
    m = title_pattern.search(html)
    if not m:
        return -1, -1, 0
    week_num = int(m.group(1))
    title_pos = m.start()
    box_start = html.rfind('<div class="week-box">', 0, title_pos)
    if box_start < 0:
        return -1, -1, 0
    box_end = find_matching_div(html, box_start)
    if box_end < 0:
        return -1, -1, 0
    return box_start, box_end, week_num


def find_month_section(html: str, month_num: int) -> tuple:
    """
    定位指定月份的 month-section 区块（id="summary-{month_num}"）。
    返回 (start, end)，未找到返回 (-1, -1)。
    """
    pat = re.compile(r'<div[^>]*id="summary-' + str(month_num) + r'"[^>]*>')
    m = pat.search(html)
    if not m:
        return -1, -1
    sec_start = m.start()
    sec_end = find_matching_div(html, sec_start)
    if sec_end < 0:
        return -1, -1
    return sec_start, sec_end


# ========== 周/月汇总 ==========

def get_week_trading_days(date_str: str) -> List[str]:
    """获取当周（周一至当天）所有A股交易日"""
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    monday = dt - timedelta(days=dt.weekday())
    dates = []
    cur = monday
    while cur <= dt:
        ds = cur.strftime("%Y-%m-%d")
        if is_trading_day(ds):
            dates.append(ds)
        cur += timedelta(days=1)
    return dates


def get_month_trading_days(date_str: str) -> List[str]:
    """获取当月（1日至当天）所有A股交易日"""
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    first = dt.replace(day=1)
    dates = []
    cur = first
    while cur <= dt:
        ds = cur.strftime("%Y-%m-%d")
        if is_trading_day(ds):
            dates.append(ds)
        cur += timedelta(days=1)
    return dates


def aggregate_inst_period(date_list: List[str]) -> Dict:
    """
    聚合一段时间的机构净买卖数据（按股票代码累计）
    返回: {"stocks": [...], "date_count": int}
    """
    stock_map = {}
    valid_count = 0
    for ds in date_list:
        print(f"    📅 [机构] 汇总 {ds} ...")
        try:
            inst_data = get_institution_data(ds)
            if not inst_data["buy_sorted"] and not inst_data["sell_sorted"]:
                continue
            valid_count += 1
            all_stocks = inst_data["buy_sorted"] + [
                s for s in inst_data["sell_sorted"]
                if s["code"] not in {x["code"] for x in inst_data["buy_sorted"]}
            ]
            for s in all_stocks:
                code = s["code"]
                if code not in stock_map:
                    stock_map[code] = {
                        "code": code,
                        "name": s["name"],
                        "net_buy_wan": 0.0,
                        "buy_wan": 0.0,
                        "sell_wan": 0.0,
                    }
                stock_map[code]["net_buy_wan"] += s.get("net_buy_wan", 0)
                stock_map[code]["buy_wan"] += s.get("buy_wan", 0)
                stock_map[code]["sell_wan"] += s.get("sell_wan", 0)
        except Exception as e:
            print(f"    ⚠️  [机构] 汇总 {ds} 失败: {e}")
            continue

    stocks = list(stock_map.values())
    for s in stocks:
        s["net_buy_wan"] = round(s["net_buy_wan"], 2)
        s["buy_wan"] = round(s["buy_wan"], 2)
        s["sell_wan"] = round(s["sell_wan"], 2)

    return {"stocks": stocks, "date_count": valid_count}


def aggregate_youzi_period(date_list: List[str]) -> Dict:
    """
    聚合一段时间的游资净买卖数据（龙虎榜营业部口径，剔除机构/北向，按股票聚合）
    返回: {"stocks": [...], "date_count": int}
    """
    stock_map = {}
    valid_count = 0
    for ds in date_list:
        print(f"    📅 [游资] 汇总 {ds} ...")
        try:
            youzi_data = get_youzi_stock_data(ds)
            if not youzi_data["buy_sorted"] and not youzi_data["sell_sorted"]:
                continue
            valid_count += 1
            all_stocks = youzi_data["buy_sorted"] + [
                s for s in youzi_data["sell_sorted"]
                if s["code"] not in {x["code"] for x in youzi_data["buy_sorted"]}
            ]
            for s in all_stocks:
                code = s["code"]
                if code not in stock_map:
                    stock_map[code] = {
                        "code": code,
                        "name": s["name"],
                        "net_buy_wan": 0.0,
                        "buy_wan": 0.0,
                        "sell_wan": 0.0,
                    }
                stock_map[code]["net_buy_wan"] += s.get("net_buy_wan", 0)
                stock_map[code]["buy_wan"] += s.get("buy_wan", 0)
                stock_map[code]["sell_wan"] += s.get("sell_wan", 0)
        except Exception as e:
            print(f"    ⚠️  [游资] 汇总 {ds} 失败: {e}")
            continue

    stocks = list(stock_map.values())
    for s in stocks:
        s["net_buy_wan"] = round(s["net_buy_wan"], 2)
        s["buy_wan"] = round(s["buy_wan"], 2)
        s["sell_wan"] = round(s["sell_wan"], 2)

    return {"stocks": stocks, "date_count": valid_count}


def get_youzi_active_data(date_str: str) -> Dict:
    """
    获取单日游资活跃榜（龙虎榜营业部买入明细，剔除机构/北向，按营业部+股票组合聚合）
    返回: {"items": [{"dept", "stock_code", "stock_name", "buy_wan"}, ...]}
    按买入金额降序排列。
    """
    filter_expr = f"(TRADE_DATE='{date_str}')"
    buy_raw = fetch_eastmoney_api(
        REPORT_BUY_DETAILS,
        filter_expr=filter_expr,
        sort_columns="TRADE_DATE",
        sort_types="-1",
        page_size=500, max_pages=5,
    )

    # 先获取股票名映射
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

    dept_stock_map = {}
    for item in buy_raw:
        dept = item.get("OPERATEDEPT_NAME", "")
        code = item.get("SECURITY_CODE", "")
        if not dept or not code:
            continue
        # 剔除非营业部席位（黑名单+白名单双校验）
        if not _is_real_business_department(dept):
            continue
        key = (dept, code)
        if key not in dept_stock_map:
            dept_stock_map[key] = {
                "dept": dept,
                "stock_code": code,
                "stock_name": name_map.get(code, code),
                "buy_wan": 0.0,
            }
        dept_stock_map[key]["buy_wan"] += _safe_num(item.get("BUY")) / 10000.0

    items = list(dept_stock_map.values())
    for it in items:
        it["buy_wan"] = round(it["buy_wan"], 2)
    items.sort(key=lambda x: x["buy_wan"], reverse=True)
    return {"items": items}


def aggregate_youzi_active_period(date_list: List[str]) -> Dict:
    """
    聚合一段时间的游资活跃榜（按营业部+股票组合累计买入额）
    返回: {"items": [...], "date_count": int}
    """
    dept_stock_map = {}
    valid_count = 0
    for ds in date_list:
        print(f"    📅 [游资活跃] 汇总 {ds} ...")
        try:
            daily = get_youzi_active_data(ds)
            if not daily["items"]:
                continue
            valid_count += 1
            for it in daily["items"]:
                key = (it["dept"], it["stock_code"])
                if key not in dept_stock_map:
                    dept_stock_map[key] = {
                        "dept": it["dept"],
                        "stock_code": it["stock_code"],
                        "stock_name": it["stock_name"],
                        "buy_wan": 0.0,
                    }
                dept_stock_map[key]["buy_wan"] += it["buy_wan"]
        except Exception as e:
            print(f"    ⚠️  [游资活跃] 汇总 {ds} 失败: {e}")
            continue

    items = list(dept_stock_map.values())
    for it in items:
        it["buy_wan"] = round(it["buy_wan"], 2)
    items.sort(key=lambda x: x["buy_wan"], reverse=True)
    return {"items": items, "date_count": valid_count}


# ========== 构建汇总条目 ==========

def _simplify_dept_name(dept_name: str) -> str:
    """简化营业部名称（去掉'证券营业部'等后缀，提取辨识度高的前缀）"""
    import re as _re
    name = dept_name
    # 常见前缀简化
    name = _re.sub(r'^中国银河证券', '银河', name)
    name = _re.sub(r'^国泰君安证券', '国泰君安', name)
    name = _re.sub(r'^中信建投证券', '中信建投', name)
    name = _re.sub(r'^中信证券(?!股份)', '中信', name)
    name = _re.sub(r'^华泰证券', '华泰', name)
    name = _re.sub(r'^招商证券', '招商', name)
    name = _re.sub(r'^东方财富证券', '东财', name)
    name = _re.sub(r'^海通证券', '海通', name)
    name = _re.sub(r'^广发证券', '广发', name)
    name = _re.sub(r'^兴业证券', '兴业', name)
    name = _re.sub(r'^申万宏源证券', '申万', name)
    name = _re.sub(r'^光大证券', '光大', name)
    name = _re.sub(r'^平安证券', '平安', name)
    # 去掉 "证券营业部"、"证券股份有限公司"
    name = _re.sub(r'证券股份有限公司', '', name)
    name = _re.sub(r'证券有限公司', '', name)
    name = _re.sub(r'证券营业部', '', name)
    name = _re.sub(r'营业部', '', name)
    return name.strip()


def _build_jiyou_week_inst_items(top_list: List[Dict]) -> str:
    """构建机构周净买入TOP5 week-stock-item HTML"""
    if not top_list:
        return (
            '                            <div class="week-stock-item">\n'
            '                                <span class="week-stock-rank">1</span>\n'
            '                                <span class="week-stock-name">暂无数据</span>\n'
            '                                <span class="week-stock-amount" style="color:#6e7681;">--</span>\n'
            '                            </div>'
        )
    items = []
    for i, s in enumerate(top_list, 1):
        items.append(
            '                            <div class="week-stock-item">\n'
            f'                                <span class="week-stock-rank">{i}</span>\n'
            f'                                <span class="week-stock-name">{s["name"]}</span>\n'
            f'                                <span class="week-stock-amount" style="color:#f85149;">+{format_amount(s["net_buy_wan"])}</span>\n'
            '                            </div>'
        )
    return "\n".join(items)


def _build_jiyou_week_active_items(items: List[Dict]) -> str:
    """构建游资活跃周TOP5 week-stock-item HTML（格式：股票名 营业部名 金额）"""
    if not items:
        return (
            '                            <div class="week-stock-item">\n'
            '                                <span class="week-stock-rank">1</span>\n'
            '                                <span class="week-stock-name">暂无数据</span>\n'
            '                                <span class="week-stock-amount" style="color:#6e7681;">--</span>\n'
            '                            </div>'
        )
    out = []
    for i, it in enumerate(items, 1):
        dept_simple = _simplify_dept_name(it["dept"])
        out.append(
            '                            <div class="week-stock-item">\n'
            f'                                <span class="week-stock-rank">{i}</span>\n'
            f'                                <span class="week-stock-name">{it["stock_name"]} {dept_simple}</span>\n'
            f'                                <span class="week-stock-amount" style="color:#f85149;">+{format_amount(it["buy_wan"])}</span>\n'
            '                            </div>'
        )
    return "\n".join(out)


def _build_jiyou_month_rank_items(top_list: List[Dict], buy: bool,
                                   top_n: int = 10,
                                   show_code: bool = True,
                                   prefix_text: str = "") -> str:
    """构建机游月度 rank-item HTML 列表"""
    items = []
    for i in range(top_n):
        if i < len(top_list):
            s = top_list[i]
            rank_class = "top" if i < 3 else "other"
            sign = "+" if buy else "-"
            amount_class = "buy" if buy else "sell"
            name = s["name"] if "name" in s else s.get("stock_name", "")
            code = s.get("code", s.get("stock_code", ""))
            amount = s.get("net_buy_wan", s.get("buy_wan", 0))
            code_html = f'<span class="rank-code">{code}</span>' if show_code else ''
            display_name = f"{prefix_text}{name}" if prefix_text else name
            items.append(
                f'                    <li class="rank-item">'
                f'<span class="rank-num {rank_class}">{i+1}</span>'
                f'<span class="rank-name">{display_name}</span>'
                f'{code_html}'
                f'<span class="rank-amount {amount_class}">{sign}{format_amount(amount)}</span>'
                f'</li>'
            )
    return "\n".join(items)


def _build_jiyou_month_active_items(items: List[Dict], top_n: int = 10) -> str:
    """构建游资活跃月度TOP10 rank-item HTML（格式：营业部·股票）"""
    out = []
    for i in range(top_n):
        if i < len(items):
            it = items[i]
            rank_class = "top" if i < 3 else "other"
            dept_simple = _simplify_dept_name(it["dept"])
            display_name = f"{dept_simple}·{it['stock_name']}"
            out.append(
                f'                    <li class="rank-item">'
                f'<span class="rank-num {rank_class}">{i+1}</span>'
                f'<span class="rank-name">{display_name}</span>'
                f'<span class="rank-amount buy">+{format_amount(it["buy_wan"])}</span>'
                f'</li>'
            )
    return "\n".join(out)


# ========== 周汇总更新 ==========

def update_jiyou_weekly_summary(html_path: str, target_date: str) -> bool:
    """
    更新机游共振日历当周汇总（只更新已有2个section的数据，不改版式）：
    - 机构净买入TOP5：按股票聚合
    - 游资活跃TOP5：按营业部+股票聚合
    """
    try:
        with open(html_path, "r", encoding="utf-8") as f:
            html = f.read()
    except Exception as e:
        print(f"❌ 读取HTML文件失败: {e}")
        return False

    dt = datetime.strptime(target_date, "%Y-%m-%d")
    monday = dt - timedelta(days=dt.weekday())
    sunday = monday + timedelta(days=6)
    week_label = f"{monday.month}/{monday.day}-{sunday.month}/{sunday.day}"

    # 定位当周 week-box
    box_start, box_end, week_num = find_week_box_by_title(html, week_label)
    if box_start < 0:
        print(f"❌ 无法定位第?周 {week_label} 的周汇总区块")
        return False

    old_week_box = html[box_start:box_end]
    print(f"📆 定位到第{week_num}周 {week_label} 的周汇总区块（{len(old_week_box)} 字节）")

    # 获取当周数据
    week_dates = get_week_trading_days(target_date)
    print(f"   当周交易日: {week_dates}")
    if not week_dates:
        print("   ⚠️  当周无有效交易日，跳过周汇总更新")
        return False

    # 聚合机构净买入数据
    inst_agg = aggregate_inst_period(week_dates)
    inst_top = [s for s in sorted(inst_agg["stocks"], key=lambda x: x["net_buy_wan"], reverse=True) if s["net_buy_wan"] > 0][:5]

    # 聚合游资活跃数据
    active_agg = aggregate_youzi_active_period(week_dates)
    active_top = active_agg["items"][:5]

    print(f"   ✅ 机构周净买入TOP5: {len(inst_top)}只")
    for i, s in enumerate(inst_top, 1):
        print(f"     #{i}: {s['name']} +{format_amount(s['net_buy_wan'])}")
    print(f"   ✅ 游资周活跃TOP5: {len(active_top)}条")
    for i, it in enumerate(active_top, 1):
        dept_simple = _simplify_dept_name(it["dept"])
        print(f"     #{i}: {it['stock_name']} {dept_simple} +{format_amount(it['buy_wan'])}")

    # 替换机构净买入TOP5 section的内容（保留section结构，只换item）
    new_week_box = old_week_box

    # 替换第一个 section（机构净买入TOP5）的 week-stock-item 列表
    inst_section_pat = re.compile(
        r'(<div class="week-section">\s*'
        r'<div class="week-section-title">✅ 机构净买入TOP5</div>\s*)'
        r'(.*?)'
        r'(\s*</div>\s*(?=<div class="week-section">|</div>\s*</div>))',
        re.DOTALL,
    )
    m_inst = inst_section_pat.search(new_week_box)
    if m_inst:
        new_items = _build_jiyou_week_inst_items(inst_top)
        new_section = m_inst.group(1) + new_items + m_inst.group(3)
        new_week_box = new_week_box[:m_inst.start()] + new_section + new_week_box[m_inst.end():]
        print("   ✅ 机构周净买入TOP5已更新")
    else:
        print("   ⚠️  未找到机构净买入TOP5 section，跳过机构部分")

    # 替换第二个 section（游资活跃TOP5）的 week-stock-item 列表
    active_section_pat = re.compile(
        r'(<div class="week-section">\s*'
        r'<div class="week-section-title">🔥 游资活跃TOP5</div>\s*)'
        r'(.*?)'
        r'(\s*</div>\s*(?=<div class="week-section">|</div>\s*</div>))',
        re.DOTALL,
    )
    m_active = active_section_pat.search(new_week_box)
    if m_active:
        new_items = _build_jiyou_week_active_items(active_top)
        new_section = m_active.group(1) + new_items + m_active.group(3)
        new_week_box = new_week_box[:m_active.start()] + new_section + new_week_box[m_active.end():]
        print("   ✅ 游资周活跃TOP5已更新")
    else:
        print("   ⚠️  未找到游资活跃TOP5 section，跳过游资活跃部分")

    # 写回
    html = html[:box_start] + new_week_box + html[box_end:]

    try:
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"✅ 机游周汇总已更新: 第{week_num}周 {week_label}")
        return True
    except Exception as e:
        print(f"❌ 写入HTML文件失败: {e}")
        return False


# ========== 月度汇总更新 ==========

def update_jiyou_monthly_summary(html_path: str, target_date: str) -> bool:
    """
    更新机游共振日历月度汇总（只更新已有2个box的数据，不改版式）：
    - 机构净买入TOP10
    - 游资活跃TOP10
    """
    try:
        with open(html_path, "r", encoding="utf-8") as f:
            html = f.read()
    except Exception as e:
        print(f"❌ 读取HTML文件失败: {e}")
        return False

    dt = datetime.strptime(target_date, "%Y-%m-%d")
    month_num = dt.month

    # 定位月度区块
    sec_start, sec_end = find_month_section(html, month_num)
    if sec_start < 0:
        print(f"❌ 未找到月度汇总区块 summary-{month_num}")
        return False

    section_html = html[sec_start:sec_end]
    print(f"📆 定位到月度汇总区块 summary-{month_num}（{len(section_html)} 字节）")

    # 获取当月数据
    month_dates = get_month_trading_days(target_date)
    print(f"   当月交易日: {len(month_dates)}天")
    if not month_dates:
        print("   ⚠️  当月无有效交易日，跳过月度汇总更新")
        return False

    inst_agg = aggregate_inst_period(month_dates)
    inst_top = [s for s in sorted(inst_agg["stocks"], key=lambda x: x["net_buy_wan"], reverse=True) if s["net_buy_wan"] > 0][:10]

    active_agg = aggregate_youzi_active_period(month_dates)
    active_top = active_agg["items"][:10]

    print(f"   ✅ 机构月净买入TOP{len(inst_top)}")
    print(f"   ✅ 游资月活跃TOP{len(active_top)}")

    # 替换第一个 rank-list（机构净买入TOP10）
    buy_ul_pattern = re.compile(
        r'(<h3 class="buy">.*?</h3>\s*<ul class="rank-list">)(.*?)(</ul>)',
        re.DOTALL,
    )
    buy_m = buy_ul_pattern.search(section_html)
    if buy_m:
        new_buy_items = _build_jiyou_month_rank_items(
            inst_top, buy=True, top_n=10, show_code=True,
        )
        new_buy_ul = buy_m.group(1) + "\n" + new_buy_items + "\n                " + buy_m.group(3)
        section_html = section_html[:buy_m.start()] + new_buy_ul + section_html[buy_m.end():]
        print("   ✅ 机构月度买入TOP10已替换")

    # 替换第二个 rank-list（游资活跃TOP10，h3 class="sell"）
    sell_ul_pattern = re.compile(
        r'(<h3 class="sell">.*?</h3>\s*<ul class="rank-list">)(.*?)(</ul>)',
        re.DOTALL,
    )
    sell_m = sell_ul_pattern.search(section_html)
    if sell_m:
        new_sell_items = _build_jiyou_month_active_items(active_top, top_n=10)
        new_sell_ul = sell_m.group(1) + "\n" + new_sell_items + "\n                " + sell_m.group(3)
        section_html = section_html[:sell_m.start()] + new_sell_ul + section_html[sell_m.end():]
        print("   ✅ 游资月度活跃TOP10已替换")

    # 写回
    html = html[:sec_start] + section_html + html[sec_end:]

    try:
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"✅ 机游月度汇总已更新: {month_num}月")
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
        # 信号统计
        sig = daily_data.get("daily_signals", {})
        print(f"   信号精选-买入: {len(sig.get('signal_resonance_buy', []))} 只")
        print(f"   信号精选-卖出: {len(sig.get('signal_resonance_sell', []))} 只")
        print(f"   信号精选-机构出货游资接盘: {len(sig.get('signal_inst_sell_youzi_buy', []))} 只")
        print(f"   信号精选-机构接盘游资出货: {len(sig.get('signal_inst_buy_youzi_sell', []))} 只")

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
