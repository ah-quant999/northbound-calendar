#!/usr/bin/env python3
"""
机游共振日历自动更新脚本
功能：每天17:35自动搜索当日龙虎榜机构+游资数据，更新HTML文件并推送到GitHub

参数：
  --html_path: HTML文件路径 (默认: /app/data/所有对话/主对话/机游共振日历.html)
  --repo_path: Git仓库路径 (默认: /tmp/nb-calendar/)
  --force: 强制更新，忽略状态检查
  --date: 指定日期 (格式: YYYY-MM-DD, 默认: 今天)
  --result_mode: 结果模式 (默认: auto)

result_mode: auto
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

from codeact_sdk import CodeActSDK
from pydantic import BaseModel, Field

sdk = CodeActSDK()

# 工具schema版本
TOOL_SCHEMA_VERSIONS = {
    "codeact_search_web": "v1_5ac1b0eba8c26f2a",
    "codeact_fetch_web": "v1_2c8d0580b3f93a58",
    "file_to_url": "v1_fe3416acf3d7b53b",
}

# A股法定假日集合（落在工作日的休市日，周末已被rrule排除）
# 来源：上海证券交易所2026年休市安排
# https://www.sse.com.cn/disclosure/dealinstruc/closed
A_STOCK_HOLIDAYS_2026 = {
    # 元旦：1月1日-3日（1日周四、2日周五、3日周六工作日部分）
    "2026-01-01", "2026-01-02", "2026-01-03",
    # 春节：2月15日-23日（16日周一~20日周五、23日周一）
    "2026-02-16", "2026-02-17", "2026-02-18", "2026-02-19", "2026-02-20",
    "2026-02-23",
    # 清明节：4月4日-6日（6日周一）
    "2026-04-06",
    # 劳动节：5月1日-5日（1日周五、4日周一、5日周二）
    "2026-05-01", "2026-05-04", "2026-05-05",
    # 端午节：6月19日-21日（19日周五）
    "2026-06-19",
    # 中秋节：9月25日-27日（25日周五）
    "2026-09-25",
    # 国庆节：10月1日-7日（1日周四~7日周三）
    "2026-10-01", "2026-10-02", "2026-10-05", "2026-10-06", "2026-10-07",
}

# 补充：港股独立休市日（北向通道关闭，但A股正常交易）
HK_HOLIDAYS_2026 = {
    "2026-07-01",  # 香港回归纪念日
}

def is_a_stock_holiday(date_str: str) -> bool:
    """判断是否为A股休市日（法定假日落在工作日）"""
    return date_str in A_STOCK_HOLIDAYS_2026 or date_str in HK_HOLIDAYS_2026

# 数据库路径
STATE_DB = "./codeact/output/jiyou_resonance_state.db"

# GitHub配置 - 统一由 calendar_git 模块管理
from calendar_git import calendar_git_setup, calendar_git_push, calendar_git_pull, GIT_EMAIL, GIT_NAME, TOKEN, REPO


class InstitutionStock(BaseModel):
    """机构席位净买入数据"""
    name: str = Field(description="股票名称")
    amount: float = Field(description="净买入金额(万元)")
    code: str = Field(description="股票代码", default="")


class YouziItem(BaseModel):
    """游资席位数据"""
    name: str = Field(description="席位名称（如：佛山系·华天科技）")
    amount: float = Field(description="净买入金额(万元)，正数为买入，负数为卖出")
    stock: str = Field(description="关联股票名称", default="")


class ResonanceItem(BaseModel):
    """共振信号"""
    stock_name: str = Field(description="共振股票名称")
    youzi_items: List[str] = Field(description="游资席位描述", default_factory=list)


class DailyData(BaseModel):
    """单日机游共振数据"""
    date: str = Field(description="日期")
    institution_top5: List[InstitutionStock] = Field(description="机构净买入TOP5", default_factory=list)
    youzi_items: List[YouziItem] = Field(description="游资席位动向", default_factory=list)
    resonance: List[ResonanceItem] = Field(description="机游共振信号", default_factory=list)
    data_source: str = Field(description="数据来源", default="")


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
            youzi_items TEXT,
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
        return {
            "date": row[0],
            "updated_at": row[1],
            "data_source": row[2],
            "institution_top5": json.loads(row[3]) if row[3] else [],
            "youzi_items": json.loads(row[4]) if row[4] else [],
            "resonance": json.loads(row[5]) if row[5] else [],
            "pushed_at": row[6],
        }
    return None


def save_update(data: DailyData, pushed_at: Optional[str] = None):
    """保存更新记录"""
    conn = sqlite3.connect(STATE_DB)
    now = datetime.now().isoformat()
    conn.execute("""
        INSERT OR REPLACE INTO update_history
        (date, updated_at, data_source, institution_top5, youzi_items, resonance, pushed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        data.date,
        now,
        data.data_source,
        json.dumps([s.model_dump() for s in data.institution_top5], ensure_ascii=False),
        json.dumps([s.model_dump() for s in data.youzi_items], ensure_ascii=False),
        json.dumps([s.model_dump() for s in data.resonance], ensure_ascii=False),
        pushed_at or now,
    ))
    conn.commit()
    conn.close()


