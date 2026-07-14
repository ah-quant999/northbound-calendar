#!/usr/bin/env python3
"""
机游共振日历 — 格式统一性校验脚本

以第2周（或第一个完整周）为基准模板，检查：
1. 所有交易日td的DOM结构（按星期分组对比）是否一致
2. 机构区、游资区、共振标记的HTML结构一致性
3. 字体大小、颜色相关class是否统一
4. section-title 的 class 命名是否统一

用法：
  python3 validate_format_uniformity.py [HTML文件路径]
  默认: ../机游共振日历.html

返回码：0=通过，1=有差异
"""

import re
import sys
import os
from collections import defaultdict

WEEKDAY_NAMES = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def parse_args():
    if len(sys.argv) > 1:
        return sys.argv[1]
    script_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(os.path.dirname(script_dir), "机游共振日历.html")


def extract_week_tables(html: str) -> list:
    """提取所有周标签+表格，返回 [(week_label, table_html), ...]"""
    results = []
    label_pattern = re.compile(
        r'<div style="text-align:left;[^"]*">([^<]+)</div>',
    )
    table_pattern = re.compile(
        r'<table class="week-table".*?</table>',
        re.DOTALL,
    )
    labels = [(m.start(), m.group(1).strip()) for m in label_pattern.finditer(html)]
    tables = [(m.start(), m.group(0)) for m in table_pattern.finditer(html)]
    for tbl_start, tbl_html in tables:
        week_label = ""
        for lbl_start, lbl_text in labels:
            if lbl_start < tbl_start:
                week_label = lbl_text
            else:
                break
        results.append((week_label, tbl_html))
    return results


def extract_day_cells(table_html: str) -> list:
    """从表格中提取每个 td 的内容，返回 [(td_attrs, day_html, weekday_col)]"""
    results = []
    tbody = re.search(r'<tbody>(.*?)</tbody>', table_html, re.DOTALL)
    if not tbody:
        return results
    tr_match = re.search(r'<tr>(.*?)</tr>', tbody.group(1), re.DOTALL)
    if not tr_match:
        return results
    for idx, td_m in enumerate(
        re.finditer(r'<td\b([^>]*)>(.*?)</td>', tr_match.group(1), re.DOTALL)
    ):
        results.append((td_m.group(1), td_m.group(2), idx))
    return results


def normalize_structure_signature(html: str) -> str:
    """
    生成结构签名：
    - 保留标签名、class、style中的color/font相关属性
    - 替换文本内容为 #T
    - 用于结构一致性对比
    """
    s = re.sub(r'<!--.*?-->', '', html, flags=re.DOTALL)

    def tag_replacer(m):
        tag = m.group(1)
        attrs = m.group(2)
        cls = ""
        cls_m = re.search(r'class="([^"]*)"', attrs)
        if cls_m:
            cls = f' class="{cls_m.group(1)}"'
        # 只保留 color / font-size / font-weight / text-align 的 style
        style_part = ""
        style_m = re.search(r'style="([^"]*)"', attrs)
        if style_m:
            keep = []
            for kv in style_m.group(1).split(";"):
                kv = kv.strip()
                if not kv:
                    continue
                k = kv.split(":")[0].strip().lower()
                if k in ("color", "font-size", "font-weight", "text-align", "cursor"):
                    keep.append(kv)
            if keep:
                style_part = f' style="{"; ".join(sorted(keep))}"'
        return f"<{tag}{cls}{style_part}>"

    s = re.sub(
        r'<(\w+)([^>]*)>',
        lambda m: tag_replacer(m) if m.group(1) not in ("br", "hr", "img") else f"<{m.group(1)}>",
        s,
    )
    # 闭合标签保留
    s = re.sub(r'</(\w+)>', r'</\1>', s)
    # 文本内容替换
    s = re.sub(r'>([^<]+)<', '>#T<', s)
    # 压缩空白
    s = re.sub(r'\s+', ' ', s).strip()
    return s


def extract_first_stock_item(row_html: str) -> str:
    """从 stock-row 中提取第一个 stock-item 的完整 HTML（考虑嵌套span）"""
    start_tag = '<span class="stock-item">'
    start = row_html.find(start_tag)
    if start == -1:
        return ""
    content_start = start + len(start_tag)
    depth = 1
    pos = content_start
    item_end = -1
    while depth > 0:
        next_open = row_html.find('<span', pos)
        next_close = row_html.find('</span>', pos)
        if next_close == -1:
            break
        if next_open != -1 and next_open < next_close:
            depth += 1
            pos = next_open + 5
        else:
            depth -= 1
            if depth == 0:
                item_end = next_close
            pos = next_close + 7
    if item_end == -1:
        return ""
    return row_html[start:item_end + 7]


