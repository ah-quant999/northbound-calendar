#!/usr/bin/env python3
"""
机游共振日历自动更新脚本（东方财富官方API · 纯数据交叉版）

数据来源（唯一数据源：东方财富龙虎榜官方API）：
  - 机构买卖：RPT_ORGANIZATION_TRADE_DETAILS, filter=(TRADE_DATE='YYYY-MM-DD')
  - 龙虎榜个股明细：RPT_DAILYBILLBOARD_DETAILSNEW, filter=(TRADE_DATE='YYYY-MM-DD')

共振逻辑：
  机构净买入TOP5  ∩  龙虎榜个股买入净额TOP20  =  机游共振
  （龙虎榜买入净额代表市场整体游资/主力资金买入力度，不做任何席位识别）

参数：
  --html_path: HTML文件路径
  --repo_path: Git仓库路径
  --force: 强制更新，忽略状态检查
  --date: 指定日期 (格式: YYYY-MM-DD, 默认: 今天)
  --result_mode: 结果模式 (默认: auto)
  --dry-run: 只抓取不写入
  --no-push: 跳过GitHub推送
  --skip-self-check: 跳过数据一致性自检
"""

import asyncio
import json
import os
import re
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Any
from collections import defaultdict

import requests
from codeact_sdk import CodeActSDK
from pydantic import BaseModel, Field

sdk = CodeActSDK()

# 工具schema版本
TOOL_SCHEMA_VERSIONS = {
    "codeact_search_web": "v1_5ac1b0eba8c26f2a",
    "codeact_fetch_web": "v1_2c8d0580b3f93a58",
    "file_to_url": "v1_fe3416acf3d7b53b",
}

# ========== 配置区 ==========

# 东方财富API基础配置
EASTMONEY_API_BASE = "https://datacenter-web.eastmoney.com/api/data/v1/get"
EASTMONEY_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://data.eastmoney.com/",
    "Accept": "application/json, text/plain, */*",
}

# ===== 报表名称 =====
# 机构买卖每日统计
REPORT_INSTITUTION = "RPT_ORGANIZATION_TRADE_DETAILS"
# 龙虎榜个股明细（用于共振判断：市场整体买入力度TOP20）
REPORT_DAILY_DETAILS = "RPT_DAILYBILLBOARD_DETAILSNEW"

# 共振参数：龙虎榜个股买入净额 TOP N
RESONANCE_LHB_TOP_N = 20

# A股法定假日集合
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

# 状态数据库路径
STATE_DB = "./codeact/output/jiyou_resonance_state.db"

# Git 模块
from calendar_git import calendar_git_setup, calendar_git_push, GIT_EMAIL, GIT_NAME, TOKEN, REPO


# ========== 数据模型 ==========

class InstitutionStock(BaseModel):
    """机构席位净买入/卖出数据"""
    name: str = Field(description="股票名称")
    amount: float = Field(description="净买入金额(万元)，正数净买，负数净卖")
    code: str = Field(description="股票代码", default="")


class ResonanceItem(BaseModel):
    """共振信号"""
    stock_name: str = Field(description="共振股票名称")
    inst_amount: float = Field(description="机构净买入金额(万元)", default=0.0)
    lhb_amount: float = Field(description="龙虎榜净买入金额(万元)", default=0.0)


class DailyData(BaseModel):
    """单日机游共振数据"""
    date: str = Field(description="日期")
    institution_top5: List[InstitutionStock] = Field(description="机构净买入TOP5", default_factory=list)
    institution_sell_top5: List[InstitutionStock] = Field(description="机构净卖出TOP5", default_factory=list)
    resonance: List[ResonanceItem] = Field(description="机游共振信号", default_factory=list)
    data_source: str = Field(description="数据来源", default="东方财富龙虎榜官方API")


# ========== 状态管理 ==========

def init_state_db():
    """初始化状态数据库"""
    os.makedirs(os.path.dirname(STATE_DB), exist_ok=True)
    conn = sqlite3.connect(STATE_DB)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS update_history (
            date TEXT PRIMARY KEY,
            updated_at TEXT NOT NULL,
            data_source TEXT,
            institution_top5 TEXT,
            institution_sell_top5 TEXT,
            resonance TEXT,
            pushed_at TEXT
        )
    """)
    conn.commit()
    conn.close()


def get_last_update(date: str) -> Optional[Dict]:
    """获取某日的最后更新记录"""
    conn = sqlite3.connect(STATE_DB)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM update_history WHERE date = ?", (date,))
    row = cursor.fetchone()
    conn.close()
    if row:
        cols = [desc[0] for desc in cursor.description]
        data = dict(zip(cols, row))
        for k in ["institution_top5", "institution_sell_top5", "resonance"]:
            val = data.get(k)
            data[k] = json.loads(val) if val else []
        return data
    return None


def save_update(data: DailyData, pushed_at: Optional[str] = None):
    """保存更新记录"""
    conn = sqlite3.connect(STATE_DB)
    now = datetime.now().isoformat()
    conn.execute("""
        INSERT OR REPLACE INTO update_history
        (date, updated_at, data_source, institution_top5, institution_sell_top5,
         resonance, pushed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        data.date,
        now,
        data.data_source,
        json.dumps([s.model_dump() for s in data.institution_top5], ensure_ascii=False),
        json.dumps([s.model_dump() for s in data.institution_sell_top5], ensure_ascii=False),
        json.dumps([s.model_dump() for s in data.resonance], ensure_ascii=False),
        pushed_at or now,
    ))
    conn.commit()
    conn.close()