# ========== 数据获取 ==========

def build_publish_time_window(lookback_days: int) -> dict:
    """构造搜索时间窗口"""
    tz = timezone(timedelta(hours=8))
    end = datetime.now(tz)
    start = end - timedelta(days=lookback_days)
    return {
        "start": start.isoformat(timespec="seconds"),
        "end": end.isoformat(timespec="seconds"),
    }


async def search_data(date: str) -> List[Dict]:
    """搜索机构+游资龙虎榜数据"""
    date_obj = datetime.strptime(date, "%Y-%m-%d")
    month_day = f"{date_obj.month}月{date_obj.day}日"

    # 龙虎榜数据发布较晚（通常在18:00-19:00后），使用更宽的时间窗口
    # 覆盖多个数据源：东方财富网、证券时报·数据宝、金融界等
    queries = [
        f"{month_day} 龙虎榜 机构净买入 排名 数据宝 证券时报",
        f"{month_day} 龙虎榜 机构专用席位 净买入 top5",
        f"{month_day} 龙虎榜 游资 机构 席位 买入 详情",
        f"{month_day} 龙虎榜揭秘 机构净买入 游资动向",
        f"{month_day} 龙虎榜 机构席位 净买入 top5 东方财富",
        f"{month_day} 龙虎榜 机构 游资 净买入 金融界",
    ]

    all_results = []
    for query in queries:
        try:
            result = await sdk.call_tool(
                "codeact_search_web",
                {
                    "query": query,
                    "publish_time": build_publish_time_window(lookback_days=7),
                    "response_length": "medium",
                },
                schema_version=TOOL_SCHEMA_VERSIONS["codeact_search_web"],
            )
            if result.get("is_success") and result.get("results"):
                all_results.extend(result["results"])
        except Exception as e:
            print(f"搜索失败: {query}, 错误: {e}")

    # 去重并按来源排序
    seen_urls = set()
    unique_results = []
    for r in all_results:
        url = r.get("url", "").split("?")[0].split("#")[0].rstrip("/")
        if url and url not in seen_urls:
            seen_urls.add(url)
            unique_results.append(r)

    def source_score(r):
        url = r.get("url", "")
        if "eastmoney" in url or "finance.eastmoney" in url:
            return 0
        if "stcn.com" in url or "data.stcn" in url:
            return 1
        if "sina" in url or "sina.cn" in url:
            return 2
        if "toutiao" in url:
            return 3
        return 4

    unique_results.sort(key=source_score)
    return unique_results


async def fetch_page(url: str) -> Optional[str]:
    """获取网页内容"""
    try:
        result = await sdk.call_tool(
            "codeact_fetch_web",
            {"url": url},
            schema_version=TOOL_SCHEMA_VERSIONS["codeact_fetch_web"],
        )
        if result.get("is_success"):
            return result.get("content", "")
    except Exception as e:
        print(f"获取网页失败: {url}, 错误: {e}")
    return None


