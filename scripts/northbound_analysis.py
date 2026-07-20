#!/usr/bin/env python3
"""
北向资金分析 — 独立页面生成脚本

功能：
  1. 行业配置趋势（近7天/30天TOP10行业，北向净买入聚合）
  2. 北向连续加仓个股（连续N天北向净买入TOP20）
  3. 北向+机构共振（同时被北向和机构净买入的个股）
  4. 北向持仓变化榜（近7天/30天净买入/净卖出TOP10）

数据源：
  - 北向：东方财富龙虎榜北向席位（沪股通专用+深股通专用）
  - 机构：东方财富龙虎榜机构席位
  - 行情：腾讯 gtimg

用法：
  python3 northbound_analysis.py --backfill 2026-07-01..2026-07-19 --html northbound-analysis.html
  python3 northbound_analysis.py --date 2026-07-17 --html northbound-analysis.html
  python3 northbound_analysis.py --add-entry 北向资金日历.html  # 在主页面加入口链接
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Optional

import requests

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from update_northbound_gha import (  # noqa: E402
    EASTMONEY_HEADERS,
    REPORT_DAILY_DETAILS,
    NORTHBOUND_KEYWORDS,
    _safe_num,
    fetch_eastmoney_api,
    get_northbound_dept_data,
    aggregate_northbound,
    get_stock_name_map,
    is_trading_day,
    is_northbound_open,
    format_amount,
    A_STOCK_HOLIDAYS_2026,
    HK_HOLIDAYS_2026,
)

from update_jiyou_resonance_gha import (  # noqa: E402
    REPORT_BUY_DETAILS,
    REPORT_SELL_DETAILS,
    get_institution_data,
)

# ========== 配置 ==========

# 腾讯行情接口
TENCENT_QUOTE_URL = "https://qt.gtimg.cn/q="

# 北向连续加仓阈值（万元）
NB_CONTINUOUS_THRESHOLD = 1000.0  # 北向单日净买≥1000万才算加仓

# 北向+机构共振阈值
NB_INST_RESONANCE_NB = 1000.0  # 北向净买≥1000万
NB_INST_RESONANCE_INST = 2000.0  # 机构净买≥2000万


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
    return "sh" + code


def fetch_tencent_quotes(codes: List[str]) -> Dict[str, Dict]:
    """批量获取腾讯行情数据"""
    if not codes:
        return {}
    results = {}
    batch_size = 50
    for i in range(0, len(codes), batch_size):
        batch = codes[i:i + batch_size]
        q_codes = ",".join([code_to_gtimg_prefix(c) for c in batch])
        try:
            url = TENCENT_QUOTE_URL + q_codes
            r = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
            r.encoding = "gbk"
            text = r.text.strip()
            for line in text.split("\n"):
                line = line.strip()
                if not line or '="' not in line:
                    continue
                m = re.match(r'v_([a-z]{2}\d+)="([^"]*)"', line)
                if not m:
                    continue
                raw_code = m.group(1)[2:]
                fields = m.group(2).split("~")
                if len(fields) < 50:
                    continue
                info = {
                    "code": raw_code,
                    "name": fields[1] if len(fields) > 1 else "",
                    "current": _safe_num(fields[3]),
                    "prev_close": _safe_num(fields[4]),
                    "change_pct": _safe_num(fields[32]),
                    "turnover_rate": _safe_num(fields[38]),
                    "amount_wan": _safe_num(fields[37]),
                    "total_mktcap_yi": _safe_num(fields[45]),
                }
                results[raw_code] = info
        except Exception as e:
            log_warn(f"腾讯行情查询失败 (批次{i//batch_size}): {e}")
        time.sleep(0.1)
    return results


def get_stock_industry(code: str, name: str) -> str:
    """获取股票行业（暂未接入行业API，返回空）"""
    return ""


# ========== 北向单日数据获取 ==========

def get_northbound_daily(date_str: str) -> Dict:
    """
    获取单日北向资金明细
    返回: {date, stocks: [{code, name, net_wan, buy_wan, sell_wan}], total_net_wan}
    """
    if not is_northbound_open(date_str):
        return {"date": date_str, "stocks": [], "total_net_wan": 0.0, "hk_holiday": True}

    name_map = get_stock_name_map(date_str)
    dept_rows = get_northbound_dept_data(date_str)
    if not dept_rows:
        return {"date": date_str, "stocks": [], "total_net_wan": 0.0, "hk_holiday": False}

    agg = aggregate_northbound(dept_rows, name_map)
    return {
        "date": date_str,
        "stocks": agg["stocks"],
        "total_net_wan": agg["total_net_wan"],
        "hk_holiday": False,
    }


# ========== 分析模块 ==========

def compute_industry_trend(northbound_data: Dict[str, Dict],
                           period_dates: List[str]) -> Dict:
    """
    按行业聚合北向净买入（行业数据暂未接入，返回空结构）
    """
    industry_map = {}
    for ds in period_dates:
        day_data = northbound_data.get(ds, {})
        for s in day_data.get("stocks", []):
            industry = get_stock_industry(s["code"], s["name"])
            if not industry:
                industry = "未分类"
            industry_map[industry] = industry_map.get(industry, 0.0) + s.get("net_wan", 0)

    top_buy = [{"industry": k, "net_buy_wan": round(v, 2)}
               for k, v in sorted(industry_map.items(), key=lambda x: x[1], reverse=True)[:10]]
    top_sell = [{"industry": k, "net_sell_wan": round(abs(v), 2)}
                for k, v in sorted(industry_map.items(), key=lambda x: x[1])[:10]
                if v < 0]
    has_data = len(industry_map) > 1 or (len(industry_map) == 1 and "未分类" not in industry_map)

    return {
        "top_buy": top_buy,
        "top_sell": top_sell,
        "has_industry_data": has_data,
    }


def compute_continuous_buy(northbound_data: Dict[str, Dict],
                           period_dates: List[str],
                           quotes: Dict[str, Dict]) -> List[Dict]:
    """
    北向连续加仓个股：连续N天北向净买入的个股
    返回按连续天数+累计净买排序的TOP20
    """
    # 构建每只股票的每日净买序列
    stock_daily = {}  # {code: {name, dates: {date: net_wan}}}
    for ds in period_dates:
        day_data = northbound_data.get(ds, {})
        for s in day_data.get("stocks", []):
            code = s["code"]
            if code not in stock_daily:
                stock_daily[code] = {"name": s["name"], "dates": {}}
            stock_daily[code]["dates"][ds] = s.get("net_wan", 0)

    # 计算连续加仓天数
    result = []
    for code, info in stock_daily.items():
        sorted_dates = sorted(info["dates"].keys())
        max_streak = 0
        current_streak = 0
        for ds in sorted_dates:
            net = info["dates"][ds]
            if net >= NB_CONTINUOUS_THRESHOLD:
                current_streak += 1
                if current_streak > max_streak:
                    max_streak = current_streak
            else:
                current_streak = 0

        if max_streak >= 2:
            # 累计净买（窗口内北向净买为正的部分）
            total_net = sum(v for v in info["dates"].values() if v > 0)
            # 区间涨跌幅
            q = quotes.get(code, {})
            change_pct = q.get("change_pct", 0.0)
            result.append({
                "code": code,
                "name": info["name"],
                "streak_days": max_streak,
                "total_net_wan": round(total_net, 2),
                "change_pct": round(change_pct, 2),
            })

    result.sort(key=lambda x: (x["streak_days"], x["total_net_wan"]), reverse=True)
    return result[:20]


def compute_northbound_inst_resonance(northbound_data: Dict[str, Dict],
                                      inst_data_map: Dict[str, Dict],
                                      period_dates: List[str]) -> List[Dict]:
    """
    北向+机构共振：同一周期内同时被北向和机构净买入的个股
    共振强度 = 北向累计净买 + 机构累计净买
    """
    # 聚合窗口内北向每只股票的净买
    nb_stock_map = {}  # {code: {name, total_net}}
    for ds in period_dates:
        day_data = northbound_data.get(ds, {})
        for s in day_data.get("stocks", []):
            code = s["code"]
            if code not in nb_stock_map:
                nb_stock_map[code] = {"name": s["name"], "total_net": 0.0}
            nb_stock_map[code]["total_net"] += s.get("net_wan", 0)

    # 聚合窗口内机构每只股票的净买
    inst_stock_map = {}  # {code: {name, total_net}}
    for ds in period_dates:
        inst_data = inst_data_map.get(ds, {})
        for s in inst_data.get("buy_sorted", []) + inst_data.get("sell_sorted", []):
            code = s["code"]
            if code not in inst_stock_map:
                inst_stock_map[code] = {"name": s["name"], "total_net": 0.0}
            inst_stock_map[code]["total_net"] += s.get("net_buy_wan", 0)

    # 找交集
    result = []
    for code in nb_stock_map:
        if code not in inst_stock_map:
            continue
        nb_net = nb_stock_map[code]["total_net"]
        inst_net = inst_stock_map[code]["total_net"]
        if nb_net >= NB_INST_RESONANCE_NB and inst_net >= NB_INST_RESONANCE_INST:
            name = nb_stock_map[code]["name"] or inst_stock_map[code]["name"]
            resonance_strength = nb_net + inst_net
            result.append({
                "code": code,
                "name": name,
                "nb_net_wan": round(nb_net, 2),
                "inst_net_wan": round(inst_net, 2),
                "resonance_strength": round(resonance_strength, 2),
            })

    result.sort(key=lambda x: x["resonance_strength"], reverse=True)
    return result[:20]


def compute_holding_change(northbound_data: Dict[str, Dict],
                           period_dates: List[str]) -> Dict:
    """
    北向持仓变化榜：近7天/30天净买入/净卖出TOP10
    """
    stock_map = {}  # {code: {name, code, net_wan}}
    for ds in period_dates:
        day_data = northbound_data.get(ds, {})
        for s in day_data.get("stocks", []):
            code = s["code"]
            if code not in stock_map:
                stock_map[code] = {"code": code, "name": s["name"], "net_wan": 0.0}
            stock_map[code]["net_wan"] += s.get("net_wan", 0)

    stocks = list(stock_map.values())
    top_buy = sorted(stocks, key=lambda x: x["net_wan"], reverse=True)[:10]
    top_buy = [s for s in top_buy if s["net_wan"] > 0]

    top_sell = sorted(stocks, key=lambda x: x["net_wan"])[:10]
    top_sell = [{"code": s["code"], "name": s["name"], "net_sell_wan": round(abs(s["net_wan"]), 2)}
                for s in top_sell if s["net_wan"] < 0]

    return {"top_buy": top_buy, "top_sell": top_sell}


def compute_northbound_heat(northbound_data: Dict[str, Dict],
                            period_dates: List[str]) -> Dict:
    """
    北向热度指数（每日上榜股票数量、北向总净买等）
    """
    daily = []
    for ds in period_dates:
        day_data = northbound_data.get(ds, {})
        stocks = day_data.get("stocks", [])
        total_net = day_data.get("total_net_wan", 0.0)
        # 净买入股票数、净卖出股票数
        buy_count = sum(1 for s in stocks if s.get("net_wan", 0) > 0)
        sell_count = sum(1 for s in stocks if s.get("net_wan", 0) < 0)
        daily.append({
            "date": ds,
            "stock_count": len(stocks),
            "buy_count": buy_count,
            "sell_count": sell_count,
            "total_net_wan": round(total_net, 2),
        })

    # 计算均值
    if daily:
        avg_stocks = round(sum(d["stock_count"] for d in daily) / len(daily), 1)
        avg_net = round(sum(d["total_net_wan"] for d in daily) / len(daily), 2)
    else:
        avg_stocks = 0
        avg_net = 0.0

    return {
        "daily": daily,
        "avg_stocks": avg_stocks,
        "avg_net_wan": avg_net,
    }


# ========== 页面生成 ==========

PAGE_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>北向资金分析</title>
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
            color: #58a6ff;
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
            color: #58a6ff;
            font-weight: 600;
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
        .section-sub {
            font-size: 12px;
            font-weight: normal;
            color: #6e7681;
            margin-left: 8px;
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
            background: rgba(88, 166, 255, 0.15);
            border-color: #58a6ff;
            color: #58a6ff;
            font-weight: 500;
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
        .industry-bar-fill.buy { background: linear-gradient(90deg, #58a6ff, #1f6feb); }
        .industry-bar-fill.sell { background: linear-gradient(90deg, #238636, #2ea043); }
        .industry-amount {
            width: 80px;
            text-align: right;
            flex-shrink: 0;
            font-size: 12px;
        }
        .up { color: #f85149; }
        .down { color: #3fb950; }
        .empty {
            color: #6e7681;
            font-size: 13px;
            padding: 10px 0;
            text-align: center;
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
        .stock-code {
            color: #6e7681;
            font-size: 11px;
            margin-left: 4px;
            font-weight: normal;
        }

        /* 双栏表格区域 */
        .dual-table-grid {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 14px;
        }
        .dual-table-box {
            background: #0d1117;
            border: 1px solid #30363d;
            border-radius: 8px;
            overflow: hidden;
        }
        .dual-table-title {
            padding: 12px 16px;
            font-size: 14px;
            font-weight: 600;
            color: #f0f6fc;
            border-bottom: 1px solid #21262d;
        }
        .dual-table-title.buy { color: #58a6ff; }
        .dual-table-title.sell { color: #3fb950; }
        .dual-table-box .rank-table {
            font-size: 12px;
        }
        .dual-table-box .rank-table th,
        .dual-table-box .rank-table td {
            padding: 8px 12px;
        }

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
        .heat-stat-value.up { color: #58a6ff; }
        .heat-stat-value.down { color: #3fb950; }
        .heat-daily-list {
            max-height: 300px;
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
            width: 100px;
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
            min-width: 90px;
            text-align: right;
        }

        /* 滚动条 */
        .heat-daily-list::-webkit-scrollbar {
            width: 6px;
        }
        .heat-daily-list::-webkit-scrollbar-track {
            background: #161b22;
        }
        .heat-daily-list::-webkit-scrollbar-thumb {
            background: #30363d;
            border-radius: 3px;
        }

        @media (max-width: 768px) {
            .industry-grid, .dual-table-grid {
                grid-template-columns: 1fr;
            }
            .container {
                padding: 15px;
            }
            body {
                padding: 10px;
            }
            .heat-stats {
                grid-template-columns: 1fr;
            }
            .rank-table th, .rank-table td {
                padding: 8px 10px;
                font-size: 12px;
            }
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div class="breadcrumb">
                <a href="北向资金日历.html">北向资金日历</a>
                <span>›</span>
                <span class="current">深度分析</span>
            </div>
            <h1>🌊 北向资金深度分析</h1>
            <div class="subtitle">基于龙虎榜北向席位数据的趋势与共振分析</div>
        </div>

        <div class="update-time" id="update-time">--</div>

        <!-- 周期切换 -->
        <div class="period-toggle">
            <span class="period-label">统计周期：</span>
            <button class="period-btn active" onclick="switchPeriod('week')" id="period-week">近7日</button>
            <button class="period-btn" onclick="switchPeriod('month')" id="period-month">近30日</button>
        </div>

        <!-- ① 行业配置趋势 -->
        <div class="section">
            <div class="section-title">🏭 行业配置趋势</div>
            <div class="industry-grid" id="nb-industry-grid">
                <!-- JS动态渲染 -->
            </div>
        </div>

        <!-- ② 北向连续加仓个股 -->
        <div class="section">
            <div class="section-title">📈 北向连续加仓榜 <span class="section-sub">连续2天及以上北向净买入≥1000万</span></div>
            <div class="rank-table-wrap" id="nb-continuous">
                <!-- JS动态渲染 -->
            </div>
        </div>

        <!-- ③ 北向+机构共振 -->
        <div class="section">
            <div class="section-title">🤝 北向+机构共振 <span class="section-sub">同时被北向与机构净买入</span></div>
            <div class="rank-table-wrap" id="nb-inst-resonance">
                <!-- JS动态渲染 -->
            </div>
        </div>

        <!-- ④ 北向持仓变化榜 -->
        <div class="section">
            <div class="section-title">📊 北向持仓变化榜</div>
            <div class="dual-table-grid" id="nb-holding-change">
                <!-- JS动态渲染 -->
            </div>
        </div>

        <!-- ⑤ 北向热度指数 -->
        <div class="section">
            <div class="section-title">🌡️ 北向热度指数</div>
            <div class="heat-index-wrap" id="nb-heat-index">
                <!-- JS动态渲染 -->
            </div>
        </div>
    </div>

    <script>
    // ========== 数据 ==========
    // __NB_DATA_INJECT__

    var currentPeriod = 'week';

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

    function getRankNumClass(i) {
        if (i === 0) return 'top1';
        if (i === 1) return 'top2';
        if (i === 2) return 'top3';
        return '';
    }

    // ========== 周期切换 ==========
    function switchPeriod(period) {
        currentPeriod = period;
        document.getElementById('period-week').classList.toggle('active', period === 'week');
        document.getElementById('period-month').classList.toggle('active', period === 'month');
        renderPage();
    }

    // ========== 渲染：行业配置趋势 ==========
    function renderIndustry() {
        var container = document.getElementById('nb-industry-grid');
        var data = nbAnalysis[currentPeriod] || {};
        var ind = data.industry_trend || {};
        var topBuy = ind.top_buy || [];
        var topSell = ind.top_sell || [];

        var maxBuy = 0;
        for (var i = 0; i < topBuy.length; i++) {
            if (topBuy[i].net_buy_wan > maxBuy) maxBuy = topBuy[i].net_buy_wan;
        }
        var maxSell = 0;
        for (var i = 0; i < topSell.length; i++) {
            if (topSell[i].net_sell_wan > maxSell) maxSell = topSell[i].net_sell_wan;
        }

        var html = '';
        // 净买入TOP行业
        html += '<div class="industry-box">';
        html += '<div class="industry-box-title">📈 净买入TOP行业</div>';
        if (!ind.has_industry_data) {
            html += '<div class="empty">行业数据接口接入中...</div>';
        } else if (topBuy.length === 0) {
            html += '<div class="empty">暂无数据</div>';
        } else {
            for (var i = 0; i < topBuy.length; i++) {
                var it = topBuy[i];
                var pct = maxBuy > 0 ? (it.net_buy_wan / maxBuy * 100) : 0;
                html += '<div class="industry-item">';
                html += '<div class="industry-name" title="' + it.industry + '">' + it.industry + '</div>';
                html += '<div class="industry-bar"><div class="industry-bar-fill buy" style="width:' + pct + '%;"></div></div>';
                html += '<div class="industry-amount up">+' + fmtAmount(it.net_buy_wan) + '</div>';
                html += '</div>';
            }
        }
        html += '</div>';

        // 净卖出TOP行业
        html += '<div class="industry-box">';
        html += '<div class="industry-box-title">📉 净卖出TOP行业</div>';
        if (!ind.has_industry_data) {
            html += '<div class="empty">行业数据接口接入中...</div>';
        } else if (topSell.length === 0) {
            html += '<div class="empty">暂无数据</div>';
        } else {
            for (var i = 0; i < topSell.length; i++) {
                var it = topSell[i];
                var pct = maxSell > 0 ? (it.net_sell_wan / maxSell * 100) : 0;
                html += '<div class="industry-item">';
                html += '<div class="industry-name" title="' + it.industry + '">' + it.industry + '</div>';
                html += '<div class="industry-bar"><div class="industry-bar-fill sell" style="width:' + pct + '%;"></div></div>';
                html += '<div class="industry-amount down">-' + fmtAmount(it.net_sell_wan) + '</div>';
                html += '</div>';
            }
        }
        html += '</div>';
        container.innerHTML = html;
    }

    // ========== 渲染：北向连续加仓榜 ==========
    function renderContinuousBuy() {
        var container = document.getElementById('nb-continuous');
        var data = nbAnalysis[currentPeriod] || {};
        var list = data.continuous_buy || [];
        if (list.length === 0) {
            container.innerHTML = '<div class="empty" style="padding:30px;text-align:center;">暂无连续加仓个股</div>';
            return;
        }
        var html = '<table class="rank-table"><thead><tr>';
        html += '<th style="width:50px;">排名</th><th>股票名称</th>';
        html += '<th style="width:90px;">连续天数</th>';
        html += '<th style="width:110px;">累计净买</th>';
        html += '<th style="width:100px;">区间涨跌幅</th>';
        html += '</tr></thead><tbody>';
        for (var i = 0; i < list.length; i++) {
            var s = list[i];
            html += '<tr>';
            html += '<td><span class="rank-num ' + getRankNumClass(i) + '">' + (i + 1) + '</span></td>';
            html += '<td>' + s.name + '<span class="stock-code">' + s.code + '</span></td>';
            html += '<td style="color:#58a6ff;font-weight:600;">' + s.streak_days + ' 天</td>';
            html += '<td class="up">' + fmtAmount(s.total_net_wan) + '</td>';
            html += '<td class="' + pctClass(s.change_pct) + '">' + fmtPct(s.change_pct) + '</td>';
            html += '</tr>';
        }
        html += '</tbody></table>';
        container.innerHTML = html;
    }

    // ========== 渲染：北向+机构共振 ==========
    function renderResonance() {
        var container = document.getElementById('nb-inst-resonance');
        var data = nbAnalysis[currentPeriod] || {};
        var list = data.resonance || [];
        if (list.length === 0) {
            container.innerHTML = '<div class="empty" style="padding:30px;text-align:center;">暂无共振个股</div>';
            return;
        }
        var html = '<table class="rank-table"><thead><tr>';
        html += '<th style="width:50px;">排名</th><th>股票名称</th>';
        html += '<th style="width:120px;">北向净买</th>';
        html += '<th style="width:120px;">机构净买</th>';
        html += '<th style="width:120px;">共振强度</th>';
        html += '</tr></thead><tbody>';
        for (var i = 0; i < list.length; i++) {
            var s = list[i];
            html += '<tr>';
            html += '<td><span class="rank-num ' + getRankNumClass(i) + '">' + (i + 1) + '</span></td>';
            html += '<td>' + s.name + '<span class="stock-code">' + s.code + '</span></td>';
            html += '<td style="color:#58a6ff;font-weight:500;">' + fmtAmount(s.nb_net_wan) + '</td>';
            html += '<td style="color:#f85149;font-weight:500;">' + fmtAmount(s.inst_net_wan) + '</td>';
            html += '<td style="color:#a371f7;font-weight:600;">' + fmtAmount(s.resonance_strength) + '</td>';
            html += '</tr>';
        }
        html += '</tbody></table>';
        container.innerHTML = html;
    }

    // ========== 渲染：持仓变化榜 ==========
    function renderHoldingChange() {
        var container = document.getElementById('nb-holding-change');
        var data = nbAnalysis[currentPeriod] || {};
        var hc = data.holding_change || {};
        var topBuy = hc.top_buy || [];
        var topSell = hc.top_sell || [];

        var html = '';
        // 净买入
        html += '<div class="dual-table-box">';
        html += '<div class="dual-table-title buy">📈 净买入 TOP10</div>';
        if (topBuy.length === 0) {
            html += '<div class="empty" style="padding:20px;">暂无数据</div>';
        } else {
            html += '<table class="rank-table"><thead><tr><th style="width:40px;">#</th><th>股票</th><th style="width:90px;">净买额</th></tr></thead><tbody>';
            for (var i = 0; i < topBuy.length; i++) {
                var s = topBuy[i];
                html += '<tr>';
                html += '<td><span class="rank-num ' + getRankNumClass(i) + '">' + (i + 1) + '</span></td>';
                html += '<td>' + s.name + '<span class="stock-code">' + s.code + '</span></td>';
                html += '<td class="up">' + fmtAmount(s.net_wan) + '</td>';
                html += '</tr>';
            }
            html += '</tbody></table>';
        }
        html += '</div>';

        // 净卖出
        html += '<div class="dual-table-box">';
        html += '<div class="dual-table-title sell">📉 净卖出 TOP10</div>';
        if (topSell.length === 0) {
            html += '<div class="empty" style="padding:20px;">暂无数据</div>';
        } else {
            html += '<table class="rank-table"><thead><tr><th style="width:40px;">#</th><th>股票</th><th style="width:90px;">净卖额</th></tr></thead><tbody>';
            for (var i = 0; i < topSell.length; i++) {
                var s = topSell[i];
                html += '<tr>';
                html += '<td><span class="rank-num ' + getRankNumClass(i) + '">' + (i + 1) + '</span></td>';
                html += '<td>' + s.name + '<span class="stock-code">' + s.code + '</span></td>';
                html += '<td class="down">' + fmtAmount(s.net_sell_wan) + '</td>';
                html += '</tr>';
            }
            html += '</tbody></table>';
        }
        html += '</div>';
        container.innerHTML = html;
    }

    // ========== 渲染：热度指数 ==========
    function renderHeatIndex() {
        var container = document.getElementById('nb-heat-index');
        var data = nbAnalysis[currentPeriod] || {};
        var heat = data.heat_index || {};
        var daily = heat.daily || [];
        var reversed = daily.slice().reverse();  // 最新在前

        var html = '';
        html += '<div class="heat-stats">';
        html += '<div class="heat-stat-card">';
        html += '<div class="heat-stat-label">日均上榜股票数</div>';
        html += '<div class="heat-stat-value">' + (heat.avg_stocks || 0) + ' 只</div>';
        html += '</div>';
        html += '<div class="heat-stat-card">';
        html += '<div class="heat-stat-label">日均北向净买卖</div>';
        var net = heat.avg_net_wan || 0;
        var cls = net > 0 ? 'up' : 'down';
        var sign = net > 0 ? '+' : '';
        html += '<div class="heat-stat-value ' + cls + '">' + sign + fmtAmount(net) + '</div>';
        html += '</div>';
        html += '<div class="heat-stat-card">';
        html += '<div class="heat-stat-label">累计交易日</div>';
        html += '<div class="heat-stat-value" style="color:#58a6ff;">' + daily.length + ' 天</div>';
        html += '</div>';
        html += '</div>';

        html += '<div style="font-size:13px;color:#8b949e;margin-bottom:10px;font-weight:500;">每日明细（最新在前）</div>';
        html += '<div class="heat-daily-list">';
        if (reversed.length === 0) {
            html += '<div class="empty">暂无数据</div>';
        } else {
            for (var i = 0; i < reversed.length; i++) {
                var d = reversed[i];
                var netAmt = d.total_net_wan || 0;
                html += '<div class="heat-daily-item">';
                html += '<div class="heat-daily-date">' + d.date + '</div>';
                html += '<div class="heat-daily-metrics">';
                html += '<span style="color:#c9d1d9;">上榜 ' + d.stock_count + ' 只</span>';
                html += '<span class="' + (netAmt > 0 ? 'up' : 'down') + '">' + (netAmt > 0 ? '+' : '') + '净买 ' + fmtAmount(netAmt) + '</span>';
                html += '<span style="color:#58a6ff;">买 ' + d.buy_count + ' / 卖 ' + d.sell_count + '</span>';
                html += '</div></div>';
            }
        }
        html += '</div>';
        container.innerHTML = html;
    }

    // ========== 渲染主函数 ==========
    function renderPage() {
        var data = nbAnalysis[currentPeriod];
        if (!data) {
            document.getElementById('update-time').textContent = '暂无数据';
            return;
        }
        renderIndustry();
        renderContinuousBuy();
        renderResonance();
        renderHoldingChange();
        renderHeatIndex();
    }

    // ========== 初始化 ==========
    function init() {
        var dates = Object.keys(nbDailyData || {}).sort();
        if (dates.length > 0) {
            var latest = dates[dates.length - 1];
            document.getElementById('update-time').textContent = '数据截至 ' + latest;
        }
        renderPage();
    }
    init();
    </script>
</body>
</html>
"""


