#!/usr/bin/env python3
"""
机游信号分析 — 独立页面生成脚本

功能：
  1. 计算指定日期的机游信号数据（4大类 + 行业汇总 + 知名游资 + 细分信号）
  2. 生成/更新 jiyou-signal-analysis.html 页面
  3. 支持多日数据同时注入（历史回补用）

依赖：
  - update_jiyou_resonance_gha.py 中的基础数据获取函数
  - 腾讯 gtimg 行情接口（补充涨跌幅、换手率、量比等）

用法：
  python3 jiyou_signal_analysis.py --date 2026-07-17 --html jiyou-signal-analysis.html
  python3 jiyou_signal_analysis.py --backfill 2026-07-01..2026-07-19 --html jiyou-signal-analysis.html
  python3 jiyou_signal_analysis.py --add-entry 机游共振日历.html  # 在主页面加入口链接
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Optional, Tuple

import requests

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from update_jiyou_resonance_gha import (  # noqa: E402
    EASTMONEY_HEADERS,
    REPORT_BUY_DETAILS,
    REPORT_SELL_DETAILS,
    REPORT_DAILY_DETAILS,
    _safe_num,
    _is_real_business_department,
    fetch_eastmoney_api,
    get_institution_data,
    get_youzi_stock_data,
    is_trading_day,
    format_amount,
)

# ========== 配置 ==========

# 知名游资席位定义
FAMOUS_YOUZI = [
    {
        "name": "章盟主",
        "keywords": ["国泰君安证券股份有限公司上海江苏路", "国泰君安证券上海江苏路"],
        "color": "#f85149",
    },
    {
        "name": "赵老哥",
        "keywords": [
            "中国银河证券股份有限公司绍兴证券营业部",
            "中国银河证券绍兴证券营业部",
            "浙商证券股份有限公司绍兴分公司",
            "浙商证券绍兴分公司",
        ],
        "color": "#d29922",
    },
    {
        "name": "作手新一",
        "keywords": [
            "国泰君安证券股份有限公司南京太平南路",
            "国泰君安证券南京太平南路",
        ],
        "color": "#a371f7",
    },
    {
        "name": "炒股养家",
        "keywords": [
            "华鑫证券有限责任公司上海分公司",
            "华鑫证券上海分公司",
            "华鑫证券有限责任公司上海茅台路",
            "华鑫证券上海茅台路",
        ],
        "color": "#3fb950",
    },
    {
        "name": "方新侠",
        "keywords": [
            "兴业证券股份有限公司陕西分公司",
            "兴业证券陕西分公司",
        ],
        "color": "#58a6ff",
    },
    {
        "name": "溧阳路",
        "keywords": [
            "中信证券股份有限公司上海溧阳路",
            "中信证券上海溧阳路",
        ],
        "color": "#ff7b72",
    },
    {
        "name": "上塘路",
        "keywords": [
            "财通证券股份有限公司杭州上塘路",
            "财通证券杭州上塘路",
        ],
        "color": "#d2a8ff",
    },
    {
        "name": "量化打板",
        "keywords": [
            "华鑫证券有限责任公司上海分公司",
            "华鑫证券上海分公司",
        ],
        "color": "#79c0ff",
    },
    {
        "name": "拉萨天团",
        "keywords": [
            "西藏东方财富证券股份有限公司拉萨团结路",
            "西藏东方财富证券拉萨团结路",
            "西藏东方财富证券股份有限公司拉萨东环路",
            "西藏东方财富证券拉萨东环路",
            "西藏东方财富证券股份有限公司拉萨江苏路",
            "西藏东方财富证券拉萨江苏路",
            "东方财富证券股份有限公司拉萨团结路",
            "东方财富证券拉萨团结路",
            "东方财富证券股份有限公司拉萨东环路",
            "东方财富证券拉萨东环路",
            "东方财富证券股份有限公司拉萨江苏路",
            "东方财富证券拉萨江苏路",
        ],
        "color": "#ffa657",
    },
]

# 腾讯行情接口
TENCENT_QUOTE_URL = "https://qt.gtimg.cn/q="

# 细分信号阈值
SIG_INST_SOLO_BUY = 5000.0       # 机构独食：机构净买≥5000万
SIG_YOUZI_SOLO_BUY = 5000.0      # 游资独食：游资净买≥5000万
SIG_INST_RUSH_AMOUNT = 10000.0   # 机构抢筹：机构净买≥1亿
SIG_INST_RUSH_RATIO = 10.0       # 机构抢筹：净买占比>10%
SIG_INST_RUSH_LIMITUP = 9.8      # 机构抢筹：涨停（涨幅≥9.8%）
SIG_INST_DISTRIBUTE = 10000.0    # 机构派发：机构净卖≥1亿
SIG_DISTRIBUTE_VOL_RATIO = 1.5   # 机构派发：量比>1.5


# ========== 工具函数 ==========

def log_info(msg: str) -> None:
    print(f"🟢 {msg}")


def log_warn(msg: str) -> None:
    print(f"🟡 {msg}")


def log_error(msg: str) -> None:
    print(f"🔴 {msg}", file=sys.stderr)


def code_to_gtimg_prefix(code: str) -> str:
    """根据股票代码生成腾讯行情前缀（sh/sz/bj）"""
    if not code:
        return ""
    code = code.strip()
    if code.startswith("6") or code.startswith("9"):
        return "sh" + code
    elif code.startswith("0") or code.startswith("3") or code.startswith("2"):
        return "sz" + code
    elif code.startswith("4") or code.startswith("8"):
        return "bj" + code
    return "sh" + code  # 默认


def fetch_tencent_quotes(codes: List[str]) -> Dict[str, Dict]:
    """
    批量获取腾讯行情数据
    返回: {code: {name, current, prev_close, change, change_pct, high, low, open,
                 volume, amount, turnover_rate, vol_ratio, circulating_mktcap, ...}}
    """
    if not codes:
        return {}

    # 分批查询，每次最多50只
    results = {}
    batch_size = 50
    for i in range(0, len(codes), batch_size):
        batch = codes[i:i + batch_size]
        q_codes = ",".join([code_to_gtimg_prefix(c) for c in batch])
        try:
            url = TENCENT_QUOTE_URL + q_codes
            r = requests.get(url, timeout=10, headers={
                "User-Agent": "Mozilla/5.0",
            })
            r.encoding = "gbk"
            text = r.text.strip()
            # 按行解析
            for line in text.split("\n"):
                line = line.strip()
                if not line or "=\"" not in line:
                    continue
                # v_sh600519="..."
                m = re.match(r'v_([a-z]{2}\d+)="([^"]*)"', line)
                if not m:
                    continue
                gtimg_code = m.group(1)
                raw_code = gtimg_code[2:]  # 去掉sh/sz
                content = m.group(2)
                fields = content.split("~")
                if len(fields) < 50:
                    continue

                info = {
                    "code": raw_code,
                    "name": fields[1] if len(fields) > 1 else "",
                    "current": _safe_num(fields[3]) if len(fields) > 3 else 0,
                    "prev_close": _safe_num(fields[4]) if len(fields) > 4 else 0,
                    "open": _safe_num(fields[5]) if len(fields) > 5 else 0,
                    "volume": _safe_num(fields[6]) if len(fields) > 6 else 0,  # 手
                    "amount_wan": _safe_num(fields[37]) if len(fields) > 37 else 0,  # 万元
                    "high": _safe_num(fields[33]) if len(fields) > 33 else 0,
                    "low": _safe_num(fields[34]) if len(fields) > 34 else 0,
                    "change_pct": _safe_num(fields[32]) if len(fields) > 32 else 0,  # 涨跌幅%
                    "turnover_rate": _safe_num(fields[38]) if len(fields) > 38 else 0,  # 换手率%
                    "pe": _safe_num(fields[39]) if len(fields) > 39 else 0,
                    "amplitude": _safe_num(fields[43]) if len(fields) > 43 else 0,  # 振幅%
                    "total_mktcap_yi": _safe_num(fields[45]) if len(fields) > 45 else 0,  # 总市值（亿）
                    "circulating_mktcap_yi": _safe_num(fields[44]) if len(fields) > 44 else 0,  # 流通市值（亿）
                    "pb": _safe_num(fields[46]) if len(fields) > 46 else 0,
                    "vol_ratio": _safe_num(fields[49]) if len(fields) > 49 else 0,  # 量比
                }
                results[raw_code] = info
        except Exception as e:
            log_warn(f"腾讯行情查询失败 (批次{i//batch_size}): {e}")
        time.sleep(0.1)

    return results


def get_stock_industry(code: str, name: str) -> str:
    """
    获取股票所属行业。
    当前实现：暂时留空（东财龙虎榜接口不含行业字段，
    后续可接入东财行业分类API）。
    """
    # TODO: 接入东财行业分类接口
    return ""


# ========== 游资营业部明细（含所有买入/卖出，用于知名游资匹配） ==========

def get_buy_dept_details(date_str: str) -> List[Dict]:
    """获取当日所有买入营业部明细（含机构/北向，不过滤）"""
    buy_raw = fetch_eastmoney_api(
        REPORT_BUY_DETAILS,
        filter_expr=f"(TRADE_DATE='{date_str}')",
        sort_columns="TRADE_DATE",
        sort_types="-1",
        page_size=500, max_pages=5,
    )
    # 获取名称映射
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

    result = []
    for item in buy_raw:
        code = item.get("SECURITY_CODE", "")
        dept = item.get("OPERATEDEPT_NAME", "")
        if not code or not dept:
            continue
        result.append({
            "code": code,
            "name": name_map.get(code, code),
            "dept": dept,
            "buy_wan": round(_safe_num(item.get("BUY")) / 10000.0, 2),
        })
    return result


def get_sell_dept_details(date_str: str) -> List[Dict]:
    """获取当日所有卖出营业部明细"""
    sell_raw = fetch_eastmoney_api(
        REPORT_SELL_DETAILS,
        filter_expr=f"(TRADE_DATE='{date_str}')",
        sort_columns="TRADE_DATE",
        sort_types="-1",
        page_size=500, max_pages=5,
    )
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

    result = []
    for item in sell_raw:
        code = item.get("SECURITY_CODE", "")
        dept = item.get("OPERATEDEPT_NAME", "")
        if not code or not dept:
            continue
        result.append({
            "code": code,
            "name": name_map.get(code, code),
            "dept": dept,
            "sell_wan": round(_safe_num(item.get("SELL")) / 10000.0, 2),
        })
    return result


# ========== 信号计算 ==========

def compute_famous_youzi(buy_details: List[Dict], sell_details: List[Dict]) -> List[Dict]:
    """
    匹配知名游资席位当日动向
    返回: [{"name":游资名, "color":颜色, "stocks": [{code, name, net_buy_wan, buy_wan, sell_wan}, ...]}, ...]
    """
    # 构建每只股票的买卖明细（按游资分组）
    # 先把买卖明细按游资+code聚合
    youzi_stock_map = {}  # {游资名: {code: {code, name, buy_wan, sell_wan}}}

    for yz in FAMOUS_YOUZI:
        youzi_stock_map[yz["name"]] = {}

    # 处理买入
    for item in buy_details:
        for yz in FAMOUS_YOUZI:
            matched = False
            for kw in yz["keywords"]:
                if kw in item["dept"]:
                    matched = True
                    break
            if matched:
                code = item["code"]
                yz_map = youzi_stock_map[yz["name"]]
                if code not in yz_map:
                    yz_map[code] = {
                        "code": code,
                        "name": item["name"],
                        "buy_wan": 0.0,
                        "sell_wan": 0.0,
                    }
                yz_map[code]["buy_wan"] += item["buy_wan"]
                # 不break，同一营业部可能属于多个游资标签（如华鑫上海分公司=炒股养家+量化打板）

    # 处理卖出
    for item in sell_details:
        for yz in FAMOUS_YOUZI:
            matched = False
            for kw in yz["keywords"]:
                if kw in item["dept"]:
                    matched = True
                    break
            if matched:
                code = item["code"]
                yz_map = youzi_stock_map[yz["name"]]
                if code not in yz_map:
                    yz_map[code] = {
                        "code": code,
                        "name": item["name"],
                        "buy_wan": 0.0,
                        "sell_wan": 0.0,
                    }
                yz_map[code]["sell_wan"] += item["sell_wan"]

    # 整理结果
    result = []
    for yz in FAMOUS_YOUZI:
        stocks = list(youzi_stock_map[yz["name"]].values())
        for s in stocks:
            s["net_buy_wan"] = round(s["buy_wan"] - s["sell_wan"], 2)
            s["buy_wan"] = round(s["buy_wan"], 2)
            s["sell_wan"] = round(s["sell_wan"], 2)
        stocks.sort(key=lambda x: abs(x["net_buy_wan"]), reverse=True)
        if stocks:
            result.append({
                "name": yz["name"],
                "color": yz["color"],
                "stocks": stocks,
            })

    return result


def compute_industry_summary(inst_data: Dict, youzi_data: Dict) -> Dict:
    """
    按行业汇总机构和游资净买卖
    当前行业数据为空，返回空数据结构（保留接口位）
    """
    # 合并所有股票
    all_codes = set()
    inst_map = {}
    for s in inst_data.get("buy_sorted", []) + inst_data.get("sell_sorted", []):
        inst_map[s["code"]] = s
        all_codes.add(s["code"])
    youzi_map = {}
    for s in youzi_data.get("buy_sorted", []) + youzi_data.get("sell_sorted", []):
        youzi_map[s["code"]] = s
        all_codes.add(s["code"])

    # 按行业分组
    inst_industry = {}  # {行业: net_buy_wan}
    youzi_industry = {}

    for code in all_codes:
        inst_net = inst_map.get(code, {}).get("net_buy_wan", 0.0)
        youzi_net = youzi_map.get(code, {}).get("net_buy_wan", 0.0)
        industry = get_stock_industry(code, "")
        if not industry:
            industry = "未分类"
        inst_industry[industry] = inst_industry.get(industry, 0.0) + inst_net
        youzi_industry[industry] = youzi_industry.get(industry, 0.0) + youzi_net

    inst_top = [{"industry": k, "net_buy_wan": round(v, 2)}
                for k, v in sorted(inst_industry.items(), key=lambda x: x[1], reverse=True)]
    youzi_top = [{"industry": k, "net_buy_wan": round(v, 2)}
                 for k, v in sorted(youzi_industry.items(), key=lambda x: x[1], reverse=True)]

    return {
        "inst_top10": inst_top[:10],
        "youzi_top10": youzi_top[:10],
        "has_industry_data": len(inst_industry) > 1 or (len(inst_industry) == 1 and "未分类" not in inst_industry),
    }


def compute_sub_signals(inst_data: Dict, youzi_data: Dict, quotes: Dict[str, Dict]) -> Dict:
    """
    计算细分信号：
    - 机构独食：机构净买≥5000万 且 游资净买<1500万
    - 游资独食：游资净买≥5000万 且 机构净卖>0
    - 机构抢筹：机构净买≥1亿 且 净买占比>10% 且 涨停（涨幅≥9.8%）
    - 机构派发：机构净卖≥1亿 且 高位放量（量比>1.5，涨幅<0）
    - 低吸信号：机构+游资共振净买 且 当日收阴线（跌幅>0）
    """
    # 构建全量map
    inst_map = {}
    for s in inst_data.get("buy_sorted", []) + inst_data.get("sell_sorted", []):
        inst_map[s["code"]] = s
    youzi_map = {}
    for s in youzi_data.get("buy_sorted", []) + youzi_data.get("sell_sorted", []):
        youzi_map[s["code"]] = s

    all_codes = set(inst_map.keys()) | set(youzi_map.keys())

    inst_solo_buy = []     # 机构独食
    youzi_solo_buy = []    # 游资独食
    inst_rush_buy = []     # 机构抢筹
    inst_distribute = []   # 机构派发
    low_suction = []       # 低吸信号

    for code in all_codes:
        inst = inst_map.get(code, {})
        youzi = youzi_map.get(code, {})
        inst_net = inst.get("net_buy_wan", 0.0)
        youzi_net = youzi.get("net_buy_wan", 0.0)
        inst_accum = inst.get("accum_amount", 0.0)  # 元
        name = inst.get("name", "") or youzi.get("name", "") or code

        q = quotes.get(code, {})
        change_pct = q.get("change_pct", 0.0)
        turnover_rate = q.get("turnover_rate", 0.0)
        vol_ratio = q.get("vol_ratio", 0.0)
        amount_wan = q.get("amount_wan", 0.0)

        # 净买占比（用成交额计算）
        total_amount_wan = amount_wan if amount_wan > 0 else (
            inst_accum / 10000.0 if inst_accum > 0 else 0)
        net_buy_ratio = 0.0
        if total_amount_wan > 0:
            net_buy_ratio = max(abs(inst_net), abs(youzi_net)) / total_amount_wan * 100

        stock_info = {
            "code": code,
            "name": name,
            "inst_net_wan": round(inst_net, 2),
            "youzi_net_wan": round(youzi_net, 2),
            "change_pct": round(change_pct, 2),
            "turnover_rate": round(turnover_rate, 2),
            "net_buy_ratio": round(net_buy_ratio, 2),
            "vol_ratio": round(vol_ratio, 2),
        }

        # 1. 机构独食
        if inst_net >= SIG_INST_SOLO_BUY and abs(youzi_net) < 1500.0:
            stock_info["reason"] = f"机构净买{format_amount(inst_net)}，游资净买卖仅{format_amount(youzi_net)}"
            inst_solo_buy.append(stock_info)

        # 2. 游资独食
        if youzi_net >= SIG_YOUZI_SOLO_BUY and inst_net < 0:
            stock_info["reason"] = f"游资净买{format_amount(youzi_net)}，机构净卖{format_amount(-inst_net)}"
            youzi_solo_buy.append(stock_info)

        # 3. 机构抢筹
        if (inst_net >= SIG_INST_RUSH_AMOUNT and
                net_buy_ratio > SIG_INST_RUSH_RATIO and
                change_pct >= SIG_INST_RUSH_LIMITUP):
            stock_info["reason"] = (f"机构净买{format_amount(inst_net)}，"
                                    f"净买占比{net_buy_ratio:.1f}%，涨幅{change_pct:.2f}%涨停")
            inst_rush_buy.append(stock_info)

        # 4. 机构派发
        if (inst_net <= -SIG_INST_DISTRIBUTE and
                vol_ratio > SIG_DISTRIBUTE_VOL_RATIO and
                change_pct < 0):
            stock_info["reason"] = (f"机构净卖{format_amount(-inst_net)}，"
                                    f"量比{vol_ratio:.2f}，跌幅{change_pct:.2f}%")
            inst_distribute.append(stock_info)

        # 5. 低吸信号
        if inst_net > 0 and youzi_net > 0 and change_pct < 0:
            stock_info["reason"] = (f"机游共振净买（机构{format_amount(inst_net)}，"
                                    f"游资{format_amount(youzi_net)}），当日收阴{change_pct:.2f}%")
            low_suction.append(stock_info)

    # 排序
    inst_solo_buy.sort(key=lambda x: x["inst_net_wan"], reverse=True)
    youzi_solo_buy.sort(key=lambda x: x["youzi_net_wan"], reverse=True)
    inst_rush_buy.sort(key=lambda x: x["inst_net_wan"], reverse=True)
    inst_distribute.sort(key=lambda x: x["inst_net_wan"])
    low_suction.sort(key=lambda x: (x["inst_net_wan"] + x["youzi_net_wan"]), reverse=True)

    return {
        "inst_solo_buy": inst_solo_buy,
        "youzi_solo_buy": youzi_solo_buy,
        "inst_rush_buy": inst_rush_buy,
        "inst_distribute": inst_distribute,
        "low_suction": low_suction,
    }


def compute_basic_signals(inst_data: Dict, youzi_data: Dict, quotes: Dict[str, Dict]) -> Dict:
    """
    计算4大类基础信号（比compute_daily_signals更详细，含涨跌幅、换手率等）
    """
    inst_map = {}
    all_inst = inst_data.get("buy_sorted", []) + [
        s for s in inst_data.get("sell_sorted", [])
        if s["code"] not in {x["code"] for x in inst_data.get("buy_sorted", [])}
    ]
    for s in all_inst:
        inst_map[s["code"]] = s

    youzi_map = {}
    all_youzi = youzi_data.get("buy_sorted", []) + [
        s for s in youzi_data.get("sell_sorted", [])
        if s["code"] not in {x["code"] for x in youzi_data.get("buy_sorted", [])}
    ]
    for s in all_youzi:
        youzi_map[s["code"]] = s

    common_codes = set(inst_map.keys()) & set(youzi_map.keys())

    resonance_buy = []
    resonance_sell = []
    inst_sell_youzi_buy = []
    inst_buy_youzi_sell = []

    for code in common_codes:
        inst = inst_map[code]
        youzi = youzi_map[code]
        inst_net = inst.get("net_buy_wan", 0.0)
        youzi_net = youzi.get("net_buy_wan", 0.0)

        if abs(inst_net) < 1000.0 and abs(youzi_net) < 1000.0:
            continue

        q = quotes.get(code, {})
        change_pct = q.get("change_pct", 0.0)
        turnover_rate = q.get("turnover_rate", 0.0)
        amount_wan = q.get("amount_wan", 0.0)
        accum_wan = amount_wan if amount_wan > 0 else (inst.get("accum_amount", 0) / 10000.0)

        net_ratio = 0.0
        if accum_wan > 0:
            net_ratio = round(max(abs(inst_net), abs(youzi_net)) / accum_wan * 100, 2)

        item = {
            "code": code,
            "name": inst.get("name", "") or youzi.get("name", ""),
            "inst_net_wan": round(inst_net, 2),
            "youzi_net_wan": round(youzi_net, 2),
            "net_buy_ratio": net_ratio,
            "change_pct": round(change_pct, 2),
            "turnover_rate": round(turnover_rate, 2),
        }

        if inst_net > 0 and youzi_net > 0:
            resonance_buy.append(item)
        elif inst_net < 0 and youzi_net < 0:
            resonance_sell.append(item)
        elif inst_net < 0 and youzi_net > 0:
            inst_sell_youzi_buy.append(item)
        elif inst_net > 0 and youzi_net < 0:
            inst_buy_youzi_sell.append(item)

    resonance_buy.sort(key=lambda x: x["inst_net_wan"] + x["youzi_net_wan"], reverse=True)
    resonance_sell.sort(key=lambda x: x["inst_net_wan"] + x["youzi_net_wan"])
    inst_sell_youzi_buy.sort(key=lambda x: x["youzi_net_wan"], reverse=True)
    inst_buy_youzi_sell.sort(key=lambda x: x["inst_net_wan"], reverse=True)

    return {
        "resonance_buy": resonance_buy,
        "resonance_sell": resonance_sell,
        "inst_sell_youzi_buy": inst_sell_youzi_buy,
        "inst_buy_youzi_sell": inst_buy_youzi_sell,
    }


def compute_signals_for_date(date_str: str) -> Dict:
    """
    计算指定日期的全部信号数据
    输入：日期字符串
    输出：完整信号数据字典
    """
    log_info(f"计算 {date_str} 信号数据 ...")

    # 1. 获取机构数据
    try:
        inst_data = get_institution_data(date_str)
    except Exception as e:
        log_error(f"获取机构数据失败: {e}")
        inst_data = {"buy_sorted": [], "sell_sorted": []}

    # 2. 获取游资数据
    try:
        youzi_data = get_youzi_stock_data(date_str)
    except Exception as e:
        log_error(f"获取游资数据失败: {e}")
        youzi_data = {"buy_sorted": [], "sell_sorted": []}

    # 3. 收集所有上榜股票代码
    all_codes = set()
    for s in inst_data.get("buy_sorted", []) + inst_data.get("sell_sorted", []):
        all_codes.add(s["code"])
    for s in youzi_data.get("buy_sorted", []) + youzi_data.get("sell_sorted", []):
        all_codes.add(s["code"])

    # 4. 获取行情数据
    quotes = {}
    if all_codes:
        log_info(f"获取 {len(all_codes)} 只股票的行情数据 ...")
        try:
            quotes = fetch_tencent_quotes(list(all_codes))
            log_info(f"  成功获取 {len(quotes)} 只股票行情")
        except Exception as e:
            log_warn(f"行情数据获取失败: {e}")

    # 5. 计算4大类基础信号
    log_info("  计算基础信号 ...")
    basic_signals = compute_basic_signals(inst_data, youzi_data, quotes)

    # 6. 计算行业汇总
    log_info("  计算行业汇总 ...")
    industry = compute_industry_summary(inst_data, youzi_data)

    # 7. 获取营业部明细（用于知名游资匹配）
    log_info("  获取营业部明细 ...")
    try:
        buy_details = get_buy_dept_details(date_str)
        sell_details = get_sell_dept_details(date_str)
    except Exception as e:
        log_warn(f"营业部明细获取失败: {e}")
        buy_details = []
        sell_details = []

    # 8. 匹配知名游资
    log_info("  匹配知名游资 ...")
    famous_youzi = compute_famous_youzi(buy_details, sell_details)

    # 9. 计算细分信号
    log_info("  计算细分信号 ...")
    sub_signals = compute_sub_signals(inst_data, youzi_data, quotes)

    # 10. 统计数据
    stats = {
        "total_inst_stocks": len(inst_data.get("buy_sorted", [])) + len(inst_data.get("sell_sorted", [])),
        "total_youzi_stocks": len(youzi_data.get("buy_sorted", [])) + len(youzi_data.get("sell_sorted", [])),
        "total_billboard_stocks": len(all_codes),
    }

    result = {
        "date": date_str,
        "update_time": datetime.utcnow() + timedelta(hours=8).strftime("%Y-%m-%d %H:%M"),
        "stats": stats,
        "basic_signals": basic_signals,
        "industry": industry,
        "famous_youzi": famous_youzi,
        "sub_signals": sub_signals,
    }

    # 打印概要
    print(f"  📊 机游共振买入: {len(basic_signals['resonance_buy'])} 只")
    print(f"  📉 机游共振卖出: {len(basic_signals['resonance_sell'])} 只")
    print(f"  🔄 机构出货游资接盘: {len(basic_signals['inst_sell_youzi_buy'])} 只")
    print(f"  🔄 机构接盘游资出货: {len(basic_signals['inst_buy_youzi_sell'])} 只")
    print(f"  👑 知名游资上榜: {len(famous_youzi)} 位")
    print(f"  ⚡ 机构独食: {len(sub_signals['inst_solo_buy'])} 只")
    print(f"  ⚡ 游资独食: {len(sub_signals['youzi_solo_buy'])} 只")
    print(f"  ⚡ 机构抢筹: {len(sub_signals['inst_rush_buy'])} 只")
    print(f"  ⚡ 机构派发: {len(sub_signals['inst_distribute'])} 只")
    print(f"  ⚡ 低吸信号: {len(sub_signals['low_suction'])} 只")

    return result


# ========== 连续性追踪计算（第二批功能） ==========

def compute_continuous_tracking(date_data_map: Dict[str, Dict],
                                window_days: int = 30) -> Dict:
    """
    基于多日信号数据，计算连续性追踪指标
    输入: date_data_map = {date_str: signal_data_dict}
    输出: 连续追踪数据字典
    """
    sorted_dates = sorted(date_data_map.keys())
    if not sorted_dates:
        return {}

    # 截取窗口内数据
    window_date_list = sorted_dates[-window_days:] if len(sorted_dates) > window_days else sorted_dates
    week_dates = sorted_dates[-7:] if len(sorted_dates) > 7 else sorted_dates

    # ========== 1. 行业板块趋势（周/月） ==========
    def aggregate_industry_trend(dates: List[str]) -> Dict:
        inst_industry = {}  # {industry: net_buy_wan}
        youzi_industry = {}
        for ds in dates:
            day_data = date_data_map.get(ds, {})
            ind = day_data.get("industry", {})
            for item in ind.get("inst_top10", []):
                name = item["industry"]
                inst_industry[name] = inst_industry.get(name, 0.0) + item["net_buy_wan"]
            for item in ind.get("youzi_top10", []):
                name = item["industry"]
                youzi_industry[name] = youzi_industry.get(name, 0.0) + item["net_buy_wan"]

        inst_top = [{"industry": k, "net_buy_wan": round(v, 2)}
                    for k, v in sorted(inst_industry.items(), key=lambda x: x[1], reverse=True)[:10]]
        youzi_top = [{"industry": k, "net_buy_wan": round(v, 2)}
                     for k, v in sorted(youzi_industry.items(), key=lambda x: x[1], reverse=True)[:10]]
        has_data = len(inst_industry) > 1 or (len(inst_industry) == 1 and "未分类" not in inst_industry)
        return {
            "inst_top10": inst_top,
            "youzi_top10": youzi_top,
            "has_industry_data": has_data,
        }

    industry_trend = {
        "week": aggregate_industry_trend(week_dates),
        "month": aggregate_industry_trend(window_date_list),
    }

    # ========== 2. 机构连续加仓榜 ==========
    # 构建每只股票的每日机构净买序列
    inst_stock_daily = {}  # {code: {name, dates: {date: net_wan}}}
    for ds in window_date_list:
        day_data = date_data_map.get(ds, {})
        basic = day_data.get("basic_signals", {})
        # 从所有基础信号中提取机构净买
        for sig_key in ["resonance_buy", "resonance_sell",
                        "inst_sell_youzi_buy", "inst_buy_youzi_sell"]:
            for s in basic.get(sig_key, []):
                code = s["code"]
                if code not in inst_stock_daily:
                    inst_stock_daily[code] = {"name": s["name"], "dates": {}}
                inst_stock_daily[code]["dates"][ds] = s["inst_net_wan"]

    # 计算连续加仓（找最长连续净买天数）
    inst_continuous = []
    for code, info in inst_stock_daily.items():
        stock_dates = sorted(info["dates"].keys())
        # 找从最近日期往前的连续净买天数
        max_streak = 0
        current_streak = 0
        streak_net = 0.0
        # 计算最长连续净买序列
        for ds in stock_dates:
            net = info["dates"][ds]
            if net > 2000:  # 机构净买≥2000万才算加仓
                current_streak += 1
                streak_net += net
                if current_streak > max_streak:
                    max_streak = current_streak
            else:
                current_streak = 0
                streak_net = 0.0

        # 重新计算累计净买（使用最长连续序列的）
        if max_streak >= 2:
            # 累计净买（全窗口内净买额之和）
            total_net = sum(v for v in info["dates"].values() if v > 0)
            inst_continuous.append({
                "code": code,
                "name": info["name"],
                "streak_days": max_streak,
                "total_net_wan": round(total_net, 2),
            })

    inst_continuous.sort(key=lambda x: (x["streak_days"], x["total_net_wan"]), reverse=True)
    inst_continuous = inst_continuous[:20]

    # ========== 3. 游资接力榜 ==========
    # 统计每只股票有多少天有游资净买入（不同游资接力也算）
    youzi_stock_daily = {}  # {code: {name, dates: {date: net_wan}, youzi_count: int}}
    for ds in window_date_list:
        day_data = date_data_map.get(ds, {})
        basic = day_data.get("basic_signals", {})
        for sig_key in ["resonance_buy", "resonance_sell",
                        "inst_sell_youzi_buy", "inst_buy_youzi_sell"]:
            for s in basic.get(sig_key, []):
                code = s["code"]
                if code not in youzi_stock_daily:
                    youzi_stock_daily[code] = {"name": s["name"], "dates": {}}
                youzi_stock_daily[code]["dates"][ds] = s["youzi_net_wan"]

    youzi_relay = []
    for code, info in youzi_stock_daily.items():
        stock_dates = sorted(info["dates"].keys())
        # 连续接力天数
        max_streak = 0
        current_streak = 0
        for ds in stock_dates:
            net = info["dates"][ds]
            if net > 1500:  # 游资净买≥1500万
                current_streak += 1
                if current_streak > max_streak:
                    max_streak = current_streak
            else:
                current_streak = 0

        # 参与游资数量（从知名游资统计）
        youzi_count = 0
        for ds in window_date_list:
            day_data = date_data_map.get(ds, {})
            for yz in day_data.get("famous_youzi", []):
                for stk in yz.get("stocks", []):
                    if stk["code"] == code and stk.get("net_buy_wan", 0) > 0:
                        youzi_count += 1
                        break

        if max_streak >= 2:
            total_net = sum(v for v in info["dates"].values() if v > 0)
            youzi_relay.append({
                "code": code,
                "name": info["name"],
                "relay_days": max_streak,
                "youzi_count": youzi_count,
                "total_net_wan": round(total_net, 2),
            })

    youzi_relay.sort(key=lambda x: (x["relay_days"], x["youzi_count"], x["total_net_wan"]), reverse=True)
    youzi_relay = youzi_relay[:20]

    # ========== 4. 高频上榜股 ==========
    stock_appearances = {}  # {code: {name, count, up_days}}
    for ds in window_date_list:
        day_data = date_data_map.get(ds, {})
        basic = day_data.get("basic_signals", {})
        seen_today = set()
        for sig_key in ["resonance_buy", "resonance_sell",
                        "inst_sell_youzi_buy", "inst_buy_youzi_sell"]:
            for s in basic.get(sig_key, []):
                code = s["code"]
                if code in seen_today:
                    continue
                seen_today.add(code)
                if code not in stock_appearances:
                    stock_appearances[code] = {"name": s["name"], "count": 0, "up_days": 0}
                stock_appearances[code]["count"] += 1
                if s.get("change_pct", 0) > 0:
                    stock_appearances[code]["up_days"] += 1

    def top_appearances(dates_subset: List[str], top_n: int = 20) -> List[Dict]:
        sub_map = {}
        for ds in dates_subset:
            day_data = date_data_map.get(ds, {})
            basic = day_data.get("basic_signals", {})
            seen_today = set()
            for sig_key in ["resonance_buy", "resonance_sell",
                            "inst_sell_youzi_buy", "inst_buy_youzi_sell"]:
                for s in basic.get(sig_key, []):
                    code = s["code"]
                    if code in seen_today:
                        continue
                    seen_today.add(code)
                    if code not in sub_map:
                        sub_map[code] = {"name": s["name"], "count": 0, "up_days": 0}
                    sub_map[code]["count"] += 1
                    if s.get("change_pct", 0) > 0:
                        sub_map[code]["up_days"] += 1
        result = [{"code": k, "name": v["name"], "count": v["count"], "up_days": v["up_days"]}
                  for k, v in sub_map.items()]
        result.sort(key=lambda x: (x["count"], x["up_days"]), reverse=True)
        return result[:top_n]

    high_freq = {
        "week": top_appearances(week_dates, 20),
        "month": top_appearances(window_date_list, 20),
    }

    # ========== 5. 龙虎榜热度指数 ==========
    heat_index = []  # [{date, stock_count, inst_total_net, youzi_total_net}]
    for ds in window_date_list:
        day_data = date_data_map.get(ds, {})
        stats = day_data.get("stats", {})
        basic = day_data.get("basic_signals", {})
        # 计算机构总净买
        inst_total = 0.0
        youzi_total = 0.0
        seen_inst = set()
        seen_youzi = set()
        for sig_key in ["resonance_buy", "resonance_sell",
                        "inst_sell_youzi_buy", "inst_buy_youzi_sell"]:
            for s in basic.get(sig_key, []):
                code = s["code"]
                if code not in seen_inst:
                    seen_inst.add(code)
                    inst_total += s.get("inst_net_wan", 0)
                if code not in seen_youzi:
                    seen_youzi.add(code)
                    youzi_total += s.get("youzi_net_wan", 0)

        heat_index.append({
            "date": ds,
            "stock_count": stats.get("total_billboard_stocks", 0),
            "inst_total_net_wan": round(inst_total, 2),
            "youzi_total_net_wan": round(youzi_total, 2),
        })

    # 7日/30日均量
    week_heat = heat_index[-7:] if len(heat_index) > 7 else heat_index
    month_heat = heat_index[-30:] if len(heat_index) > 30 else heat_index

    week_avg_stocks = round(sum(x["stock_count"] for x in week_heat) / max(len(week_heat), 1), 0)
    week_avg_inst = round(sum(x["inst_total_net_wan"] for x in week_heat) / max(len(week_heat), 1), 2)
    week_avg_youzi = round(sum(x["youzi_total_net_wan"] for x in week_heat) / max(len(week_heat), 1), 2)
    month_avg_stocks = round(sum(x["stock_count"] for x in month_heat) / max(len(month_heat), 1), 0)
    month_avg_inst = round(sum(x["inst_total_net_wan"] for x in month_heat) / max(len(month_heat), 1), 2)
    month_avg_youzi = round(sum(x["youzi_total_net_wan"] for x in month_heat) / max(len(month_heat), 1), 2)

    return {
        "window_days": window_days,
        "actual_days": len(window_date_list),
        "industry_trend": industry_trend,
        "inst_continuous": inst_continuous,
        "youzi_relay": youzi_relay,
        "high_freq": high_freq,
        "heat_index": {
            "daily": heat_index,
            "week_avg": {
                "avg_stocks": week_avg_stocks,
                "avg_inst_net_wan": week_avg_inst,
                "avg_youzi_net_wan": week_avg_youzi,
            },
            "month_avg": {
                "avg_stocks": month_avg_stocks,
                "avg_inst_net_wan": month_avg_inst,
                "avg_youzi_net_wan": month_avg_youzi,
            },
        },
    }


# ========== 页面生成 ==========

PAGE_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>机游信号分析</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif;
            background: #0d1117;
            min-height: 100vh;
            padding: 20px;
            color: #c9d1d9;
        }
        .container {
            max-width: 1400px;
            margin: 0 auto;
            background: #161b22;
            border-radius: 12px;
            border: 1px solid #30363d;
            padding: 30px;
        }
        .header {
            text-align: center;
            padding: 15px 0 20px;
            border-bottom: 1px solid #30363d;
            margin-bottom: 25px;
        }
        .header h1 {
            font-size: 26px;
            font-weight: 600;
            color: #e8a0b0;
            margin-bottom: 6px;
        }
        .header .subtitle {
            color: #8b949e;
            font-size: 13px;
        }
        .breadcrumb {
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 10px;
            margin-bottom: 12px;
            font-size: 13px;
            color: #8b949e;
        }
        .breadcrumb a {
            color: #58a6ff;
            text-decoration: none;
        }
        .breadcrumb a:hover {
            text-decoration: underline;
        }
        .breadcrumb .current {
            color: #e8a0b0;
            font-weight: 600;
        }

        .date-nav {
            display: flex;
            justify-content: center;
            align-items: center;
            gap: 20px;
            margin-bottom: 10px;
            font-size: 16px;
        }
        .date-nav .nav-btn {
            color: #e8a0b0;
            text-decoration: none;
            padding: 6px 16px;
            border: 1px solid #30363d;
            border-radius: 6px;
            transition: all 0.2s;
            font-size: 14px;
            cursor: pointer;
            background: #21262d;
        }
        .date-nav .nav-btn:hover {
            background: #30363d;
            border-color: #e8a0b0;
        }
        .date-nav .date-text {
            font-weight: 600;
            font-size: 20px;
            color: #f0f6fc;
            min-width: 160px;
            text-align: center;
        }
        .update-time {
            text-align: center;
            color: #6e7681;
            font-size: 12px;
            margin-bottom: 25px;
        }

        .section {
            margin-bottom: 30px;
        }
        .section-title {
            font-size: 18px;
            font-weight: 600;
            color: #f0f6fc;
            margin-bottom: 14px;
            display: flex;
            align-items: center;
            gap: 8px;
        }

        /* 四卡片布局 */
        .signal-cards {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 14px;
        }
        .signal-card {
            background: #0d1117;
            border: 1px solid #30363d;
            border-radius: 8px;
            padding: 14px 16px;
        }
        .signal-card-title {
            font-size: 15px;
            font-weight: 600;
            margin-bottom: 10px;
            display: flex;
            align-items: center;
            justify-content: space-between;
        }
        .signal-card-count {
            font-size: 12px;
            font-weight: normal;
            background: rgba(232, 160, 176, 0.15);
            color: #e8a0b0;
            padding: 2px 8px;
            border-radius: 10px;
        }
        .stock-list {
            max-height: 300px;
            overflow-y: auto;
        }
        .stock-item {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 6px 0;
            border-bottom: 1px solid #21262d;
            font-size: 13px;
        }
        .stock-item:last-child {
            border-bottom: none;
        }
        .stock-name {
            color: #c9d1d9;
            font-weight: 500;
            flex-shrink: 0;
        }
        .stock-code {
            color: #6e7681;
            font-size: 11px;
            margin-left: 4px;
            font-weight: normal;
        }
        .stock-meta {
            text-align: right;
            line-height: 1.4;
        }
        .stock-meta .row1 {
            font-size: 12px;
            color: #8b949e;
        }
        .stock-meta .row2 {
            font-size: 11px;
            color: #6e7681;
        }
        .up { color: #f85149; }
        .down { color: #3fb950; }
        .empty {
            color: #6e7681;
            font-size: 13px;
            padding: 10px 0;
            text-align: center;
        }

        /* 行业板块 */
        .industry-grid {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 14px;
        }
        .industry-box {
            background: #0d1117;
            border: 1px solid #30363d;
            border-radius: 8px;
            padding: 14px 16px;
        }
        .industry-box-title {
            font-size: 14px;
            font-weight: 600;
            margin-bottom: 10px;
            color: #58a6ff;
        }
        .industry-item {
            display: flex;
            align-items: center;
            padding: 5px 0;
            font-size: 13px;
            border-bottom: 1px solid #21262d;
        }
        .industry-item:last-child { border-bottom: none; }
        .industry-name {
            width: 110px;
            flex-shrink: 0;
            color: #c9d1d9;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }
        .industry-bar {
            flex: 1;
            height: 18px;
            background: #21262d;
            border-radius: 3px;
            margin: 0 8px;
            position: relative;
            overflow: hidden;
        }
        .industry-bar-fill {
            height: 100%;
            border-radius: 3px;
            transition: width 0.3s;
        }
        .industry-bar-fill.buy { background: linear-gradient(90deg, #f85149, #da3633); }
        .industry-bar-fill.sell { background: linear-gradient(90deg, #238636, #2ea043); }
        .industry-bar-fill.youzi-buy { background: linear-gradient(90deg, #d29922, #bf8700); }
        .industry-amount {
            width: 80px;
            text-align: right;
            flex-shrink: 0;
            font-size: 12px;
        }

        /* 知名游资 */
        .youzi-grid {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 12px;
        }
        .youzi-card {
            background: #0d1117;
            border: 1px solid #30363d;
            border-radius: 8px;
            padding: 12px 14px;
        }
        .youzi-card-title {
            font-size: 14px;
            font-weight: 600;
            margin-bottom: 8px;
            display: flex;
            align-items: center;
            justify-content: space-between;
        }
        .youzi-name-dot {
            display: inline-block;
            width: 8px;
            height: 8px;
            border-radius: 50%;
            margin-right: 6px;
        }
        .youzi-stock {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 4px 0;
            font-size: 12px;
            border-bottom: 1px solid #21262d;
        }
        .youzi-stock:last-child { border-bottom: none; }
        .youzi-stock-name {
            color: #c9d1d9;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
            flex-shrink: 0;
            max-width: 150px;
        }
        .youzi-stock-net {
            font-weight: 500;
        }

        /* 细分信号 */
        .sub-signal-section {
            margin-bottom: 18px;
        }
        .sub-signal-title {
            font-size: 14px;
            font-weight: 600;
            margin-bottom: 8px;
            display: flex;
            align-items: center;
            gap: 6px;
        }
        .sub-signal-title .badge {
            font-size: 11px;
            font-weight: normal;
            background: rgba(163, 113, 247, 0.15);
            color: #a371f7;
            padding: 2px 8px;
            border-radius: 10px;
        }
        .sub-signal-list {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 8px;
        }
        .sub-stock-item {
            background: #0d1117;
            border: 1px solid #30363d;
            border-radius: 6px;
            padding: 8px 10px;
            font-size: 12px;
        }
        .sub-stock-header {
            display: flex;
            justify-content: space-between;
            margin-bottom: 4px;
        }
        .sub-stock-name {
            font-weight: 600;
            color: #c9d1d9;
        }
        .sub-stock-reason {
            color: #8b949e;
            font-size: 11px;
            line-height: 1.4;
        }
        .sub-stock-meta {
            display: flex;
            gap: 8px;
            margin-top: 4px;
            flex-wrap: wrap;
        }
        .sub-stock-meta span {
            font-size: 11px;
            color: #6e7681;
        }

        /* 滚动条 */
        .stock-list::-webkit-scrollbar {
            width: 6px;
        }
        .stock-list::-webkit-scrollbar-track {
            background: #161b22;
        }
        .stock-list::-webkit-scrollbar-thumb {
            background: #30363d;
            border-radius: 3px;
        }

        /* Tab 导航 */
        .tab-nav {
            display: flex;
            align-items: center;
            gap: 8px;
            margin-bottom: 20px;
            border-bottom: 1px solid #30363d;
            padding-bottom: 0;
        }
        .tab-btn {
            background: transparent;
            border: none;
            color: #8b949e;
            padding: 12px 20px;
            font-size: 15px;
            cursor: pointer;
            border-bottom: 2px solid transparent;
            margin-bottom: -1px;
            transition: all 0.2s;
            font-family: inherit;
        }
        .tab-btn:hover {
            color: #c9d1d9;
        }
        .tab-btn.active {
            color: #e8a0b0;
            border-bottom-color: #e8a0b0;
            font-weight: 600;
        }
        .tab-btn.tab-link {
            color: #58a6ff;
            text-decoration: none;
            font-size: 13px;
            padding: 6px 14px;
            border: 1px solid #30363d;
            border-radius: 6px;
            border-bottom: 1px solid #30363d;
            margin-bottom: 8px;
        }
        .tab-btn.tab-link:hover {
            background: #21262d;
            text-decoration: none;
        }
        .tab-pane {
            display: none;
        }
        .tab-pane.active {
            display: block;
        }

        /* 周期切换 */
        .period-toggle {
            display: flex;
            align-items: center;
            gap: 8px;
            margin-bottom: 20px;
        }
        .period-label {
            font-size: 13px;
            color: #8b949e;
        }
        .period-btn {
            background: #21262d;
            border: 1px solid #30363d;
            color: #8b949e;
            padding: 5px 14px;
            font-size: 13px;
            cursor: pointer;
            border-radius: 6px;
            transition: all 0.2s;
            font-family: inherit;
        }
        .period-btn:hover {
            background: #30363d;
            color: #c9d1d9;
        }
        .period-btn.active {
            background: rgba(232, 160, 176, 0.15);
            border-color: #e8a0b0;
            color: #e8a0b0;
            font-weight: 500;
        }

        .section-sub {
            font-size: 12px;
            font-weight: normal;
            color: #6e7681;
            margin-left: 8px;
        }

        /* 排名表格 */
        .rank-table-wrap {
            background: #0d1117;
            border: 1px solid #30363d;
            border-radius: 8px;
            overflow: hidden;
        }
        .rank-table {
            width: 100%;
            border-collapse: collapse;
            font-size: 13px;
        }
        .rank-table th {
            background: #161b22;
            color: #8b949e;
            font-weight: 500;
            font-size: 12px;
            padding: 10px 12px;
            text-align: left;
            border-bottom: 1px solid #30363d;
        }
        .rank-table td {
            padding: 10px 12px;
            border-bottom: 1px solid #21262d;
            color: #c9d1d9;
        }
        .rank-table tr:last-child td {
            border-bottom: none;
        }
        .rank-table tr:hover td {
            background: #161b22;
        }
        .rank-num {
            display: inline-block;
            width: 24px;
            height: 24px;
            line-height: 24px;
            text-align: center;
            border-radius: 50%;
            background: #21262d;
            color: #8b949e;
            font-size: 12px;
            font-weight: 600;
        }
        .rank-num.top1 { background: linear-gradient(135deg, #f0b429, #d4a017); color: #0d1117; }
        .rank-num.top2 { background: linear-gradient(135deg, #a0aec0, #718096); color: #0d1117; }
        .rank-num.top3 { background: linear-gradient(135deg, #c05621, #9c4221); color: #0d1117; }

        /* 热度指数 */
        .heat-index-wrap {
            background: #0d1117;
            border: 1px solid #30363d;
            border-radius: 8px;
            padding: 20px;
        }
        .heat-stats {
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 16px;
            margin-bottom: 20px;
        }
        .heat-stat-card {
            background: #161b22;
            border: 1px solid #30363d;
            border-radius: 8px;
            padding: 16px;
            text-align: center;
        }
        .heat-stat-label {
            font-size: 12px;
            color: #8b949e;
            margin-bottom: 8px;
        }
        .heat-stat-value {
            font-size: 22px;
            font-weight: 600;
            color: #f0f6fc;
        }
        .heat-stat-value.up { color: #f85149; }
        .heat-stat-value.down { color: #3fb950; }
        .heat-daily-list {
            max-height: 280px;
            overflow-y: auto;
        }
        .heat-daily-item {
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 8px 0;
            border-bottom: 1px solid #21262d;
            font-size: 13px;
        }
        .heat-daily-item:last-child {
            border-bottom: none;
        }
        .heat-daily-date {
            color: #8b949e;
            width: 90px;
            flex-shrink: 0;
        }
        .heat-daily-metrics {
            display: flex;
            gap: 20px;
            flex: 1;
            justify-content: flex-end;
        }
        .heat-daily-metrics span {
            font-size: 12px;
            min-width: 80px;
            text-align: right;
        }

        @media (max-width: 768px) {
            .signal-cards, .industry-grid, .youzi-grid, .sub-signal-list {
                grid-template-columns: 1fr;
            }
            .container {
                padding: 15px;
            }
            body {
                padding: 10px;
            }
            .sub-signal-list {
                grid-template-columns: 1fr;
            }
            .heat-stats {
                grid-template-columns: 1fr;
            }
            .rank-table th, .rank-table td {
                padding: 8px 10px;
                font-size: 12px;
            }
            .tab-btn {
                padding: 10px 14px;
                font-size: 14px;
            }
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div class="breadcrumb">
                <a href="机游共振日历.html">机游共振日历</a>
                <span>›</span>
                <span class="current">信号分析</span>
            </div>
            <h1>🎯 机游信号分析</h1>
            <div class="subtitle">基于龙虎榜机构与游资数据的深度信号挖掘</div>
        </div>

        <div class="date-nav">
            <button class="nav-btn" onclick="prevDay()">← 前一日</button>
            <span class="date-text" id="current-date">--</span>
            <button class="nav-btn" onclick="nextDay()">后一日 →</button>
        </div>
        <div class="update-time" id="update-time">--</div>

        <!-- Tab 导航 -->
        <div class="tab-nav">
            <button class="tab-btn active" onclick="switchTab('daily')" id="tab-daily">📊 单日分析</button>
            <button class="tab-btn" onclick="switchTab('continuous')" id="tab-continuous">📈 连续追踪</button>
            <a href="signal-guide.html" class="tab-btn tab-link" style="margin-left:auto;">📖 信号说明</a>
        </div>

        <!-- Tab 1: 单日分析 -->
        <div class="tab-pane active" id="pane-daily">
            <!-- 一、当日信号总览 -->
            <div class="section">
                <div class="section-title">🎯 当日信号总览</div>
                <div class="signal-cards" id="signal-cards">
                    <!-- JS动态渲染 -->
                </div>
            </div>

            <!-- 二、行业板块追踪 -->
            <div class="section">
                <div class="section-title">🏭 行业板块追踪（当日）</div>
                <div class="industry-grid" id="industry-grid">
                    <!-- JS动态渲染 -->
                </div>
            </div>

            <!-- 三、知名游资追踪 -->
            <div class="section">
                <div class="section-title">🏦 知名游资追踪（当日）</div>
                <div class="youzi-grid" id="youzi-grid">
                    <!-- JS动态渲染 -->
                </div>
            </div>

            <!-- 四、细分信号 -->
            <div class="section">
                <div class="section-title">⚡ 细分信号</div>
                <div id="sub-signals">
                    <!-- JS动态渲染 -->
                </div>
            </div>
        </div>

        <!-- Tab 2: 连续追踪 -->
        <div class="tab-pane" id="pane-continuous">
            <!-- 周期切换 -->
            <div class="period-toggle">
                <span class="period-label">统计周期：</span>
                <button class="period-btn active" onclick="switchPeriod('week')" id="period-week">近7日</button>
                <button class="period-btn" onclick="switchPeriod('month')" id="period-month">近30日</button>
            </div>

            <!-- ① 行业板块趋势 -->
            <div class="section">
                <div class="section-title">🏭 行业板块趋势</div>
                <div class="industry-grid" id="ct-industry-grid">
                    <!-- JS动态渲染 -->
                </div>
            </div>

            <!-- ② 机构连续加仓榜 -->
            <div class="section">
                <div class="section-title">🏢 机构连续加仓榜 <span class="section-sub">连续2天及以上机构净买入≥2000万</span></div>
                <div class="rank-table-wrap" id="ct-inst-continuous">
                    <!-- JS动态渲染 -->
                </div>
            </div>

            <!-- ③ 游资接力榜 -->
            <div class="section">
                <div class="section-title">⚡ 游资接力榜 <span class="section-sub">连续2天及以上游资净买入≥1500万</span></div>
                <div class="rank-table-wrap" id="ct-youzi-relay">
                    <!-- JS动态渲染 -->
                </div>
            </div>

            <!-- ④ 高频上榜股 -->
            <div class="section">
                <div class="section-title">🔥 高频上榜股 TOP20</div>
                <div class="rank-table-wrap" id="ct-high-freq">
                    <!-- JS动态渲染 -->
                </div>
            </div>

            <!-- ⑤ 龙虎榜热度指数 -->
            <div class="section">
                <div class="section-title">🌡️ 龙虎榜热度指数</div>
                <div class="heat-index-wrap" id="ct-heat-index">
                    <!-- JS动态渲染 -->
                </div>
            </div>
        </div>
    </div>

    <script>
    // ========== 数据 ==========
    // __SIGNAL_DATA_INJECT__

    // 所有有数据的日期列表（用于翻页）
    var availableDates = [];

    function fmtAmount(wan) {
        if (wan === undefined || wan === null) return '--';
        var abs = Math.abs(wan);
        if (abs >= 10000) return (wan / 10000).toFixed(2) + '亿';
        return wan.toFixed(0) + '万';
    }

    function fmtPct(pct) {
        if (pct === undefined || pct === null) return '--';
        var s = pct.toFixed(2);
        if (pct > 0) return '+' + s + '%';
        return s + '%';
    }

    function pctClass(pct) {
        if (pct > 0) return 'up';
        if (pct < 0) return 'down';
        return '';
    }

    // ========== 渲染：当日信号总览 ==========
    function renderSignalCards(data) {
        var container = document.getElementById('signal-cards');
        var cards = [
            { key: 'resonance_buy', title: '① 机游共振买入', color: '#f85149', icon: '📈' },
            { key: 'resonance_sell', title: '② 机游共振卖出', color: '#3fb950', icon: '📉' },
            { key: 'inst_sell_youzi_buy', title: '③ 机构出货 游资接盘', color: '#d29922', icon: '🔄' },
            { key: 'inst_buy_youzi_sell', title: '④ 机构接盘 游资出货', color: '#a371f7', icon: '🔄' },
        ];
        var html = '';
        for (var i = 0; i < cards.length; i++) {
            var c = cards[i];
            var list = data.basic_signals[c.key] || [];
            html += '<div class="signal-card">';
            html += '<div class="signal-card-title" style="color:' + c.color + ';">';
            html += '<span>' + c.icon + ' ' + c.title + '</span>';
            html += '<span class="signal-card-count">' + list.length + ' 只</span>';
            html += '</div>';
            if (list.length === 0) {
                html += '<div class="empty">暂无</div>';
            } else {
                html += '<div class="stock-list">';
                for (var j = 0; j < list.length; j++) {
                    var s = list[j];
                    html += '<div class="stock-item">';
                    html += '<div class="stock-name">' + s.name + '<span class="stock-code">' + s.code + '</span></div>';
                    html += '<div class="stock-meta">';
                    html += '<div class="row1"><span ' + (s.inst_net_wan > 0 ? 'class="up"' : 'class="down"') + '>机构</span><span ' + (s.inst_net_wan > 0 ? 'class="up"' : 'class="down"') + '>' + fmtAmount(s.inst_net_wan) + '</span> / <span ' + (s.youzi_net_wan > 0 ? 'class="up"' : 'class="down"') + '>游资' + fmtAmount(s.youzi_net_wan) + '</span></div>';
                    html += '<div class="row2">占比' + s.net_buy_ratio.toFixed(1) + '% · <span class="' + pctClass(s.change_pct) + '">' + fmtPct(s.change_pct) + '</span> · 换手' + s.turnover_rate.toFixed(1) + '%</div>';
                    html += '</div>';
                    html += '</div>';
                }
                html += '</div>';
            }
            html += '</div>';
        }
        container.innerHTML = html;
    }

    // ========== 渲染：行业板块 ==========
    function renderIndustry(data) {
        var container = document.getElementById('industry-grid');
        var ind = data.industry || {};
        var instTop = ind.inst_top10 || [];
        var youziTop = ind.youzi_top10 || [];

        var maxInst = 0;
        for (var i = 0; i < instTop.length; i++) {
            var abs = Math.abs(instTop[i].net_buy_wan);
            if (abs > maxInst) maxInst = abs;
        }
        var maxYouzi = 0;
        for (var i = 0; i < youziTop.length; i++) {
            var abs = Math.abs(youziTop[i].net_buy_wan);
            if (abs > maxYouzi) maxYouzi = abs;
        }

        var html = '';
        // 机构
        html += '<div class="industry-box">';
        html += '<div class="industry-box-title">🏢 机构净买TOP行业</div>';
        if (!ind.has_industry_data) {
            html += '';
        } else if (instTop.length === 0) {
            html += '<div class="empty">暂无数据</div>';
        } else {
            for (var i = 0; i < instTop.length; i++) {
                var it = instTop[i];
                var pct = maxInst > 0 ? (Math.abs(it.net_buy_wan) / maxInst * 100) : 0;
                var cls = it.net_buy_wan > 0 ? 'buy' : 'sell';
                var amtCls = it.net_buy_wan > 0 ? 'up' : 'down';
                var sign = it.net_buy_wan > 0 ? '+' : '';
                html += '<div class="industry-item">';
                html += '<div class="industry-name" title="' + it.industry + '">' + it.industry + '</div>';
                html += '<div class="industry-bar"><div class="industry-bar-fill ' + cls + '" style="width:' + pct + '%;"></div></div>';
                html += '<div class="industry-amount ' + amtCls + '">' + sign + fmtAmount(it.net_buy_wan) + '</div>';
                html += '</div>';
            }
        }
        html += '</div>';

        // 游资
        html += '<div class="industry-box">';
        html += '<div class="industry-box-title">⚡ 游资净买TOP行业</div>';
        if (!ind.has_industry_data) {
            html += '';
        } else if (youziTop.length === 0) {
            html += '<div class="empty">暂无数据</div>';
        } else {
            for (var i = 0; i < youziTop.length; i++) {
                var it = youziTop[i];
                var pct = maxYouzi > 0 ? (Math.abs(it.net_buy_wan) / maxYouzi * 100) : 0;
                var cls = it.net_buy_wan > 0 ? 'youzi-buy' : 'sell';
                var amtCls = it.net_buy_wan > 0 ? 'up' : 'down';
                var sign = it.net_buy_wan > 0 ? '+' : '';
                html += '<div class="industry-item">';
                html += '<div class="industry-name" title="' + it.industry + '">' + it.industry + '</div>';
                html += '<div class="industry-bar"><div class="industry-bar-fill ' + cls + '" style="width:' + pct + '%;"></div></div>';
                html += '<div class="industry-amount ' + amtCls + '">' + sign + fmtAmount(it.net_buy_wan) + '</div>';
                html += '</div>';
            }
        }
        html += '</div>';

        container.innerHTML = html;
    }

    // ========== 渲染：知名游资 ==========
    function renderFamousYouzi(data) {
        var container = document.getElementById('youzi-grid');
        var list = data.famous_youzi || [];
        if (list.length === 0) {
            container.innerHTML = '<div class="empty" style="grid-column:1/-1;">当日无知名游资上榜</div>';
            return;
        }
        var html = '';
        for (var i = 0; i < list.length; i++) {
            var yz = list[i];
            html += '<div class="youzi-card">';
            html += '<div class="youzi-card-title">';
            html += '<span><span class="youzi-name-dot" style="background:' + yz.color + ';"></span>' + yz.name + '</span>';
            html += '<span style="font-size:11px;color:#6e7681;font-weight:normal;">' + yz.stocks.length + ' 只</span>';
            html += '</div>';
            if (yz.stocks.length === 0) {
                html += '<div class="empty" style="font-size:12px;padding:6px 0;">暂无</div>';
            } else {
                for (var j = 0; j < yz.stocks.length; j++) {
                    var s = yz.stocks[j];
                    var netCls = s.net_buy_wan > 0 ? 'up' : 'down';
                    var sign = s.net_buy_wan > 0 ? '+' : '';
                    html += '<div class="youzi-stock">';
                    html += '<div class="youzi-stock-name" title="' + s.name + '">' + s.name + '</div>';
                    html += '<div class="youzi-stock-net ' + netCls + '">' + sign + fmtAmount(s.net_buy_wan) + '</div>';
                    html += '</div>';
                }
            }
            html += '</div>';
        }
        container.innerHTML = html;
    }

    // ========== 渲染：细分信号 ==========
    function renderSubSignals(data) {
        var container = document.getElementById('sub-signals');
        var subs = data.sub_signals || {};
        var defs = [
            { key: 'inst_solo_buy', title: '机构独食', desc: '机构净买≥5000万 且 游资净买卖<1500万', icon: '🏢', color: '#f85149' },
            { key: 'youzi_solo_buy', title: '游资独食', desc: '游资净买≥5000万 且 机构净卖>0', icon: '⚡', color: '#d29922' },
            { key: 'inst_rush_buy', title: '机构抢筹', desc: '机构净买≥1亿 且 净买占比>10% 且 涨停', icon: '🚀', color: '#da3633' },
            { key: 'inst_distribute', title: '机构派发', desc: '机构净卖≥1亿 且 高位放量（量比>1.5，跌幅>0）', icon: '📉', color: '#238636' },
            { key: 'low_suction', title: '低吸信号', desc: '机游共振净买 且 当日收阴线（跌幅>0）', icon: '🔻', color: '#a371f7' },
        ];

        var html = '';
        for (var i = 0; i < defs.length; i++) {
            var d = defs[i];
            var list = subs[d.key] || [];
            html += '<div class="sub-signal-section">';
            html += '<div class="sub-signal-title" style="color:' + d.color + ';">';
            html += '<span>' + d.icon + ' ' + d.title + '</span>';
            html += '<span class="badge">' + list.length + ' 只</span>';
            html += '<span style="font-size:11px;color:#6e7681;font-weight:normal;margin-left:8px;">' + d.desc + '</span>';
            html += '</div>';
            if (list.length === 0) {
                html += '<div class="empty" style="text-align:left;padding:6px 0;">暂无</div>';
            } else {
                html += '<div class="sub-signal-list">';
                for (var j = 0; j < list.length; j++) {
                    var s = list[j];
                    html += '<div class="sub-stock-item">';
                    html += '<div class="sub-stock-header">';
                    html += '<div class="sub-stock-name">' + s.name + '<span class="stock-code">' + s.code + '</span></div>';
                    html += '<div><span class="' + pctClass(s.change_pct) + '">' + fmtPct(s.change_pct) + '</span></div>';
                    html += '</div>';
                    html += '<div class="sub-stock-reason">' + s.reason + '</div>';
                    html += '<div class="sub-stock-meta">';
                    html += '<span>机构' + fmtAmount(s.inst_net_wan) + '</span>';
                    html += '<span>游资' + fmtAmount(s.youzi_net_wan) + '</span>';
                    html += '<span>占比' + s.net_buy_ratio.toFixed(1) + '%</span>';
                    html += '<span>换手' + s.turnover_rate.toFixed(1) + '%</span>';
                    if (s.vol_ratio) html += '<span>量比' + s.vol_ratio.toFixed(2) + '</span>';
                    html += '</div>';
                    html += '</div>';
                }
                html += '</div>';
            }
            html += '</div>';
        }
        container.innerHTML = html;
    }

    // ========== 当前周期（周/月） ==========
    var currentPeriod = 'week';

    // ========== Tab 切换 ==========
    function switchTab(tab) {
        var dailyBtn = document.getElementById('tab-daily');
        var contBtn = document.getElementById('tab-continuous');
        var dailyPane = document.getElementById('pane-daily');
        var contPane = document.getElementById('pane-continuous');
        if (tab === 'daily') {
            dailyBtn.classList.add('active');
            contBtn.classList.remove('active');
            dailyPane.classList.add('active');
            contPane.classList.remove('active');
        } else {
            dailyBtn.classList.remove('active');
            contBtn.classList.add('active');
            dailyPane.classList.remove('active');
            contPane.classList.add('active');
            renderContinuousTracking();
        }
    }

    // ========== 周期切换 ==========
    function switchPeriod(period) {
        currentPeriod = period;
        document.getElementById('period-week').classList.toggle('active', period === 'week');
        document.getElementById('period-month').classList.toggle('active', period === 'month');
        renderContinuousTracking();
    }

    // ========== 渲染：连续追踪 ==========
    function renderContinuousTracking() {
        var ct = continuousData;
        if (!ct || !ct.heat_index) {
            var empty = '<div class="empty" style="padding:40px;text-align:center;">暂无连续追踪数据</div>';
            document.getElementById('ct-industry-grid').innerHTML = empty;
            document.getElementById('ct-inst-continuous').innerHTML = empty;
            document.getElementById('ct-youzi-relay').innerHTML = empty;
            document.getElementById('ct-high-freq').innerHTML = empty;
            document.getElementById('ct-heat-index').innerHTML = empty;
            return;
        }

        // 1. 行业板块趋势
        renderCTIndustry(ct);

        // 2. 机构连续加仓榜
        renderCTInstContinuous(ct);

        // 3. 游资接力榜
        renderCTYouziRelay(ct);

        // 4. 高频上榜股
        renderCTHighFreq(ct);

        // 5. 热度指数
        renderCTHeatIndex(ct);
    }

    function renderCTIndustry(ct) {
        var container = document.getElementById('ct-industry-grid');
        var ind = ct.industry_trend[currentPeriod] || {};
        var instTop = ind.inst_top10 || [];
        var youziTop = ind.youzi_top10 || [];

        var maxInst = 0;
        for (var i = 0; i < instTop.length; i++) {
            var abs = Math.abs(instTop[i].net_buy_wan);
            if (abs > maxInst) maxInst = abs;
        }
        var maxYouzi = 0;
        for (var i = 0; i < youziTop.length; i++) {
            var abs = Math.abs(youziTop[i].net_buy_wan);
            if (abs > maxYouzi) maxYouzi = abs;
        }

        var html = '';
        // 机构
        html += '<div class="industry-box">';
        html += '<div class="industry-box-title">🏢 机构净买TOP行业</div>';
        if (!ind.has_industry_data) {
            html += '';
        } else if (instTop.length === 0) {
            html += '<div class="empty">暂无数据</div>';
        } else {
            for (var i = 0; i < instTop.length; i++) {
                var it = instTop[i];
                var pct = maxInst > 0 ? (Math.abs(it.net_buy_wan) / maxInst * 100) : 0;
                var cls = it.net_buy_wan > 0 ? 'buy' : 'sell';
                var amtCls = it.net_buy_wan > 0 ? 'up' : 'down';
                var sign = it.net_buy_wan > 0 ? '+' : '';
                html += '<div class="industry-item">';
                html += '<div class="industry-name" title="' + it.industry + '">' + it.industry + '</div>';
                html += '<div class="industry-bar"><div class="industry-bar-fill ' + cls + '" style="width:' + pct + '%;"></div></div>';
                html += '<div class="industry-amount ' + amtCls + '">' + sign + fmtAmount(it.net_buy_wan) + '</div>';
                html += '</div>';
            }
        }
        html += '</div>';
        // 游资
        html += '<div class="industry-box">';
        html += '<div class="industry-box-title">⚡ 游资净买TOP行业</div>';
        if (!ind.has_industry_data) {
            html += '';
        } else if (youziTop.length === 0) {
            html += '<div class="empty">暂无数据</div>';
        } else {
            for (var i = 0; i < youziTop.length; i++) {
                var it = youziTop[i];
                var pct = maxYouzi > 0 ? (Math.abs(it.net_buy_wan) / maxYouzi * 100) : 0;
                var cls = it.net_buy_wan > 0 ? 'youzi-buy' : 'sell';
                var amtCls = it.net_buy_wan > 0 ? 'up' : 'down';
                var sign = it.net_buy_wan > 0 ? '+' : '';
                html += '<div class="industry-item">';
                html += '<div class="industry-name" title="' + it.industry + '">' + it.industry + '</div>';
                html += '<div class="industry-bar"><div class="industry-bar-fill ' + cls + '" style="width:' + pct + '%;"></div></div>';
                html += '<div class="industry-amount ' + amtCls + '">' + sign + fmtAmount(it.net_buy_wan) + '</div>';
                html += '</div>';
            }
        }
        html += '</div>';
        container.innerHTML = html;
    }

    function getRankNumClass(i) {
        if (i === 0) return 'top1';
        if (i === 1) return 'top2';
        if (i === 2) return 'top3';
        return '';
    }

    function renderCTInstContinuous(ct) {
        var container = document.getElementById('ct-inst-continuous');
        var list = ct.inst_continuous || [];
        if (list.length === 0) {
            container.innerHTML = '<div class="empty" style="padding:30px;text-align:center;">暂无连续加仓个股</div>';
            return;
        }
        var html = '<table class="rank-table"><thead><tr>';
        html += '<th style="width:50px;">排名</th><th>股票名称</th>';
        html += '<th style="width:100px;">连续天数</th><th style="width:120px;">累计净买</th>';
        html += '</tr></thead><tbody>';
        for (var i = 0; i < list.length; i++) {
            var s = list[i];
            html += '<tr>';
            html += '<td><span class="rank-num ' + getRankNumClass(i) + '">' + (i + 1) + '</span></td>';
            html += '<td>' + s.name + '<span class="stock-code">' + s.code + '</span></td>';
            html += '<td style="color:#e8a0b0;font-weight:600;">' + s.streak_days + ' 天</td>';
            html += '<td class="up">' + fmtAmount(s.total_net_wan) + '</td>';
            html += '</tr>';
        }
        html += '</tbody></table>';
        container.innerHTML = html;
    }

    function renderCTYouziRelay(ct) {
        var container = document.getElementById('ct-youzi-relay');
        var list = ct.youzi_relay || [];
        if (list.length === 0) {
            container.innerHTML = '<div class="empty" style="padding:30px;text-align:center;">暂无游资接力个股</div>';
            return;
        }
        var html = '<table class="rank-table"><thead><tr>';
        html += '<th style="width:50px;">排名</th><th>股票名称</th>';
        html += '<th style="width:90px;">接力天数</th><th style="width:90px;">游资参与</th>';
        html += '<th style="width:120px;">游资累计净买</th>';
        html += '</tr></thead><tbody>';
        for (var i = 0; i < list.length; i++) {
            var s = list[i];
            html += '<tr>';
            html += '<td><span class="rank-num ' + getRankNumClass(i) + '">' + (i + 1) + '</span></td>';
            html += '<td>' + s.name + '<span class="stock-code">' + s.code + '</span></td>';
            html += '<td style="color:#d29922;font-weight:600;">' + s.relay_days + ' 天</td>';
            html += '<td>' + s.youzi_count + ' 位</td>';
            html += '<td class="up">' + fmtAmount(s.total_net_wan) + '</td>';
            html += '</tr>';
        }
        html += '</tbody></table>';
        container.innerHTML = html;
    }

    function renderCTHighFreq(ct) {
        var container = document.getElementById('ct-high-freq');
        var list = (ct.high_freq && ct.high_freq[currentPeriod]) ? ct.high_freq[currentPeriod] : [];
        if (list.length === 0) {
            container.innerHTML = '<div class="empty" style="padding:30px;text-align:center;">暂无数据</div>';
            return;
        }
        var html = '<table class="rank-table"><thead><tr>';
        html += '<th style="width:50px;">排名</th><th>股票名称</th>';
        html += '<th style="width:100px;">上榜次数</th><th style="width:100px;">上涨天数</th>';
        html += '<th style="width:100px;">上涨占比</th>';
        html += '</tr></thead><tbody>';
        for (var i = 0; i < list.length; i++) {
            var s = list[i];
            var ratio = s.count > 0 ? (s.up_days / s.count * 100).toFixed(1) : '0.0';
            html += '<tr>';
            html += '<td><span class="rank-num ' + getRankNumClass(i) + '">' + (i + 1) + '</span></td>';
            html += '<td>' + s.name + '<span class="stock-code">' + s.code + '</span></td>';
            html += '<td style="font-weight:600;">' + s.count + ' 次</td>';
            html += '<td class="up">' + s.up_days + ' 天</td>';
            html += '<td>' + ratio + '%</td>';
            html += '</tr>';
        }
        html += '</tbody></table>';
        container.innerHTML = html;
    }

    function renderCTHeatIndex(ct) {
        var container = document.getElementById('ct-heat-index');
        var heat = ct.heat_index || {};
        var avg = heat[currentPeriod + '_avg'] || {};
        var daily = heat.daily || [];
        // 取对应周期的数据
        var days = currentPeriod === 'week' ? 7 : 30;
        var periodDaily = daily.slice(-Math.min(days, daily.length));
        periodDaily.reverse();  // 最新在上面

        var html = '';
        html += '<div class="heat-stats">';
        html += '<div class="heat-stat-card">';
        html += '<div class="heat-stat-label">日均上榜股票数</div>';
        html += '<div class="heat-stat-value">' + (avg.avg_stocks || 0) + ' 只</div>';
        html += '</div>';
        html += '<div class="heat-stat-card">';
        html += '<div class="heat-stat-label">日均机构净买卖</div>';
        var instNet = avg.avg_inst_net_wan || 0;
        var instCls = instNet > 0 ? 'up' : 'down';
        var instSign = instNet > 0 ? '+' : '';
        html += '<div class="heat-stat-value ' + instCls + '">' + instSign + fmtAmount(instNet) + '</div>';
        html += '</div>';
        html += '<div class="heat-stat-card">';
        html += '<div class="heat-stat-label">日均游资净买卖</div>';
        var youziNet = avg.avg_youzi_net_wan || 0;
        var youziCls = youziNet > 0 ? 'up' : 'down';
        var youziSign = youziNet > 0 ? '+' : '';
        html += '<div class="heat-stat-value ' + youziCls + '">' + youziSign + fmtAmount(youziNet) + '</div>';
        html += '</div>';
        html += '</div>';

        html += '<div style="font-size:13px;color:#8b949e;margin-bottom:10px;font-weight:500;">每日明细（最新在前）</div>';
        html += '<div class="heat-daily-list">';
        if (periodDaily.length === 0) {
            html += '<div class="empty">暂无数据</div>';
        } else {
            for (var i = 0; i < periodDaily.length; i++) {
                var d = periodDaily[i];
                var instN = d.inst_total_net_wan || 0;
                var yzN = d.youzi_total_net_wan || 0;
                html += '<div class="heat-daily-item">';
                html += '<div class="heat-daily-date">' + d.date + '</div>';
                html += '<div class="heat-daily-metrics">';
                html += '<span style="color:#c9d1d9;">上榜 ' + d.stock_count + ' 只</span>';
                html += '<span class="' + (instN > 0 ? 'up' : 'down') + '">机构 ' + (instN > 0 ? '+' : '') + fmtAmount(instN) + '</span>';
                html += '<span class="' + (yzN > 0 ? 'up' : 'down') + '">游资 ' + (yzN > 0 ? '+' : '') + fmtAmount(yzN) + '</span>';
                html += '</div></div>';
            }
        }
        html += '</div>';
        container.innerHTML = html;
    }

    // ========== 渲染主函数 ==========
    function renderPage(dateStr) {
        var data = signalData[dateStr];
        if (!data) {
            document.getElementById('current-date').textContent = dateStr;
            document.getElementById('update-time').textContent = '暂无数据';
            document.getElementById('signal-cards').innerHTML = '<div class="empty" style="grid-column:1/-1;padding:40px;">该日期暂无信号数据</div>';
            document.getElementById('industry-grid').innerHTML = '';
            document.getElementById('youzi-grid').innerHTML = '';
            document.getElementById('sub-signals').innerHTML = '';
            return;
        }
        document.getElementById('current-date').textContent = dateStr;
        document.getElementById('update-time').textContent = '更新于 ' + (data.update_time || '--');
        renderSignalCards(data);
        renderIndustry(data);
        renderFamousYouzi(data);
        renderSubSignals(data);
    }

    // ========== 翻页 ==========
    function getCurrentDateIndex() {
        var cur = document.getElementById('current-date').textContent;
        for (var i = 0; i < availableDates.length; i++) {
            if (availableDates[i] === cur) return i;
        }
        return -1;
    }
    function prevDay() {
        var idx = getCurrentDateIndex();
        if (idx < 0) idx = availableDates.length;
        if (idx > 0) {
            renderPage(availableDates[idx - 1]);
            window.scrollTo(0, 0);
        }
    }
    function nextDay() {
        var idx = getCurrentDateIndex();
        if (idx < 0) idx = -1;
        if (idx < availableDates.length - 1) {
            renderPage(availableDates[idx + 1]);
            window.scrollTo(0, 0);
        }
    }

    // ========== 初始化 ==========
    function init() {
        var dates = Object.keys(signalData).sort();
        availableDates = dates;
        if (dates.length > 0) {
            // 显示最新一天
            var latest = dates[dates.length - 1];
            // 如果URL有date参数，优先使用
            var params = new URLSearchParams(window.location.search);
            var reqDate = params.get('date');
            if (reqDate && signalData[reqDate]) {
                latest = reqDate;
            }
            renderPage(latest);
        } else {
            document.getElementById('current-date').textContent = '--';
            document.getElementById('update-time').textContent = '暂无数据';
        }
    }
    init();
    </script>
</body>
</html>
"""


