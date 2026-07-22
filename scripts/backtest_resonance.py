#!/usr/bin/env python3
"""
机游北向共振策略胜率回测脚本

功能：
  1. 对指定历史区间逐日获取机构/游资/北向三方龙虎榜净买卖数据
  2. 按资金门槛筛选共振信号（两方/三方共振）
  3. 基于腾讯前复权K线计算 T+1 / T+3 / T+5 持有周期收益率
  4. 剔除一字涨停（次日无法进场）、ST/退市股
  5. 统计总体胜率、盈亏比、期望收益、分类型/行业/逐月胜率
  6. 生成回测详情页 resonance-backtest.html + 结构化数据 data/backtest_result.json

数据来源（全部东方财富公开API + 腾讯行情K线，与现有代码风格一致）：
  - 机构净买卖：RPT_ORGANIZATION_TRADE_DETAILS
  - 游资净买卖：RPT_BILLBOARD_DAILYDETAILSBUY/SELL（剔除机构/北向席位）
  - 北向净买卖：RPT_BILLBOARD_DAILYDETAILSBUY/SELL（沪/深股通专用席位）
  - 日K线：web.ifzq.gtimg.cn（前复权）
  - 行业/基本信息：emweb.securities.eastmoney.com PC_HSF10

用法：
  python3 scripts/backtest_resonance.py
  python3 scripts/backtest_resonance.py --start 2026-01-01 --end 2026-07-21
  python3 scripts/backtest_resonance.py --cache-dir /tmp/bt_cache --refresh
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
ROOT_DIR = SCRIPT_DIR.parent

# ========== 东财API基础配置（与 update_jiyou_resonance_gha.py 风格一致） ==========

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

# 游资席位过滤
YOUZI_EXCLUDE_DEPT_KEYWORDS = [
    "沪股通", "深股通", "陆股通", "香港中央结算",
    "机构专用",
    "自然人", "中小投资者", "其他自然人", "机构投资者",
    "个人投资者", "一般法人", "国有法人", "境内非国有法人",
    "境外法人", "境内自然人", "境外自然人", "内部职工股",
    "战略投资者", "网下配售", "公募基金", "社保基金",
    "养老金", "保险资金", "企业年金", "信托",
]
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

# 北向席位
NORTHBOUND_KEYWORDS = ("沪股通专用", "深股通专用")

# 港股休市日
HK_HOLIDAYS_2026 = {"2026-07-01"}

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


# ========== 通用工具函数 ==========

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
    if not is_trading_day(date_str):
        return False
    if is_hk_holiday(date_str):
        return False
    return True


def _is_real_business_department(dept_name: str) -> bool:
    if not dept_name:
        return False
    for kw in YOUZI_EXCLUDE_DEPT_KEYWORDS:
        if kw in dept_name:
            return False
    for kw in YOUZI_INCLUDE_DEPT_KEYWORDS:
        if kw in dept_name:
            return True
    return False


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
                    EASTMONEY_API_BASE, params=params,
                    headers=EASTMONEY_HEADERS, timeout=15,
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


# ========== 机构数据 ==========

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
    return {"buy_sorted": buy_sorted, "sell_sorted": sell_sorted}


# ========== 游资数据 ==========

def get_youzi_stock_data(date_str: str) -> Dict[str, List[Dict]]:
    """获取游资净买卖数据（龙虎榜营业部明细，剔除机构+北向）"""
    print(f"  📡 [游资] 调用营业部买卖明细（剔除机构+北向）...")

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

    buy_raw = fetch_eastmoney_api(
        REPORT_BUY_DETAILS, filter_expr=filter_expr,
        sort_columns="TRADE_DATE", sort_types="-1",
        page_size=500, max_pages=5,
    )
    print(f"    买入明细原始记录数: {len(buy_raw)}")

    sell_raw = fetch_eastmoney_api(
        REPORT_SELL_DETAILS, filter_expr=filter_expr,
        sort_columns="TRADE_DATE", sort_types="-1",
        page_size=500, max_pages=5,
    )
    print(f"    卖出明细原始记录数: {len(sell_raw)}")

    stock_map = {}

    def _agg_item(item, is_buy):
        code = item.get("SECURITY_CODE", "")
        dept = item.get("OPERATEDEPT_NAME", "")
        if not code:
            return
        if not _is_real_business_department(dept):
            return
        if code not in stock_map:
            stock_map[code] = {
                "code": code, "name": name_map.get(code, code),
                "net_buy": 0.0, "buy_amt": 0.0, "sell_amt": 0.0,
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
    return {"buy_sorted": buy_sorted, "sell_sorted": sell_sorted}


# ========== 北向数据 ==========

def get_northbound_data(date_str: str) -> List[Dict]:
    """获取指定日期北向龙虎榜席位净买卖数据（按股票聚合）"""
    if not is_northbound_open(date_str):
        return []

    daily_details = fetch_eastmoney_api(
        REPORT_DAILY_DETAILS,
        filter_expr=f"(TRADE_DATE='{date_str}')",
        sort_columns="BILLBOARD_NET_AMT,TRADE_DATE,SECURITY_CODE",
        sort_types="-1,-1,1",
        page_size=200, max_pages=5,
    )
    name_map = {}
    for item in daily_details:
        code = item.get("SECURITY_CODE", "")
        name = item.get("SECURITY_NAME_ABBR", "")
        if code and name:
            name_map[code] = name

    all_rows = []
    for rpt in [REPORT_BUY_DETAILS, REPORT_SELL_DETAILS]:
        raw = fetch_eastmoney_api(
            rpt,
            filter_expr=f"(TRADE_DATE='{date_str}')",
            sort_columns="TRADE_DATE,SECURITY_CODE",
            sort_types="-1,1",
            page_size=200, max_pages=10,
        )
        all_rows.extend(raw)

    nb_rows = [
        r for r in all_rows
        if any(kw in r.get("OPERATEDEPT_NAME", "") for kw in NORTHBOUND_KEYWORDS)
    ]

    seen = set()
    unique = []
    for r in nb_rows:
        key = (r.get("SECURITY_CODE", ""), r.get("OPERATEDEPT_NAME", ""), r.get("TRADE_ID", ""))
        if key in seen:
            continue
        seen.add(key)
        unique.append(r)

    stock_map = {}
    for r in unique:
        code = r.get("SECURITY_CODE", "")
        if not code:
            continue
        buy = _safe_num(r.get("BUY"))
        sell = _safe_num(r.get("SELL"))
        net = _safe_num(r.get("NET"))
        if net == 0 and (buy != 0 or sell != 0):
            net = buy - sell
        if code not in stock_map:
            stock_map[code] = {"code": code, "name": name_map.get(code, code),
                               "buy": 0.0, "sell": 0.0, "net": 0.0}
        stock_map[code]["buy"] += buy
        stock_map[code]["sell"] += sell
        stock_map[code]["net"] += net

    stocks = list(stock_map.values())
    for s in stocks:
        s["buy_wan"] = round(s["buy"] / 10000.0, 2)
        s["sell_wan"] = round(s["sell"] / 10000.0, 2)
        s["net_wan"] = round(s["net"] / 10000.0, 2)

    stocks.sort(key=lambda x: x["net_wan"], reverse=True)
    return stocks


# ========== 配置（回测参数 ==========

DEFAULT_START = "2026-01-01"
DEFAULT_END = "2026-07-21"

THRESHOLD_INST = 2000.0
THRESHOLD_YOUZI = 1500.0
THRESHOLD_NORTH = 500.0

HOLD_PERIODS = [1, 3, 5]

# 腾讯K线接口
KLINE_API_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"

# 东财F10
F10_COMPANY_URL = "https://emweb.securities.eastmoney.com/PC_HSF10/CompanySurvey/PageAjax"


# ========== 交易日工具 ==========

def gen_trading_days(start: str, end: str) -> List[str]:
    days = []
    cur = datetime.strptime(start, "%Y-%m-%d")
    end_dt = datetime.strptime(end, "%Y-%m-%d")
    while cur <= end_dt:
        ds = cur.strftime("%Y-%m-%d")
        if is_trading_day(ds):
            days.append(ds)
        cur += timedelta(days=1)
    return days


def shift_trading_day(date_str: str, n: int) -> Optional[str]:
    if n <= 0:
        return date_str
    cur = datetime.strptime(date_str, "%Y-%m-%d")
    count = 0
    for _ in range(60):
        cur += timedelta(days=1)
        ds = cur.strftime("%Y-%m-%d")
        if is_trading_day(ds):
            count += 1
            if count == n:
                return ds
    return None


# ========== K线数据（腾讯前复权） ==========

def code_to_gtimg_prefix(code: str) -> str:
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


def fetch_kline(code: str, lmt: int = 60) -> List[Dict]:
    """
    获取某只股票前复权日K线。
    返回按日期升序。
    """
    gtimg_code = code_to_gtimg_prefix(code)
    count = max(lmt, 60)
    params = {"param": f"{gtimg_code},day,,,{count},qfq"}
    try:
        r = requests.get(
            KLINE_API_URL, params=params, timeout=10,
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"},
        )
        r.raise_for_status()
        data = r.json()
        if data.get("code") != 0 or not data.get("data"):
            return []
        stock_data = list(data["data"].values())[0]
        kline_data = stock_data.get("qfqday") or stock_data.get("day") or []
        if not kline_data:
            return []
        klines = []
        for row in kline_data:
            if len(row) < 6:
                continue
            date = row[0]
            open_p = _safe_num(row[1])
            close_p = _safe_num(row[2])
            high_p = _safe_num(row[3])
            low_p = _safe_num(row[4])
            volume = _safe_num(row[5])
            prev_close = klines[-1]["close"] if klines else None
            change_pct = 0.0
            if prev_close and prev_close > 0:
                change_pct = (close_p - prev_close) / prev_close * 100.0
            klines.append({
                "date": date, "open": open_p, "close": close_p,
                "high": high_p, "low": low_p,
                "volume": volume, "change_pct": round(change_pct, 2),
            })
        return klines
    except Exception as e:
        print(f"    ⚠️  K线获取失败 {code}: {e}")
        return []


# ========== 股票基础信息 ==========

def fetch_stock_info(code: str) -> Dict:
    sec_prefix = "SH" if code.startswith("6") else "SZ"
    try:
        r = requests.get(
            F10_COMPANY_URL, params={"code": f"{sec_prefix}{code}"},
            headers=EASTMONEY_HEADERS, timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        jbzl = data.get("jbzl", [])
        if not jbzl:
            return {"code": code, "name": code, "industry": "未分类",
                    "is_st": False, "is_delisted": False}
        info = jbzl[0]
        name = info.get("SECURITY_NAME_ABBR", code)
        industry_full = info.get("INDUSTRYCSRC1", "") or ""
        industry = industry_full.split("-")[0] if industry_full else "未分类"
        is_st = ("ST" in name) or ("*ST" in name)
        is_delisted = "退" in name
        return {
            "code": code, "name": name,
            "industry": industry if industry else "未分类",
            "is_st": is_st, "is_delisted": is_delisted,
        }
    except Exception as e:
        print(f"    ⚠️  基础信息获取失败 {code}: {e}")
        return {"code": code, "name": code, "industry": "未分类",
                "is_st": False, "is_delisted": False}


# ========== 缓存 ==========

class DataCache:
    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._stock_info_cache: Dict[str, Dict] = {}

    def _daily_file(self, date_str: str, source: str) -> Path:
        return self.cache_dir / f"{source}_{date_str}.json"

    def get_daily(self, date_str: str, source: str) -> Optional[List]:
        fp = self._daily_file(date_str, source)
        if fp.exists():
            try:
                with open(fp, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return None
        return None

    def set_daily(self, date_str: str, source: str, data: List) -> None:
        fp = self._daily_file(date_str, source)
        try:
            with open(fp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
        except Exception:
            pass

    def get_stock_info(self, code: str) -> Optional[Dict]:
        if code in self._stock_info_cache:
            return self._stock_info_cache[code]
        fp = self.cache_dir / f"stock_info_{code}.json"
        if fp.exists():
            try:
                with open(fp, "r", encoding="utf-8") as f:
                    self._stock_info_cache[code] = json.load(f)
                    return self._stock_info_cache[code]
            except Exception:
                pass
        return None

    def set_stock_info(self, code: str, info: Dict) -> None:
        self._stock_info_cache[code] = info
        fp = self.cache_dir / f"stock_info_{code}.json"
        try:
            with open(fp, "w", encoding="utf-8") as f:
                json.dump(info, f, ensure_ascii=False)
        except Exception:
            pass


# ========== 共振信号识别 ==========

def identify_resonance_signals(
    date_str: str,
    inst_data: Dict,
    youzi_data: Dict,
    northbound_data: List[Dict],
    threshold_inst: float,
    threshold_youzi: float,
    threshold_north: float,
) -> List[Dict]:
    inst_map = {s["code"]: s["net_buy_wan"] for s in inst_data.get("buy_sorted", [])
                if s["net_buy_wan"] >= threshold_inst}
    youzi_map = {s["code"]: s["net_buy_wan"] for s in youzi_data.get("buy_sorted", [])
                 if s["net_buy_wan"] >= threshold_youzi}
    north_map = {s["code"]: s["net_wan"] for s in northbound_data
                 if s["net_wan"] >= threshold_north}

    name_map = {}
    for s in inst_data.get("buy_sorted", []):
        name_map[s["code"]] = s["name"]
    for s in youzi_data.get("buy_sorted", []):
        name_map.setdefault(s["code"], s["name"])
    for s in northbound_data:
        name_map.setdefault(s["code"], s["name"])

    all_codes = set(inst_map.keys()) | set(youzi_map.keys()) | set(north_map.keys())

    signals = []
    for code in all_codes:
        inst_net = inst_map.get(code, 0.0)
        youzi_net = youzi_map.get(code, 0.0)
        north_net = north_map.get(code, 0.0)

        inst_ok = inst_net >= threshold_inst
        youzi_ok = youzi_net >= threshold_youzi
        north_ok = north_net >= threshold_north

        sides = sum([inst_ok, youzi_ok, north_ok])
        if sides < 2:
            continue

        if inst_ok and youzi_ok and north_ok:
            res_type = "triple"
        elif inst_ok and youzi_ok:
            res_type = "inst_youzi"
        elif inst_ok and north_ok:
            res_type = "inst_north"
        else:
            res_type = "youzi_north"

        total_net = inst_net + youzi_net + north_net
        signals.append({
            "date": date_str,
            "code": code,
            "name": name_map.get(code, code),
            "inst_net_wan": round(inst_net, 2),
            "youzi_net_wan": round(youzi_net, 2),
            "north_net_wan": round(north_net, 2),
            "res_type": res_type,
            "total_net_wan": round(total_net, 2),
        })

    signals.sort(key=lambda x: x["total_net_wan"], reverse=True)
    return signals


# ========== 收益率计算 ==========

def compute_signal_returns(signal: Dict, hold_periods: List[int]) -> Dict:
    code = signal["code"]
    t0 = signal["date"]

    klines = fetch_kline(code, lmt=80)
    if not klines:
        signal["error"] = "no_kline"
        return signal

    kline_map = {k["date"]: k for k in klines}

    t1 = shift_trading_day(t0, 1)
    if not t1 or t1 not in kline_map:
        signal["error"] = "no_t1"
        return signal

    t1_k = kline_map[t1]
    open_price = t1_k["open"]
    if open_price <= 0:
        signal["error"] = "invalid_open"
        return signal

    # 一字涨停判断
    is_limit_up_day = False
    if t1_k["high"] == t1_k["low"] == t1_k["open"] == t1_k["close"] and t1_k["change_pct"] >= 9.8:
        is_limit_up_day = True
    if code.startswith("3") or code.startswith("688"):
        if t1_k["high"] == t1_k["low"] == t1_k["open"] == t1_k["close"] and t1_k["change_pct"] >= 19.8:
            is_limit_up_day = True
    if code.startswith("4") or code.startswith("8"):
        if t1_k["high"] == t1_k["low"] == t1_k["open"] == t1_k["close"] and t1_k["change_pct"] >= 29.8:
            is_limit_up_day = True

    if is_limit_up_day:
        signal["error"] = "limit_up_open"
        return signal

    returns = {}
    for n in hold_periods:
        tn = shift_trading_day(t0, n)
        if not tn or tn not in kline_map:
            returns[f"T{n}"] = None
            continue
        close_n = kline_map[tn]["close"]
        if close_n <= 0:
            returns[f"T{n}"] = None
            continue
        ret_pct = (close_n - open_price) / open_price * 100.0
        returns[f"T{n}"] = round(ret_pct, 2)

    signal["entry_price"] = round(open_price, 2)
    signal["returns"] = returns
    signal["error"] = None
    return signal


# ========== 统计 ==========

def calc_stats(signals: List[Dict], period_key: str) -> Dict:
    valid = [s for s in signals if s.get("returns") and s["returns"].get(period_key) is not None]
    if not valid:
        return {
            "count": 0, "win": 0, "loss": 0, "win_rate": 0,
            "avg_return": 0, "win_avg": 0, "loss_avg": 0,
            "profit_loss_ratio": 0, "expectation": 0,
            "max_win": 0, "max_loss": 0,
        }
    rets = [s["returns"][period_key] for s in valid]
    wins = [r for r in rets if r > 0]
    losses = [r for r in rets if r <= 0]
    win_rate = len(wins) / len(rets) * 100.0
    avg_return = sum(rets) / len(rets)
    win_avg = sum(wins) / len(wins) if wins else 0
    loss_avg = sum(losses) / len(losses) if losses else 0
    pl_ratio = (win_avg / abs(loss_avg)) if losses and loss_avg != 0 else 0
    expectation = win_rate / 100 * win_avg + (1 - win_rate / 100) * loss_avg

    return {
        "count": len(valid),
        "win": len(wins),
        "loss": len(losses),
        "win_rate": round(win_rate, 2),
        "avg_return": round(avg_return, 2),
        "win_avg": round(win_avg, 2),
        "loss_avg": round(loss_avg, 2),
        "profit_loss_ratio": round(pl_ratio, 2),
        "expectation": round(expectation, 2),
        "max_win": round(max(rets), 2),
        "max_loss": round(min(rets), 2),
    }


def group_by_res_type(signals: List[Dict]) -> Dict[str, List[Dict]]:
    groups = {}
    for s in signals:
        rt = s["res_type"]
        groups.setdefault(rt, []).append(s)
    return groups


def group_by_industry(signals: List[Dict]) -> Dict[str, List[Dict]]:
    groups = {}
    for s in signals:
        ind = s.get("industry", "未分类") or "未分类"
        groups.setdefault(ind, []).append(s)
    return groups


def group_by_month(signals: List[Dict]) -> Dict[str, List[Dict]]:
    groups = {}
    for s in signals:
        ym = s["date"][:7]
        groups.setdefault(ym, []).append(s)
    return groups


# ========== 页面生成 ==========

RES_TYPE_LABELS = {
    "triple": "三方共振（机构+游资+北向）",
    "inst_youzi": "机构+游资",
    "inst_north": "机构+北向",
    "youzi_north": "游资+北向",
}
RES_TYPE_COLORS = {
    "triple": "#e888a0",
    "inst_youzi": "#58a6ff",
    "inst_north": "#3fb950",
    "youzi_north": "#d29922",
}


def build_html(result: Dict, output_path: Path) -> None:
    start = result["params"]["start"]
    end = result["params"]["end"]
    thresholds = result["params"]["thresholds"]
    overview = result["overview"]
    by_type = result["by_type"]
    by_industry = result["by_industry"]
    by_month = result["by_month"]
    signals = result["signals"]

    def pct_color(v):
        if v is None:
            return "#c9d1d9"
        return "#f85149" if v < 0 else ("#3fb950" if v > 0 else "#c9d1d9")

    def stat_card(title, value, sub="", color="#e888a0"):
        return f'''
        <div class="stat-card">
            <div class="stat-title">{title}</div>
            <div class="stat-value" style="color:{color}">{value}</div>
            <div class="stat-sub">{sub}</div>
        </div>'''

    overview_cards_html = ""
    for period in ["T1", "T3", "T5"]:
        stats = overview[period]
        label = {"T1": "T+1", "T3": "T+3", "T5": "T+5"}[period]
        overview_cards_html += f'''
        <div class="period-block">
            <div class="period-title">{label} 持有周期</div>
            <div class="stats-grid">
                {stat_card("信号总数", stats["count"], "有效样本", "#c9d1d9")}
                {stat_card("胜率", f'{stats["win_rate"]}%', f'{stats["win"]}胜 / {stats["loss"]}负',
                           "#3fb950" if stats["win_rate"] >= 50 else "#f85149")}
                {stat_card("平均收益", f'{stats["avg_return"]}%', "期望收益",
                           pct_color(stats["avg_return"]))}
                {stat_card("盈亏比", f'{stats["profit_loss_ratio"]}', f'胜均{stats["win_avg"]}% / 负均{stats["loss_avg"]}%',
                           "#e888a0" if stats["profit_loss_ratio"] >= 1 else "#f85149")}
                {stat_card("最大盈利", f'{stats["max_win"]}%', "", "#3fb950")}
                {stat_card("最大亏损", f'{stats["max_loss"]}%', "", "#f85149")}
            </div>
        </div>'''

    type_table_rows = ""
    type_order = ["triple", "inst_youzi", "inst_north", "youzi_north"]
    for rt in type_order:
        if rt not in by_type:
            continue
        t_stats = by_type[rt]
        t3 = t_stats["T3"]
        t5 = t_stats["T5"]
        color = RES_TYPE_COLORS.get(rt, "#c9d1d9")
        type_table_rows += f'''
        <tr>
            <td style="color:{color};font-weight:600">{RES_TYPE_LABELS.get(rt, rt)}</td>
            <td>{t_stats["T1"]["count"]}</td>
            <td style="color:{pct_color(t_stats["T1"]["avg_return"])}">{t_stats["T1"]["win_rate"]}%</td>
            <td style="color:{pct_color(t_stats["T1"]["avg_return"])}">{t_stats["T1"]["avg_return"]}%</td>
            <td style="color:{pct_color(t3["avg_return"])}">{t3["win_rate"]}%</td>
            <td style="color:{pct_color(t3["avg_return"])}">{t3["avg_return"]}%</td>
            <td style="color:{pct_color(t5["avg_return"])}">{t5["win_rate"]}%</td>
            <td style="color:{pct_color(t5["avg_return"])}">{t5["avg_return"]}%</td>
        </tr>'''

    industry_rows = ""
    industry_list = []
    for ind, data in by_industry.items():
        t3 = data["T3"]
        if t3["count"] >= 3:
            industry_list.append((ind, data))
    industry_list.sort(key=lambda x: x[1]["T3"]["win_rate"], reverse=True)

    for ind, data in industry_list[:20]:
        t1 = data["T1"]
        t3 = data["T3"]
        t5 = data["T5"]
        industry_rows += f'''
        <tr>
            <td>{ind}</td>
            <td>{t3["count"]}</td>
            <td style="color:{pct_color(t1["avg_return"])}">{t1["win_rate"]}%</td>
            <td style="color:{pct_color(t3["avg_return"])}">{t3["win_rate"]}%</td>
            <td style="color:{pct_color(t5["avg_return"])}">{t5["win_rate"]}%</td>
            <td style="color:{pct_color(t3["avg_return"])}">{t3["avg_return"]}%</td>
        </tr>'''

    if not industry_rows:
        industry_rows = '<tr><td colspan="6" style="text-align:center;color:#6e7681">行业样本不足</td></tr>'

    months_sorted = sorted(by_month.keys())
    month_rows = ""
    for ym in months_sorted:
        data = by_month[ym]
        t1 = data["T1"]
        t3 = data["T3"]
        t5 = data["T5"]
        month_rows += f'''
        <tr>
            <td>{ym}</td>
            <td>{t3["count"]}</td>
            <td style="color:{pct_color(t1["avg_return"])}">{t1["win_rate"]}% / {t1["avg_return"]}%</td>
            <td style="color:{pct_color(t3["avg_return"])}">{t3["win_rate"]}% / {t3["avg_return"]}%</td>
            <td style="color:{pct_color(t5["avg_return"])}">{t5["win_rate"]}% / {t5["avg_return"]}%</td>
        </tr>'''

    if not month_rows:
        month_rows = '<tr><td colspan="5" style="text-align:center;color:#6e7681">暂无数据</td></tr>'

    signal_rows = ""
    sorted_signals = sorted(signals, key=lambda x: x["date"], reverse=True)
    for s in sorted_signals:
        if s.get("error"):
            continue
        returns = s.get("returns", {})
        t1 = returns.get("T1")
        t3 = returns.get("T3")
        t5 = returns.get("T5")
        type_label = RES_TYPE_LABELS.get(s["res_type"], s["res_type"])
        type_color = RES_TYPE_COLORS.get(s["res_type"], "#c9d1d9")
        signal_rows += f'''
        <tr>
            <td>{s["date"]}</td>
            <td style="font-family:monospace">{s["code"]}</td>
            <td>{s["name"]}</td>
            <td><span class="res-tag" style="color:{type_color};border-color:{type_color}">{type_label}</span></td>
            <td style="text-align:right">{format_amount(s["inst_net_wan"])}</td>
            <td style="text-align:right">{format_amount(s["youzi_net_wan"])}</td>
            <td style="text-align:right">{format_amount(s["north_net_wan"])}</td>
            <td style="color:{pct_color(t1)}">{f"{t1}%" if t1 is not None else "-"}</td>
            <td style="color:{pct_color(t3)}">{f"{t3}%" if t3 is not None else "-"}</td>
            <td style="color:{pct_color(t5)}">{f"{t5}%" if t5 is not None else "-"}</td>
            <td>{s.get("industry", "未分类")}</td>
        </tr>'''

    total_valid = len([s for s in signals if not s.get("error")])
    total_filtered = len([s for s in signals if s.get("error")])

    html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>机游北向共振策略回测</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif;
    background: #0d1117;
    min-height: 100vh;
    padding: 20px;
    color: #c9d1d9;
}}
.container {{
    max-width: 1400px;
    margin: 0 auto;
    background: #161b22;
    border-radius: 12px;
    border: 1px solid #30363d;
    padding: 30px;
}}
.header {{
    text-align: center;
    padding: 15px 0 20px;
    border-bottom: 1px solid #30363d;
    margin-bottom: 25px;
}}
.header h1 {{
    font-size: 26px;
    font-weight: 600;
    color: #e8a0b0;
    margin-bottom: 6px;
}}
.header .subtitle {{
    color: #8b949e;
    font-size: 13px;
}}
.breadcrumb {{
    display: flex; align-items: center; justify-content: center;
    gap: 10px; margin-bottom: 12px;
    font-size: 13px; color: #8b949e;
}}
.breadcrumb a {{ color: #58a6ff; text-decoration: none; }}
.breadcrumb a:hover {{ text-decoration: underline; }}
.breadcrumb .current {{ color: #e8a0b0; font-weight: 600; }}

.params-bar {{
    display: flex; flex-wrap: wrap; gap: 20px; justify-content: center;
    background: #21262d; border: 1px solid #30363d;
    border-radius: 8px; padding: 12px 20px; margin-bottom: 25px;
    font-size: 13px;
}}
.params-bar .param-item {{ display: flex; align-items: center; gap: 6px; }}
.params-bar .param-label {{ color: #8b949e; }}
.params-bar .param-value {{ color: #e8a0b0; font-weight: 600; }}

.section {{ margin-bottom: 30px; }}
.section-title {{
    font-size: 18px;
    font-weight: 600;
    color: #e8a0b0;
    margin-bottom: 15px;
    padding-bottom: 8px;
    border-bottom: 1px solid #30363d;
    display: flex;
    align-items: center;
    gap: 8px;
}}
.section-title::before {{
    content: ""; display: inline-block;
    width: 4px; height: 18px;
    background: #e888a0;
    border-radius: 2px;
}}

.period-block {{
    background: #21262d;
    border: 1px solid #30363d;
    border-radius: 8px;
    padding: 18px;
    margin-bottom: 18px;
}}
.period-title {{
    font-size: 15px;
    font-weight: 600;
    color: #c9d1d9;
    margin-bottom: 12px;
}}
.stats-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
    gap: 12px;
}}
.stat-card {{
    background: #161b22;
    border: 1px solid #30363d;
    border-radius: 6px;
    padding: 12px;
    text-align: center;
}}
.stat-title {{
    font-size: 12px;
    color: #8b949e;
    margin-bottom: 6px;
}}
.stat-value {{
    font-size: 22px;
    font-weight: 700;
    margin-bottom: 4px;
}}
.stat-sub {{
    font-size: 11px;
    color: #6e7681;
}}

.table-wrap {{
    overflow-x: auto;
    border: 1px solid #30363d;
    border-radius: 8px;
    background: #21262d;
}}
table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
}}
th {{
    background: #161b22;
    color: #e8a0b0;
    padding: 10px 12px;
    text-align: left;
    font-weight: 600;
    border-bottom: 1px solid #30363d;
    white-space: nowrap;
    position: sticky;
    top: 0;
}}
td {{
    padding: 8px 12px;
    border-bottom: 1px solid #30363d;
    white-space: nowrap;
}}
tr:hover {{ background: #161b22; }}
tr:last-child td {{ border-bottom: none; }}

.res-tag {{
    display: inline-block;
    padding: 2px 8px;
    border: 1px solid;
    border-radius: 10px;
    font-size: 11px;
    font-weight: 500;
}}

.filter-bar {{
    display: flex;
    gap: 10px;
    margin-bottom: 12px;
    flex-wrap: wrap;
}}
.filter-btn {{
    padding: 6px 14px;
    border: 1px solid #30363d;
    border-radius: 6px;
    background: #21262d;
    color: #8b949e;
    cursor: pointer;
    font-size: 12px;
    transition: all 0.2s;
}}
.filter-btn:hover {{ border-color: #e888a0; color: #e8a0b0; }}
.filter-btn.active {{
    background: #e888a022;
    border-color: #e888a0;
    color: #e8a0b0;
}}

.footer {{
    text-align: center;
    color: #6e7681;
    font-size: 12px;
    padding-top: 20px;
    border-top: 1px solid #30363d;
    margin-top: 20px;
}}
</style>
</head>
<body>
<div class="container">

<div class="breadcrumb">
    <a href="index.html">首页</a>
    <span>›</span>
    <a href="jiyou-resonance.html">机游共振</a>
    <span>›</span>
    <span class="current">策略回测</span>
</div>

<div class="header">
    <h1>📊 机游北向共振策略胜率回测</h1>
    <div class="subtitle">东方财富龙虎榜数据 · 前复权K线 · 次日开盘进场</div>
</div>

<div class="params-bar">
    <div class="param-item">
        <span class="param-label">回测区间:</span>
        <span class="param-value">{start} ~ {end}</span>
    </div>
    <div class="param-item">
        <span class="param-label">机构门槛:</span>
        <span class="param-value">≥{format_amount(thresholds["inst"])}</span>
    </div>
    <div class="param-item">
        <span class="param-label">游资门槛:</span>
        <span class="param-value">≥{format_amount(thresholds["youzi"])}</span>
    </div>
    <div class="param-item">
        <span class="param-label">北向门槛:</span>
        <span class="param-value">≥{format_amount(thresholds["north"])}</span>
    </div>
    <div class="param-item">
        <span class="param-label">有效信号:</span>
        <span class="param-value">{total_valid} 只</span>
    </div>
    <div class="param-item">
        <span class="param-label">剔除:</span>
        <span class="param-value">{total_filtered} 只</span>
    </div>
</div>

<!-- 总体概览 -->
<div class="section">
    <div class="section-title">总体表现</div>
    {overview_cards_html}
</div>

<!-- 分类型对比 -->
<div class="section">
    <div class="section-title">分共振类型对比</div>
    <div class="table-wrap">
    <table>
        <thead>
            <tr>
                <th>共振类型</th>
                <th>样本数</th>
                <th>T+1 胜率</th>
                <th>T+1 均收益</th>
                <th>T+3 胜率</th>
                <th>T+3 均收益</th>
                <th>T+5 胜率</th>
                <th>T+5 均收益</th>
            </tr>
        </thead>
        <tbody>
            {type_table_rows}
        </tbody>
    </table>
    </div>
</div>

<!-- 行业胜率 -->
<div class="section">
    <div class="section-title">行业胜率排行（T+3 胜率排序，样本≥3）</div>
    <div class="table-wrap">
    <table>
        <thead>
            <tr>
                <th>行业</th>
                <th>样本数</th>
                <th>T+1 胜率</th>
                <th>T+3 胜率</th>
                <th>T+5 胜率</th>
                <th>T+3 均收益</th>
            </tr>
        </thead>
        <tbody>
            {industry_rows}
        </tbody>
    </table>
    </div>
</div>

<!-- 逐月胜率 -->
<div class="section">
    <div class="section-title">逐月胜率</div>
    <div class="table-wrap">
    <table>
        <thead>
            <tr>
                <th>月份</th>
                <th>信号数</th>
                <th>T+1（胜率/均收益）</th>
                <th>T+3（胜率/均收益）</th>
                <th>T+5（胜率/均收益）</th>
            </tr>
        </thead>
        <tbody>
            {month_rows}
        </tbody>
    </table>
    </div>
</div>

<!-- 信号列表 -->
<div class="section">
    <div class="section-title">信号明细（共 {total_valid} 条）</div>
    <div class="filter-bar">
        <button class="filter-btn active" onclick="filterTable('all', this)">全部</button>
        <button class="filter-btn" onclick="filterTable('triple', this)">三方共振</button>
        <button class="filter-btn" onclick="filterTable('inst_youzi', this)">机构+游资</button>
        <button class="filter-btn" onclick="filterTable('inst_north', this)">机构+北向</button>
        <button class="filter-btn" onclick="filterTable('youzi_north', this)">游资+北向</button>
    </div>
    <div class="table-wrap">
    <table id="signalTable">
        <thead>
            <tr>
                <th>信号日</th>
                <th>代码</th>
                <th>名称</th>
                <th>共振类型</th>
                <th style="text-align:right">机构净买</th>
                <th style="text-align:right">游资净买</th>
                <th style="text-align:right">北向净买</th>
                <th>T+1</th>
                <th>T+3</th>
                <th>T+5</th>
                <th>行业</th>
            </tr>
        </thead>
        <tbody>
            {signal_rows}
        </tbody>
    </table>
    </div>
</div>

<div class="footer">
    <p>数据来源：东方财富龙虎榜官方API + 腾讯行情K线接口 · 前复权</p>
    <p>回测说明：T日收盘后产生信号 · T+1开盘价进场 · 第N个交易日收盘价卖出 · 已剔除一字涨停/ST/退市</p>
</div>

</div>

<script>
function filterTable(type, btn) {{
    var rows = document.querySelectorAll('#signalTable tbody tr');
    document.querySelectorAll('.filter-btn').forEach(function(b) {{ b.classList.remove('active'); }});
    btn.classList.add('active');
    rows.forEach(function(row) {{
        var cell = row.querySelector('td:nth-child(4) .res-tag');
        if (!cell) return;
        var text = cell.textContent;
        var show = false;
        if (type === 'all') {{ show = true; }}
        else if (type === 'triple') {{ show = text.indexOf('三方') >= 0; }}
        else if (type === 'inst_youzi') {{ show = text.indexOf('机构+游资') >= 0 && text.indexOf('三方') < 0; }}
        else if (type === 'inst_north') {{ show = text.indexOf('机构+北向') >= 0 && text.indexOf('三方') < 0; }}
        else if (type === 'youzi_north') {{ show = text.indexOf('游资+北向') >= 0 && text.indexOf('三方') < 0; }}
        row.style.display = show ? '' : 'none';
    }});
}}
</script>
</body>
</html>'''

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"✅ 回测详情页已生成: {output_path}")


# ========== 主流程 ==========

def run_backtest(args) -> Dict:
    start_date = args.start
    end_date = args.end

    cache_dir = Path(args.cache_dir) if args.cache_dir else ROOT_DIR / ".backtest_cache"
    cache = DataCache(cache_dir)
    if args.refresh:
        import shutil
        if cache_dir.exists():
            shutil.rmtree(cache_dir)
        cache = DataCache(cache_dir)

    trading_days = gen_trading_days(start_date, end_date)
    print(f"📅 回测区间: {start_date} ~ {end_date}")
    print(f"📊 交易日数: {len(trading_days)}")
    print(f"🎯 资金门槛: 机构≥{THRESHOLD_INST}万, 游资≥{THRESHOLD_YOUZI}万, 北向≥{THRESHOLD_NORTH}万")
    print(f"⏱️  持有周期: {', '.join(f'T+{n}' for n in HOLD_PERIODS)}")
    print()

    all_signals = []

    for i, date_str in enumerate(trading_days):
        print(f"[{i+1}/{len(trading_days)}] {date_str}")

        inst_cache = cache.get_daily(date_str, "inst")
        if inst_cache is not None:
            inst_data = {"buy_sorted": inst_cache, "sell_sorted": []}
        else:
            inst_result = get_institution_data(date_str)
            inst_data = inst_result
            cache.set_daily(date_str, "inst", inst_result["buy_sorted"])

        youzi_cache = cache.get_daily(date_str, "youzi")
        if youzi_cache is not None:
            youzi_data = {"buy_sorted": youzi_cache, "sell_sorted": []}
        else:
            youzi_result = get_youzi_stock_data(date_str)
            youzi_data = youzi_result
            cache.set_daily(date_str, "youzi", youzi_result["buy_sorted"])

        north_cache = cache.get_daily(date_str, "north")
        if north_cache is not None:
            north_stocks = north_cache
        else:
            north_stocks = get_northbound_data(date_str)
            cache.set_daily(date_str, "north", north_stocks)

        day_signals = identify_resonance_signals(
            date_str, inst_data, youzi_data, north_stocks,
            THRESHOLD_INST, THRESHOLD_YOUZI, THRESHOLD_NORTH,
        )
        print(f"  🎯 共振信号: {len(day_signals)} 条")
        if day_signals:
            for s in day_signals[:5]:
                type_label = RES_TYPE_LABELS.get(s["res_type"], s["res_type"])
                print(f"    - {s['name']}({s['code']}) [{type_label}] 总净买{format_amount(s['total_net_wan'])}")
            if len(day_signals) > 5:
                print(f"    ... 还有 {len(day_signals)-5} 条")

        all_signals.extend(day_signals)
        time.sleep(0.1)

    print(f"\n{'='*60}")
    print(f"📊 共振信号总数: {len(all_signals)} 条")

    print(f"\n🔍 计算收益率与基本面信息...")
    all_codes = list({s["code"] for s in all_signals})
    print(f"  涉及股票数: {len(all_codes)}")

    info_map = {}
    for idx, code in enumerate(all_codes):
        cached = cache.get_stock_info(code)
        if cached:
            info_map[code] = cached
        else:
            if idx % 20 == 0:
                print(f"  基础信息进度: {idx}/{len(all_codes)}")
            info = fetch_stock_info(code)
            info_map[code] = info
            cache.set_stock_info(code, info)
            time.sleep(0.05)

    valid_signals = []
    for idx, sig in enumerate(all_signals):
        code = sig["code"]
        info = info_map.get(code, {})

        if info.get("is_st") or info.get("is_delisted"):
            sig["error"] = "st_delisted"
            sig["industry"] = info.get("industry", "未分类")
            continue

        sig["industry"] = info.get("industry", "未分类")
        sig["name"] = info.get("name", sig["name"])

        if idx % 50 == 0:
            print(f"  收益率计算进度: {idx}/{len(all_signals)}")

        sig = compute_signal_returns(sig, HOLD_PERIODS)
        valid_signals.append(sig)
        time.sleep(0.02)

    print(f"  有效信号（可交易）: {len([s for s in valid_signals if not s.get('error')])}")

    print(f"\n📈 生成统计报告...")

    overview = {}
    for period in ["T1", "T3", "T5"]:
        overview[period] = calc_stats(valid_signals, period)

    by_type_groups = group_by_res_type(valid_signals)
    by_type = {}
    for rt, sigs in by_type_groups.items():
        by_type[rt] = {}
        for period in ["T1", "T3", "T5"]:
            by_type[rt][period] = calc_stats(sigs, period)

    by_industry_groups = group_by_industry(valid_signals)
    by_industry = {}
    for ind, sigs in by_industry_groups.items():
        by_industry[ind] = {}
        for period in ["T1", "T3", "T5"]:
            by_industry[ind][period] = calc_stats(sigs, period)

    by_month_groups = group_by_month(valid_signals)
    by_month = {}
    for ym, sigs in by_month_groups.items():
        by_month[ym] = {}
        for period in ["T1", "T3", "T5"]:
            by_month[ym][period] = calc_stats(sigs, period)

    result = {
        "params": {
            "start": start_date,
            "end": end_date,
            "thresholds": {
                "inst": THRESHOLD_INST,
                "youzi": THRESHOLD_YOUZI,
                "north": THRESHOLD_NORTH,
            },
            "hold_periods": HOLD_PERIODS,
        },
        "overview": overview,
        "by_type": by_type,
        "by_industry": by_industry,
        "by_month": by_month,
        "signals": valid_signals,
        "generate_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

    return result


def main():
    parser = argparse.ArgumentParser(description="机游北向共振策略回测")
    parser.add_argument("--start", default=DEFAULT_START, help="回测起始日期 YYYY-MM-DD")
    parser.add_argument("--end", default=DEFAULT_END, help="回测结束日期 YYYY-MM-DD")
    parser.add_argument("--cache-dir", default=None, help="缓存目录")
    parser.add_argument("--refresh", action="store_true", help="刷新缓存")
    parser.add_argument("--html", default=None, help="输出 HTML 路径")
    parser.add_argument("--json", default=None, help="输出 JSON 路径")
    args = parser.parse_args()

    result = run_backtest(args)

    json_path = Path(args.json) if args.json else (ROOT_DIR / "data" / "backtest_result.json")
    json_path.parent.mkdir(parents=True, exist_ok=True)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"✅ 结构化数据已保存: {json_path}")

    html_path = Path(args.html) if args.html else (ROOT_DIR / "resonance-backtest.html")
    build_html(result, html_path)

    print(f"\n{'='*60}")
    print("📊 回测概览")
    print(f"{'='*60}")
    for period in ["T1", "T3", "T5"]:
        s = result["overview"][period]
        print(f"  {period}: 样本{s['count']} | 胜率{s['win_rate']}% | 均收益{s['avg_return']}% | 盈亏比{s['profit_loss_ratio']}")
    print(f"\n✅ 回测完成！")


if __name__ == "__main__":
    main()