# ========== 数据注入 ==========

def build_northbound_analysis(dates: List[str]) -> Dict:
    """
    构建完整的北向分析数据
    返回: {week: {...}, month: {...}, daily_data: {...}}
    """
    # 1. 获取所有日期的北向数据
    log_info(f"获取 {len(dates)} 天的北向数据 ...")
    nb_data = {}
    all_codes = set()
    for ds in dates:
        try:
            day_data = get_northbound_daily(ds)
            nb_data[ds] = day_data
            for s in day_data.get("stocks", []):
                all_codes.add(s["code"])
            time.sleep(0.2)
        except Exception as e:
            log_error(f"获取 {ds} 北向数据失败: {e}")
            nb_data[ds] = {"date": ds, "stocks": [], "total_net_wan": 0.0}

    # 2. 获取所有日期的机构数据
    log_info(f"获取 {len(dates)} 天的机构数据 ...")
    inst_data_map = {}
    for ds in dates:
        try:
            inst_data = get_institution_data(ds)
            inst_data_map[ds] = inst_data
            for s in inst_data.get("buy_sorted", []) + inst_data.get("sell_sorted", []):
                all_codes.add(s["code"])
            time.sleep(0.2)
        except Exception as e:
            log_error(f"获取 {ds} 机构数据失败: {e}")
            inst_data_map[ds] = {"buy_sorted": [], "sell_sorted": []}

    # 3. 获取行情数据
    log_info(f"获取 {len(all_codes)} 只股票行情 ...")
    quotes = {}
    if all_codes:
        try:
            quotes = fetch_tencent_quotes(list(all_codes))
            log_info(f"  成功获取 {len(quotes)} 只股票行情")
        except Exception as e:
            log_warn(f"行情数据获取失败: {e}")

    # 4. 计算各周期分析数据
    sorted_dates = sorted(nb_data.keys())
    week_dates = sorted_dates[-7:] if len(sorted_dates) > 7 else sorted_dates
    month_dates = sorted_dates[-30:] if len(sorted_dates) > 30 else sorted_dates

    def compute_period(period_dates: List[str]) -> Dict:
        return {
            "industry_trend": compute_industry_trend(nb_data, period_dates),
            "continuous_buy": compute_continuous_buy(nb_data, period_dates, quotes),
            "resonance": compute_northbound_inst_resonance(nb_data, inst_data_map, period_dates),
            "holding_change": compute_holding_change(nb_data, period_dates),
            "heat_index": compute_northbound_heat(nb_data, period_dates),
        }

    return {
        "week": compute_period(week_dates),
        "month": compute_period(month_dates),
    }, nb_data