def inject_data_into_page(html_content: str, date_data_map: Dict[str, Dict]) -> str:
    """
    将多日信号数据 + 连续追踪数据 注入到HTML中
    date_data_map: {date_str: signal_data_dict}
    """
    # 按日期排序
    sorted_dates = sorted(date_data_map.keys())
    data_json = json.dumps(date_data_map, ensure_ascii=False, separators=(',', ':'))

    # 计算连续追踪数据
    continuous_data = compute_continuous_tracking(date_data_map, window_days=30)
    continuous_json = json.dumps(continuous_data, ensure_ascii=False, separators=(',', ':'))

    # 替换注入标记
    inject_marker = "// __SIGNAL_DATA_INJECT__"
    replacement = (
        f"// __SIGNAL_DATA_INJECT__\n"
        f"    signalData = {data_json};\n"
        f"    continuousData = {continuous_json};\n"
        f"    // 有数据的日期列表\n"
            )

    if inject_marker in html_content:
        html_content = html_content.replace(inject_marker, replacement, 1)
    else:
        # 尝试另一种方式：找到 var signalData = { } 这一行
        pattern = r"\s*var signalData = \{\s*\};"
        html_content = re.sub(pattern, f"\n    signalData = {data_json};\n", html_content)

    return html_content


def generate_signal_page(output_path: str, date_data_map: Dict[str, Dict]) -> bool:
    """
    生成信号分析页面
    如果文件已存在，只更新数据部分；否则用模板生成
    """
    html_content = PAGE_HTML_TEMPLATE
    html_content = inject_data_into_page(html_content, date_data_map)

    # 确保输出目录存在
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html_content)

    # 注入密码保护
    _pwd_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "password_protect.py")
    if os.path.isfile(_pwd_script):
        sys.path.insert(0, os.path.dirname(_pwd_script))
        from password_protect import inject_password
        inject_password(output_path, "#e888a0")

    log_info(f"信号分析页面已生成: {output_path}（{len(date_data_map)}天数据）")
    return True