# ========== 东财API数据获取 ==========

def _safe_num(v) -> float:
    """安全转数字"""
    if v is None:
        return 0.0
    try:
        return float(v)
    except (ValueError, TypeError):
        return 0.0


def fetch_eastmoney_api(report_name: str, filter_expr: str,
                        sort_columns: str, sort_types: str = "-1",
                        page_size: int = 200, max_pages: int = 5,
                        retries: int = 3) -> List[Dict]:
    """
    调用东方财富数据中心API获取全量数据（自动翻页）

    Args:
        report_name: 报表名称
        filter_expr: filter 表达式（完整的括号串）
        sort_columns: 排序列名（多列用英文逗号分隔）
        sort_types: 排序方向（-1降序 / 1升序，多列用英文逗号分隔）
        page_size: 每页条数
        max_pages: 最大翻页数
        retries: 重试次数

    Returns:
        数据列表
    """
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
                import time
                time.sleep(2 * (attempt + 1))
            else:
                raise
    return all_data


# ===== 机构买卖数据 =====

def get_institution_data(date_str: str) -> Dict[str, List[Dict]]:
    """
    获取机构买卖数据，按股票聚合（同一只股票多次上榜合并）

    数据源: RPT_ORGANIZATION_TRADE_DETAILS

    Returns:
        {"buy_sorted": [...], "sell_sorted": [...]}
        每项: {code, name, net_buy_wan, buy_wan, sell_wan, buy_count, sell_count}
    """
    print(f"  📡 [机构] 调用 {REPORT_INSTITUTION} ...")
    raw_data = fetch_eastmoney_api(
        REPORT_INSTITUTION,
        filter_expr=f"(TRADE_DATE='{date_str}')",
        sort_columns="NET_BUY_AMT,TRADE_DATE,SECURITY_CODE",
        sort_types="-1,-1,1",
        page_size=200, max_pages=5,
    )
    print(f"    原始记录数: {len(raw_data)}")

    # 按股票代码聚合（同一股票可能因不同上榜原因出现多条）
    stock_map = {}
    for item in raw_data:
        code = item.get("SECURITY_CODE", "")
        name = item.get("SECURITY_NAME_ABBR", "")
        net_buy = _safe_num(item.get("NET_BUY_AMT"))   # 元
        buy_amt = _safe_num(item.get("BUY_AMT"))
        sell_amt = _safe_num(item.get("SELL_AMT"))
        buy_times = int(_safe_num(item.get("BUY_TIMES")))
        sell_times = int(_safe_num(item.get("SELL_TIMES")))

        if code not in stock_map:
            stock_map[code] = {
                "code": code,
                "name": name,
                "net_buy": 0.0,
                "buy_amt": 0.0,
                "sell_amt": 0.0,
                "buy_count": 0,
                "sell_count": 0,
            }
        stock_map[code]["net_buy"] += net_buy
        stock_map[code]["buy_amt"] += buy_amt
        stock_map[code]["sell_amt"] += sell_amt
        stock_map[code]["buy_count"] = max(stock_map[code]["buy_count"], buy_times)
        stock_map[code]["sell_count"] = max(stock_map[code]["sell_count"], sell_times)

    stocks = list(stock_map.values())
    # 转换为万元
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


# ===== 龙虎榜个股明细数据 =====