def inject_data_into_page(html_content: str, analysis_data: Dict,
                          daily_data: Dict[str, Dict]) -> str:
    """将分析数据和日数据注入到HTML中"""
    analysis_json = json.dumps(analysis_data, ensure_ascii=False, separators=(',', ':'))
    daily_json = json.dumps(daily_data, ensure_ascii=False, separators=(',', ':'))

    inject_marker = "// __NB_DATA_INJECT__"
    replacement = (
        f"// __NB_DATA_INJECT__\n"
        f"    nbAnalysis = {analysis_json};\n"
        f"    var nbDailyData = {daily_json};\n"
    )

    if inject_marker in html_content:
        html_content = html_content.replace(inject_marker, replacement, 1)

    return html_content


def generate_page(output_path: str, dates: List[str]) -> bool:
    """生成北向分析页面（全量覆盖）"""
    analysis_data, daily_data = build_northbound_analysis(dates)

    html_content = PAGE_HTML_TEMPLATE
    html_content = inject_data_into_page(html_content, analysis_data, daily_data)

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html_content)

    # 注入密码保护
    _pwd_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "password_protect.py")
    if os.path.isfile(_pwd_script):
        sys.path.insert(0, os.path.dirname(_pwd_script))
        from password_protect import inject_password
        inject_password(output_path, "#58a6ff")

    log_info(f"北向分析页面已生成: {output_path}（{len(dates)}天数据）")
    return True