def update_signal_page_data(page_path: str, new_date_data: Dict[str, Dict]) -> bool:
    """
    更新已有页面中的数据（增量或覆盖）
    """
    if not os.path.isfile(page_path):
        return generate_signal_page(page_path, new_date_data)

    with open(page_path, "r", encoding="utf-8") as f:
        content = f.read()

    # 提取现有signalData
    existing_data = {}
    m = re.search(r"signalData\s*=\s*(\{.*?\});", content, re.DOTALL)
    if m:
        try:
            existing_data = json.loads(m.group(1))
        except Exception:
            existing_data = {}

    # 合并新数据（新数据覆盖旧数据）
    merged = {**existing_data, **new_date_data}

    # 重新注入
    content = inject_data_into_page(PAGE_HTML_TEMPLATE, merged)

    with open(page_path, "w", encoding="utf-8") as f:
        f.write(content)

    # 注入密码保护
    _pwd_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "password_protect.py")
    if os.path.isfile(_pwd_script):
        sys.path.insert(0, os.path.dirname(_pwd_script))
        from password_protect import inject_password
        inject_password(page_path, "#e888a0")

    log_info(f"信号分析页已更新: {page_path}（共{len(merged)}天数据，本次更新{len(new_date_data)}天）")
    return True


# ========== 主页面入口链接添加 ==========