def get_lhb_stock_data(date_str: str) -> Dict[str, List[Dict]]:
    """
    获取龙虎榜个股明细数据，按股票聚合（同一股票可能因不同上榜原因出现多条）

    数据源: RPT_DAILYBILLBOARD_DETAILSNEW
    用途：龙虎榜净买入TOP20代表市场整体买入力度（游资+主力），用于共振判断

    Returns:
        {"buy_sorted": [...], "sell_sorted": [...]}
        每项: {code, name, net_buy_wan, buy_wan, sell_wan}
    """
    print(f"  📡 [龙虎榜] 调用 {REPORT_DAILY_DETAILS} ...")
    raw_data = fetch_eastmoney_api(
        REPORT_DAILY_DETAILS,
        filter_expr=f"(TRADE_DATE='{date_str}')",
        sort_columns="BILLBOARD_NET_AMT,TRADE_DATE,SECURITY_CODE",
        sort_types="-1,-1,1",
        page_size=200, max_pages=5,
    )
    print(f"    原始记录数: {len(raw_data)}")

    # 按股票代码聚合（同一股票可能因不同上榜原因出现多条）
    stock_map = {}
    for item in raw_data:
        code = item.get("SECURITY_CODE", "")
        name = item.get("SECURITY_NAME_ABBR", "")
        net_buy = _safe_num(item.get("BILLBOARD_NET_AMT"))   # 元
        buy_amt = _safe_num(item.get("BILLBOARD_BUY_AMT"))
        sell_amt = _safe_num(item.get("BILLBOARD_SELL_AMT"))

        if code not in stock_map:
            stock_map[code] = {
                "code": code,
                "name": name,
                "net_buy": 0.0,
                "buy_amt": 0.0,
                "sell_amt": 0.0,
            }
        stock_map[code]["net_buy"] += net_buy
        stock_map[code]["buy_amt"] += buy_amt
        stock_map[code]["sell_amt"] += sell_amt

    stocks = list(stock_map.values())
    for s in stocks:
        s["net_buy_wan"] = s["net_buy"] / 10000.0
        s["buy_wan"] = s["buy_amt"] / 10000.0
        s["sell_wan"] = s["sell_amt"] / 10000.0

    buy_sorted = sorted(stocks, key=lambda x: x["net_buy"], reverse=True)
    sell_sorted = sorted(stocks, key=lambda x: x["net_buy"])

    print(f"    去重后股票数: {len(stocks)}")
    print(f"    龙虎榜净买入TOP10: {[(s['name'], round(s['net_buy_wan'],2)) for s in buy_sorted[:10]]}")
    print(f"    龙虎榜净卖出TOP5: {[(s['name'], round(s['net_buy_wan'],2)) for s in sell_sorted[:5]]}")

    return {"buy_sorted": buy_sorted, "sell_sorted": sell_sorted}


# ===== 数据校验 =====

def validate_data(inst_data: Dict, lhb_data: Dict, date_str: str) -> List[str]:
    """
    数据校验：检查数据完整性、金额方向、数量等

    Returns:
        错误列表（空列表表示校验通过）
    """
    errors = []

    # 机构数据校验
    buy_stocks = [s for s in inst_data["buy_sorted"] if s["net_buy"] > 0]

    if len(buy_stocks) < 5:
        errors.append(f"机构净买入股票数不足5只（实际{len(buy_stocks)}只）")
    else:
        # TOP5必须全部为正
        top5 = inst_data["buy_sorted"][:5]
        for s in top5:
            if s["net_buy"] <= 0:
                errors.append(f"机构净买入榜中{s['name']}金额非正: {s['net_buy_wan']:.2f}万")

    # 净卖出榜方向校验
    sell_stocks = [s for s in inst_data["sell_sorted"] if s["net_buy"] < 0]
    if sell_stocks:
        top_sell = inst_data["sell_sorted"][:5]
        for s in top_sell:
            if s["net_buy"] >= 0:
                errors.append(f"机构净卖出榜中{s['name']}金额非负: {s['net_buy_wan']:.2f}万")

    # 金额合理性校验（单日单只股票机构净买入一般不超过100亿）
    for s in buy_stocks[:5]:
        if abs(s["net_buy_wan"]) > 1000000:  # 100亿
            errors.append(f"机构净买入金额异常: {s['name']} {s['net_buy_wan']:.2f}万")

    # 龙虎榜数据校验
    lhb_buy = [s for s in lhb_data["buy_sorted"] if s["net_buy"] > 0]
    if len(lhb_buy) < RESONANCE_LHB_TOP_N:
        # 可能是节假日后或特殊日期，只作警告
        print(f"  ⚠️  龙虎榜净买入股票数不足{RESONANCE_LHB_TOP_N}只（实际{len(lhb_buy)}只）")

    return errors


# ===== 共振计算 =====