async def extract_data(content: str, date: str) -> Optional[DailyData]:
    """使用LLM从网页内容提取机构+游资龙虎榜数据"""
    prompt = f"""你是一个金融数据提取助手。请从以下网页内容中提取 {date} 的龙虎榜数据。

需要提取的信息：
1. **机构专用席位净买入TOP5**：当日机构专用席位净买入金额最大的前5只股票（名称+金额万元）
2. **游资席位动向**：知名游资席位的买卖情况（席位名称·股票，如"章盟主·多氟多"，金额万元）
3. **机游共振信号**：同一只股票同时出现机构净买入和知名游资净买入，标注为共振

识别游资席位的规则：
- 知名游资：章盟主、作手新一、佛山系、宁波桑田路、中山东路、北京中关村、溧阳路、赵老哥、炒股养家、欢乐海岸、小鳄鱼、刺客、著名刺客、方新侠、上塘路、西湖国贸、湖州劳动路、桑田路等
- 营业部游资：华泰证券某营业部、中信证券某营业部、国泰海通某营业部等知名游资聚集地
- 机构专用席位：标注为"机构专用"或"机构席位"的

金额单位统一为"万元"（原文是"亿元"则乘以10000，是"万"则不变）。

网页内容：
{content[:12000]}

请以JSON格式返回，不要有其他说明文字：
{{
    "institution_top5": [
        {{"name": "股票名称", "code": "股票代码", "amount": 机构净买入金额（万元）}},
        ...
    ],
    "youzi_items": [
        {{"name": "席位名称·股票（如：佛山系·华天科技）", "amount": 净买入金额（万元，正数净买，负数净卖）, "stock": "关联股票名称"}},
        ...
    ],
    "resonance": [
        {{"stock_name": "共振股票名称", "youzi_items": ["游资A·股票", "游资B·股票"]}},
        ...
    ]
}}
如果找不到相关数据，请返回：{{"institution_top5": [], "youzi_items": [], "resonance": []}}
"""
    try:
        response = await sdk.call_llm(
            messages=[
                {"role": "system", "content": "你是一个专业的金融数据提取助手，只返回JSON格式数据。"},
                {"role": "user", "content": prompt},
            ]
        )

        content_str = ""
        if isinstance(response, str):
            content_str = response
        elif isinstance(response, dict):
            content_str = response.get("content", response.get("text", str(response)))
        else:
            content_str = str(response)

        # 提取JSON
        if "```json" in content_str:
            match = re.search(r'```json\s*(.*?)\s*```', content_str, re.DOTALL)
            if match:
                content_str = match.group(1)
        elif "```" in content_str:
            match = re.search(r'```\s*(.*?)\s*```', content_str, re.DOTALL)
            if match:
                content_str = match.group(1)

        content_str = content_str.strip()
        if not content_str or content_str == "null":
            return None

        data = json.loads(content_str)
        if not isinstance(data, dict):
            return None

        institution_top5 = []
        for item in data.get("institution_top5", []):
            if isinstance(item, dict) and "name" in item and "amount" in item:
                institution_top5.append(InstitutionStock(
                    name=item["name"],
                    code=item.get("code", ""),
                    amount=float(item["amount"]),
                ))

        youzi_items = []
        for item in data.get("youzi_items", []):
            if isinstance(item, dict) and "name" in item:
                youzi_items.append(YouziItem(
                    name=item["name"],
                    amount=float(item.get("amount", 0)),
                    stock=item.get("stock", ""),
                ))

        resonance = []
        for item in data.get("resonance", []):
            if isinstance(item, dict) and "stock_name" in item:
                resonance.append(ResonanceItem(
                    stock_name=item["stock_name"],
                    youzi_items=item.get("youzi_items", []),
                ))

        return DailyData(
            date=date,
            institution_top5=institution_top5[:5],
            youzi_items=youzi_items,
            resonance=resonance,
            data_source="data_bao",
        )
    except Exception as e:
        print(f"提取数据失败: {e}")
        return None


# ========== HTML更新 ==========

def format_amount(amount_wan: float) -> str:
    """格式化金额"""
    if abs(amount_wan) >= 10000:
        return f"{amount_wan / 10000:.2f}亿"
    else:
        return f"{amount_wan:.0f}万"