def extract_existing_daily_data(page_path: str) -> Dict[str, Dict]:
    """
    从已有HTML页面中提取nbDailyData数据（增量更新的历史基础）
    """
    if not os.path.isfile(page_path):
        return {}

    with open(page_path, "r", encoding="utf-8") as f:
        content = f.read()

    # 匹配 var nbDailyData = {...};
    m = re.search(r"var\s+nbDailyData\s*=\s*(\{.*?\});", content, re.DOTALL)
    if not m:
        log_warn("未在现有页面中找到 nbDailyData，无法提取历史数据")
        return {}

    try:
        data = json.loads(m.group(1))
        log_info(f"从现有页面提取到 {len(data)} 天历史数据")
        return data
    except Exception as e:
        log_error(f"解析 nbDailyData 失败: {e}")
        return {}


def update_page_incremental(output_path: str, dates: List[str]) -> bool:
    """
    增量更新北向分析页面：
    1. 如果页面不存在，走全量生成逻辑
    2. 如果页面存在，先提取历史nbDailyData，再用新日期数据覆盖/追加
    3. 基于合并后的全部日期重新计算 week/month 汇总
    4. 重新生成完整HTML并写入
    """
    # 页面不存在 -> 全量生成
    if not os.path.isfile(output_path):
        log_info("页面不存在，走全量生成逻辑")
        return generate_page(output_path, dates)

    log_info(f"开始增量更新，本次更新 {len(dates)} 天: {', '.join(sorted(dates))}")

    # 1. 提取现有历史数据
    existing_daily = extract_existing_daily_data(output_path)
    if not existing_daily:
        log_warn("未提取到历史数据，走全量生成逻辑")
        return generate_page(output_path, dates)

    # 2. 获取本次新日期的北向数据
    log_info(f"获取本次 {len(dates)} 天的北向数据 ...")
    new_daily = {}
    all_codes = set()
    for ds in dates:
        try:
            day_data = get_northbound_daily(ds)
            new_daily[ds] = day_data
            for s in day_data.get("stocks", []):
                all_codes.add(s["code"])
            time.sleep(0.2)
        except Exception as e:
            log_error(f"获取 {ds} 北向数据失败: {e}")
            new_daily[ds] = {"date": ds, "stocks": [], "total_net_wan": 0.0}

    # 3. 合并：新数据覆盖旧数据，保留所有历史日期
    merged_daily = {**existing_daily, **new_daily}
    all_sorted_dates = sorted(merged_daily.keys())
    log_info(f"合并后共 {len(merged_daily)} 天数据，范围: {all_sorted_dates[0]} ~ {all_sorted_dates[-1]}")

    # 4. 获取所有日期的机构数据（用于重新计算共振）
    # 优化：只获取机构数据，不重新抓北向
    log_info(f"获取 {len(merged_daily)} 天的机构数据（重算共振）...")
    inst_data_map = {}
    for ds in all_sorted_dates:
        try:
            inst_data = get_institution_data(ds)
            inst_data_map[ds] = inst_data
            for s in inst_data.get("buy_sorted", []) + inst_data.get("sell_sorted", []):
                all_codes.add(s["code"])
            time.sleep(0.2)
        except Exception as e:
            log_error(f"获取 {ds} 机构数据失败: {e}")
            inst_data_map[ds] = {"buy_sorted": [], "sell_sorted": []}

    # 5. 获取行情数据
    log_info(f"获取 {len(all_codes)} 只股票行情 ...")
    quotes = {}
    if all_codes:
        try:
            quotes = fetch_tencent_quotes(list(all_codes))
            log_info(f"  成功获取 {len(quotes)} 只股票行情")
        except Exception as e:
            log_warn(f"行情数据获取失败: {e}")

    # 6. 基于合并后的全部数据重新计算分析
    nb_data = merged_daily
    sorted_dates = sorted(nb_data.keys())
    week_dates = sorted_dates[-7:] if len(sorted_dates) > 7 else sorted_dates
    month_dates = sorted_dates[-30:] if len(sorted_dates) > 30 else sorted_dates

    def compute_period(period_dates: List[str]) -> Dict:
        return {
            "industry_trend": compute_industry_trend(nb_data, period_dates),
            "continuous_buy": compute_continuous_buy(nb_data, period_dates, quotes),
            "resonance": compute_northbound_inst_resonance(nb_data, inst_data_map, period_dates),
            "holding_change": compute_holding_change(nb_data, period_dates),
            "heat_index": compute_northbound_heat(nb_data, period_dates),
        }

    analysis_data = {
        "week": compute_period(week_dates),
        "month": compute_period(month_dates),
    }

    # 7. 重新生成完整HTML（用模板 + 注入新数据）
    html_content = PAGE_HTML_TEMPLATE
    html_content = inject_data_into_page(html_content, analysis_data, nb_data)

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html_content)

    # 注入密码保护
    _pwd_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "password_protect.py")
    if os.path.isfile(_pwd_script):
        sys.path.insert(0, os.path.dirname(_pwd_script))
        from password_protect import inject_password
        inject_password(output_path, "#58a6ff")

    log_info(f"北向分析页增量更新完成: {output_path}（共{len(merged_daily)}天，本次{len(dates)}天）")
    return True