def compute_resonance(inst_top5: List[Dict], lhb_data: Dict) -> List[ResonanceItem]:
    """
    计算机游共振：机构净买入TOP5 ∩ 龙虎榜个股买入净额TOP20

    逻辑：
      - 机构净买入TOP5：代表机构资金强烈看好
      - 龙虎榜净买入TOP20：代表市场整体（游资+主力）资金强烈买入
      - 两者交集即为"机游共振"——机构与市场主力资金同向买入

    Args:
        inst_top5: 机构净买入TOP5列表
        lhb_data: 龙虎榜数据（含 buy_sorted）

    Returns:
        共振信号列表（按机构排名顺序）
    """
    # 机构TOP5股票名称集合
    inst_stock_names = {s["name"] for s in inst_top5[:5]}
    inst_stock_codes = {s["code"] for s in inst_top5[:5]}

    # 龙虎榜净买入TOP N 股票名称/代码集合
    lhb_top_n = lhb_data["buy_sorted"][:RESONANCE_LHB_TOP_N]
    lhb_stock_names = {s["name"] for s in lhb_top_n}
    lhb_stock_codes = {s["code"] for s in lhb_top_n}

    # 找出重叠股票（优先按代码匹配，名称兜底）
    overlap_codes = inst_stock_codes & lhb_stock_codes

    # 构建龙虎榜股票的代码->金额映射
    lhb_code_to_amount = {s["code"]: s["net_buy_wan"] for s in lhb_top_n}

    resonance = []
    # 按机构排名顺序输出
    for s in inst_top5[:5]:
        if s["code"] in overlap_codes:
            resonance.append(ResonanceItem(
                stock_name=s["name"],
                inst_amount=round(s["net_buy_wan"], 2),
                lhb_amount=round(lhb_code_to_amount.get(s["code"], 0.0), 2),
            ))

    print(f"    共振股票数: {len(resonance)}")
    for r in resonance:
        print(f"      - {r.stock_name}: 机构+{r.inst_amount:.0f}万, 龙虎榜+{r.lhb_amount:.0f}万")

    return resonance


# ===== 构建单日数据 =====

def build_daily_data(date_str: str) -> DailyData:
    """构建单日完整数据"""
    print(f"📊 正在获取 {date_str} 的龙虎榜数据...")

    # 1. 获取机构数据
    inst_data = get_institution_data(date_str)
    # 2. 获取龙虎榜个股明细数据
    lhb_data = get_lhb_stock_data(date_str)

    # 数据校验
    errors = validate_data(inst_data, lhb_data, date_str)
    if errors:
        print("⚠️  数据校验警告:")
        for e in errors:
            print(f"   - {e}")
        # 如果是严重错误（机构数据为空），抛出异常
        if len(inst_data["buy_sorted"]) == 0:
            raise ValueError(f"机构数据为空，无法继续。校验错误: {errors}")

    # ===== 机构TOP5 =====
    inst_top5 = [
        InstitutionStock(
            name=s["name"],
            code=s["code"],
            amount=round(s["net_buy_wan"], 2),
        )
        for s in inst_data["buy_sorted"][:5]
        if s["net_buy"] > 0
    ]

    # ===== 机构净卖出TOP5 =====
    inst_sell_top5 = [
        InstitutionStock(
            name=s["name"],
            code=s["code"],
            amount=round(s["net_buy_wan"], 2),  # 负数
        )
        for s in inst_data["sell_sorted"][:5]
        if s["net_buy"] < 0
    ]

    # 共振判断
    resonance = compute_resonance(inst_data["buy_sorted"][:5], lhb_data)

    return DailyData(
        date=date_str,
        institution_top5=inst_top5,
        institution_sell_top5=inst_sell_top5,
        resonance=resonance,
        data_source="东方财富龙虎榜官方API",
    )


# ========== HTML更新 ==========

def format_amount(amount_wan: float) -> str:
    """格式化金额"""
    if abs(amount_wan) >= 10000:
        return f"{amount_wan / 10000:.2f}亿"
    else:
        return f"{amount_wan:.0f}万"


