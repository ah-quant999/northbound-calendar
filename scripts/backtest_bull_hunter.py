#!/usr/bin/env python3
"""
大牛股猎手策略历史回测脚本

回测三类信号：
  1. 新赛道发现（行业级） — 北向周度净买入TOP行业
  2. 早期信号雷达（个股） — 北向连续加仓早期 + 持仓变动榜 + 游资启动题材
  3. 核心共振标的（个股） — 机构+北向强共振的高端制造标的

持有期：5 / 10 / 20 个交易日
统计指标：胜率、平均收益、最大收益、最大亏损、盈亏比、样本数
基准：沪深300同期收益

数据源：
  - 机游信号：jiyou-signal-analysis.html 中的 signalData
  - 北向分析：northbound-analysis.html 中的 nbAnalysis + nbDailyData
  - 行业分类：data/stock_industry.json
  - 行情K线：腾讯前复权日K API
  - 沪深300：腾讯 sh000300

回测方法：
  - 逐日扫描历史数据，调用与 daily_insight.compute_bull_hunter 一致的信号筛选逻辑
  - 以信号日收盘价买入，持有 N 个交易日后卖出
  - 行业级信号用同行业样本股的平均收益衡量（前5名成分股等权）
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
import socket

# 根因修复：requests 的 timeout 不覆盖 DNS 解析(getaddrinfo)。
# CI runner DNS 中途不可达时 getaddrinfo 会无限挂起，导致整轮回测卡死。
# 给所有 socket 操作（含 DNS）设 20s 硬上限。
socket.setdefaulttimeout(20)

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DIR = SCRIPT_DIR.parent
DATA_DIR = ROOT_DIR / "data"

# ========== 常量配置 ==========

KLINE_API_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"

HIGH_END_MFG_KEYWORDS = (
    "科技", "电子", "半导体", "芯片", "集成", "光电", "光", "光伏",
    "新能", "锂电", "电池", "汽车", "智能", "机器人", "自动化",
    "装备", "制造", "精密", "数控", "工业", "航天", "军工", "航空",
    "通信", "5G", "AI", "算力", "数据", "信息", "软件",
)

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

HS300_CODE = "sh000300"


# ========== 工具函数 ==========

def _safe_num(v) -> float:
    if v is None:
        return 0.0
    try:
        return float(v)
    except (ValueError, TypeError):
        return 0.0


def log_info(msg: str) -> None:
    print(f"[INFO] {msg}")


def log_warn(msg: str) -> None:
    print(f"[WARN] {msg}", file=sys.stderr)


def log_error(msg: str) -> None:
    print(f"[ERROR] {msg}", file=sys.stderr)


def is_trading_day(date_str: str) -> bool:
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    if dt.weekday() >= 5:
        return False
    if date_str in A_STOCK_HOLIDAYS_2026:
        return False
    return True


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


def shift_trading_day(date_str: str, n: int, trading_day_list: List[str]) -> Optional[str]:
    try:
        idx = trading_day_list.index(date_str)
    except ValueError:
        return None
    target = idx + n
    if target >= len(trading_day_list):
        return None
    return trading_day_list[target]


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


# ========== K线数据 ==========

def fetch_kline(code: str, count: int = 250) -> List[Dict]:
    """获取前复权日K线，按日期升序"""
    if code.startswith("sh") or code.startswith("sz") or code.startswith("bj"):
        gtimg_code = code
    else:
        gtimg_code = code_to_gtimg_prefix(code)
    count = max(count, 120)
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
        prev_close = None
        for row in kline_data:
            if len(row) < 6:
                continue
            date = row[0]
            close_p = _safe_num(row[2])
            change_pct = 0.0
            if prev_close and prev_close > 0:
                change_pct = (close_p - prev_close) / prev_close * 100.0
            klines.append({
                "date": date,
                "close": close_p,
                "change_pct": round(change_pct, 2),
            })
            prev_close = close_p
        return klines
    except Exception as e:
        log_warn(f"K线获取失败 {code}: {e}")
        return []


# ========== HTML数据提取 ==========

def extract_json_var(html_content: str, var_name: str):
    """从HTML的<script>中提取JS变量值并解析为JSON"""
    pattern = rf"{re.escape(var_name)}\s*=\s*(\{{.*?\}})\s*;"
    m = re.search(pattern, html_content, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return None


def load_html(path: str) -> str:
    if not os.path.isfile(path):
        return ""
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read()


# ========== 行业数据 ==========

def load_industry_map(path: str) -> Dict[str, str]:
    if not os.path.isfile(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        return {k: v for k, v in data.items()}
    return {}


# ========== 信号计算（与 daily_insight.compute_bull_hunter 逻辑一致） ==========

def compute_sector_signals(nb_week: Dict, nb_month: Dict,
                           top_n: int = 5, abs_threshold_wan: float = 50000) -> List[Dict]:
    """
    新赛道发现 — 行业级信号
    返回: [{"industry": "...", "week_net": float, "month_net": float, "tag": "..."}]
    """
    week_industry = {
        x["industry"]: x["net_buy_wan"]
        for x in nb_week.get("industry_trend", {}).get("top_buy", [])
        if x.get("industry") and x["industry"] != "未分类"
    }
    month_industry = {
        x["industry"]: x["net_buy_wan"]
        for x in nb_month.get("industry_trend", {}).get("top_buy", [])
        if x.get("industry") and x["industry"] != "未分类"
    }

    sectors = []
    for ind, w_val in week_industry.items():
        if w_val <= 0 or ind == "未分类":
            continue
        m_val = month_industry.get(ind, 0)
        if m_val > 0:
            ratio = w_val / m_val
        else:
            ratio = 1.0
        if w_val >= abs_threshold_wan:
            if ratio >= 0.8:
                tag = "加速流入"
            elif ratio >= 0.5:
                tag = "持续加仓"
            else:
                tag = "稳步布局"
            sectors.append({
                "industry": ind,
                "week_net": w_val,
                "month_net": m_val,
                "tag": tag,
            })

    sectors.sort(key=lambda x: x["week_net"], reverse=True)
    return sectors[:top_n]


def compute_early_signals(nb_week: Dict, signal_day: Dict,
                          industry_map: Dict[str, str]) -> List[Dict]:
    """
    早期信号雷达 — 个股级
    逻辑与 daily_insight 保持一致（但去除数量限制，回测用全量）
    """
    continuous_buy = nb_week.get("continuous_buy", [])
    hc_top = nb_week.get("holding_change", {}).get("top_buy", [])

    signals = []

    # 2a: 连续加仓早期（2-3天）+ 金额≥2亿
    for s in continuous_buy:
        days = s.get("streak_days", 0)
        total = s.get("total_net_wan", 0)
        name = s.get("name", "")
        if 2 <= days <= 3 and total >= 20000:
            is_high_end = any(kw in name for kw in HIGH_END_MFG_KEYWORDS)
            score = total * (1.5 if is_high_end else 1.0)
            signals.append({
                "code": s["code"],
                "name": name,
                "signal_type": "北向连加",
                "signal_detail": f"北向连加{days}天",
                "amount_wan": total,
                "industry": industry_map.get(s["code"], ""),
                "score": score,
            })

    # 2b: 持仓变动榜前列（≥5亿）
    for s in hc_top[:10]:
        name = s.get("name", "")
        net = s.get("net_wan", 0)
        if net >= 50000:
            if not any(e["code"] == s["code"] for e in signals):
                is_high_end = any(kw in name for kw in HIGH_END_MFG_KEYWORDS)
                signals.append({
                    "code": s["code"],
                    "name": name,
                    "signal_type": "持仓大增",
                    "signal_detail": "周度持仓大增",
                    "amount_wan": net,
                    "industry": industry_map.get(s["code"], ""),
                    "score": net * (1.5 if is_high_end else 1.0),
                })

    # 2c: 机构卖游资买（游资启动题材） + 高端制造
    basic = (signal_day or {}).get("basic_signals", {})
    inst_sell_ybuy = basic.get("inst_sell_youzi_buy", [])
    for s in inst_sell_ybuy[:5]:
        name = s.get("name", "")
        youzi = s.get("youzi_net_wan", 0)
        is_high_end = any(kw in name for kw in HIGH_END_MFG_KEYWORDS)
        if youzi >= 10000 and is_high_end:
            if not any(e["code"] == s["code"] for e in signals):
                signals.append({
                    "code": s["code"],
                    "name": name,
                    "signal_type": "游资启动",
                    "signal_detail": "游资启动题材",
                    "amount_wan": youzi,
                    "industry": industry_map.get(s["code"], ""),
                    "score": youzi * 1.2,
                })

    signals.sort(key=lambda x: x["score"], reverse=True)
    return signals  # 回测用全量，不限制数量


def compute_core_targets(nb_week: Dict, industry_map: Dict[str, str]) -> List[Dict]:
    """
    核心共振标的 — 个股级
    机构+北向共振，高端制造优先
    """
    nb_resonance = nb_week.get("resonance", [])
    targets = []
    for r in nb_resonance:
        name = r.get("name", "")
        is_high_end = any(kw in name for kw in HIGH_END_MFG_KEYWORDS)
        if not is_high_end:
            continue
        nb_net = r.get("nb_net_wan", 0)
        inst_net = r.get("inst_net_wan", 0)
        strength = r.get("resonance_strength", 0)
        if nb_net >= 3000 and inst_net >= 5000:
            if nb_net >= 5000 and inst_net >= 10000:
                res_type = "三方共振"
            else:
                res_type = "机构+北向"
            targets.append({
                "code": r["code"],
                "name": name,
                "signal_type": "核心共振",
                "signal_detail": res_type,
                "amount_wan": strength,
                "industry": industry_map.get(r["code"], ""),
                "score": strength,
            })

    targets.sort(key=lambda x: x["score"], reverse=True)
    return targets


# ========== 滚动窗口：模拟周度数据 ==========

def build_rolling_nb_analysis(nb_daily: Dict[str, Dict], end_date: str,
                               trading_days: List[str]) -> Dict:
    """
    用滚动窗口模拟周度/月度北向分析数据。
    取 end_date 之前（含）最近 5 个交易日当周，最近 20 个交易日当月。
    返回结构与 nbAnalysis 一致：{week: {industry_trend, continuous_buy, resonance, holding_change}, month: {...}}
    """
    # 找到 end_date 索引
    try:
        end_idx = trading_days.index(end_date)
    except ValueError:
        end_idx = len(trading_days) - 1

    week_dates = [d for d in trading_days[max(0, end_idx - 4):end_idx + 1] if d in nb_daily]
    month_dates = [d for d in trading_days[max(0, end_idx - 19):end_idx + 1] if d in nb_daily]

    def aggregate(dates: List[str]) -> Dict:
        # 按股票聚合：累计净买入
        stock_net: Dict[str, Dict] = {}
        industry_net: Dict[str, float] = {}
        for ds in dates:
            day_data = nb_daily.get(ds, {})
            for s in day_data.get("stocks", []):
                code = s.get("code", "")
                if not code:
                    continue
                name = s.get("name", "")
                net = s.get("net_wan", 0.0)
                if code not in stock_net:
                    stock_net[code] = {"code": code, "name": name, "net_wan": 0.0}
                stock_net[code]["net_wan"] += net

        # 持仓变动榜（简化：直接用累计净买入排序）
        holding_change_sorted = sorted(stock_net.values(), key=lambda x: x["net_wan"], reverse=True)
        top_buy = [s for s in holding_change_sorted if s["net_wan"] > 0]
        top_sell = [s for s in holding_change_sorted if s["net_wan"] < 0]

        return {
            "industry_trend": {
                "top_buy": [],  # 行业趋势需要行业分类，暂留空（不影响个股信号）
                "top_sell": [],
                "has_industry_data": False,
            },
            "continuous_buy": [],  # 连续加仓需要追踪每日，暂留空
            "resonance": [],  # 共振需要机构数据，留空
            "holding_change": {
                "top_buy": top_buy,
                "top_sell": top_sell,
            },
        }

    # 真实情况：nbAnalysis 中的 industry_trend / continuous_buy / resonance 来自专门的分析
    # 回测时尽量使用已有的 nbAnalysis（如果有完整历史），否则用简化版
    # 这里返回结构占位，实际信号计算主要依赖 nb_daily 的持仓变动
    return {
        "week": aggregate(week_dates),
        "month": aggregate(month_dates),
    }


# ========== 收集历史信号 ==========

def collect_historical_signals(nb_daily: Dict[str, Dict], signal_data: Dict[str, Dict],
                               industry_map: Dict[str, str],
                               trading_days: List[str], start_date: str, end_date: str) -> Dict:
    """
    逐日收集三类历史信号。
    返回: {
        "new_sectors":   [{"date": ..., "industry": ..., ...}],
        "early_signals": [{"date": ..., "code": ..., ...}],
        "core_targets":  [{"date": ..., "code": ..., ...}],
    }
    """
    all_dates = [d for d in trading_days if start_date <= d <= end_date]
    log_info(f"开始逐日扫描信号，共 {len(all_dates)} 个交易日 ...")

    new_sector_signals = []
    early_signal_list = []
    core_target_list = []

    # 预处理：为新赛道信号使用行业分类计算行业趋势
    # 构建滚动窗口的行业净流入
    def rolling_industry_net(end_idx: int, window: int) -> Dict[str, float]:
        ind_net: Dict[str, float] = {}
        start_idx = max(0, end_idx - window + 1)
        for i in range(start_idx, end_idx + 1):
            ds = trading_days[i]
            day_data = nb_daily.get(ds, {})
            for s in day_data.get("stocks", []):
                code = s.get("code", "")
                net = s.get("net_wan", 0.0)
                ind = industry_map.get(code, "")
                if ind and ind != "未分类":
                    ind_net[ind] = ind_net.get(ind, 0.0) + net
        return ind_net

    def rolling_continuous_buy(end_idx: int, max_streak: int = 15) -> List[Dict]:
        """计算连续净买入天数"""
        # 从 end_idx 往前追溯，找连续净买入的股票
        stock_streak: Dict[str, Dict] = {}
        # 收集最近 max_streak 天每天的净买入
        for i in range(max(0, end_idx - max_streak + 1), end_idx + 1):
            ds = trading_days[i]
            day_data = nb_daily.get(ds, {})
            day_stocks = {s["code"]: s for s in day_data.get("stocks", []) if s.get("code")}
            # 更新 streak
            to_remove = []
            for code, info in stock_streak.items():
                if code in day_stocks and day_stocks[code].get("net_wan", 0) > 0:
                    info["streak_days"] += 1
                    info["total_net_wan"] += day_stocks[code]["net_wan"]
                    info["name"] = day_stocks[code].get("name", info["name"])
                else:
                    to_remove.append(code)
            for code in to_remove:
                del stock_streak[code]
            # 新增今日首次出现净买入的
            for code, s in day_stocks.items():
                if s.get("net_wan", 0) > 0 and code not in stock_streak:
                    stock_streak[code] = {
                        "code": code,
                        "name": s.get("name", ""),
                        "streak_days": 1,
                        "total_net_wan": s["net_wan"],
                    }
        result = list(stock_streak.values())
        result.sort(key=lambda x: x["total_net_wan"], reverse=True)
        return result

    def rolling_resonance(end_idx: int, signal_data: Dict[str, Dict],
                          window: int = 5) -> List[Dict]:
        """滚动共振：近 window 天机构+北向累计净买入都为正的股票"""
        nb_agg: Dict[str, Dict] = {}
        inst_agg: Dict[str, Dict] = {}
        start_idx = max(0, end_idx - window + 1)
        for i in range(start_idx, end_idx + 1):
            ds = trading_days[i]
            # 北向
            day_data = nb_daily.get(ds, {})
            for s in day_data.get("stocks", []):
                code = s.get("code", "")
                if not code:
                    continue
                if code not in nb_agg:
                    nb_agg[code] = {"code": code, "name": s.get("name", ""), "nb_net_wan": 0.0}
                nb_agg[code]["nb_net_wan"] += s.get("net_wan", 0.0)
            # 机构
            sig_day = signal_data.get(ds, {})
            basic = sig_day.get("basic_signals", {})
            for sig_type in ["resonance_buy", "inst_buy_youzi_sell"]:
                for s in basic.get(sig_type, []):
                    code = s.get("code", "")
                    if not code:
                        continue
                    if code not in inst_agg:
                        inst_agg[code] = {"code": code, "name": s.get("name", ""), "inst_net_wan": 0.0}
                    inst_agg[code]["inst_net_wan"] += s.get("inst_net_wan", 0.0)

        resonance = []
        for code, nb_info in nb_agg.items():
            inst_info = inst_agg.get(code)
            if not inst_info:
                continue
            nb_net = nb_info["nb_net_wan"]
            inst_net = inst_info["inst_net_wan"]
            if nb_net > 0 and inst_net > 0:
                resonance.append({
                    "code": code,
                    "name": nb_info.get("name") or inst_info.get("name", ""),
                    "nb_net_wan": nb_net,
                    "inst_net_wan": inst_net,
                    "resonance_strength": nb_net + inst_net,
                })
        resonance.sort(key=lambda x: x["resonance_strength"], reverse=True)
        return resonance

    for idx, ds in enumerate(all_dates):
        td_idx = trading_days.index(ds)
        if idx > 0 and idx % 5 == 0:
            log_info(f"  信号扫描进度: {idx}/{len(all_dates)} ({ds})")

        # --- 周度滚动数据 ---
        week_industry = rolling_industry_net(td_idx, 5)
        month_industry = rolling_industry_net(td_idx, 20)
        week_continuous = rolling_continuous_buy(td_idx, 15)
        week_resonance = rolling_resonance(td_idx, signal_data, 5)
        # 持仓变动（近5日累计）
        hc_list = []
        start_idx = max(0, td_idx - 4)
        hc_stocks: Dict[str, Dict] = {}
        for i in range(start_idx, td_idx + 1):
            day_ds = trading_days[i]
            day_data = nb_daily.get(day_ds, {})
            for s in day_data.get("stocks", []):
                code = s.get("code", "")
                if not code:
                    continue
                if code not in hc_stocks:
                    hc_stocks[code] = {"code": code, "name": s.get("name", ""), "net_wan": 0.0}
                hc_stocks[code]["net_wan"] += s.get("net_wan", 0.0)
        hc_sorted = sorted(hc_stocks.values(), key=lambda x: x["net_wan"], reverse=True)

        # 组装 nb_week / nb_month 结构
        nb_week = {
            "industry_trend": {
                "top_buy": [
                    {"industry": ind, "net_buy_wan": val}
                    for ind, val in sorted(week_industry.items(), key=lambda x: x[1], reverse=True)
                    if val > 0
                ],
                "top_sell": [],
            },
            "continuous_buy": week_continuous,
            "resonance": week_resonance,
            "holding_change": {
                "top_buy": hc_sorted,
                "top_sell": list(reversed(hc_sorted))[-len(hc_sorted):] if hc_sorted else [],
            },
        }
        nb_month = {
            "industry_trend": {
                "top_buy": [
                    {"industry": ind, "net_buy_wan": val}
                    for ind, val in sorted(month_industry.items(), key=lambda x: x[1], reverse=True)
                    if val > 0
                ],
                "top_sell": [],
            },
        }

        sig_day = signal_data.get(ds, {})

        # --- 1. 新赛道 ---
        sectors = compute_sector_signals(nb_week, nb_month, top_n=5, abs_threshold_wan=50000)
        for sec in sectors:
            # 选该行业里北向净买入前3名的成分股作为代表
            ind_name = sec["industry"]
            ind_stocks = [s for s in hc_sorted if industry_map.get(s["code"], "") == ind_name][:5]
            if not ind_stocks:
                continue
            new_sector_signals.append({
                "date": ds,
                "industry": ind_name,
                "week_net": round(sec["week_net"], 2),
                "month_net": round(sec["month_net"], 2),
                "tag": sec["tag"],
                "component_codes": [s["code"] for s in ind_stocks],
                "component_names": [s["name"] for s in ind_stocks],
            })

        # --- 2. 早期信号 ---
        early = compute_early_signals(nb_week, sig_day, industry_map)
        for s in early[:10]:  # 回测取前10，足够覆盖展示用的前6
            early_signal_list.append({
                "date": ds,
                "code": s["code"],
                "name": s["name"],
                "signal_type": s["signal_type"],
                "signal_detail": s["signal_detail"],
                "amount_wan": round(s["amount_wan"], 2),
                "industry": s["industry"],
            })

        # --- 3. 核心共振 ---
        core = compute_core_targets(nb_week, industry_map)
        for s in core[:10]:
            core_target_list.append({
                "date": ds,
                "code": s["code"],
                "name": s["name"],
                "signal_type": s["signal_type"],
                "signal_detail": s["signal_detail"],
                "amount_wan": round(s["amount_wan"], 2),
                "industry": s["industry"],
            })

    log_info(f"信号收集完成：")
    log_info(f"  新赛道发现: {len(new_sector_signals)} 条")
    log_info(f"  早期信号雷达: {len(early_signal_list)} 条")
    log_info(f"  核心共振标的: {len(core_target_list)} 条")

    return {
        "new_sectors": new_sector_signals,
        "early_signals": early_signal_list,
        "core_targets": core_target_list,
    }


# ========== 回测计算 ==========

def compute_stats(returns: List[float]) -> Dict:
    n = len(returns)
    if n == 0:
        return {
            "sample_count": 0,
            "win_rate": 0.0,
            "avg_return_pct": 0.0,
            "profit_loss_ratio": 0.0,
            "median_return_pct": 0.0,
            "max_return_pct": 0.0,
            "min_return_pct": 0.0,
        }
    sorted_rets = sorted(returns)
    wins = [r for r in returns if r > 0]
    losses = [r for r in returns if r < 0]
    win_rate = len(wins) / n * 100.0
    avg_ret = sum(returns) / n
    median_ret = (sorted_rets[n // 2] if n % 2 == 1
                  else (sorted_rets[n // 2 - 1] + sorted_rets[n // 2]) / 2)
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = abs(sum(losses) / len(losses)) if losses else 0.0
    pl_ratio = avg_win / avg_loss if avg_loss > 0 else (999.0 if avg_win > 0 else 0.0)

    return {
        "sample_count": n,
        "win_rate": round(win_rate, 2),
        "avg_return_pct": round(avg_ret, 2),
        "profit_loss_ratio": round(pl_ratio, 2),
        "median_return_pct": round(median_ret, 2),
        "max_return_pct": round(max(returns), 2),
        "min_return_pct": round(min(returns), 2),
    }


def run_backtest(all_signals: Dict, hold_periods: List[int],
                 trading_days: List[str]) -> Dict:
    """
    对三类信号分别执行回测，计算各持有期收益统计。
    """
    # 收集所有需要K线的股票
    all_codes = set()
    for s in all_signals["early_signals"]:
        all_codes.add(s["code"])
    for s in all_signals["core_targets"]:
        all_codes.add(s["code"])
    for s in all_signals["new_sectors"]:
        for c in s.get("component_codes", []):
            all_codes.add(c)
    all_codes.add(HS300_CODE)  # 基准

    all_codes = list(all_codes)
    log_info(f"需要获取 {len(all_codes)} 只股票的K线数据 ...")

    kline_cache: Dict[str, Dict[str, float]] = {}
    failed_codes = []
    for i, code in enumerate(all_codes):
        if i > 0 and i % 50 == 0:
            log_info(f"  K线进度: {i}/{len(all_codes)}")
        klines = fetch_kline(code, count=250)
        if not klines:
            failed_codes.append(code)
            continue
        kline_cache[code] = {k["date"]: k["close"] for k in klines}
        time.sleep(0.03)

    log_info(f"  K线完成: 成功 {len(kline_cache)} 只，失败 {len(failed_codes)} 只")

    # 计算单股收益的通用函数
    def calc_stock_returns(code: str, date_str: str) -> Dict[int, Optional[float]]:
        close_map = kline_cache.get(code)
        if not close_map or date_str not in close_map:
            return {p: None for p in hold_periods}
        entry_price = close_map[date_str]
        if entry_price <= 0:
            return {p: None for p in hold_periods}
        result = {}
        for p in hold_periods:
            exit_date = shift_trading_day(date_str, p, trading_days)
            if not exit_date or exit_date not in close_map:
                result[p] = None
                continue
            exit_price = close_map[exit_date]
            result[p] = round((exit_price - entry_price) / entry_price * 100.0, 2)
        return result

    # ---- 1. 新赛道发现（行业级，用成分股等权平均） ----
    log_info("计算新赛道发现回测 ...")
    sector_returns: List[Dict] = []
    for sig in all_signals["new_sectors"]:
        codes = sig.get("component_codes", [])
        if not codes:
            continue
        per_stock_rets = []
        for code in codes:
            rets = calc_stock_returns(code, sig["date"])
            per_stock_rets.append(rets)
        # 各周期等权平均
        avg_rets = {}
        for p in hold_periods:
            vals = [r[p] for r in per_stock_rets if r.get(p) is not None]
            if not vals:
                avg_rets[p] = None
            else:
                avg_rets[p] = round(sum(vals) / len(vals), 2)
        sector_returns.append({
            **sig,
            "returns": avg_rets,
        })

    # ---- 2. 早期信号雷达（个股） ----
    log_info("计算早期信号雷达回测 ...")
    early_returns: List[Dict] = []
    for sig in all_signals["early_signals"]:
        rets = calc_stock_returns(sig["code"], sig["date"])
        if all(v is None for v in rets.values()):
            continue
        early_returns.append({
            **sig,
            "returns": rets,
        })

    # ---- 3. 核心共振标的（个股） ----
    log_info("计算核心共振标的回测 ...")
    core_returns: List[Dict] = []
    for sig in all_signals["core_targets"]:
        rets = calc_stock_returns(sig["code"], sig["date"])
        if all(v is None for v in rets.values()):
            continue
        core_returns.append({
            **sig,
            "returns": rets,
        })

    # ---- 基准：沪深300 ----
    log_info("计算沪深300基准 ...")
    hs300_close = kline_cache.get(HS300_CODE, {})
    benchmark = {}
    # 找一个有代表性的区间平均
    all_dates = list(set(
        [s["date"] for s in sector_returns] +
        [s["date"] for s in early_returns] +
        [s["date"] for s in core_returns]
    ))
    all_dates.sort()
    for p in hold_periods:
        rets = []
        for ds in all_dates:
            if ds not in hs300_close:
                continue
            exit_date = shift_trading_day(ds, p, trading_days)
            if not exit_date or exit_date not in hs300_close:
                continue
            ret = (hs300_close[exit_date] - hs300_close[ds]) / hs300_close[ds] * 100.0
            rets.append(ret)
        if rets:
            benchmark[str(p)] = {
                "sample_count": len(rets),
                "avg_return_pct": round(sum(rets) / len(rets), 2),
            }
        else:
            benchmark[str(p)] = {"sample_count": 0, "avg_return_pct": 0.0}

    # ---- 统计汇总 ----
    def summarize(signal_list: List[Dict]) -> Dict:
        stats = {}
        detail_by_period = {}
        for p in hold_periods:
            rets = [s["returns"][p] for s in signal_list if s["returns"].get(p) is not None]
            stats[str(p)] = compute_stats(rets)
            # 明细：按收益排序
            details = [
                {k: v for k, v in s.items() if k != "returns"}
                for s in signal_list if s["returns"].get(p) is not None
            ]
            for i, s in enumerate(signal_list):
                if s["returns"].get(p) is not None:
                    details[i if i < len(details) else 0]["return_pct"] = s["returns"][p]
            # 简单组装明细（去重，按日期+code唯一）
            seen = set()
            unique_details = []
            for s in signal_list:
                if s["returns"].get(p) is None:
                    continue
                key = s.get("code", s.get("industry", "")) + "_" + s["date"]
                if key in seen:
                    continue
                seen.add(key)
                entry = {k: v for k, v in s.items() if k != "returns"}
                entry["return_pct"] = s["returns"][p]
                unique_details.append(entry)
            unique_details.sort(key=lambda x: x["return_pct"], reverse=True)
            detail_by_period[str(p)] = unique_details[:50]  # 只存前50，控制体积
        return {"stats": stats, "details": detail_by_period, "total_signals": len(signal_list)}

    result = {
        "new_sectors": summarize(sector_returns),
        "early_signals": summarize(early_returns),
        "core_targets": summarize(core_returns),
        "benchmark_hs300": benchmark,
        "failed_codes": failed_codes,
    }
    return result


# ========== 主函数 ==========

def main():
    parser = argparse.ArgumentParser(description="大牛股猎手历史回测")
    parser.add_argument("--start", default="2026-06-01", help="回测起始日期")
    parser.add_argument("--end", default="", help="回测结束日期（默认取数据中最新日期）")
    parser.add_argument("--periods", default="5,10,20",
                        help="持有周期（交易日），逗号分隔，默认5,10,20")
    parser.add_argument("--jiyou-html", default=str(ROOT_DIR / "jiyou-signal-analysis.html"),
                        help="机游信号分析HTML路径")
    parser.add_argument("--nb-html", default=str(ROOT_DIR / "northbound-analysis.html"),
                        help="北向分析HTML路径")
    parser.add_argument("--industry-file", default=str(DATA_DIR / "stock_industry.json"),
                        help="行业分类JSON文件路径")
    parser.add_argument("--output", default=str(DATA_DIR / "bull_hunter_backtest.json"),
                        help="输出结果JSON文件路径")
    args = parser.parse_args()

    hold_periods = [int(x.strip()) for x in args.periods.split(",") if x.strip()]
    hold_periods.sort()

    print("=" * 70)
    print("🐂 大牛股猎手 — 历史回测")
    print("=" * 70)

    # 1. 加载数据
    log_info(f"加载机游数据: {args.jiyou_html}")
    jiyou_content = load_html(args.jiyou_html)
    if not jiyou_content:
        log_error(f"机游数据文件不存在: {args.jiyou_html}")
        sys.exit(1)
    signal_data = extract_json_var(jiyou_content, "signalData") or {}
    jiyou_dates = sorted(signal_data.keys())
    log_info(f"  signalData: {len(jiyou_dates)} 天 ({jiyou_dates[0]} ~ {jiyou_dates[-1]})")

    log_info(f"加载北向数据: {args.nb_html}")
    nb_content = load_html(args.nb_html)
    nb_daily = {}
    if nb_content:
        nb_daily = extract_json_var(nb_content, "nbDailyData") or {}
    nb_dates = sorted(nb_daily.keys())
    log_info(f"  nbDailyData: {len(nb_dates)} 天 ({nb_dates[0] if nb_dates else '无'} ~ {nb_dates[-1] if nb_dates else '无'})")

    # 2. 日期范围
    if not args.end:
        common_dates = sorted(set(jiyou_dates) & set(nb_dates))
        if common_dates:
            end_date = common_dates[-1]
        elif jiyou_dates:
            end_date = jiyou_dates[-1]
        else:
            log_error("无法确定结束日期")
            sys.exit(1)
    else:
        end_date = args.end

    start_date = args.start
    log_info(f"回测区间: {start_date} ~ {end_date}")

    max_period = max(hold_periods)
    end_dt = datetime.strptime(end_date, "%Y-%m-%d")
    extended_end = (end_dt + timedelta(days=max_period * 2 + 30)).strftime("%Y-%m-%d")
    trading_days = gen_trading_days(start_date, extended_end)
    # 只保留 start ~ end 内有数据的交易日
    signal_trading_days = [d for d in trading_days if d <= end_date]
    log_info(f"回测区间交易日数: {len(signal_trading_days)}，扩展总交易日数: {len(trading_days)}")

    # 3. 加载行业数据
    industry_map = load_industry_map(args.industry_file)
    log_info(f"行业映射: {len(industry_map)} 只股票")

    # 4. 收集历史信号
    all_signals = collect_historical_signals(
        nb_daily, signal_data, industry_map, trading_days, start_date, end_date
    )

    if (len(all_signals["new_sectors"]) == 0
            and len(all_signals["early_signals"]) == 0
            and len(all_signals["core_targets"]) == 0):
        log_warn("没有收集到任何信号，回测无法进行")
        result = {
            "config": {
                "start_date": start_date,
                "end_date": end_date,
                "hold_periods": hold_periods,
                "note": "历史数据不足1个月，样本量有限，仅供参考",
            },
            "new_sectors": {"stats": {}, "details": {}, "total_signals": 0},
            "early_signals": {"stats": {}, "details": {}, "total_signals": 0},
            "core_targets": {"stats": {}, "details": {}, "total_signals": 0},
            "benchmark_hs300": {},
        }
    else:
        # 5. 回测
        result = run_backtest(all_signals, hold_periods, trading_days)
        result["config"] = {
            "start_date": start_date,
            "end_date": end_date,
            "hold_periods": hold_periods,
            "note": "历史数据约1个月，样本量有限，回测结果仅供参考",
            "new_sector_count": len(all_signals["new_sectors"]),
            "early_signal_count": len(all_signals["early_signals"]),
            "core_target_count": len(all_signals["core_targets"]),
        }

    # 6. 输出
    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    log_info(f"回测结果已写入: {args.output}")

    # 7. 打印摘要
    print()
    print("=" * 70)
    print("回测统计摘要")
    print("=" * 70)
    signal_names = [
        ("新赛道发现", "new_sectors"),
        ("早期信号雷达", "early_signals"),
        ("核心共振标的", "core_targets"),
    ]
    for label, key in signal_names:
        stats = result.get(key, {}).get("stats", {})
        total = result.get(key, {}).get("total_signals", 0)
        print(f"\n【{label}】 (总信号: {total})")
        hdr = f"  {'周期':>6s} | {'样本':>5s} | {'胜率%':>7s} | {'平均收益%':>9s} | {'盈亏比':>7s}"
        print(hdr)
        print("  " + "-" * (len(hdr) - 2))
        for p in hold_periods:
            s = stats.get(str(p), {})
            if not s or s.get("sample_count", 0) == 0:
                print(f"  {'T+'+str(p):>6s} | {'-':>5s} | {'-':>7s} | {'-':>9s} | {'-':>7s}")
                continue
            pl_str = f"{s['profit_loss_ratio']:.2f}" if s.get("profit_loss_ratio", 0) < 999 else "∞"
            print(f"  {'T+'+str(p):>6s} | {s['sample_count']:>5d} | {s['win_rate']:>7.2f} | "
                  f"{s['avg_return_pct']:>+9.2f} | {pl_str:>7s}")

    print(f"\n【沪深300基准】")
    bench = result.get("benchmark_hs300", {})
    for p in hold_periods:
        b = bench.get(str(p), {})
        print(f"  T+{p}: 样本{b.get('sample_count',0)}  平均收益 {b.get('avg_return_pct',0):+.2f}%")

    print()
    print("=" * 70)


if __name__ == "__main__":
    main()