def build_day_cell_html(data: DailyData) -> str:
    """构建机游共振日历日期单元格的HTML - 横向流式排版，共振不重复"""
    day = datetime.strptime(data.date, "%Y-%m-%d").day
    has_resonance = len(data.resonance) > 0
    has_data = data.institution_top5 or data.youzi_items

    if not has_data:
        return f"""                <div class="day-cell">
                    <div class="day-header"><span class="day-number">{day}</span></div>
                    <div class="empty-content">--</div>
                </div>"""

    # Build resonance stock name set for marking
    resonance_names = set()
    resonance_details = {}
    if data.resonance:
        for res in data.resonance:
            resonance_names.add(res.stock_name)
            details_parts = []
            if res.youzi_items:
                details_parts = res.youzi_items
            resonance_details[res.stock_name] = details_parts

    lines = []
    lines.append(f'                <div class="day-cell">')

    # day-header
    if has_resonance:
        lines.append(f'                    <div class="day-header"><span class="day-number">{day}</span><span class="amount resonance-tag">★共振</span></div>')
    else:
        lines.append(f'                    <div class="day-header"><span class="day-number">{day}</span></div>')

    lines.append(f'                    <div class="stock-list">')

    # 1. 机构净买入TOP5 - 横向流式，共振股票标★
    if data.institution_top5:
        lines.append(f'                        <div class="section-title">▲ 机构净买入</div>')
        lines.append(f'                        <div class="stock-flow">')
        for stock in data.institution_top5[:5]:
            amount_str = f"+{format_amount(stock.amount)}"
            if stock.name in resonance_names:
                lines.append(f'                            <span class="stock-chip resonance-chip">★{stock.name} {amount_str}</span>')
            else:
                lines.append(f'                            <span class="stock-chip buy-chip">▲{stock.name} {amount_str}</span>')
        lines.append(f'                        </div>')

    # 2. 机游共振详情（仅显示共振金额细节，不重复股票名）
    if data.resonance:
        lines.append(f'                        <div class="section-title resonance-title">★ 机游共振</div>')
        lines.append(f'                        <div class="stock-flow">')
        for res in data.resonance:
            detail_str = " ".join(res.youzi_items) if res.youzi_items else ""
            if detail_str:
                lines.append(f'                            <span class="stock-chip resonance-detail-chip">★{res.stock_name} {detail_str}</span>')
            else:
                lines.append(f'                            <span class="stock-chip resonance-detail-chip">★{res.stock_name}</span>')
        lines.append(f'                        </div>')

    # 3. 游资席位（红买绿卖，横向流式）
    if data.youzi_items:
        buys = [yz for yz in data.youzi_items if yz.amount >= 0]
        sells = [yz for yz in data.youzi_items if yz.amount < 0]
        
        if buys:
            lines.append(f'                        <div class="section-title youzi-title">▲ 游资买入</div>')
            lines.append(f'                        <div class="stock-flow">')
            for yz in buys[:4]:
                display_name = yz.name
                if yz.stock and yz.stock not in yz.name:
                    display_name = f"{yz.stock} {yz.name}"
                amount_str = f"+{format_amount(yz.amount)}"
                lines.append(f'                            <span class="stock-chip buy-chip">▲{display_name} {amount_str}</span>')
            lines.append(f'                        </div>')
        
        if sells:
            lines.append(f'                        <div class="section-title youzi-sell-title">▼ 游资卖出</div>')
            lines.append(f'                        <div class="stock-flow">')
            for yz in sells[:4]:
                display_name = yz.name
                if yz.stock and yz.stock not in yz.name:
                    display_name = f"{yz.stock} {yz.name}"
                amount_str = f"-{format_amount(abs(yz.amount))}"
                lines.append(f'                            <span class="stock-chip sell-chip">▼{display_name} {amount_str}</span>')
            lines.append(f'                        </div>')
    else:
        lines.append(f'                        <div class="section-title">▼ 游资席位动向</div>')
        lines.append(f'                        <div class="stock-flow"><span class="stock-chip" style="color:#6e7681;">暂无数据</span></div>')

    lines.append(f'                    </div>')
    lines.append(f'                </div>')
    return "\n".join(lines)


