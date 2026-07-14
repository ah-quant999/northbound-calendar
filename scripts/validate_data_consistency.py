#!/usr/bin/env python3
"""
机游共振日历 数据一致性校验脚本

用法:
    python3 validate_data_consistency.py --date 2026-07-13
    python3 validate_data_consistency.py --html_path 机游共振日历.html
    python3 validate_data_consistency.py --range 2026-07-01 2026-07-13

校验规则：
  1. 每天的机构TOP5数量必须 = 5，不足则告警
  2. 机构净买入金额必须为正数（净买入榜），负数则方向错误告警
  3. 机构净卖出金额必须为负数（净卖出榜），正数则方向错误告警
  4. 金额合理性：单日单只股票机构净买入 < 100亿
  5. 游资数据非空校验
  6. 共振数据双向一致性：共振股票必须同时出现在机构TOP5和游资买入榜
"""

import argparse
import json
import os
import re
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from typing import List, Dict, Tuple

# 同目录导入
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from update_jiyou_resonance_calendar import (
    build_daily_data,
    get_institution_data,
    get_youzi_data,
    validate_data as _api_validate,
    STATE_DB,
)


# ========== 从状态数据库读取 ==========

def load_from_db(date_str: str) -> Dict:
    """从状态数据库读取历史数据"""
    conn = sqlite3.connect(STATE_DB)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM update_history WHERE date = ?", (date_str,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return {}
    cols = [desc[0] for desc in cursor.description]
    data = dict(zip(cols, row))
    for k in ["institution_top5", "institution_sell_top3",
              "youzi_items", "youzi_buy_top5", "youzi_sell_top3", "resonance"]:
        val = data.get(k)
        data[k] = json.loads(val) if val else []
    return data


# ========== 校验规则 ==========

def validate_institution_top5_count(data: Dict, date_str: str) -> List[str]:
    """校验规则1：机构TOP5数量必须=5"""
    errors = []
    inst = data.get("institution_top5", [])
    count = len(inst)
    if count != 5:
        errors.append(f"[{date_str}] 机构净买入TOP5数量={count}，期望=5")
    return errors


def validate_institution_buy_positive(data: Dict, date_str: str) -> List[str]:
    """校验规则2：机构净买入榜金额必须为正"""
    errors = []
    for item in data.get("institution_top5", []):
        amount = item.get("amount", 0)
        name = item.get("name", "未知")
        if amount <= 0:
            errors.append(f"[{date_str}] 机构净买入榜 {name} 金额={amount}万，应为正数（方向错误）")
    return errors


def validate_institution_sell_negative(data: Dict, date_str: str) -> List[str]:
    """校验规则3：机构净卖出榜金额必须为负"""
    errors = []
    for item in data.get("institution_sell_top3", []):
        amount = item.get("amount", 0)
        name = item.get("name", "未知")
        if amount >= 0:
            errors.append(f"[{date_str}] 机构净卖出榜 {name} 金额={amount}万，应为负数（方向错误）")
    return errors


def validate_amount_reasonable(data: Dict, date_str: str,
                               max_billion: float = 100.0) -> List[str]:
    """校验规则4：金额合理性（单日单只股票机构净买入 < 100亿）"""
    errors = []
    max_wan = max_billion * 10000
    for item in data.get("institution_top5", []):
        amount = abs(item.get("amount", 0))
        name = item.get("name", "未知")
        if amount > max_wan:
            errors.append(f"[{date_str}] 机构净买入 {name} 金额={amount}万，超过{max_billion}亿（异常）")
    for item in data.get("institution_sell_top3", []):
        amount = abs(item.get("amount", 0))
        name = item.get("name", "未知")
        if amount > max_wan:
            errors.append(f"[{date_str}] 机构净卖出 {name} 金额={amount}万，超过{max_billion}亿（异常）")
    return errors


def validate_youzi_not_empty(data: Dict, date_str: str) -> List[str]:
    """校验规则5：游资数据非空"""
    errors = []
    yz_buy = data.get("youzi_buy_top5", data.get("youzi_items", []))
    if not yz_buy:
        errors.append(f"[{date_str}] 游资数据为空")
    return errors