ENTRY_LINK_HTML = (
    '<a href="jiyou-signal-analysis.html" '
    'style="font-size:13px;color:#58a6ff;text-decoration:none;margin-left:auto;'
    'font-weight:normal;" onmouseover="this.style.textDecoration=\'underline\'" '
    'onmouseout="this.style.textDecoration=\'none\'">查看完整分析 →</a>'
)


def add_entry_link(main_html_path: str) -> bool:
    """
    在机游主页面的"每日信号精选"标题旁加入口链接
    三大日历母版铁律：只加链接，不改其他
    """
    with open(main_html_path, "r", encoding="utf-8") as f:
        content = f.read()

    # 检查是否已有链接
    if "jiyou-signal-analysis.html" in content:
        log_warn("主页面已存在信号分析入口链接，跳过")
        return False

    # 找到"每日信号精选"标题行（JS渲染中的这一行）
    # sigHtml += '<div class="signals-header">每日信号精选</div>';
    old_line = "sigHtml += '<div class=\"signals-header\">每日信号精选</div>';"
    new_line = (
        "sigHtml += '<div class=\"signals-header\" style=\"position:relative;\">每日信号精选"
        + ENTRY_LINK_HTML.replace('"', '\\"')
        + "</div>';"
    )

    if old_line in content:
        content = content.replace(old_line, new_line, 1)
        with open(main_html_path, "w", encoding="utf-8") as f:
            f.write(content)
        log_info(f"已在主页面添加入口链接: {main_html_path}")
        return True
    else:
        log_warn("未找到'每日信号精选'标题行，无法添加链接")
        return False