def update_html(html_path: str, data: DailyData) -> bool:
    """更新HTML文件中的指定日期数据（按月份+日期精确匹配，防止跨月误写）"""
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
    has_data = data.institution_top5 or data.youzi_items

    # 更新数据更新时间
    now = datetime.now()
    update_time_str = now.strftime("%Y-%m-%d %H:%M")
    html = re.sub(
        r'数据更新时间：\d{4}-\d{2}-\d{2} \d{2}:\d{2}',
        f'数据更新时间：{update_time_str}',
        html,
    )

    # 查找所有包含目标 day-number 的 td 标签，按月份+日期精确匹配
    all_matches = list(re.finditer(
        rf'(<td[^>]*>)\s*<div class="day-cell">.*?<span class="day-number">\s*{day}\s*</span>.*?</div>\s*</td>',
        html,
        re.DOTALL,
    ))

    if not all_matches:
        all_matches = list(re.finditer(
            rf'(<td[^>]*>)\s*<div class="day-cell">.*?<span class="day-number">\s*{day}\s*</span>.*?</div>\s*</div>\s*</td>',
            html,
            re.DOTALL,
        ))

    if all_matches:
        # 找到月份匹配的单元格
        target_match = None
        for m in all_matches:
            before = html[max(0, m.start() - 200):m.start()]
            date_comment = re.search(r'<!--\s*(\d+)/(\d+)\s', before)
            if date_comment:
                cell_month = int(date_comment.group(1))
                cell_day = int(date_comment.group(2))
                if cell_month == month and cell_day == day:
                    target_match = m
                    print(f"✅ 按注释匹配: {cell_month}/{cell_day} → 目标 {month}/{day}")
                    break
            else:
                if not target_match:
                    target_match = m

        if not target_match and all_matches:
            target_match = all_matches[0]
            print("⚠️ 未找到月份注释，使用第一个匹配")

        if target_match:
            td_open = target_match.group(1)
            # 如果有机游共振，给td加上has-resonance类
            if has_resonance and has_data:
                if 'class="' in td_open:
                    if 'has-resonance' not in td_open:
                        td_open = td_open.replace('class="', 'class="has-resonance ')
                else:
                    td_open = td_open.rstrip('>') + ' class="has-resonance">'

            new_html = f'{td_open}\n{new_cell_html}\n                    </td>'
            # 只替换这一处
            html = html[:target_match.start()] + new_html + html[target_match.end():]
            print(f"✅ 更新了 {month}月{day}日 的单元格")
        else:
            print(f"⚠️ 未找到 {month}月{day}日 的匹配单元格")
            return False
    else:
        print(f"⚠️ 未找到日期 {day} 日的单元格")
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


# ========== GitHub推送（委托 calendar_git 模块，强制 calendar-pages 分支） ==========

def git_push(repo_path: str, file_name: str, date: str) -> bool:
    """推送到GitHub（强制走 calendar-pages 分支）"""
    try:
        # 初始化并确保在正确分支
        if not calendar_git_setup(repo_path):
            print("❌ Git 初始化/分支切换失败")
            return False

        import shutil
        src = html_path
        dst = os.path.join(repo_path, file_name)
        shutil.copy2(src, dst)
        # 同时复制为 index.html 供 GitHub Pages 首页使用
        shutil.copy2(src, os.path.join(repo_path, "index.html"))
        print(f"📄 已复制到仓库: {os.path.join(repo_path, "index.html")}")
        print(f"📄 已复制到仓库: {dst}")

        # 委托共享模块推送
        return calendar_git_push(
            repo_path,
            [file_name, "index.html"],
            f"auto: 机游共振日历更新 {date}",
        )
    except Exception as e:
        print(f"Git推送失败: {e}")
        return False


# ========== 主函数 ==========