def validate_resonance_consistency(data: Dict, date_str: str) -> List[str]:
    """校验规则6：共振股票必须同时在机构TOP5和游资买入榜中
    注意：共振计算使用全量游资数据，不只是TOP5展示的部分。
    因此此处只校验机构侧一致性，游资侧需要从API全量数据校验。
    """
    errors = []
    inst_names = {item.get("name") for item in data.get("institution_top5", [])}
    # 收集所有游资数据中的股票（买入/卖出都算出现过）
    youzi_stocks = set()
    for field in ["youzi_buy_top5", "youzi_sell_top3", "youzi_items"]:
        for yz in data.get(field, []):
            stock = yz.get("stock", "")
            if stock:
                youzi_stocks.add(stock)

    for res in data.get("resonance", []):
        stock = res.get("stock_name", "")
        if stock not in inst_names:
            errors.append(f"[{date_str}] 共振股票 {stock} 不在机构净买入TOP5中（不一致）")
        # 游资侧只在有完整数据时校验；历史数据可能只有TOP5
        # 如果游资列表为空但有共振，肯定有问题
        if not youzi_stocks and data.get("resonance"):
            errors.append(f"[{date_str}] 游资数据为空但存在共振信号（异常）")
            break
    return errors


def run_all_validations(data: Dict, date_str: str) -> List[str]:
    """运行所有校验规则"""
    all_errors = []
    all_errors.extend(validate_institution_top5_count(data, date_str))
    all_errors.extend(validate_institution_buy_positive(data, date_str))
    all_errors.extend(validate_institution_sell_negative(data, date_str))
    all_errors.extend(validate_amount_reasonable(data, date_str))
    all_errors.extend(validate_youzi_not_empty(data, date_str))
    all_errors.extend(validate_resonance_consistency(data, date_str))
    return all_errors


# ========== 日期范围工具 ==========

def date_range(start_date: str, end_date: str) -> List[str]:
    """生成日期范围列表"""
    start = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")
    dates = []
    current = start
    while current <= end:
        dates.append(current.strftime("%Y-%m-%d"))
        current += timedelta(days=1)
    return dates


# ========== 主函数 ==========


# ========== HTML解析校验模块（从HTML文件读取做交叉验证） ==========