# ========== 入口链接 ==========

def add_entry_link(main_html_path: str) -> bool:
    """在北向日历主页面顶部添加'北向分析'入口链接"""
    if not os.path.isfile(main_html_path):
        log_error(f"主页面不存在: {main_html_path}")
        return False

    with open(main_html_path, "r", encoding="utf-8") as f:
        content = f.read()

    # 检查是否已存在
    if "northbound-analysis.html" in content:
        log_info("入口链接已存在，跳过")
        return True

    # 在返回九宝日历精选的按钮后面添加
    link_html = (
        '<a href="northbound-analysis.html" '
        'style="display:inline-flex;align-items:center;gap:6px;padding:8px 20px;'
        'border-radius:20px;background:linear-gradient(135deg,#58a6ff,#1f6feb);'
        'color:#fff;text-decoration:none;font-size:14px;font-weight:600;'
        'box-shadow:0 2px 8px rgba(88,166,255,0.35);transition:transform .15s;'
        'margin-left:10px;cursor:pointer;">📊 北向分析</a>'
    )

    old = '<a href="portal.html"'
    if old in content:
        # 找到这一行结束位置，在其后面添加
        pattern = r'(<a href="portal\.html"[^>]*>[^<]*</a>)'
        new = r'\1' + link_html
        content = re.sub(pattern, new, content, count=1)

        with open(main_html_path, "w", encoding="utf-8") as f:
            f.write(content)
        log_info(f"已在北向日历页面添加入口链接: {main_html_path}")
        return True
    else:
        log_warn("未找到portal.html入口位置，无法添加链接")
        return False