async def main():
    global html_path
    html_path = "/app/data/所有对话/主对话/机游共振日历.html"
    repo_path = "/tmp/nb-calendar/"
    force_update = False
    target_date = datetime.now().strftime("%Y-%m-%d")
    result_mode = "auto"

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
        else:
            i += 1

    actual_mode = result_mode if result_mode != "auto" else "display_only"
    file_name = os.path.basename(html_path)

    print(f"📅 目标日期: {target_date}")
    print(f"📄 HTML路径: {html_path}")
    print(f"📁 仓库路径: {repo_path}")
    print(f"🔧 强制模式: {force_update}")

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

        # 检查是否为A股法定假日（落在工作日的假期）
        if is_a_stock_holiday(target_date):
            print(f"🏛️ {target_date} 是A股法定假日，休市")
            await sdk.submit_result(
                message=f"[{target_date}] A股法定假日休市，无龙虎榜数据",
                result_mode="no_reply",
                status="success",
            )
            return

        # 搜索数据
        print("🔍 正在搜索机构+游资龙虎榜数据...")
        search_results = await search_data(target_date)

        # 第一轮没找到，用更宽泛的词再搜一轮
        if not search_results:
            print("⚠️ 第一轮未找到，用更宽泛的词再搜一轮...")
            month_day = f"{date_obj.month}月{date_obj.day}日"
            backup_queries = [
                f"{month_day} 龙虎榜 机构 买入 席位",
                f"{month_day} 龙虎榜 游资 营业部 买入",
                f"{month_day} 龙虎榜 揭秘 龙虎榜数据",
            ]
            for query in backup_queries:
                try:
                    result = await sdk.call_tool(
                        "codeact_search_web",
                        {
                            "query": query,
                            "publish_time": build_publish_time_window(lookback_days=7),
                            "response_length": "medium",
                        },
                        schema_version=TOOL_SCHEMA_VERSIONS["codeact_search_web"],
                    )
                    if result.get("is_success") and result.get("results"):
                        search_results.extend(result["results"])
                except Exception as e:
                    print(f"二次搜索失败: {query}, 错误: {e}")

        if not search_results:
            print("⚠️ 未找到搜索结果")
            empty_data = DailyData(date=target_date)
            update_html(html_path, empty_data)
            await sdk.submit_result(
                message=f"[{target_date}] 未找到龙虎榜机构/游资数据，已标记为暂无数据",
                result_mode="display_only",
                status="success",
            )
            return

        # 并行获取前5个结果的内容
        print(f"📄 找到 {len(search_results)} 个结果，正在并行获取详情...")
        all_data = []

        async def fetch_and_extract(result: dict, sem: asyncio.Semaphore):
            url = result.get("url", "")
            title = result.get("title", "")
            async with sem:
                content = await fetch_page(url)
                if content and len(content) > 200:
                    data = await extract_data(content, target_date)
                    if data and (data.institution_top5 or data.youzi_items):
                        data.data_source = url
                        return data
                return None

        sem = asyncio.Semaphore(3)
        tasks = [fetch_and_extract(r, sem) for r in search_results[:5]]
        task_results = await asyncio.gather(*tasks, return_exceptions=True)

        for r in task_results:
            if r and not isinstance(r, Exception):
                all_data.append(r)
                print(f"    ✅ 机构TOP5: {len(r.institution_top5)}, 游资: {len(r.youzi_items)}, 共振: {len(r.resonance)}")

        if not all_data:
            print("⚠️ 未能提取有效数据")
            empty_data = DailyData(date=target_date)
            update_html(html_path, empty_data)
            await sdk.submit_result(
                message=f"[{target_date}] 未能提取有效的机构/游资数据",
                result_mode="display_only",
                status="success",
            )
            return

        best_data = all_data[0]
        print(f"✅ 获取到数据: 机构TOP5 {len(best_data.institution_top5)} 只, 游资 {len(best_data.youzi_items)} 条, 共振 {len(best_data.resonance)} 个")

        # 更新HTML
        print("📝 正在更新HTML文件...")
        if not update_html(html_path, best_data):
            await sdk.submit_result(
                message=f"[{target_date}] 更新HTML文件失败",
                result_mode="notify",
                status="error",
            )
            return

        # 推送到GitHub
        print("📤 正在推送到GitHub...")
        push_ok = git_push(repo_path, file_name, target_date)
        pushed_at = datetime.now().isoformat() if push_ok else None

        save_update(best_data, pushed_at)

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
        message_parts = [f"📊 [{target_date}] 机游共振日历已更新\n"]

        if best_data.institution_top5:
            message_parts.append(f"\n🏦 机构净买入TOP5:\n")
            for i, stock in enumerate(best_data.institution_top5[:5], 1):
                message_parts.append(f"  {i}. {stock.name}  +{format_amount(stock.amount)}\n")

        if best_data.resonance:
            message_parts.append(f"\n⭐ 机游共振信号:\n")
            for res in best_data.resonance:
                message_parts.append(f"  ★ {res.stock_name}: {', '.join(res.youzi_items)}\n")

        if best_data.youzi_items:
            message_parts.append(f"\n🔥 游资席位动向:\n")
            for yz in best_data.youzi_items[:4]:
                if yz.amount >= 0:
                    message_parts.append(f"  {yz.name}: +{format_amount(yz.amount)}\n")
                else:
                    message_parts.append(f"  {yz.name}: {format_amount(yz.amount)}\n")

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
                "institution_count": len(best_data.institution_top5),
                "youzi_count": len(best_data.youzi_items),
                "resonance_count": len(best_data.resonance),
                "pushed": push_ok,
            },
        )
        print("✅ 更新完成")

    except Exception as e:
        print(f"❌ 执行失败: {e}")
        await sdk.submit_result(
            result_mode="notify",
            status="error",
            message=f"机游共振日历更新失败: {e}",
            data={"error_type": type(e).__name__},
        )


if __name__ == "__main__":
    asyncio.run(main())