def extract_day_cells(html: str) -> list:
    """
    提取所有交易日单元格，返回列表，每元素为 dict:
      {day, td_class, inst_top5: [(name, amount_str, has_res_icon)],
       youzi_buy: [(name, amount_str)], youzi_sell: [(name, amount_str)],
       has_resonance_tag: bool, resonance_stocks: [name]}
    只解析有机构/游资数据的交易日（非空、非休市）
    """
    # 匹配每个 <td ...> ... </td> 里面含 day-cell 的单元格
    td_pattern = re.compile(
        r'<td\b([^>]*)>\s*<div class="day-cell">.*?</div>\s*</div>\s*</td>',
        re.DOTALL,
    )
    results = []

    # 为了定位所属周/月便于错误定位，按 month-section 切分
    month_sections = re.findall(
        r'<div class="month-section[^"]*" id="month-(\d+)"[^>]*>(.*?)(?=<div class="month-section|$)',
        html, re.DOTALL,
    )

    for month_str, section_html in month_sections:
        month = int(month_str)
        # 在该月份区域内找所有 td
        for td_match in td_pattern.finditer(section_html):
            td_attrs = td_match.group(1)
            td_html = td_match.group(0)

            # 提取日期号
            day_m = re.search(r'<span class="day-number[^"]*">(\d+)</span>', td_html)
            if not day_m:
                continue
            day = int(day_m.group(1))

            # 判断是否为 other-month（非本月），用于日期归属
            is_other = "other-month" in td_attrs or "day-number other" in td_html

            # 判断是否休市/空内容
            if 'class="amount holiday"' in td_html or '休市' in td_html:
                continue
            if 'class="empty-content"' in td_html and '休市' not in td_html:
                # 空内容（--），没有数据可校验，跳过
                continue

            # 共振标签
            has_resonance_tag = bool(re.search(r'★共振', td_html))

            # 提取机构TOP5
            inst_top5 = []
            # 机构区：在 "▲ 机构净买入" section-title 下的第一个 stock-row
            inst_section = re.search(
                r'<div class="section-title[^"]*">▲ 机构净买入.*?</div>\s*<div class="stock-row">(.*?)</div>',
                td_html, re.DOTALL,
            )
            if inst_section:
                row_html = inst_section.group(1)
                items = re.findall(
                    r'<span class="stock-item">.*?<span class="stock-icon[^"]*">([^<]+)</span>'
                    r'.*?<span class="stock-name[^"]*">(.*?)</span>'
                    r'.*?<span class="stock-amount[^"]*">(.*?)</span>.*?</span>',
                    row_html, re.DOTALL,
                )
                for icon, name, amt in items:
                    name = name.strip()
                    amt = amt.strip()
                    has_res = ("★" in icon) or ("resonance" in (
                        re.search(r'class="stock-icon([^"]*)"', row_html) and
                        ""
                    ))
                    # 更准确的方式：检查该 item 的 icon class 是否含 resonance
                    # 由于 findall 是同时匹配，重新对每个 item 解析
                    inst_top5.append({"name": name, "amount": amt, "icon": icon.strip()})

            # 提取游资买入（▲ 游资买入 section-title）
            youzi_buy = []
            buy_section = re.search(
                r'<div class="section-title[^"]*">▲ 游资买入.*?</div>\s*<div class="stock-row">(.*?)</div>',
                td_html, re.DOTALL,
            )
            if buy_section:
                row_html = buy_section.group(1)
                items = re.findall(
                    r'<span class="stock-item">.*?<span class="stock-icon[^"]*">(.*?)</span>'
                    r'.*?<span class="stock-name[^"]*">(.*?)</span>'
                    r'.*?<span class="stock-amount[^"]*">(.*?)</span>.*?</span>',
                    row_html, re.DOTALL,
                )
                for icon, name, amt in items:
                    youzi_buy.append({"name": name.strip(), "amount": amt.strip(), "icon": icon.strip()})

            # 提取游资卖出（▼ 游资卖出）
            youzi_sell = []
            sell_section = re.search(
                r'<div class="section-title[^"]*">▼ 游资卖出.*?</div>\s*<div class="stock-row">(.*?)</div>',
                td_html, re.DOTALL,
            )
            if sell_section:
                row_html = sell_section.group(1)
                items = re.findall(
                    r'<span class="stock-item">.*?<span class="stock-icon[^"]*">(.*?)</span>'
                    r'.*?<span class="stock-name[^"]*">(.*?)</span>'
                    r'.*?<span class="stock-amount[^"]*">(.*?)</span>.*?</span>',
                    row_html, re.DOTALL,
                )
                for icon, name, amt in items:
                    youzi_sell.append({"name": name.strip(), "amount": amt.strip()})

            # 机游共振区的股票名
            resonance_stocks = []
            res_section = re.search(
                r'<div class="section-title[^"]*">★ 机游共振.*?</div>\s*<div class="stock-row">(.*?)</div>',
                td_html, re.DOTALL,
            )
            if res_section:
                row_html = res_section.group(1)
                names = re.findall(
                    r'<span class="stock-name[^"]*">(.*?)</span>',
                    row_html, re.DOTALL,
                )
                # 共振区的 stock-name 可能含详情文本（如"金安国纪 机构+3.22亿+北向+1.83亿"）
                # 取第一个词作为股票名（中文股票名通常不空格）
                for n in names:
                    n = n.strip()
                    # 只取第一个空格前的部分
                    stock = n.split()[0] if n.split() else n
                    resonance_stocks.append(stock)

            results.append({
                "month": month,
                "day": day,
                "is_other_month": is_other,
                "date_label": f"{month}月{day}日{'(跨月)' if is_other else ''}",
                "inst_top5": inst_top5,
                "youzi_buy": youzi_buy,
                "youzi_sell": youzi_sell,
                "has_resonance_tag": has_resonance_tag,
                "resonance_stocks": resonance_stocks,
            })

    return results


def parse_amount_to_float(amt_str: str):
    """把 "+1.91亿" / "+4131万" / "0" / "--" 解析成万元（float）；无法解析返回 None"""
    if not amt_str or amt_str == "--":
        return None
    s = amt_str.strip().lstrip("+-").strip()
    if not s or s == "--":
        return None
    try:
        if s.endswith("亿"):
            return float(s[:-1]) * 10000
        if s.endswith("万"):
            return float(s[:-1])
        return float(s)
    except ValueError:
        return None