def build_day_cell_html(data: DailyData) -> str:
    """构建机游共振日历日期单元格的HTML"""
    day = datetime.strptime(data.date, "%Y-%m-%d").day
    has_resonance = len(data.resonance) > 0
    has_data = data.institution_top5 or data.resonance

    if not has_data:
        return f"""                <div class="day-cell">
                    <div class="day-header"><span class="day-number">{day}</span></div>
                    <div class="empty-content">--</div>
                </div>"""

    # 共振股票集合
    resonance_names = {r.stock_name for r in data.resonance}

    lines = []
    lines.append(f'                <div class="day-cell">')

    # day-header
    if has_resonance:
        lines.append(f'                    <div class="day-header"><span class="day-number">{day}</span><span class="amount resonance-tag">★共振</span></div>')
    else:
        lines.append(f'                    <div class="day-header"><span class="day-number">{day}</span></div>')

    lines.append(f'                    <div class="stock-list">')

    # 1. 机构净买入TOP5
    if data.institution_top5:
        lines.append(f'                        <div class="section-title">▲ 机构净买入TOP5</div>')
        lines.append(f'                        <div class="stock-row">')
        for stock in data.institution_top5[:5]:
            amount_str = f"+{format_amount(stock.amount)}"
            if stock.name in resonance_names:
                lines.append(f'                            <span class="stock-item"><span class="stock-icon resonance">★</span><span class="stock-name">{stock.name}</span><span class="stock-amount resonance-amount">{amount_str}</span></span>')
            else:
                lines.append(f'                            <span class="stock-item"><span class="stock-icon up">▲</span><span class="stock-name">{stock.name}</span><span class="stock-amount up">{amount_str}</span></span>')
        lines.append(f'                        </div>')

    # 2. 机构净卖出TOP5
    if data.institution_sell_top5:
        lines.append(f'                        <div class="section-title sell-title">▼ 机构净卖出TOP5</div>')
        lines.append(f'                        <div class="stock-row">')
        for stock in data.institution_sell_top5[:5]:
            amount_str = f"{format_amount(stock.amount)}"
            lines.append(f'                            <span class="stock-item"><span class="stock-icon down">▼</span><span class="stock-name">{stock.name}</span><span class="stock-amount down">{amount_str}</span></span>')
        lines.append(f'                        </div>')

    # 3. 机游共振
    if data.resonance:
        lines.append(f'                        <div class="section-title resonance-title">★ 机游共振</div>')
        lines.append(f'                        <div class="stock-row">')
        for res in data.resonance:
            display = f"{res.stock_name}"
            lines.append(f'                            <span class="stock-item"><span class="stock-icon resonance">★</span><span class="stock-name">{display}</span><span class="stock-amount resonance-amount">+{format_amount(res.lhb_amount)}</span></span>')
        lines.append(f'                        </div>')

    lines.append(f'                    </div>')
    lines.append(f'                </div>')
    return "\n".join(lines)


def update_html(html_path: str, data: DailyData) -> bool:
    """更新HTML文件中的指定日期数据"""
    try:
        with open(html_path, "r", encoding="utf-8") as f:
            html = f.read()
    except Exception as e:
        print(f"读取HTML文件失败: {e}")
        return False

    dt = datetime.strptime(data.date, "%Y-%m-%d")
    day = dt.day
    month = dt.month
    new_cell_html = build_day_cell_html(data)
    has_resonance = len(data.resonance) > 0
    has_data = data.institution_top5 or data.resonance

    # ====== 写入前自检：确保目标td的日期注释与写入日期一致 ======
    target_dt = datetime.strptime(data.date, "%Y-%m-%d")
    target_weekday = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][target_dt.weekday()]

    # 更新数据更新时间
    now = datetime.now()
    update_time_str = now.strftime("%Y-%m-%d %H:%M")
    html = re.sub(
        r'数据更新时间：\d{4}-\d{2}-\d{2} \d{2}:\d{2}',
        f'数据更新时间：{update_time_str}',
        html,
    )

    # 找到正确的月份区域
    month_section_pattern = rf'<div class="month-section[^"]*" id="month-{month}"'
    month_section_match = re.search(month_section_pattern, html)
    if not month_section_match:
        print(f"⚠️ 未找到月份 {month} 的区域")
        return False

    section_start = month_section_match.start()
    next_section = re.search(r'<div class="month-section', html[section_start + 1:])
    section_end = section_start + 1 + next_section.start() if next_section else len(html)

    section_html = html[section_start:section_end]

    # 在月份区域内查找目标日期的单元格
    all_matches = list(re.finditer(
        rf'(<td[^>]*>)\s*<div class="day-cell">((?!</td>).)*?<span class="day-number">\s*{day}\s*</span>((?!</td>).)*?</div>\s*</div>\s*</td>',
        section_html,
        re.DOTALL,
    ))

    if not all_matches:
        all_matches = list(re.finditer(
            rf'(<td[^>]*>)\s*<div class="day-cell">((?!</td>).)*?<span class="day-number">\s*{day}\s*</span>((?!</td>).)*?</div>\s*</td>',
            section_html,
            re.DOTALL,
        ))

    # 回退：月末日期可能出现在下月第一周
    if not all_matches:
        print(f"⚠️ 在 month-{month} 中未找到 {day}日，尝试回退到 month-{month+1}...")
        fallback_pattern = rf'<div class="month-section[^"]*" id="month-{month+1}"'
        fallback_match = re.search(fallback_pattern, html)
        if fallback_match:
            fb_start = fallback_match.start()
            fb_next = re.search(r'<div class="month-section', html[fb_start + 1:])
            fb_end = fb_start + 1 + fb_next.start() if fb_next else len(html)
            fb_section_html = html[fb_start:fb_end]
            all_matches = list(re.finditer(
                rf'(<td[^>]*>)\s*<div class="day-cell">((?!</td>).)*?<span class="day-number">\s*{day}\s*</span>((?!</td>).)*?</div>\s*</div>\s*</td>',
                fb_section_html,
                re.DOTALL,
            ))
            if not all_matches:
                all_matches = list(re.finditer(
                    rf'(<td[^>]*>)\s*<div class="day-cell">((?!</td>).)*?<span class="day-number">\s*{day}\s*</span>((?!</td>).)*?</div>\s*</td>',
                    fb_section_html,
                    re.DOTALL,
                ))
            if all_matches:
                section_start = fb_start
                section_html = fb_section_html
                print(f"✅ 在 month-{month+1} 中找到 {day}日")

    if all_matches:
        target_match = all_matches[0]
        td_open = target_match.group(1)

        # ====== 写入前自检：检查目标td上方的日期注释是否匹配 ======
        td_abs_start = section_start + target_match.start()
        pre_context = html[max(0, td_abs_start - 300):td_abs_start]
        comment_m = re.search(r'!--\s*(\d+)/(\d+)\s*([一二三四五六日天]+)\s*--', pre_context)
        if comment_m:
            cm_month = int(comment_m.group(1))
            cm_day = int(comment_m.group(2))
            cm_weekday = comment_m.group(3)
            if cm_month != month or cm_day != day:
                print(f"❌ 日期注释不匹配！注释={cm_month}/{cm_day}，写入日期={month}/{day}")
                print(f"   上下文: {pre_context[-100:]}")
                return False
            real_weekday = ["周一","周二","周三","周四","周五","周六","周日"][target_dt.weekday()]
            if cm_weekday not in real_weekday and real_weekday not in cm_weekday:
                print(f"⚠️  星期标注不匹配：注释={cm_weekday}，真实={real_weekday}")
        else:
            print(f"⚠️  未找到 {month}月{day}日 的日期注释，跳过注释校验")

        # 如果有机游共振，给td加上has-resonance类
        if has_resonance and has_data:
            if 'class="' in td_open:
                if 'has-resonance' not in td_open:
                    td_open = td_open.replace('class="', 'class="has-resonance ')
            else:
                td_open = td_open.rstrip('>') + ' class="has-resonance">'

        abs_start = section_start + target_match.start()
        abs_end = section_start + target_match.end()
        new_html = f'{td_open}\n{new_cell_html}\n                    </td>'
        html = html[:abs_start] + new_html + html[abs_end:]
        print(f"✅ 更新了 {month}月{day}日 的单元格")
    else:
        print(f"⚠️ 未找到 {month}月{day}日 的匹配单元格")
        return False

    # 更新生成日期
    html = re.sub(
        r'生成日期：\d{4}-\d{2}-\d{2}',
        f'生成日期：{now.strftime("%Y-%m-%d")}',
        html,
    )

    try:
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"✅ HTML文件已更新: {html_path}")
        return True
    except Exception as e:
        print(f"写入HTML文件失败: {e}")
        return False