# ========== 主函数 ==========

def parse_date_range(range_str: str) -> List[str]:
    """解析日期范围，如 2026-07-01..2026-07-19"""
    if ".." in range_str:
        parts = range_str.split("..")
        start = datetime.strptime(parts[0], "%Y-%m-%d")
        end = datetime.strptime(parts[1], "%Y-%m-%d")
        dates = []
        cur = start
        while cur <= end:
            ds = cur.strftime("%Y-%m-%d")
            if is_trading_day(ds):
                dates.append(ds)
            cur += timedelta(days=1)
        return dates
    else:
        return [range_str] if is_trading_day(range_str) else []


def main():
    parser = argparse.ArgumentParser(description="机游信号分析 — 独立页面生成")
    parser.add_argument("--date", default="", help="目标日期（YYYY-MM-DD）")
    parser.add_argument("--backfill", default="",
                        help="历史回补日期范围，如 2026-07-01..2026-07-19")
    parser.add_argument("--html", default="jiyou-signal-analysis.html",
                        help="输出HTML文件路径")
    parser.add_argument("--add-entry", default="",
                        help="在指定主页面加入口链接（主页面路径）")
    parser.add_argument("--main-html", default="机游共振日历.html",
                        help="机游主页面路径（用于添加入口链接）")
    parser.add_argument("--repo-dir", default=".",
                        help="仓库根目录")
    args = parser.parse_args()

    repo_dir = str(Path(args.repo_dir).resolve())
    html_path = os.path.join(repo_dir, args.html) if not os.path.isabs(args.html) else args.html

    # 模式1：添加入口链接
    if args.add_entry:
        entry_path = os.path.join(repo_dir, args.add_entry) if not os.path.isabs(args.add_entry) else args.add_entry
        add_entry_link(entry_path)
        return

    # 模式2：单日期或回补
    target_dates = []
    if args.backfill:
        target_dates = parse_date_range(args.backfill)
        log_info(f"历史回补: {len(target_dates)} 个交易日")
    elif args.date:
        if is_trading_day(args.date):
            target_dates = [args.date]
        else:
            log_warn(f"{args.date} 非交易日，跳过")
            return
    else:
        # 默认今天
        today = datetime.utcnow() + timedelta(hours=8).strftime("%Y-%m-%d")
        if is_trading_day(today):
            target_dates = [today]
        else:
            log_warn(f"今天({today})非交易日，跳过")
            return

    if not target_dates:
        log_warn("没有需要处理的日期")
        return

    # 逐天计算信号
    all_data = {}
    for ds in target_dates:
        try:
            data = compute_signals_for_date(ds)
            all_data[ds] = data
        except Exception as e:
            log_error(f"计算 {ds} 信号失败: {e}")
            import traceback
            traceback.print_exc()
        time.sleep(0.3)  # 避免请求过快

    if not all_data:
        log_error("没有成功计算任何日期的信号")
        sys.exit(1)

    # 更新页面
    update_signal_page_data(html_path, all_data)

    print()
    print("=" * 60)
    print(f"✅ 信号分析页生成完成")
    print(f"📅 数据日期: {', '.join(sorted(all_data.keys()))}")
    print(f"📄 输出文件: {html_path}")
    print("=" * 60)


if __name__ == "__main__":
    main()