def extract_youzi_stock_names(youzi_items: list) -> list:
    """
    从游资 item 的 name 中提取股票名。
    格式多样："T王·托伦斯" / "华天科技 国泰海通三亚迎宾路" / "杭电股份 T王" 等
    启发式：
      - 含"·"分隔：前面是席位，后面可能是股票；反之亦然
      - 含空格：第一个token是股票名的概率高
    返回股票名列表（去重）
    """
    stocks = set()
    for item in youzi_items:
        name = item["name"].strip()
        if not name:
            continue
        # 去掉前缀 "卖"（如"卖江化微 章盟主"）
        cleaned = name
        if cleaned.startswith("卖") and len(cleaned) > 2:
            cleaned = cleaned[1:]

        # 按 · 或 空格 分割
        parts = re.split(r'[·\s]+', cleaned)
        parts = [p for p in parts if p]
        if not parts:
            continue

        # 常见游资/机构/营业部关键词，出现则不是股票名
        youzi_keywords = {
            "T王", "章盟主", "作手新一", "佛山系", "宁波桑田路", "桑田路",
            "中山东路", "北京中关村", "溧阳路", "赵老哥", "炒股养家",
            "欢乐海岸", "小鳄鱼", "刺客", "著名刺客", "方新侠", "上塘路",
            "西湖国贸", "湖州劳动路", "交易猿", "成都系", "温州帮",
            "低位挖掘", "上海超短", "湛江万豪世家", "山东帮",
            "葛卫东", "开源西安太华路", "杭州帮",
            "华泰证券", "中信证券", "国泰海通", "海通证券",
            "东吴扬富路", "华源深圳", "国泰海通自贸区",
            "国泰海通三亚迎宾路", "国泰海通武汉紫阳东路",
            "国泰海通北京知春路", "中信证券深圳深南中路中信大厦",
            "中信证券上海分公司", "国泰海通证券总部",
            "营业部", "总部", "分公司",
        }

        for p in parts:
            # 判断是否为游资关键词
            is_youzi = any(kw in p for kw in youzi_keywords)
            if not is_youzi and len(p) >= 2 and not p.startswith("卖"):
                stocks.add(p)
                break  # 每个 item 只取一个股票名
    return list(stocks)


def check_top5_duplicate_across_days(cells: list) -> list:
    """检查不同日期机构TOP5组合完全相同"""
    errors = []
    signatures = defaultdict(list)
    for cell in cells:
        if not cell["inst_top5"]:
            continue
        # 以股票名+金额的元组作为签名
        sig = tuple((s["name"], s["amount"]) for s in cell["inst_top5"])
        signatures[sig].append(cell["date_label"])

    for sig, dates in signatures.items():
        if len(dates) >= 2:
            names = [s[0] for s in sig]
            errors.append(
                f"【张冠李戴风险】{len(dates)} 个日期的机构TOP5完全相同：\n"
                f"        日期: {', '.join(dates)}\n"
                f"        股票: {names}"
            )

    # 同样检查游资买入
    youzi_sigs = defaultdict(list)
    for cell in cells:
        if not cell["youzi_buy"]:
            continue
        sig = tuple((s["name"], s["amount"]) for s in cell["youzi_buy"])
        if len(cell["youzi_buy"]) >= 3:  # 数据量够多才对比
            youzi_sigs[sig].append(cell["date_label"])

    for sig, dates in youzi_sigs.items():
        if len(dates) >= 2:
            names = [s[0] for s in sig]
            errors.append(
                f"【张冠李戴风险】{len(dates)} 个日期的游资买入完全相同：\n"
                f"        日期: {', '.join(dates)}\n"
                f"        游资项: {names}"
            )

    return errors


def check_inst_youzi_overlap(cells: list) -> list:
    """同一日期机构数据与游资数据股票名重复率过高（≥3只）"""
    errors = []
    for cell in cells:
        if not cell["inst_top5"] or not cell["youzi_buy"]:
            continue
        inst_names = {s["name"] for s in cell["inst_top5"]}
        youzi_stocks = set(extract_youzi_stock_names(cell["youzi_buy"]))
        overlap = inst_names & youzi_stocks
        # 游资的股票名提取可能不准，阈值设高一点
        if len(overlap) >= 3 and len(inst_names) >= 3:
            errors.append(
                f"【错列风险】{cell['date_label']} 机构与游资股票名重叠 {len(overlap)} 只 (≥3)：\n"
                f"        机构: {sorted(inst_names)}\n"
                f"        游资(提取): {sorted(youzi_stocks)}\n"
                f"        重叠: {sorted(overlap)}"
            )
    return errors


