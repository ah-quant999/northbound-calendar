#!/usr/bin/env python3
"""
北向资金日历自动更新脚本
功能：每天17:30自动搜索当日龙虎榜北向资金数据，更新HTML文件并推送到GitHub

参数：
  --html_path: HTML文件路径 (默认: /app/data/所有对话/主对话/北向资金日历.html)
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
# 这些日期A股开盘、港股休市，北向资金无数据
HK_HOLIDAYS_2026 = {
    "2026-07-01",  # 香港回归纪念日
}

def is_a_stock_holiday(date_str: str) -> bool:
    """判断是否为A股休市日（法定假日落在工作日）"""
    return date_str in A_STOCK_HOLIDAYS_2026 or date_str in HK_HOLIDAYS_2026

# 数据库路径
STATE_DB = "./codeact/output/northbound_calendar_state.db"

# GitHub配置 - 统一由 calendar_git 模块管理
from calendar_git import calendar_git_setup, calendar_git_push, calendar_git_pull, GIT_EMAIL, GIT_NAME, TOKEN, REPO


class StockItem(BaseModel):
    """股票数据项"""
    name: str = Field(description="股票名称")
    amount: float = Field(description="净买入金额(万元)")
    code: str = Field(description="股票代码", default="")


class DailyData(BaseModel):
    """单日北向资金数据"""
    date: str = Field(description="日期")
    total_inflow: Optional[float] = Field(description="总净流入(万元)", default=None)
    top_buy: List[StockItem] = Field(description="净买入TOP5", default_factory=list)
    top_sell: List[StockItem] = Field(description="净卖出TOP5", default_factory=list)
    data_source: str = Field(description="数据来源", default="")


# ========== 状态管理 ==========

def init_state_db():
    """初始化状态数据库（含schema迁移）"""
    os.makedirs(os.path.dirname(STATE_DB), exist_ok=True)
    conn = sqlite3.connect(STATE_DB)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS update_history (
            date TEXT PRIMARY KEY,
            updated_at TEXT NOT NULL,
            data_source TEXT,
            total_inflow REAL,
            top_buy TEXT,
            top_sell TEXT
        )
    """)
    # 迁移：添加pushed_at列（旧表没有）
    try:
        conn.execute("ALTER TABLE update_history ADD COLUMN pushed_at TEXT")
    except Exception:
        pass  # 列已存在
    conn.commit()
    conn.close()


def get_last_update(date: str) -> Optional[Dict]:
    """获取某日的最后更新记录（兼容新旧schema）"""
    conn = sqlite3.connect(STATE_DB)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM update_history WHERE date = ?",
        (date,)
    )
    row = cursor.fetchone()
    conn.close()
    if row:
        return dict(row)
    return None