# ========== GitHub推送 ==========

def git_push(repo_path: str, file_name: str, date: str, html_path_arg: str) -> bool:
    """推送到GitHub（走 calendar-pages 分支）"""
    try:
        if not calendar_git_setup(repo_path):
            print("❌ Git 初始化/分支切换失败")
            return False

        import shutil
        dst = os.path.join(repo_path, file_name)
        shutil.copy2(html_path_arg, dst)
        shutil.copy2(html_path_arg, os.path.join(repo_path, "jiyou-resonance.html"))
        index_dst = os.path.join(repo_path, "index.html")
        if not os.path.exists(index_dst):
            shutil.copy2(html_path_arg, index_dst)
        print(f"📄 已复制到仓库: {dst}")
        print(f"📄 已复制到仓库: jiyou-resonance.html")

        # 部署前验证
        validate_script = os.path.join(repo_path, "scripts", "validate_calendar_html.py")
        if os.path.exists(validate_script):
            result = subprocess.run(
                [sys.executable, validate_script, dst],
                capture_output=True, text=True, timeout=30
            )
            print(result.stdout)
            if result.returncode != 0:
                print(f"❌ 验证未通过，取消部署: {result.stderr}")
                return False
            print("✅ 验证通过，继续部署")

        # 同步脚本到仓库
        src_script = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   "update_jiyou_resonance_calendar.py")
        dst_script_dir = os.path.join(repo_path, "scripts")
        os.makedirs(dst_script_dir, exist_ok=True)
        shutil.copy2(src_script, os.path.join(dst_script_dir, "update_jiyou_resonance_calendar.py"))

        # 同步 validate_data_consistency.py
        val_script_src = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                       "validate_data_consistency.py")
        if os.path.exists(val_script_src):
            shutil.copy2(val_script_src, os.path.join(dst_script_dir, "validate_data_consistency.py"))

        return calendar_git_push(
            repo_path,
            [file_name, "jiyou-resonance.html",
             "scripts/update_jiyou_resonance_calendar.py",
             "scripts/validate_data_consistency.py"],
            f"auto: 机游共振日历更新 {date} (东财官方API·纯数据交叉版)",
        )
    except Exception as e:
        print(f"Git推送失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def is_a_stock_holiday(date_str: str) -> bool:
    """判断是否为A股休市日"""
    return date_str in A_STOCK_HOLIDAYS_2026


# ========== 主函数 ==========

async def main():
    html_path = "/app/data/所有对话/主对话/机游共振日历.html"
    repo_path = "/tmp/nb-calendar/"
    force_update = False
    target_date = datetime.now().strftime("%Y-%m-%d")
    result_mode = "auto"
    dry_run = False
    no_push = False
    skip_self_check = False

    i = 1
    while i < len(sys.argv):
        arg = sys.argv[i]
        if arg == "--html_path" and i + 1 < len(sys.argv):
            html_path = sys.argv[i + 1]
            i += 2
        elif arg == "--repo_path" and i + 1 < len(sys.argv):
            repo_path = sys.argv[i + 1]
            i += 2
        elif arg == "--force":
            force_update = True
            i += 1
        elif arg == "--date" and i + 1 < len(sys.argv):
            target_date = sys.argv[i + 1]
            i += 2
        elif arg == "--result_mode" and i + 1 < len(sys.argv):
            result_mode = sys.argv[i + 1]
            i += 2
        elif arg == "--dry-run":
            dry_run = True
            i += 1
        elif arg == "--no-push":
            no_push = True
            i += 1
        elif arg == "--skip-self-check":
            skip_self_check = True
            i += 1
        else:
            i += 1

    actual_mode = result_mode if result_mode != "auto" else "display_only"
    file_name = os.path.basename(html_path)

    print(f"📅 目标日期: {target_date}")
    print(f"📄 HTML路径: {html_path}")
    print(f"📁 仓库路径: {repo_path}")
    print(f"🔧 强制模式: {force_update}")
    print(f"📊 数据源: 东方财富龙虎榜官方API（纯数据交叉版）")
    print(f"🔬 共振逻辑: 机构净买入TOP5 ∩ 龙虎榜净买入TOP{RESONANCE_LHB_TOP_N}")

    try:
        init_state_db()

        # 检查是否已更新
        if not force_update:
            last_update = get_last_update(target_date)
            if last_update and last_update.get("pushed_at"):
                print(f"✅ {target_date} 数据已更新并推送，跳过")
                await sdk.submit_result(
                    message=f"[{target_date}] 机游共振数据已是最新，无需更新",
                    result_mode="no_reply",
                    status="success",
                )
                return

        # 检查是否为交易日
        date_obj = datetime.strptime(target_date, "%Y-%m-%d")
        if date_obj.weekday() >= 5:
            print(f"📅 {target_date} 是周末，休市")
            await sdk.submit_result(
                message=f"[{target_date}] 周末休市，无龙虎榜数据",
                result_mode="no_reply",
                status="success",
            )
            return

        if is_a_stock_holiday(target_date):
            print(f"🏛️ {target_date} 是A股法定假日，休市")
            await sdk.submit_result(
                message=f"[{target_date}] A股法定假日休市，无龙虎榜数据",
                result_mode="no_reply",
                status="success",
            )
            return

        # 获取数据（同步调用，requests库）
        daily_data = build_daily_data(target_date)

        print(f"✅ 获取到数据:")
        print(f"   机构净买入TOP5: {len(daily_data.institution_top5)} 只")
        print(f"   机构净卖出TOP5: {len(daily_data.institution_sell_top5)} 只")
        print(f"   机游共振: {len(daily_data.resonance)} 个")

        # dry-run 模式：只打印，不写入
        if dry_run:
            print("\n🔍 [DRY-RUN] 抓取到的数据如下（不写入HTML）：")
            print(f"  📅 日期: {daily_data.date}")
            print(f"  🏦 机构净买入TOP5:")
            for i, s in enumerate(daily_data.institution_top5, 1):
                print(f"    {i}. {s.name} ({s.code}) +{format_amount(s.amount)}")
            print(f"  🏦 机构净卖出TOP5:")
            for i, s in enumerate(daily_data.institution_sell_top5, 1):
                print(f"    {i}. {s.name} ({s.code}) {format_amount(s.amount)}")
            print(f"  ⭐ 共振信号:")
            for r in daily_data.resonance:
                print(f"    ★ {r.stock_name}: 机构+{format_amount(r.inst_amount)}, 龙虎榜+{format_amount(r.lhb_amount)}")
            await sdk.submit_result(
                message=f"[DRY-RUN] [{target_date}] 机游共振数据已抓取，未写入HTML\n"
                        f"机构TOP5: {len(daily_data.institution_top5)}只, 共振: {len(daily_data.resonance)}个",
                result_mode=actual_mode,
                status="success",
                data={
                    "date": target_date,
                    "institution_count": len(daily_data.institution_top5),
                    "institution_sell_count": len(daily_data.institution_sell_top5),
                    "resonance_count": len(daily_data.resonance),
                    "dry_run": True,
                },
            )
            return

        # 更新HTML
        print("📝 正在更新HTML文件...")
        if not update_html(html_path, daily_data):
            await sdk.submit_result(
                message=f"[{target_date}] 更新HTML文件失败",
                result_mode="notify",
                status="error",
            )
            return

        # 写入完成后，运行数据一致性自检验证
        if not skip_self_check:
            print("🔍 运行数据一致性自检...")
            validate_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "validate_data_consistency.py")
            if os.path.exists(validate_script):
                validate_result = subprocess.run(
                    [sys.executable, validate_script, html_path],
                    capture_output=True, text=True, timeout=30,
                )
                print(validate_result.stdout)
                if validate_result.returncode != 0:
                    print("❌ 数据一致性自检失败，拒绝继续部署")
                    await sdk.submit_result(
                        message=f"[{target_date}] 数据一致性自检失败，已中止部署\n{validate_result.stdout[:500]}",
                        result_mode="notify",
                        status="error",
                        data={"error_type": "SelfCheckFailed", "date": target_date},
                    )
                    return
                print("✅ 数据一致性自检通过")
            else:
                print("⚠️  未找到 validate_data_consistency.py，跳过自检")

        # 推送到GitHub
        print("📤 正在推送到GitHub...")
        if no_push:
            print("🚫 [--no-push] 跳过GitHub推送")
            push_ok = False
            pushed_at = None
        else:
            push_ok = git_push(repo_path, file_name, target_date, html_path)
            pushed_at = datetime.now().isoformat() if push_ok else None

        save_update(daily_data, pushed_at)

        # 生成文件URL
        try:
            file_url_result = await sdk.call_tool(
                "file_to_url",
                {"file_path": html_path},
                schema_version=TOOL_SCHEMA_VERSIONS["file_to_url"],
            )
            file_url = file_url_result.get("url", "") if file_url_result.get("is_success") else ""
        except Exception as e:
            print(f"生成文件URL失败: {e}")
            file_url = ""

        # 构建消息
        message_parts = [f"📊 [{target_date}] 机游共振日历已更新（东财官方API·纯数据交叉版）\n"]

        if daily_data.institution_top5:
            message_parts.append(f"\n🏦 机构净买入TOP5:\n")
            for i, stock in enumerate(daily_data.institution_top5[:5], 1):
                message_parts.append(f"  {i}. {stock.name}  +{format_amount(stock.amount)}\n")

        if daily_data.institution_sell_top5:
            message_parts.append(f"\n🏦 机构净卖出TOP5:\n")
            for i, stock in enumerate(daily_data.institution_sell_top5[:5], 1):
                message_parts.append(f"  {i}. {stock.name}  {format_amount(stock.amount)}\n")

        if daily_data.resonance:
            message_parts.append(f"\n⭐ 机游共振信号（机构TOP5 ∩ 龙虎榜TOP{RESONANCE_LHB_TOP_N}）:\n")
            for res in daily_data.resonance:
                message_parts.append(f"  ★ {res.stock_name}: 机构+{format_amount(res.inst_amount)} / 龙虎榜+{format_amount(res.lhb_amount)}\n")

        if file_url:
            message_parts.append(f"\n🔗 [查看完整日历]({file_url})")
        if push_ok:
            message_parts.append(f"\n✅ GitHub已同步")

        message = "".join(message_parts)

        await sdk.submit_result(
            message=message,
            result_mode=actual_mode,
            status="success",
            data={
                "date": target_date,
                "institution_count": len(daily_data.institution_top5),
                "institution_sell_count": len(daily_data.institution_sell_top5),
                "resonance_count": len(daily_data.resonance),
                "pushed": push_ok,
                "data_source": "eastmoney_official_api_pure_data",
                "resonance_logic": f"机构TOP5 ∩ 龙虎榜TOP{RESONANCE_LHB_TOP_N}",
            },
        )
        print("✅ 更新完成")

    except Exception as e:
        print(f"❌ 执行失败: {e}")
        import traceback
        traceback.print_exc()
        await sdk.submit_result(
            result_mode="notify",
            status="error",
            message=f"机游共振日历更新失败: {e}",
            data={"error_type": type(e).__name__},
        )


if __name__ == "__main__":
    asyncio.run(main())