# ========== 主函数 ==========

def parse_date_range(range_str: str) -> List[str]:
    if ".." in range_str:
        parts = range_str.split("..")
        start = datetime.strptime(parts[0], "%Y-%m-%d")
        end = datetime.strptime(parts[1], "%Y-%m-%d")
        dates = []
        cur = start
        while cur <= end:
            ds = cur.strftime("%Y-%m-%d")
            if is_trading_day(ds) and is_northbound_open(ds):
                dates.append(ds)
            cur += timedelta(days=1)
        return dates
    else:
        return [range_str] if is_trading_day(range_str) and is_northbound_open(range_str) else []


def main():
    parser = argparse.ArgumentParser(description="北向资金分析 — 独立页面生成")
    parser.add_argument("--date", default="", help="目标日期（YYYY-MM-DD）")
    parser.add_argument("--backfill", default="",
                        help="历史回补日期范围，如 2026-07-01..2026-07-19")
    parser.add_argument("--html", default="northbound-analysis.html",
                        help="输出HTML文件路径")
    parser.add_argument("--add-entry", default="",
                        help="在指定主页面加入口链接")
    parser.add_argument("--repo-dir", default=".", help="仓库根目录")
    args = parser.parse_args()

    repo_dir = str(Path(args.repo_dir).resolve())
    html_path = os.path.join(repo_dir, args.html) if not os.path.isabs(args.html) else args.html

    if args.add_entry:
        entry_path = os.path.join(repo_dir, args.add_entry) if not os.path.isabs(args.add_entry) else args.add_entry
        add_entry_link(entry_path)
        return

    target_dates = []
    if args.backfill:
        target_dates = parse_date_range(args.backfill)
        log_info(f"历史回补: {len(target_dates)} 个交易日")
    elif args.date:
        if is_trading_day(args.date) and is_northbound_open(args.date):
            target_dates = [args.date]
        else:
            log_warn(f"{args.date} 非交易日或北向休市，跳过")
            return
    else:
        today = datetime.now().strftime("%Y-%m-%d")
        if is_trading_day(today) and is_northbound_open(today):
            target_dates = [today]
        else:
            log_warn(f"今天({today})非交易日或北向休市，跳过")
            return

    if not target_dates:
        log_warn("没有需要处理的日期")
        return

    # 判断走全量还是增量
    # backfill 且日期数 >= 5 走全量；单日期或少日期走增量
    is_full_backfill = bool(args.backfill) and len(target_dates) >= 5

    try:
        if is_full_backfill:
            log_info(f"检测到 {len(target_dates)} 天回补，走全量生成模式")
            generate_page(html_path, target_dates)
        else:
            log_info(f"检测到 {len(target_dates)} 天更新，走增量更新模式")
            update_page_incremental(html_path, target_dates)
    except Exception as e:
        log_error(f"生成页面失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    print()
    print("=" * 60)
    print(f"✅ 北向分析页生成完成")
    print(f"📅 数据日期: {', '.join(sorted(target_dates))}")
    print(f"📄 输出文件: {html_path}")
    print("=" * 60)


if __name__ == "__main__":
    main()
