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
        "update_time": datetime.now().strftime("%Y-%m-%d %H:%M"),
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
                    html += '<div class="row1"><span class="up">机构' + fmtAmount(s.inst_net_wan) + '</span> / <span ' + (s.youzi_net_wan > 0 ? 'class="up"' : 'class="down"') + '>游资' + fmtAmount(s.youzi_net_wan) + '</span></div>';
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
            html += '<div class="empty">行业数据接口接入中...</div>';
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
            html += '<div class="empty">行业数据接口接入中...</div>';
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
    将多日信号数据注入到HTML中
    date_data_map: {date_str: signal_data_dict}
    """
    # 按日期排序
    sorted_dates = sorted(date_data_map.keys())
    data_json = json.dumps(date_data_map, ensure_ascii=False, separators=(',', ':'))

    # 替换注入标记
    inject_marker = "// __SIGNAL_DATA_INJECT__"
    replacement = (
        f"// __SIGNAL_DATA_INJECT__\n"
        f"    signalData = {data_json};\n"
        f"    // 有数据的日期列表\n"
        f"    var _availableDates = {json.dumps(sorted_dates)};\n"
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
        today = datetime.now().strftime("%Y-%m-%d")
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