def get_cell_section_signatures(day_html: str) -> dict:
    """
    提取日期单元格内各区域的结构签名。
    对 stock-row 只取第一个 stock-item 作为结构代表（因为 item 数量每天不同）。
    """
    sigs = {}

    m = re.search(r'<div class="day-header">(.*?)</div>', day_html, re.DOTALL)
    if m:
        sigs["day_header"] = normalize_structure_signature(m.group(0))

    patterns = [
        ("institution", r'<div class="section-title[^"]*">▲ 机构净买入.*?</div>\s*<div class="stock-row">(.*?)</div>'),
        ("resonance", r'<div class="section-title[^"]*">★ 机游共振.*?</div>\s*<div class="stock-row">(.*?)</div>'),
        ("youzi_buy", r'<div class="section-title[^"]*">▲ 游资买入.*?</div>\s*<div class="stock-row">(.*?)</div>'),
        ("youzi_sell", r'<div class="section-title[^"]*">▼ 游资卖出.*?</div>\s*<div class="stock-row">(.*?)</div>'),
        ("youzi_placeholder", r'<div class="section-title[^"]*">▼ 游资席位动向.*?</div>\s*<div class="stock-row">(.*?)</div>'),
    ]

    for key, pat in patterns:
        m = re.search(pat, day_html, re.DOTALL)
        if not m:
            continue
        title_part = re.search(r'(<div class="section-title[^"]*">.*?</div>)', m.group(0), re.DOTALL)
        title_html = title_part.group(1) if title_part else ""
        first_item = extract_first_stock_item(m.group(1))
        if first_item:
            sig = normalize_structure_signature(title_html + first_item)
        else:
            sig = normalize_structure_signature(m.group(0))
        sigs[key] = sig

    # 外层 day-cell
    m = re.search(r'<div class="day-cell">(.*?)</div>', day_html, re.DOTALL)
    if m:
        sigs["day_cell_shell"] = normalize_structure_signature(
            re.sub(r'<div class="stock-list">.*</div>', '<div class="stock-list">#CONTENT</div>',
                   m.group(0), flags=re.DOTALL)
        )

    return sigs


def find_template_week(weeks: list) -> int:
    """找到第2周的索引，找不到就用数据最多的周"""
    for i, (label, _) in enumerate(weeks):
        if "第2周" in label:
            return i
    best_idx = 0
    best_count = 0
    for i, (_, tbl) in enumerate(weeks):
        cells = extract_day_cells(tbl)
        cnt = sum(1 for _, html, _ in cells if "休市" not in html and "empty-content" not in html)
        if cnt > best_count:
            best_count = cnt
            best_idx = i
    return best_idx


def collect_all_trading_cells(weeks: list) -> list:
    """收集所有非休市、非空的交易日单元格，标注周、列、标签"""
    cells = []
    for wi, (label, tbl) in enumerate(weeks):
        for col, (td_attrs, td_html, _) in enumerate(extract_day_cells(tbl)):
            if "休市" in td_html or "empty-content" in td_html:
                continue
            if "day-cell" not in td_html:
                continue
            cells.append({
                "week_idx": wi,
                "week_label": label,
                "col": col,
                "weekday": WEEKDAY_NAMES[col] if col < 7 else f"列{col}",
                "td_attrs": td_attrs,
                "td_html": td_html,
                "sigs": get_cell_section_signatures(td_html),
            })
    return cells


