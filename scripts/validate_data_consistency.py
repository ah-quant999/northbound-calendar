#!/usr/bin/env python3
"""
机游共振日历 数据一致性校验脚本（纯数据交叉版）

6步校验流水线：
  1. HTML结构校验（validate_calendar_html.py 已有，此处做数据层面）
  2. 机构净买入TOP5数量校验 = 5
  3. 机构净买入金额方向校验（必须为正）
  4. 机构净卖出金额方向校验（必须为负）
  5. 金额合理性校验（单日单只股票 < 100亿）
  6. 共振数据双向一致性：
       - 共振股票必须出现在机构净买入TOP5中
       - 共振股票必须出现在龙虎榜净买入TOP20中（API模式）
       - 有共振的日期必须有has-resonance类名

用法:
    python3 validate_data_consistency.py 机游共振日历.html
    python3 validate_data_consistency.py --date 2026-07-13 --mode api
    python3 validate_data_consistency.py --range 2026-07-01 2026-07-31 --mode html --html-path 机游共振日历.html
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
    get_lhb_stock_data,
    STATE_DB,
    RESONANCE_LHB_TOP_N,
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
    for k in ["institution_top5", "institution_sell_top5", "resonance"]:
        val = data.get(k)
        data[k] = json.loads(val) if val else []
    return data


# ========== 校验规则（API模式：用实时API数据校验）==========

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
    for item in data.get("institution_sell_top5", []):
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
    for item in data.get("institution_sell_top5", []):
        amount = abs(item.get("amount", 0))
        name = item.get("name", "未知")
        if amount > max_wan:
            errors.append(f"[{date_str}] 机构净卖出 {name} 金额={amount}万，超过{max_billion}亿（异常）")
    return errors


def validate_resonance_institution_side(data: Dict, date_str: str) -> List[str]:
    """校验规则5：共振股票必须在机构净买入TOP5中"""
    errors = []
    inst_names = {item.get("name") for item in data.get("institution_top5", [])}
    for res in data.get("resonance", []):
        stock = res.get("stock_name", "")
        if stock not in inst_names:
            errors.append(f"[{date_str}] 共振股票 {stock} 不在机构净买入TOP5中（不一致）")
    return errors


def validate_resonance_lhb_side(data: Dict, date_str: str) -> List[str]:
    """校验规则6：共振股票必须在龙虎榜净买入TOP N中（API模式下使用）"""
    errors = []
    # 只有在有完整龙虎榜数据时才能做这个校验
    if not data.get("_lhb_top_n_stocks"):
        return errors  # HTML模式下无此数据，跳过

    lhb_names = data["_lhb_top_n_stocks"]
    for res in data.get("resonance", []):
        stock = res.get("stock_name", "")
        if stock not in lhb_names:
            errors.append(
                f"[{date_str}] 共振股票 {stock} 不在龙虎榜净买入TOP{RESONANCE_LHB_TOP_N}中（不一致）"
            )
    return errors


def run_all_api_validations(data: Dict, date_str: str) -> List[str]:
    """运行所有API模式校验规则（6步）"""
    all_errors = []
    all_errors.extend(validate_institution_top5_count(data, date_str))
    all_errors.extend(validate_institution_buy_positive(data, date_str))
    all_errors.extend(validate_institution_sell_negative(data, date_str))
    all_errors.extend(validate_amount_reasonable(data, date_str))
    all_errors.extend(validate_resonance_institution_side(data, date_str))
    all_errors.extend(validate_resonance_lhb_side(data, date_str))
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


# ========== HTML解析校验模块 ==========

def extract_day_cells(html: str) -> list:
    """
    提取所有交易日单元格，返回列表，每元素为 dict:
      {month, day, is_other_month, date_label,
       inst_top5: [{name, amount, icon}],
       inst_sell: [{name, amount}],
       has_resonance_tag: bool,
       resonance_stocks: [{name, amount}],
       td_has_resonance_class: bool}
    """
    td_pattern = re.compile(
        r'<td\b([^>]*)>\s*<div class="day-cell">.*?</div>\s*</div>\s*</td>',
        re.DOTALL,
    )
    results = []

    month_sections = re.findall(
        r'<div class="month-section[^"]*" id="month-(\d+)"[^>]*>(.*?)(?=<div class="month-section|$)',
        html, re.DOTALL,
    )

    for month_str, section_html in month_sections:
        month = int(month_str)
        for td_match in td_pattern.finditer(section_html):
            td_attrs = td_match.group(1)
            td_html = td_match.group(0)

            day_m = re.search(r'<span class="day-number[^"]*">(\d+)</span>', td_html)
            if not day_m:
                continue
            day = int(day_m.group(1))

            is_other = "other-month" in td_attrs or "day-number other" in td_html
            td_has_resonance = "has-resonance" in td_attrs

            # 空内容跳过
            if 'class="empty-content"' in td_html:
                continue

            has_resonance_tag = bool(re.search(r'★共振', td_html))

            # 提取机构净买入TOP5
            inst_top5 = []
            inst_section = re.search(
                r'<div class="section-title[^"]*">▲ 机构净买入.*?</div>\s*<div class="stock-row">(.*?)</div>',
                td_html, re.DOTALL,
            )
            if inst_section:
                row_html = inst_section.group(1)
                items = re.findall(
                    r'<span class="stock-item">.*?'
                    r'<span class="stock-icon[^"]*">([^<]+)</span>.*?'
                    r'<span class="stock-name[^"]*">(.*?)</span>.*?'
                    r'<span class="stock-amount[^"]*">(.*?)</span>.*?'
                    r'</span>',
                    row_html, re.DOTALL,
                )
                for icon, name, amt in items:
                    inst_top5.append({
                        "name": name.strip(),
                        "amount": amt.strip(),
                        "icon": icon.strip(),
                    })

            # 提取机构净卖出TOP5
            inst_sell = []
            sell_section = re.search(
                r'<div class="section-title[^"]* sell-title[^"]*">▼ 机构净卖出.*?</div>\s*<div class="stock-row">(.*?)</div>',
                td_html, re.DOTALL,
            )
            if not sell_section:
                # 兼容旧版：section-title 不带 sell-title
                sell_section = re.search(
                    r'<div class="section-title[^"]*">▼ 机构净卖出.*?</div>\s*<div class="stock-row">(.*?)</div>',
                    td_html, re.DOTALL,
                )
            if sell_section:
                row_html = sell_section.group(1)
                items = re.findall(
                    r'<span class="stock-item">.*?'
                    r'<span class="stock-icon[^"]*">(.*?)</span>.*?'
                    r'<span class="stock-name[^"]*">(.*?)</span>.*?'
                    r'<span class="stock-amount[^"]*">(.*?)</span>.*?'
                    r'</span>',
                    row_html, re.DOTALL,
                )
                for icon, name, amt in items:
                    inst_sell.append({"name": name.strip(), "amount": amt.strip()})

            # 提取机游共振区
            resonance_stocks = []
            res_section = re.search(
                r'<div class="section-title[^"]* resonance-title[^"]*">★ 机游共振.*?</div>\s*<div class="stock-row">(.*?)</div>',
                td_html, re.DOTALL,
            )
            if res_section:
                row_html = res_section.group(1)
                items = re.findall(
                    r'<span class="stock-item">.*?'
                    r'<span class="stock-name[^"]*">(.*?)</span>.*?'
                    r'(?:<span class="stock-amount[^"]*">(.*?)</span>)?.*?'
                    r'</span>',
                    row_html, re.DOTALL,
                )
                for name, amt in items:
                    name = name.strip()
                    amt = amt.strip() if amt else ""
                    if name:
                        resonance_stocks.append({"name": name, "amount": amt})

            # 构建日期标签
            if is_other:
                # 判断属于上月还是下月
                if day > 15:
                    display_month = month - 1
                else:
                    display_month = month + 1
            else:
                display_month = month

            results.append({
                "month": month,
                "day": day,
                "is_other_month": is_other,
                "display_month": display_month,
                "date_label": f"{display_month}月{day}日",
                "inst_top5": inst_top5,
                "inst_sell": inst_sell,
                "has_resonance_tag": has_resonance_tag,
                "resonance_stocks": resonance_stocks,
                "td_has_resonance_class": td_has_resonance,
            })

    return results


def parse_amount_to_float(amt_str: str):
    """把金额字符串解析为万元（float），保留正负号；无法解析返回 None"""
    if not amt_str or amt_str == "--":
        return None
    s = amt_str.strip()
    if not s or s == "--":
        return None
    # 记录符号
    sign = 1.0
    if s.startswith("-"):
        sign = -1.0
        s = s[1:]
    elif s.startswith("+"):
        s = s[1:]
    s = s.strip()
    if not s:
        return None
    try:
        if s.endswith("亿"):
            return float(s[:-1]) * 10000 * sign
        if s.endswith("万"):
            return float(s[:-1]) * sign
        return float(s) * sign
    except ValueError:
        return None


def run_html_validations(cells: list) -> List[str]:
    """运行HTML模式的校验"""
    errors = []

    for cell in cells:
        label = cell["date_label"]
        inst_top5 = cell["inst_top5"]
        inst_sell = cell["inst_sell"]
        resonance = cell["resonance_stocks"]
        has_tag = cell["has_resonance_tag"]
        has_td_class = cell["td_has_resonance_class"]

        # 规则1：机构净买入数量（有数据的日期应该=5，空的已被过滤）
        if inst_top5 and len(inst_top5) != 5:
            errors.append(f"[{label}] 机构净买入数量={len(inst_top5)}，期望=5")

        # 规则2：机构净买入金额方向
        for s in inst_top5:
            amt = parse_amount_to_float(s["amount"])
            if amt is not None and amt <= 0:
                errors.append(f"[{label}] 机构净买入 {s['name']} 金额非正（方向错误）")

        # 规则3：机构净卖出金额方向
        for s in inst_sell:
            amt = parse_amount_to_float(s["amount"])
            if amt is not None and amt >= 0:
                errors.append(f"[{label}] 机构净卖出 {s['name']} 金额非负（方向错误）")

        # 规则4：金额合理性
        for s in inst_top5:
            amt = parse_amount_to_float(s["amount"])
            if amt is not None and amt > 1000000:  # 100亿
                errors.append(f"[{label}] 机构净买入 {s['name']} 金额异常={s['amount']}")

        # 规则5：共振股票必须也在机构TOP5中
        if resonance and inst_top5:
            inst_names = {s["name"] for s in inst_top5}
            # 共振区股票名可能带括号/额外信息，提取纯名称
            for r in resonance:
                name = r["name"]
                # 去掉括号及之后内容
                pure_name = re.split(r'[\s（(]', name)[0]
                if pure_name not in inst_names:
                    errors.append(f"[{label}] 共振股票 {pure_name} 不在机构净买入TOP5中")

        # 规则6：共振标签一致性
        if resonance:
            if not has_tag:
                errors.append(f"[{label}] 有共振数据但无★共振标签")
            if not has_td_class:
                errors.append(f"[{label}] 有共振数据但td无has-resonance类")
        else:
            if has_tag and inst_top5:  # 有数据但无共振的正常情况不应有tag
                errors.append(f"[{label}] 无共振数据但有★共振标签")

    # 额外：检查相邻日期数据完全重复（张冠李戴风险）
    sig_map = defaultdict(list)
    for cell in cells:
        if not cell["inst_top5"]:
            continue
        sig = tuple(s["name"] for s in cell["inst_top5"])
        sig_map[sig].append(cell["date_label"])

    for sig, dates in sig_map.items():
        if len(dates) >= 2:
            errors.append(
                f"【张冠李戴风险】{len(dates)} 个日期的机构TOP5股票完全相同："
                f"{', '.join(dates)}，股票: {list(sig)}"
            )

    return errors


# ========== 主函数 ==========

def validate_api_mode(date_str: str) -> int:
    """API模式：调用东财API获取数据并校验"""
    print(f"🔍 校验模式: API (目标日期: {date_str})")
    try:
        daily_data = build_daily_data(date_str)
        # 补充龙虎榜TOP N股票名用于校验
        lhb_data = get_lhb_stock_data(date_str)
        lhb_top_n = lhb_data["buy_sorted"][:RESONANCE_LHB_TOP_N]
        lhb_names = {s["name"] for s in lhb_top_n}

        data_dict = daily_data.model_dump()
        data_dict["_lhb_top_n_stocks"] = lhb_names

        errors = run_all_api_validations(data_dict, date_str)

        if errors:
            print(f"❌ 校验失败，共 {len(errors)} 个错误:")
            for e in errors:
                print(f"   - {e}")
            return 1
        else:
            print(f"✅ API模式校验通过（6步全过）")
            print(f"   机构TOP5: {len(daily_data.institution_top5)}只")
            print(f"   机构卖出TOP5: {len(daily_data.institution_sell_top5)}只")
            print(f"   共振信号: {len(daily_data.resonance)}个")
            return 0
    except Exception as e:
        print(f"❌ API校验异常: {e}")
        import traceback
        traceback.print_exc()
        return 1


def validate_html_mode(html_path: str) -> int:
    """HTML模式：从HTML文件解析并校验"""
    print(f"🔍 校验模式: HTML (文件: {html_path})")

    if not os.path.exists(html_path):
        print(f"❌ 文件不存在: {html_path}")
        return 1

    with open(html_path, "r", encoding="utf-8") as f:
        html = f.read()

    cells = extract_day_cells(html)
    print(f"   提取到 {len(cells)} 个有数据的日期单元格")

    errors = run_html_validations(cells)

    if errors:
        print(f"❌ 校验失败，共 {len(errors)} 个错误:")
        for e in errors:
            print(f"   - {e}")
        return 1
    else:
        print(f"✅ HTML模式校验通过（6步全过）")
        print(f"   校验单元格数: {len(cells)}")
        return 0


def validate_range_mode(start_date: str, end_date: str, mode: str, html_path: str = "") -> int:
    """范围模式：对指定日期范围逐一校验"""
    dates = date_range(start_date, end_date)
    print(f"🔍 范围校验: {start_date} ~ {end_date} ({len(dates)}天), 模式: {mode}")

    all_errors = []
    passed = 0
    skipped = 0

    for date_str in dates:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        # 跳过周末
        if dt.weekday() >= 5:
            skipped += 1
            continue

        if mode == "api":
            try:
                daily_data = build_daily_data(date_str)
                lhb_data = get_lhb_stock_data(date_str)
                lhb_top_n = lhb_data["buy_sorted"][:RESONANCE_LHB_TOP_N]
                lhb_names = {s["name"] for s in lhb_top_n}
                data_dict = daily_data.model_dump()
                data_dict["_lhb_top_n_stocks"] = lhb_names
                errors = run_all_api_validations(data_dict, date_str)
                if errors:
                    all_errors.extend(errors)
                else:
                    passed += 1
            except Exception as e:
                all_errors.append(f"[{date_str}] API调用异常: {e}")
        else:  # html mode
            # 范围模式下HTML需要从HTML解析
            if not os.path.exists(html_path):
                all_errors.append(f"HTML文件不存在: {html_path}")
                break
            # 这里不重复解析整个HTML，直接调用html mode一次
            break  # 用单独的html mode处理

    if mode == "html" and html_path:
        return validate_html_mode(html_path)

    if all_errors:
        print(f"❌ 范围校验失败，共 {len(all_errors)} 个错误:")
        for e in all_errors[:20]:
            print(f"   - {e}")
        if len(all_errors) > 20:
            print(f"   ... (共 {len(all_errors)} 个，仅显示前20个)")
        return 1
    else:
        print(f"✅ 范围校验通过: {passed}天通过, {skipped}天跳过(周末)")
        return 0


def main():
    parser = argparse.ArgumentParser(description="机游共振日历数据一致性校验（纯数据交叉版）")
    parser.add_argument("html_file", nargs="?", help="HTML文件路径（默认模式）")
    parser.add_argument("--date", help="单日期校验（API模式）")
    parser.add_argument("--range", nargs=2, metavar=("START", "END"), help="日期范围")
    parser.add_argument("--mode", choices=["api", "html"], default="html", help="校验模式（默认html）")
    parser.add_argument("--html-path", help="HTML文件路径（range模式下）")
    args = parser.parse_args()

    print("=" * 60)
    print("🧪 机游共振日历 — 6步数据一致性校验（纯数据交叉版）")
    print(f"📊 共振逻辑: 机构净买入TOP5 ∩ 龙虎榜净买入TOP{RESONANCE_LHB_TOP_N}")
    print("=" * 60)

    exit_code = 0

    if args.date:
        exit_code = validate_api_mode(args.date)
    elif args.range:
        exit_code = validate_range_mode(args.range[0], args.range[1], args.mode, args.html_path)
    elif args.html_file:
        exit_code = validate_html_mode(args.html_file)
    else:
        print("❌ 请指定 HTML文件路径 或 --date 或 --range")
        parser.print_help()
        exit_code = 2

    print("=" * 60)
    if exit_code == 0:
        print("🎉 全部校验通过！")
    else:
        print("💥 校验失败，请修复后重试")
    print("=" * 60)

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
