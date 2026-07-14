#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
东方财富龙虎榜 API 接口测试脚本

数据源：东方财富数据中心 (https://data.eastmoney.com/

接口列表：
1. 机构买卖每日统计 - RPT_ORGANIZATION_TRADE_DETAILS
2. 每日活跃营业部(游资) - RPT_OPERATEDEPT_ACTIVE
3. 龙虎榜个股明细 - RPT_DAILYBILLBOARD_DETAILSNEW

功能：打印最近一个交易日的机构净买入TOP10 + 知名游资买卖情况
"""

import sys
import requests
from datetime import datetime, timedelta


BASE_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://data.eastmoney.com/",
    "Accept": "application/json, text/plain, */*",
}


# 知名游资营业部名单（常见活跃席位，用于筛选）
FAMOUS_HOT_MONEY = [
    "东方财富证券股份有限公司拉萨金融城南环路证券营业部",
    "东方财富证券股份有限公司拉萨东环路第二证券营业部",
    "东方财富证券股份有限公司拉萨团结路第一证券营业部",
    "东方财富证券股份有限公司拉萨团结路第二证券营业部",
    "东方财富证券股份有限公司拉萨东环路第一证券营业部",
    "东方财富证券股份有限公司山南香曲东路证券营业部",
    "中信证券股份有限公司上海溧阳路证券营业部",
    "中信证券股份有限公司深圳深南中路中信大厦证券营业部",
    "国盛证券有限责任公司宁波桑田路证券营业部",
    "华鑫证券有限责任公司上海陆家嘴证券营业部",
    "华泰证券股份有限公司深圳益田路荣超商务中心证券营业部",
    "国泰海通证券股份有限公司上虞市民大道证券营业部",
    "广发证券股份有限公司南京汉中路证券营业部",
    "甬兴证券有限公司宁波和源路证券营业部",
    "国信证券股份有限公司浙江互联网分公司",
    "开源证券股份有限公司西安太华路证券营业部",
    "开源证券股份有限公司西安西大街证券营业部",
]


def fetch_api(report_name, sort_columns, sort_types, page_size=50, page=1,
              filter_expr=None, columns="ALL"):
    """通用东方财富 datacenter API 调用"""
    params = {
        "sortColumns": sort_columns,
        "sortTypes": sort_types,
        "pageSize": str(page_size),
        "pageNumber": str(page),
        "reportName": report_name,
        "columns": columns,
        "source": "WEB",
        "client": "WEB",
    }
    if filter_expr:
        params["filter"] = filter_expr
    try:
        r = requests.get(BASE_URL, params=params, headers=HEADERS, timeout=30)
        r.raise_for_status()
        data = r.json()
        if not data.get("success"):
            print(f"  [ERROR] API 返回失败: {data}")
            return []
        return data.get("result", {}).get("data", [])
    except Exception as e:
        print(f"  [ERROR] 请求异常: {e}")
        return []


def get_latest_trade_date(days_back=5):
    """向前探测最近一个有龙虎榜数据的交易日"""
    today = datetime.now()
    for i in range(days_back):
        d = (today - timedelta(days=i)).strftime("%Y-%m-%d")
        rows = fetch_api(
            "RPT_ORGANIZATION_TRADE_DETAILS",
            "TRADE_DATE,NET_BUY_AMT,SECURITY_CODE",
            "-1,-1,1",
            page_size=1,
            filter_expr=f"(TRADE_DATE='{d}')",
        )
        if rows:
            return d
    return (today - timedelta(days=1)).strftime("%Y-%m-%d")


def get_institution_top10(trade_date):
    """获取指定交易日机构净买入TOP10"""
    rows = fetch_api(
        "RPT_ORGANIZATION_TRADE_DETAILS",
        "NET_BUY_AMT,TRADE_DATE,SECURITY_CODE",
        "-1,-1,1",
        page_size=10,
        filter_expr=f"(TRADE_DATE='{trade_date}')",
    )
    return rows


def get_hot_money_trades(trade_date, top_n=20):
    """获取指定交易日活跃营业部交易情况，筛选知名游资"""
    rows = fetch_api(
        "RPT_OPERATEDEPT_ACTIVE",
        "TOTAL_NETAMT,ONLIST_DATE,OPERATEDEPT_CODE",
        "-1,-1,1",
        page_size=200,
        filter_expr=f"(ONLIST_DATE='{trade_date}')",
    )
    # 筛选知名游资
    famous = [r for r in rows if r.get("OPERATEDEPT_NAME") in FAMOUS_HOT_MONEY]
    # 如果知名游资不足，补充前若干名
    if len(famous) < top_n:
        famous = rows[:top_n]
    return famous[:top_n]


def format_amount(yuan):
    """格式化金额为万/亿"""
    if yuan is None:
        return "-"
    yuan = float(yuan)
    abs_y = abs(yuan)
    if abs_y >= 1e8:
        return f"{yuan/1e8:+.2f}亿"
    elif abs_y >= 1e4:
        return f"{yuan/1e4:+.2f}万"
    else:
        return f"{yuan:+.0f}元"


def main():
    print("=" * 70)
    print("  东方财富龙虎榜 API 接口测试")
    print("=" * 70)

    # 探测最近交易日
    trade_date = get_latest_trade_date()
    print(f"\n📅 最近交易日: {trade_date}\n")

    # ---- 1. 机构买卖每日统计 ----
    print("-" * 70)
    print("【1】机构买卖每日统计 API")
    print("-" * 70)
    print("  URL: " + BASE_URL)
    print("  Method: GET")
    print("  reportName: RPT_ORGANIZATION_TRADE_DETAILS")
    print("  必要参数: sortColumns, sortTypes, pageSize, pageNumber, reportName, columns, filter")
    print("  返回字段: SECURITY_CODE, SECURITY_NAME_ABBR, TRADE_DATE,")
    print("            BUY_TIMES(买入机构数), SELL_TIMES(卖出机构数),")
    print("            BUY_AMT, SELL_AMT, NET_BUY_AMT,")
    print("            CLOSE_PRICE, CHANGE_RATE, ...")
    print()

    inst_rows = get_institution_top10(trade_date)
    print(f"  机构净买入 TOP10 ({trade_date}):")
    print(f"  {'排名':<4} {'代码':<8} {'名称':<12} {'净买入额':<14} {'买机构':<6} {'卖机构':<6} {'涨跌幅':<8}")
    print("  " + "-" * 62)
    for i, r in enumerate(inst_rows[:10], 1):
        name = r.get("SECURITY_NAME_ABBR", "")
        code = r.get("SECURITY_CODE", "")
        net = r.get("NET_BUY_AMT", 0)
        buy_t = r.get("BUY_TIMES", 0)
        sell_t = r.get("SELL_TIMES", 0)
        chg = r.get("CHANGE_RATE", 0)
        print(f"  {i:<4} {code:<8} {name:<12} {format_amount(net):<14} {buy_t:<6} {sell_t:<6} {chg:>6.2f}%")

    # ---- 2. 每日活跃营业部 ----
    print()
    print("-" * 70)
    print("【2】每日活跃营业部/游资数据 API")
    print("-" * 70)
    print("  URL: " + BASE_URL)
    print("  Method: GET")
    print("  reportName: RPT_OPERATEDEPT_ACTIVE")
    print("  必要参数: sortColumns, sortTypes, pageSize, pageNumber, reportName, columns, filter(ONLIST_DATE)")
    print("  返回字段: OPERATEDEPT_NAME(营业部名称), ONLIST_DATE(上榜日),")
    print("            BUYER_APPEAR_NUM(买入个股数), SELLER_APPEAR_NUM(卖出个股数),")
    print("            TOTAL_BUYAMT, TOTAL_SELLAMT, TOTAL_NETAMT,")
    print("            BUY_STOCK(买入股票列表), OPERATEDEPT_CODE")
    print()

    yyb_rows = get_hot_money_trades(trade_date, top_n=15)
    print(f"  知名游资/活跃营业部买卖情况 ({trade_date}):")
    print(f"  {'排名':<4} {'营业部名称':<40} {'净买入额':<14} {'买股数':<6} {'卖股数':<6}")
    print("  " + "-" * 72)
    for i, r in enumerate(yyb_rows, 1):
        name = r.get("OPERATEDEPT_NAME", "")
        disp_name = name[:38] + "..." if len(name) > 38 else name
        net = r.get("TOTAL_NETAMT", 0)
        buy_n = r.get("BUYER_APPEAR_NUM", 0)
        sell_n = r.get("SELLER_APPEAR_NUM", 0)
        print(f"  {i:<4} {disp_name:<40} {format_amount(net):<14} {buy_n:<6} {sell_n:<6}")

    # ---- 3. 龙虎榜个股明细 ----
    print()
    print("-" * 70)
    print("【3】龙虎榜个股明细 API")
    print("-" * 70)
    print("  URL: " + BASE_URL)
    print("  Method: GET")
    print("  reportName: RPT_DAILYBILLBOARD_DETAILSNEW")
    print("  必要参数: sortColumns, sortTypes, pageSize, pageNumber, reportName, columns, filter(TRADE_DATE)")
    print("  返回字段: SECURITY_CODE, SECURITY_NAME_ABBR, TRADE_DATE,")
    print("            BILLBOARD_NET_AMT(龙虎榜净买额), BILLBOARD_BUY_AMT,")
    print("            BILLBOARD_SELL_AMT, BILLBOARD_DEAL_AMT,")
    print("            EXPLAIN(解读/上榜原因), CLOSE_PRICE, CHANGE_RATE,")
    print("            TURNOVERRATE(换手率), FREE_MARKET_CAP(流通市值), ...")
    print()

    detail_rows = fetch_api(
        "RPT_DAILYBILLBOARD_DETAILSNEW",
        "BILLBOARD_NET_AMT,TRADE_DATE,SECURITY_CODE",
        "-1,-1,1",
        page_size=10,
        filter_expr=f"(TRADE_DATE='{trade_date}')",
    )
    print(f"  龙虎榜净买额 TOP10 ({trade_date}):")
    print(f"  {'排名':<4} {'代码':<8} {'名称':<12} {'净买额':<14} {'涨跌幅':<8} {'上榜原因':<20}")
    print("  " + "-" * 70)
    for i, r in enumerate(detail_rows[:10], 1):
        name = r.get("SECURITY_NAME_ABBR", "")
        code = r.get("SECURITY_CODE", "")
        net = r.get("BILLBOARD_NET_AMT", 0)
        chg = r.get("CHANGE_RATE", 0)
        explain = r.get("EXPLAIN", "") or r.get("EXPLANATION", "")
        explain = (explain[:18] + "...") if len(explain) > 18 else explain
        print(f"  {i:<4} {code:<8} {name:<12} {format_amount(net):<14} {chg:>6.2f}%  {explain:<20}")

    print()
    print("=" * 70)
    print("  测试完成")
    print("=" * 70)


if __name__ == "__main__":
    main()