def save_update(data: DailyData, pushed_at: Optional[str] = None):
    """保存更新记录"""
    conn = sqlite3.connect(STATE_DB)
    now = datetime.now().isoformat()
    conn.execute("""
        INSERT OR REPLACE INTO update_history
        (date, updated_at, data_source, total_inflow, top_buy, top_sell, pushed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        data.date,
        now,
        data.data_source,
        data.total_inflow,
        json.dumps([s.model_dump() for s in data.top_buy], ensure_ascii=False),
        json.dumps([s.model_dump() for s in data.top_sell], ensure_ascii=False),
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


async def search_northbound_data(date: str) -> List[Dict]:
    """搜索北向资金龙虎榜数据"""
    date_obj = datetime.strptime(date, "%Y-%m-%d")
    month_day = f"{date_obj.month}月{date_obj.day}日"

    queries = [
        f"{month_day} 龙虎榜 沪股通 深股通 北向资金 净买入 数据宝",
        f"{month_day} 龙虎榜 深沪股通 净买入 数据宝 证券时报",
        f"{month_day} 龙虎榜 北向资金 席位 净买入",
    ]

    all_results = []
    for query in queries:
        try:
            result = await sdk.call_tool(
                "codeact_search_web",
                {
                    "query": query,
                    "publish_time": build_publish_time_window(lookback_days=3),
                    "response_length": "medium",
                },
                schema_version=TOOL_SCHEMA_VERSIONS["codeact_search_web"],
            )
            if result.get("is_success") and result.get("results"):
                all_results.extend(result["results"])
        except Exception as e:
            print(f"搜索失败: {query}, 错误: {e}")

    # 去重
    seen_urls = set()
    unique_results = []
    for r in all_results:
        url = r.get("url", "").split("?")[0].split("#")[0].rstrip("/")
        if url and url not in seen_urls:
            seen_urls.add(url)
            unique_results.append(r)

    # 按来源质量排序：东方财富 > 证券时报 > 其他
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
    """使用LLM从网页内容提取北向资金龙虎榜数据"""
    prompt = f"""你是一个金融数据提取助手。请从以下网页内容中提取 {date} 的龙虎榜北向资金（沪股通/深股通专用席位）数据。

要求：
1. 提取当日沪股通/深股通席位净买入TOP5（金额最大的前5只）
2. 提取当日沪股通/深股通席位净卖出TOP5（卖出金额最大的前5只，金额为正数）
3. 如果有"合计净买入"N亿元"或"总净买卖"等汇总数据，提取total_inflow（万元）
4. 金额单位统一为"万元"（如果原文是"亿元"则乘以10000）
5. 股票代码尽量提取，没有则留空

网页内容：
{content[:10000]}

请以JSON格式返回，不要有其他说明文字：
{{
    "total_inflow": 总净流入金额（万元，正数表示净流入，负数表示净流出，没有则填null）,
    "top_buy": [
        {{"name": "股票名称", "code": "股票代码", "amount": 净买入金额（万元）}},
        ...
    ],
    "top_sell": [
        {{"name": "股票名称", "code": "股票代码", "amount": 净卖出金额（万元，正数）}},
        ...
    ]
}}
如果找不到相关数据，请返回：{{"total_inflow": null, "top_buy": [], "top_sell": []}}
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

        top_buy = []
        for item in data.get("top_buy", []):
            if isinstance(item, dict) and "name" in item and "amount" in item:
                top_buy.append(StockItem(
                    name=item["name"],
                    code=item.get("code", ""),
                    amount=float(item["amount"]),
                ))

        top_sell = []
        for item in data.get("top_sell", []):
            if isinstance(item, dict) and "name" in item and "amount" in item:
                top_sell.append(StockItem(
                    name=item["name"],
                    code=item.get("code", ""),
                    amount=abs(float(item["amount"])),
                ))

        return DailyData(
            date=date,
            total_inflow=data.get("total_inflow"),
            top_buy=top_buy[:5],
            top_sell=top_sell[:5],
            data_source="data_bao",
        )
    except Exception as e:
        print(f"提取数据失败: {e}")
        return None


# ========== HTML更新 ==========

def format_amount(amount_wan: float) -> str:
    """格式化金额，万元转亿元显示"""
    if abs(amount_wan) >= 10000:
        return f"{amount_wan / 10000:.2f}亿"
    else:
        return f"{amount_wan:.0f}万"


def build_day_cell_html(data: DailyData) -> str:
    """构建日期单元格的HTML"""
    day = datetime.strptime(data.date, "%Y-%m-%d").day

    # 计算总净流入显示
    has_data = data.total_inflow is not None or data.top_buy or data.top_sell

    if not has_data:
        return f"""                <div class="day-cell">
                    <div class="day-header"><span class="day-number">{day}</span></div>
                    <div class="empty-content">--</div>
                </div>"""

    # 总净流入显示
    if data.total_inflow is not None:
        inflow_wan = data.total_inflow
        if inflow_wan >= 0:
            inflow_class = "inflow"
            inflow_text = f"净流入{format_amount(inflow_wan)}"
        else:
            inflow_class = "outflow"
            inflow_text = f"净流出{format_amount(abs(inflow_wan))}"
    else:
        inflow_class = "inflow"
        inflow_text = "数据已更新"

    lines = []
    lines.append(f'                <div class="day-cell">')
    lines.append(f'                    <div class="day-header"><span class="day-number">{day}</span><span class="amount {inflow_class}">{inflow_text}</span></div>')
    lines.append(f'                    <div class="stock-list">')

    if data.top_buy:
        lines.append(f'                        <div class="section-title">▲ 净买入TOP5</div>')
        for stock in data.top_buy[:5]:
            amount_str = f"+{format_amount(stock.amount)}"
            lines.append(f'                        <div class="stock-item"><span class="stock-icon up">▲</span><span class="stock-name">{stock.name}</span><span class="stock-amount up">{amount_str}</span></div>')

    if data.top_sell:
        lines.append(f'                        <div class="section-title">▼ 净卖出TOP5</div>')
        for stock in data.top_sell[:5]:
            amount_str = f"-{format_amount(stock.amount)}"
            lines.append(f'                        <div class="stock-item"><span class="stock-icon down">▼</span><span class="stock-name">{stock.name}</span><span class="stock-amount down">{amount_str}</span></div>')

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

    # 更新数据更新时间
    now = datetime.now()
    update_time_str = now.strftime("%Y-%m-%d %H:%M")
    html = re.sub(
        r'数据更新时间：\d{4}-\d{2}-\d{2} \d{2}:\d{2}',
        f'数据更新时间：{update_time_str}',
        html,
    )

    # 查找所有包含目标 day-number 的 td 标签
    # 遍历所有匹配，找到月份注释匹配的那个
    all_matches = list(re.finditer(
        rf'(<td[^>]*>)\s*<div class="day-cell">.*?<span class="day-number">\s*{day}\s*</span>.*?</div>\s*</td>',
        html,
        re.DOTALL,
    ))

    if not all_matches:
        # 尝试更宽松的匹配
        all_matches = list(re.finditer(
            rf'(<td[^>]*>)\s*<div class="day-cell">.*?<span class="day-number">\s*{day}\s*</span>.*?</div>\s*</div>\s*</td>',
            html,
            re.DOTALL,
        ))

    if all_matches:
        # 找到月份匹配的单元格
        target_match = None
        for m in all_matches:
            # 检查该单元格前的注释，匹配月份
            before = html[max(0, m.start() - 200):m.start()]
            # 找注释中的月份，如 <!-- 7/9 周四 --> 或 <!-- 6/9 周二 -->
            date_comment = re.search(r'<!--\s*(\d+)/(\d+)\s', before)
            if date_comment:
                cell_month = int(date_comment.group(1))
                cell_day = int(date_comment.group(2))
                if cell_month == month and cell_day == day:
                    target_match = m
                    print(f"✅ 按注释匹配: {cell_month}/{cell_day} → 目标 {month}/{day}")
                    break
            else:
                # 没有注释，可能是第一个月（跨月起始），也匹配
                if not target_match:
                    target_match = m

        # 如果没找到按月份的匹配，就退回到第一个匹配（兼容旧版无注释HTML）
        if not target_match and all_matches:
            target_match = all_matches[0]
            print("⚠️ 未找到月份注释，使用第一个匹配")

        if target_match:
            td_open = target_match.group(1)
            new_html = f'{td_open}\n{new_cell_html}\n                    </td>'
            # 只替换这一个匹配
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


# ========== Git操作（委托给 calendar_git 模块，强制 calendar-pages 分支） ==========

def git_pull(repo_path: str) -> bool:
    """拉取GitHub最新代码"""
    return calendar_git_pull(repo_path)

# ========== GitHub推送 ==========

def git_push(repo_path: str, file_name: str, date: str) -> bool:
    """推送到GitHub（强制走 calendar-pages 分支）"""
    try:
        # 初始化并确保在正确分支
        if not calendar_git_setup(repo_path):
            print("❌ Git 初始化/分支切换失败")
            return False

        # 复制HTML到仓库
        import shutil
        src = html_path
        dst = os.path.join(repo_path, file_name)
        shutil.copy2(src, dst)
        print(f"📄 已复制到仓库: {dst}")

        # 委托共享模块推送
        return calendar_git_push(
            repo_path,
            [file_name, "index.html"],
            f"auto: 北向资金日历更新 {date}",
        )
    except Exception as e:
        print(f"Git推送失败: {e}")
        return False


# ========== 主函数 ==========

async def main():
    # 解析参数
    global html_path
    html_path = "/app/data/所有对话/主对话/北向资金日历.html"
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
        # 初始化状态数据库
        init_state_db()

        # 检查是否已更新（非强制模式）
        if not force_update:
            last_update = get_last_update(target_date)
            if last_update and last_update.get("pushed_at"):
                print(f"✅ {target_date} 数据已更新并推送，跳过")
                await sdk.submit_result(
                    message=f"[{target_date}] 北向资金数据已是最新，无需更新",
                    result_mode="no_reply",
                    status="success",
                )
                return

        # 检查是否为交易日
        date_obj = datetime.strptime(target_date, "%Y-%m-%d")
        if date_obj.weekday() >= 5:
            print(f"📅 {target_date} 是周末，休市")
            await sdk.submit_result(
                message=f"[{target_date}] 周末休市，无北向资金数据",
                result_mode="no_reply",
                status="success",
            )
            return

        # 检查是否为A股法定假日（落在工作日的假期）
        if is_a_stock_holiday(target_date):
            print(f"🏛️ {target_date} 是A股法定假日，休市")
            await sdk.submit_result(
                message=f"[{target_date}] A股法定假日休市，无北向资金数据",
                result_mode="no_reply",
                status="success",
            )
            return

        # 搜索数据
        print("🔍 正在搜索北向资金数据...")
        search_results = await search_northbound_data(target_date)

        if not search_results:
            print("⚠️ 未找到搜索结果")
            # 更新HTML显示暂无数据
            empty_data = DailyData(date=target_date)
            update_html(html_path, empty_data)
            await sdk.submit_result(
                message=f"[{target_date}] 未找到龙虎榜北向资金数据，已标记为暂无数据",
                result_mode="display_only",
                status="success",
            )
            return

        # 获取前3个结果的内容
        print(f"📄 找到 {len(search_results)} 个结果，正在获取详情...")
        all_data = []
        for result in search_results[:5]:
            url = result.get("url", "")
            title = result.get("title", "")
            print(f"  - {title}")
            content = await fetch_page(url)
            if content and len(content) > 200:
                data = await extract_data(content, target_date)
                if data and (data.top_buy or data.top_sell):
                    data.data_source = url
                    all_data.append(data)
                    print(f"    ✅ 提取到 {len(data.top_buy)} 只买入, {len(data.top_sell)} 只卖出")
                    break  # 第一个有效数据就够了

        if not all_data:
            print("⚠️ 未能从搜索结果中提取有效数据")
            empty_data = DailyData(date=target_date)
            update_html(html_path, empty_data)
            await sdk.submit_result(
                message=f"[{target_date}] 未能提取有效的北向资金数据",
                result_mode="display_only",
                status="success",
            )
            return

        best_data = all_data[0]
        total_str = f"{best_data.total_inflow / 10000:+.2f}亿" if best_data.total_inflow is not None else "未知"
        print(f"✅ 获取到数据: 净流入 {total_str}")

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

        # 保存更新记录
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
        message_parts = [f"📊 [{target_date}] 北向资金日历已更新\n"]
        if best_data.total_inflow is not None:
            message_parts.append(f"💰 总净流入: {total_str}\n")
        message_parts.append(f"\n📈 净买入TOP5:\n")
        for i, stock in enumerate(best_data.top_buy[:5], 1):
            message_parts.append(f"  {i}. {stock.name}  +{format_amount(stock.amount)}\n")
        if best_data.top_sell:
            message_parts.append(f"\n📉 净卖出TOP5:\n")
            for i, stock in enumerate(best_data.top_sell[:5], 1):
                message_parts.append(f"  {i}. {stock.name}  -{format_amount(stock.amount)}\n")
        if file_url:
            message_parts.append(f"\n🔗 [查看完整日历]({file_url})")
        if push_ok:
            message_parts.append(f"\n✅ GitHub已同步")

        message = "".join(message_parts)

        actual_mode = "display_only"
        await sdk.submit_result(
            message=message,
            result_mode=actual_mode,
            status="success",
            data={
                "date": target_date,
                "total_inflow": best_data.total_inflow,
                "buy_count": len(best_data.top_buy),
                "sell_count": len(best_data.top_sell),
                "pushed": push_ok,
            },
        )
        print("✅ 更新完成")

    except Exception as e:
        print(f"❌ 执行失败: {e}")
        await sdk.submit_result(
            result_mode="notify",
            status="error",
            message=f"北向日历更新失败: {e}",
            data={"error_type": type(e).__name__},
        )


if __name__ == "__main__":
    asyncio.run(main())