#!/usr/bin/env python3
"""
机游共振日历 数据一致性校验脚本（纯数据交叉版 v2）

6步校验流水线：
  1. 机构净买入TOP5数量校验 = 5
  2. 机构净买入金额方向校验（必须为正）
  3. 机构净卖出金额方向校验（必须为负）
  4. 金额合理性校验（单日单只股票 < 100亿）
  5. 共振股票在机构TOP5中（机构侧一致性）
  6. 共振股票在游资净买入TOP20中（游资侧一致性，仅API模式）

附加校验：
  - 游资净买入TOP5数量校验
  - 游资净卖出TOP5数量校验
  - 共振标签与共振数据一致性
  - 张冠李戴检测（相邻日期TOP5完全相同）

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
from typing import List, Dict

# 同目录导入
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from update_jiyou_resonance_calendar import (
    build_daily_data,
    get_institution_data,
    get_youzi_stock_data,
    STATE_DB,
    RESONANCE_YOUZI_TOP_N,
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
    for k in ["institution_top5", "institution_sell_top5",
               "youzi_buy_top5", "youzi_sell_top5", "resonance"]:
        val = data.get(k)
        data[k] = json.loads(val) if val else []
    return data


# ========== 校验规则（API模式）==========

def validate_institution_top5_count(data: Dict, date_str: str) -> List[str]:
    """规则1：机构TOP5数量必须=5"""
    errors = []
    inst = data.get("institution_top5", [])
    if len(inst) != 5:
        errors.append(f"[{date_str}] 机构净买入TOP5数量={len(inst)}，期望=5")
    return errors


def validate_institution_buy_positive(data: Dict, date_str: str) -> List[str]:
    """规则2：机构净买入榜金额必须为正"""
    errors = []
    for item in data.get("institution_top5", []):
        amount = item.get("amount", 0) if isinstance(item, dict) else item.amount
        name = item.get("name", "未知") if isinstance(item, dict) else item.name
        if amount <= 0:
            errors.append(f"[{date_str}] 机构净买入榜 {name} 金额={amount}万，应为正数")
    return errors


def validate_institution_sell_negative(data: Dict, date_str: str) -> List[str]:
    """规则3：机构净卖出榜金额必须为负"""
    errors = []
    for item in data.get("institution_sell_top5", []):
        amount = item.get("amount", 0) if isinstance(item, dict) else item.amount
        name = item.get("name", "未知") if isinstance(item, dict) else item.name
        if amount >= 0:
            errors.append(f"[{date_str}] 机构净卖出榜 {name} 金额={amount}万，应为负数")
    return errors


def validate_amount_reasonable(data: Dict, date_str: str,
                               max_billion: float = 100.0) -> List[str]:
    """规则4：金额合理性（单日单只 < 100亿）"""
    errors = []
    max_wan = max_billion * 10000
    for field in ["institution_top5", "institution_sell_top5",
                  "youzi_buy_top5", "youzi_sell_top5"]:
        for item in data.get(field, []):
            amount = abs(item.get("amount", 0) if isinstance(item, dict) else item.amount)
            name = item.get("name", "未知") if isinstance(item, dict) else item.name
            if amount > max_wan:
                errors.append(f"[{date_str}] {field} {name} 金额={amount}万，超过{max_billion}亿")
    return errors


def validate_resonance_institution_side(data: Dict, date_str: str) -> List[str]:
    """规则5：共振股票必须在机构净买入TOP5中"""
    errors = []
    inst_names = {item.get("name") if isinstance(item, dict) else item.name
                  for item in data.get("institution_top5", [])}
    for res in data.get("resonance", []):
        stock = res.get("stock_name", "") if isinstance(res, dict) else res.stock_name
        if stock not in inst_names:
            errors.append(f"[{date_str}] 共振股票 {stock} 不在机构净买入TOP5中")
    return errors


def validate_resonance_youzi_side(data: Dict, date_str: str) -> List[str]:
    """规则6：共振股票必须在游资净买入TOP N中（API模式）"""
    errors = []
    youzi_names = data.get("_youzi_top_n_stocks", set())
    if not youzi_names:
        return errors  # HTML模式无此数据，跳过
    for res in data.get("resonance", []):
        stock = res.get("stock_name", "") if isinstance(res, dict) else res.stock_name
        if stock not in youzi_names:
            errors.append(
                f"[{date_str}] 共振股票 {stock} 不在游资净买入TOP{RESONANCE_YOUZI_TOP_N}中"
            )
    return errors


def validate_youzi_top5_count(data: Dict, date_str: str) -> List[str]:
    """附加：游资净买卖TOP5数量校验"""
    errors = []
    buy = data.get("youzi_buy_top5", [])
    sell = data.get("youzi_sell_top5", [])
    if len(buy) != 5:
        errors.append(f"[{date_str}] 游资净买入TOP5数量={len(buy)}，期望=5")
    if len(sell) != 5:
        errors.append(f"[{date_str}] 游资净卖出TOP5数量={len(sell)}，期望=5")
    return errors


def run_all_api_validations(data: Dict, date_str: str) -> List[str]:
    """运行所有API模式校验规则（6步 + 附加）"""
    all_errors = []
    all_errors.extend(validate_institution_top5_count(data, date_str))
    all_errors.extend(validate_institution_buy_positive(data, date_str))
    all_errors.extend(validate_institution_sell_negative(data, date_str))
    all_errors.extend(validate_amount_reasonable(data, date_str))
    all_errors.extend(validate_resonance_institution_side(data, date_str))
    all_errors.extend(validate_resonance_youzi_side(data, date_str))
    all_errors.extend(validate_youzi_top5_count(data, date_str))
    return all_errors


# ========== 日期范围工具 ==========

def date_range(start_date: str, end_date: str) -> List[str]:
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
    """提取所有交易日单元格数据"""
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

            if 'class="empty-content"' in td_html:
                continue

            has_resonance_tag = bool(re.search(r'★共振', td_html))

            # 提取机构净买入TOP5
            inst_top5 = _extract_section(td_html, r'▲ 机构净买入.*?TOP5')

            # 提取机构净卖出TOP5
            inst_sell = _extract_section(td_html, r'▼ 机构净卖出.*?TOP5', is_sell=True)

            # 提取游资净买入TOP5
            youzi_buy = _extract_section(td_html, r'▲ 游资净买入.*?TOP5')

            # 提取游资净卖出TOP5
            youzi_sell = _extract_section(td_html, r'▼ 游资净卖出.*?TOP5', is_sell=True)

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
                    if name:
                        resonance_stocks.append({"name": name, "amount": amt.strip() if amt else ""})

            # 计算展示月份
            display_month = month
            if is_other:
                display_month = month - 1 if day > 15 else month + 1

            results.append({
                "month": month,
                "day": day,
                "is_other_month": is_other,
                "display_month": display_month,
                "date_label": f"{display_month}月{day}日",
                "inst_top5": inst_top5,
                "inst_sell": inst_sell,
                "youzi_buy": youzi_buy,
                "youzi_sell": youzi_sell,
                "has_resonance_tag": has_resonance_tag,
                "resonance_stocks": resonance_stocks,
                "td_has_resonance_class": td_has_resonance,
            })

    return results


def _extract_section(td_html: str, title_pattern: str, is_sell: bool = False) -> list:
    """从td中提取一个section的股票列表"""
    # 先匹配 section-title 加 stock-row
    pat = re.compile(
        rf'<div class="section-title[^"]*">{title_pattern}.*?</div>\s*<div class="stock-row">(.*?)</div>',
        re.DOTALL,
    )
    m = pat.search(td_html)
    if not m:
        return []
    row_html = m.group(1)
    items = re.findall(
        r'<span class="stock-item">.*?'
        r'<span class="stock-icon[^"]*">(.*?)</span>.*?'
        r'<span class="stock-name[^"]*">(.*?)</span>.*?'
        r'<span class="stock-amount[^"]*">(.*?)</span>.*?'
        r'</span>',
        row_html, re.DOTALL,
    )
    result = []
    for icon, name, amt in items:
        result.append({
            "name": name.strip(),
            "amount": amt.strip(),
            "icon": icon.strip(),
        })
    return result


def parse_amount_to_float(amt_str: str):
    """解析金额字符串为万元（float），保留正负号"""
    if not amt_str or amt_str == "--":
        return None
    s = amt_str.strip()
    if not s or s == "--":
        return None
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
    """运行HTML模式校验"""
    errors = []

    for cell in cells:
        label = cell["date_label"]
        inst_top5 = cell["inst_top5"]
        inst_sell = cell["inst_sell"]
        youzi_buy = cell["youzi_buy"]
        youzi_sell = cell["youzi_sell"]
        resonance = cell["resonance_stocks"]
        has_tag = cell["has_resonance_tag"]
        has_td_class = cell["td_has_resonance_class"]

        # 1. 机构净买入数量
        if inst_top5 and len(inst_top5) != 5:
            errors.append(f"[{label}] 机构净买入数量={len(inst_top5)}，期望=5")

        # 2. 机构净买入金额方向（必须为正）
        for s in inst_top5:
            amt = parse_amount_to_float(s["amount"])
            if amt is not None and amt <= 0:
                errors.append(f"[{label}] 机构净买入 {s['name']} 金额非正（方向错误）")

        # 3. 机构净卖出金额方向（必须为负）
        for s in inst_sell:
            amt = parse_amount_to_float(s["amount"])
            if amt is not None and amt >= 0:
                errors.append(f"[{label}] 机构净卖出 {s['name']} 金额非负（方向错误）")

        # 4. 游资净买卖数量
        if youzi_buy and len(youzi_buy) != 5:
            errors.append(f"[{label}] 游资净买入数量={len(youzi_buy)}，期望=5")
        if youzi_sell and len(youzi_sell) != 5:
            errors.append(f"[{label}] 游资净卖出数量={len(youzi_sell)}，期望=5")

        # 5. 游资净买入金额方向
        for s in youzi_buy:
            amt = parse_amount_to_float(s["amount"])
            if amt is not None and amt <= 0:
                errors.append(f"[{label}] 游资净买入 {s['name']} 金额非正（方向错误）")

        # 6. 游资净卖出金额方向
        for s in youzi_sell:
            amt = parse_amount_to_float(s["amount"])
            if amt is not None and amt >= 0:
                errors.append(f"[{label}] 游资净卖出 {s['name']} 金额非负（方向错误）")

        # 7. 共振股票必须在机构TOP5中
        if resonance and inst_top5:
            inst_names = {s["name"] for s in inst_top5}
            for r in resonance:
                pure_name = re.split(r'[\s（(]', r["name"])[0]
                if pure_name not in inst_names:
                    errors.append(f"[{label}] 共振股票 {pure_name} 不在机构净买入TOP5中")

        # 8. 共振标签一致性
        if resonance:
            if not has_tag:
                errors.append(f"[{label}] 有共振数据但无★共振标签")
            if not has_td_class:
                errors.append(f"[{label}] 有共振数据但td无has-resonance类")
        elif inst_top5:  # 有机构数据但无共振的日期不应有tag
            if has_tag and not resonance:
                errors.append(f"[{label}] 无共振数据但有★共振标签")

    # 9. 张冠李戴检测：相邻日期机构TOP5完全相同
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

    # 10. 游资TOP5张冠李戴检测
    youzi_sig_map = defaultdict(list)
    for cell in cells:
        if not cell["youzi_buy"]:
            continue
        sig = tuple(s["name"] for s in cell["youzi_buy"])
        youzi_sig_map[sig].append(cell["date_label"])

    for sig, dates in youzi_sig_map.items():
        if len(dates) >= 2:
            errors.append(
                f"【张冠李戴风险】{len(dates)} 个日期的游资净买入TOP5完全相同："
                f"{', '.join(dates)}，股票: {list(sig)}"
            )

    return errors


# ========== 主函数 ==========

def validate_api_mode(date_str: str) -> int:
    """API模式校验"""
    print(f"🔍 校验模式: API (目标日期: {date_str})")
    try:
        daily_data = build_daily_data(date_str)
        youzi_data = get_youzi_stock_data(date_str)
        youzi_top_n = youzi_data["buy_sorted"][:RESONANCE_YOUZI_TOP_N]
        youzi_names = {s["name"] for s in youzi_top_n}

        data_dict = daily_data.model_dump()
        data_dict["_youzi_top_n_stocks"] = youzi_names

        errors = run_all_api_validations(data_dict, date_str)

        if errors:
            print(f"❌ 校验失败，共 {len(errors)} 个错误:")
            for e in errors:
                print(f"   - {e}")
            return 1
        else:
            print(f"✅ API模式校验通过（6步全过 + 游资TOP5校验）")
            print(f"   机构TOP5: {len(daily_data.institution_top5)}只")
            print(f"   机构卖出TOP5: {len(daily_data.institution_sell_top5)}只")
            print(f"   游资净买入TOP5: {len(daily_data.youzi_buy_top5)}只")
            print(f"   游资净卖出TOP5: {len(daily_data.youzi_sell_top5)}只")
            print(f"   共振信号: {len(daily_data.resonance)}个")
            return 0
    except Exception as e:
        print(f"❌ API校验异常: {e}")
        import traceback
        traceback.print_exc()
        return 1


def validate_html_mode(html_path: str) -> int:
    """HTML模式校验"""
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
        print(f"✅ HTML模式校验通过")
        print(f"   校验单元格数: {len(cells)}")
        return 0


def validate_range_mode(start_date: str, end_date: str, mode: str, html_path: str = "") -> int:
    """范围模式校验"""
    dates = date_range(start_date, end_date)
    print(f"🔍 范围校验: {start_date} ~ {end_date} ({len(dates)}天), 模式: {mode}")

    if mode == "html":
        return validate_html_mode(html_path)

    all_errors = []
    passed = 0
    skipped = 0

    for date_str in dates:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        if dt.weekday() >= 5:
            skipped += 1
            continue
        try:
            daily_data = build_daily_data(date_str)
            youzi_data = get_youzi_stock_data(date_str)
            youzi_top_n = youzi_data["buy_sorted"][:RESONANCE_YOUZI_TOP_N]
            youzi_names = {s["name"] for s in youzi_top_n}
            data_dict = daily_data.model_dump()
            data_dict["_youzi_top_n_stocks"] = youzi_names
            errors = run_all_api_validations(data_dict, date_str)
            if errors:
                all_errors.extend(errors)
            else:
                passed += 1
        except Exception as e:
            all_errors.append(f"[{date_str}] API调用异常: {e}")

    if all_errors:
        print(f"❌ 范围校验失败，共 {len(all_errors)} 个错误")
        for e in all_errors[:20]:
            print(f"   - {e}")
        return 1
    else:
        print(f"✅ 范围校验通过: {passed}天通过, {skipped}天跳过(周末)")
        return 0


def main():
    parser = argparse.ArgumentParser(description="机游共振日历数据一致性校验（纯数据交叉版v2）")
    parser.add_argument("html_file", nargs="?", help="HTML文件路径（默认模式）")
    parser.add_argument("--date", help="单日期校验（API模式）")
    parser.add_argument("--range", nargs=2, metavar=("START", "END"), help="日期范围")
    parser.add_argument("--mode", choices=["api", "html"], default="html", help="校验模式")
    parser.add_argument("--html-path", help="HTML文件路径（range模式）")
    args = parser.parse_args()

    print("=" * 60)
    print("🧪 机游共振日历 — 6步数据一致性校验（纯数据交叉版v2）")
    print(f"📊 共振逻辑: 机构净买入TOP5 ∩ 游资净买入TOP{RESONANCE_YOUZI_TOP_N}")
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