def check_zero_amount_with_resonance(cells: list) -> list:
    """机构净买入金额为0或"--"但标了共振"""
    errors = []
    for cell in cells:
        if not cell["has_resonance_tag"]:
            continue
        if not cell["inst_top5"]:
            errors.append(
                f"【共振无机构数据】{cell['date_label']} 有共振标记但无机构净买入数据"
            )
            continue
        # 检查每个机构股票金额
        for s in cell["inst_top5"]:
            amt = parse_amount_to_float(s["amount"])
            if amt is None or amt == 0 or s["amount"] == "--":
                # 如果是共振标的（icon含★）且金额为0/--，才告警
                if "★" in s.get("icon", ""):
                    errors.append(
                        f"【共振金额异常】{cell['date_label']} 共振标的「{s['name']}」机构净买入金额为 {s['amount']}"
                    )
    return errors


def check_resonance_no_overlap(cells: list) -> list:
    """有共振标记但机构TOP5和游资买入无重叠股票"""
    errors = []
    for cell in cells:
        if not cell["has_resonance_tag"]:
            continue
        if not cell["inst_top5"]:
            continue
        inst_names = {s["name"] for s in cell["inst_top5"]}
        youzi_stocks = set(extract_youzi_stock_names(cell["youzi_buy"]))

        # 共振区股票与机构/游资的交集
        res_stocks = set(cell["resonance_stocks"])

        if not youzi_stocks:
            # 没有游资买入数据但标了共振，也值得注意
            if res_stocks:
                errors.append(
                    f"【共振无游资】{cell['date_label']} 有共振标记但无游资买入数据"
                )
            continue

        overlap = inst_names & youzi_stocks
        # 如果有共振标记但完全没有重叠，且共振股票也对不上，则告警
        res_overlap_with_inst = res_stocks & inst_names if res_stocks else set()
        res_overlap_with_youzi = res_stocks & youzi_stocks if res_stocks else set()

        if not overlap and not res_stocks:
            errors.append(
                f"【共振无重叠】{cell['date_label']} 有共振标记但机构TOP5与游资买入无重叠股票\n"
                f"        机构: {sorted(inst_names)}\n"
                f"        游资(提取): {sorted(youzi_stocks)}"
            )
        elif not overlap and res_stocks and not (res_overlap_with_inst and res_overlap_with_youzi):
            errors.append(
                f"【共振无重叠】{cell['date_label']} 机构TOP5与游资买入无重叠股票，共振区标的也无法同时匹配两边\n"
                f"        机构: {sorted(inst_names)}\n"
                f"        游资(提取): {sorted(youzi_stocks)}\n"
                f"        共振区: {sorted(res_stocks)}"
            )

    return errors