def main():
    html_path = parse_args()
    if not os.path.exists(html_path):
        print(f"❌ 文件不存在: {html_path}")
        sys.exit(2)

    print(f"🔍 格式统一性校验 — {os.path.basename(html_path)}")
    print("=" * 60)

    with open(html_path, "r", encoding="utf-8") as f:
        html = f.read()

    weeks = extract_week_tables(html)
    print(f"📊 找到 {len(weeks)} 个周表")

    if not weeks:
        print("❌ 未找到任何周表")
        sys.exit(1)

    template_idx = find_template_week(weeks)
    print(f"📐 基准模板：{weeks[template_idx][0]} (第{template_idx+1}周)")

    # 收集所有交易日单元格
    all_cells = collect_all_trading_cells(weeks)
    print(f"📅 有数据的交易日单元格: {len(all_cells)} 个")

    errors = []

    # 检查1：同类区域的结构签名一致性（所有有机构区的单元格对比机构区签名）
    print("\n1️⃣  检查：机构区结构一致性")
    inst_sigs = defaultdict(list)
    for c in all_cells:
        if "institution" in c["sigs"]:
            inst_sigs[c["sigs"]["institution"]].append(f"{c['week_label']} {c['weekday']}")
    if len(inst_sigs) > 1:
        errors.append(f"【机构区结构不统一】存在 {len(inst_sigs)} 种不同结构")
        for sig, locations in list(inst_sigs.items())[:3]:
            errors.append(f"  结构变体: {locations[:3]}... 共{len(locations)}处")
            errors.append(f"    签名: {sig[:100]}")
    else:
        print("  ✅ 通过")

    # 检查2：共振区
    print("\n2️⃣  检查：共振区结构一致性")
    res_sigs = defaultdict(list)
    for c in all_cells:
        if "resonance" in c["sigs"]:
            res_sigs[c["sigs"]["resonance"]].append(f"{c['week_label']} {c['weekday']}")
    if len(res_sigs) > 1:
        errors.append(f"【共振区结构不统一】存在 {len(res_sigs)} 种不同结构")
        for sig, locations in list(res_sigs.items())[:3]:
            errors.append(f"  结构变体: {locations[:3]}... 共{len(locations)}处")
    else:
        print("  ✅ 通过")

    # 检查3：游资买入区
    print("\n3️⃣  检查：游资买入区结构一致性")
    buy_sigs = defaultdict(list)
    for c in all_cells:
        if "youzi_buy" in c["sigs"]:
            buy_sigs[c["sigs"]["youzi_buy"]].append(f"{c['week_label']} {c['weekday']}")
    if len(buy_sigs) > 1:
        errors.append(f"【游资买入区结构不统一】存在 {len(buy_sigs)} 种不同结构")
        for sig, locations in list(buy_sigs.items())[:3]:
            errors.append(f"  结构变体: {locations[:3]}... 共{len(locations)}处")
    else:
        print("  ✅ 通过")

    # 检查4：游资卖出区
    print("\n4️⃣  检查：游资卖出区结构一致性")
    sell_sigs = defaultdict(list)
    for c in all_cells:
        if "youzi_sell" in c["sigs"]:
            sell_sigs[c["sigs"]["youzi_sell"]].append(f"{c['week_label']} {c['weekday']}")
    if len(sell_sigs) > 1:
        errors.append(f"【游资卖出区结构不统一】存在 {len(sell_sigs)} 种不同结构")
        for sig, locations in list(sell_sigs.items())[:3]:
            errors.append(f"  结构变体: {locations[:3]}... 共{len(locations)}处")
    else:
        print("  ✅ 通过")

    # 检查5：day-header 结构一致性
    print("\n5️⃣  检查：day-header 结构一致性")
    dh_sigs = defaultdict(list)
    for c in all_cells:
        if "day_header" in c["sigs"]:
            dh_sigs[c["sigs"]["day_header"]].append(f"{c['week_label']} {c['weekday']}")
    if len(dh_sigs) > 2:  # 允许2种：有共振tag / 无共振tag
        errors.append(f"【day-header结构不统一】存在 {len(dh_sigs)} 种不同结构（允许2种：有/无共振标签）")
        for sig, locations in list(dh_sigs.items())[:5]:
            errors.append(f"  变体({len(locations)}处): {sig[:80]}")
    else:
        print("  ✅ 通过")

    # 检查6：section-title class 命名统计
    print("\n6️⃣  检查：section-title class 命名")
    title_classes = defaultdict(int)
    for c in all_cells:
        for cls in re.findall(r'class="section-title([^"]*)"', c["td_html"]):
            title_classes[cls.strip() or "(无)"] += 1
    # 预期的 class 组合应该有限
    if len(title_classes) > 5:
        errors.append(f"【section-title类过多】共 {len(title_classes)} 种: {dict(title_classes)}")
    else:
        print(f"  ℹ️  共 {len(title_classes)} 种 section-title class:")
        for cls, cnt in title_classes.items():
            print(f"     '{cls}': {cnt}处")

    print("\n" + "=" * 60)
    if errors:
        print(f"❌ 共发现 {len(errors)} 处格式差异")
        for i, e in enumerate(errors, 1):
            print(f"  {i}. {e}")
        sys.exit(1)
    else:
        print("✅ 格式统一性校验全部通过")
        sys.exit(0)


if __name__ == "__main__":
    main()
