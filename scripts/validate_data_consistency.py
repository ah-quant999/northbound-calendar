#!/usr/bin/env python3
"""
机游共振日历 — 数据一致性校验脚本

检查内容：
1. 不同日期之间TOP5组合完全相同 → 告警（张冠李戴）
2. 同一日期机构数据与游资数据股票名重复率过高（≥3只）→ 告警（错列）
3. 机构净买入金额为0或"--"但标了共振 → 告警
4. 有共振标记但机构TOP5和游资买入无重叠股票 → 告警

用法：
  python3 validate_data_consistency.py [HTML文件路径]
  默认: ../机游共振日历.html

返回码：0=通过，1=有错误
"""

import re
import sys
import os
from collections import defaultdict


def parse_args():
    if len(sys.argv) > 1:
        return sys.argv[1]
    script_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(os.path.dirname(script_dir), "机游共振日历.html")


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
    html_path = parse_args()
    if not os.path.exists(html_path):
        print(f"❌ 文件不存在: {html_path}")
        sys.exit(2)

    print(f"🔍 数据一致性校验 — {os.path.basename(html_path)}")
    print("=" * 60)

    with open(html_path, "r", encoding="utf-8") as f:
        html = f.read()

    cells = extract_day_cells(html)
    print(f"📊 找到 {len(cells)} 个有数据的交易日单元格")

    all_errors = []

    # 检查1: 不同日期TOP5完全相同
    print("\n1️⃣  检查：不同日期TOP5组合完全相同（张冠李戴）")
    errs = check_top5_duplicate_across_days(cells)
    if errs:
        for e in errs:
            print(f"  ❌ {e}")
        all_errors.extend(errs)
    else:
        print("  ✅ 通过")

    # 检查2: 同一日期机构与游资股票名重复率过高
    print("\n2️⃣  检查：机构与游资股票名重复率≥3只（错列）")
    errs = check_inst_youzi_overlap(cells)
    if errs:
        for e in errs:
            print(f"  ⚠️  {e}")
        # 此项为告警级别，视具体情况——按需求作为错误
        all_errors.extend(errs)
    else:
        print("  ✅ 通过")

    # 检查3: 机构净买入金额为0或"--"但标了共振
    print("\n3️⃣  检查：共振标的机构净买入金额为0或--")
    errs = check_zero_amount_with_resonance(cells)
    if errs:
        for e in errs:
            print(f"  ❌ {e}")
        all_errors.extend(errs)
    else:
        print("  ✅ 通过")

    # 检查4: 有共振标记但机构TOP5和游资买入无重叠
    print("\n4️⃣  检查：有共振标记但机构与游资无重叠股票")
    errs = check_resonance_no_overlap(cells)
    if errs:
        for e in errs:
            print(f"  ⚠️  {e}")
        all_errors.extend(errs)
    else:
        print("  ✅ 通过")

    print("\n" + "=" * 60)
    if all_errors:
        print(f"❌ 共发现 {len(all_errors)} 个问题")
        for i, e in enumerate(all_errors, 1):
            print(f"  {i}. {e.split(chr(10))[0]}")
        sys.exit(1)
    else:
        print("✅ 数据一致性校验全部通过")
        sys.exit(0)


if __name__ == "__main__":
    main()