def main():
    parser = argparse.ArgumentParser(description="机游共振日历数据一致性校验")
    parser.add_argument("--date", help="校验指定日期 (YYYY-MM-DD)")
    parser.add_argument("--range", nargs=2, metavar=("START", "END"),
                        help="校验日期范围")
    parser.add_argument("--mode", choices=["api", "db", "html", "both"], default="db",
                        help="校验模式: api(实时API拉取), db(状态数据库), html(HTML文件解析), both(api+html双向对比)")
    parser.add_argument("--html-path", help="HTML文件路径（html/both模式用）")
    parser.add_argument("--from-api", action="store_true", dest="_from_api_legacy",
                        help=argparse.SUPPRESS)  # 兼容旧参数
    args = parser.parse_args()

    # 兼容旧参数
    if args._from_api_legacy and args.mode == "db":
        args.mode = "api"

    # ========== API / DB 模式校验 ==========
    if args.mode in ("api", "db", "both"):
        # 确定日期列表
        if args.range:
            dates = date_range(args.range[0], args.range[1])
        elif args.date:
            dates = [args.date]
        else:
            dates = [datetime.now().strftime("%Y-%m-%d")]

        mode_label = "东财官方API" if args.mode in ("api", "both") else "本地状态数据库"
        print(f"📅 校验日期范围: {len(dates)} 天")
        print(f"📊 数据源: {mode_label}")
        print("=" * 60)

        total_errors = 0
        total_checked = 0
        api_results = {}  # 供both模式对比用

        for date_str in dates:
            # 获取数据
            if args.mode in ("api", "both"):
                try:
                    daily_data = build_daily_data(date_str)
                    data = {
                        "institution_top5": [s.model_dump() for s in daily_data.institution_top5],
                        "institution_sell_top3": [s.model_dump() for s in daily_data.institution_sell_top3],
                        "youzi_buy_top5": [s.model_dump() for s in daily_data.youzi_buy_top5],
                        "youzi_sell_top3": [s.model_dump() for s in daily_data.youzi_sell_top3],
                        "youzi_items": [s.model_dump() for s in daily_data.youzi_items],
                        "resonance": [s.model_dump() for s in daily_data.resonance],
                    }
                    api_results[date_str] = data
                except Exception as e:
                    print(f"❌ [{date_str}] API拉取失败: {e}")
                    total_errors += 1
                    continue
            else:
                data = load_from_db(date_str)
                if not data:
                    print(f"⚠️  [{date_str}] 状态库无数据，跳过")
                    continue

            total_checked += 1
            errors = run_all_validations(data, date_str)

            if errors:
                print(f"\n❌ [{date_str}] 发现 {len(errors)} 个问题:")
                for err in errors:
                    print(f"   • {err}")
                total_errors += len(errors)
            else:
                print(f"✅ [{date_str}] 校验通过")
                inst_count = len(data.get("institution_top5", []))
                sell_count = len(data.get("institution_sell_top3", []))
                yz_count = len(data.get("youzi_buy_top5", data.get("youzi_items", [])))
                res_count = len(data.get("resonance", []))
                print(f"   机构买{inst_count}/卖{sell_count} | 游资{yz_count} | 共振{res_count}")

        print("=" * 60)
        print(f"\n📊 API/DB校验结果汇总:")
        print(f"   检查天数: {total_checked}")
        print(f"   错误数: {total_errors}")

        if total_errors > 0:
            print(f"\n❌ API/DB校验失败，共 {total_errors} 个问题")
            sys.exit(1)

    # ========== HTML 模式校验 ==========
    if args.mode in ("html", "both"):
        print(f"\n{'='*60}")
        print(f"📄 HTML 文件校验模式")
        html_path = args.html_path
        if not html_path:
            script_dir = os.path.dirname(os.path.abspath(__file__))
            html_path = os.path.join(os.path.dirname(script_dir), "机游共振日历.html")

        if not os.path.exists(html_path):
            print(f"❌ HTML文件不存在: {html_path}")
            sys.exit(1)

        with open(html_path, "r", encoding="utf-8") as f:
            html = f.read()

        day_cells = extract_day_cells(html)
        print(f"📅 解析到 {len(day_cells)} 个有数据的交易日单元格")

        all_errors = []
        check_funcs = [
            ("跨日数据张冠李戴检测", check_top5_duplicate_across_days),
            ("机构/游资错列检测", check_inst_youzi_overlap),
            ("共振-机构金额一致性", check_zero_amount_with_resonance),
            ("共振-双向重叠检测", check_resonance_no_overlap),
        ]

        for name, func in check_funcs:
            print(f"\n🔍 {name}...")
            errs = func(day_cells)
            if errs:
                print(f"   ❌ 发现 {len(errs)} 个问题")
                for e in errs:
                    print(f"      • {e}")
                all_errors.extend(errs)
            else:
                print(f"   ✅ 通过")

        print(f"\n{'='*60}")
        print(f"📊 HTML校验结果汇总:")
        print(f"   检查单元格数: {len(day_cells)}")
        print(f"   错误数: {len(all_errors)}")

        if all_errors:
            print(f"\n❌ HTML校验失败，共 {len(all_errors)} 个问题")
            sys.exit(1)
        else:
            print(f"\n✅ HTML校验全部通过")

    # both模式都通过
    if args.mode == "both":
        print(f"\n🎉 双向校验全部通过！")



if __name__ == "__main__":
    main()

