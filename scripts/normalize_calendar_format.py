#!/usr/bin/env python3
"""
机游共振日历 — 格式统一脚本

扫描所有周表的单元格，对每类区域（机构/共振/游资买入/游资卖出），
选择出现次数最多的结构作为标准模板，统一全部单元格的格式。
同时统一 section-title 的 class 命名、stock-item 结构等。

用法：
  python3 normalize_calendar_format.py [HTML文件路径] [--in-place]
  默认处理 ../机游共振日历.html
  --in-place 直接修改原文件，否则输出到 .normalized.html
"""

import re
import sys
import os
from collections import Counter


def parse_args():
    args = sys.argv[1:]
    html_path = None
    in_place = False
    for a in args:
        if a == "--in-place":
            in_place = True
        else:
            html_path = a
    if not html_path:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        html_path = os.path.join(os.path.dirname(script_dir), "机游共振日历.html")
    return html_path, in_place


# ========== 基础工具 ==========

def extract_td_contents(tr_html: str) -> list:
    """从 tr 中提取所有 td，返回 [(td_attrs, td_inner_html), ...]"""
    results = []
    pos = 0
    while True:
        td_start = tr_html.find('<td', pos)
        if td_start == -1:
            break
        td_end_tag = tr_html.find('>', td_start)
        if td_end_tag == -1:
            break
        td_attrs = tr_html[td_start + 3:td_end_tag]
        td_close = tr_html.find('</td>', td_end_tag)
        if td_close == -1:
            break
        td_inner = tr_html[td_end_tag + 1:td_close]
        results.append((td_attrs, td_inner))
        pos = td_close + 5
    return results


def extract_nth_span_content(html: str, class_name: str, n: int = 0):
    """
    提取第 n 个 class=class_name 的 span 的完整内容（含开/闭标签），
    考虑嵌套 span，用深度计数。
    返回 (open_tag_html, content_text, close_tag_html) 或 None
    """
    start_tag = f'<span class="{class_name}'
    pos = 0
    found = 0
    while found <= n:
        start = html.find(start_tag, pos)
        if start == -1:
            return None
        # 找到开标签结束 >
        tag_end = html.find('>', start)
        if tag_end == -1:
            return None
        content_start = tag_end + 1
        # 深度计数找匹配的 </span>
        depth = 1
        p = content_start
        item_end = -1
        while depth > 0:
            next_open = html.find('<span', p)
            next_close = html.find('</span>', p)
            if next_close == -1:
                break
            if next_open != -1 and next_open < next_close:
                depth += 1
                p = next_open + 5
            else:
                depth -= 1
                if depth == 0:
                    item_end = next_close
                p = next_close + 7
        if item_end == -1:
            return None
        if found == n:
            open_html = html[start:tag_end + 1]
            content = html[content_start:item_end]
            close_html = html[item_end:item_end + 7]
            return open_html, content, close_html
        found += 1
        pos = item_end + 7
    return None


def extract_stock_items(row_html: str) -> list:
    """从 stock-row 中提取所有 stock-item 的数据 [{'icon','name','amount'}, ...]"""
    items = []
    pos = 0
    start_tag = '<span class="stock-item">'
    while True:
        start = row_html.find(start_tag, pos)
        if start == -1:
            break
        content_start = start + len(start_tag)
        depth = 1
        p = content_start
        item_end = -1
        while depth > 0:
            next_open = row_html.find('<span', p)
            next_close = row_html.find('</span>', p)
            if next_close == -1:
                break
            if next_open != -1 and next_open < next_close:
                depth += 1
                p = next_open + 5
            else:
                depth -= 1
                if depth == 0:
                    item_end = next_close
                p = next_close + 7
        if item_end == -1:
            break
        item_inner = row_html[content_start:item_end]

        icon = ""
        name = ""
        amount = ""
        r = extract_nth_span_content(item_inner, "stock-icon", 0)
        if r:
            icon = r[1].strip()
        r = extract_nth_span_content(item_inner, "stock-name", 0)
        if r:
            name = r[1].strip()
        r = extract_nth_span_content(item_inner, "stock-amount", 0)
        if r:
            amount = r[1].strip()

        if name:
            items.append({"icon": icon, "name": name, "amount": amount})
        pos = item_end + 7
    return items


def normalize_structure_signature(html: str) -> str:
    """生成结构签名（用于聚类识别最常见模板）"""
    s = html
    # 去掉注释
    s = re.sub(r'<!--.*?-->', '', s, flags=re.DOTALL)
    # 标签保留 class 和 style(仅color/font)
    def tag_replacer(m):
        tag = m.group(1)
        attrs = m.group(2)
        cls = ""
        cls_m = re.search(r'class="([^"]*)"', attrs)
        if cls_m:
            cls = f' class="{cls_m.group(1)}"'
        style_part = ""
        style_m = re.search(r'style="([^"]*)"', attrs)
        if style_m:
            keep = []
            for kv in style_m.group(1).split(";"):
                kv = kv.strip()
                if not kv:
                    continue
                k = kv.split(":")[0].strip().lower()
                if k in ("color", "font-size", "font-weight", "text-align"):
                    keep.append(kv)
            if keep:
                style_part = f' style="{"; ".join(sorted(keep))}"'
        return f"<{tag}{cls}{style_part}>"
    s = re.sub(r'<(\w+)([^>]*)>', tag_replacer, s)
    # 文本内容替换
    s = re.sub(r'>([^<]+)<', '>#T<', s)
    # 压缩空白
    s = re.sub(r'\s+', ' ', s).strip()
    return s


# ========== 模板构建（全局扫描，取最常见结构） ==========

SECTION_PATTERNS = {
    "institution": r'<div class="section-title[^"]*">▲ 机构净买入.*?</div>\s*<div class="stock-row">(.*?)</div>',
    "resonance": r'<div class="section-title[^"]*">★ 机游共振.*?</div>\s*<div class="stock-row">(.*?)</div>',
    "youzi_buy": r'<div class="section-title[^"]*">▲ 游资买入.*?</div>\s*<div class="stock-row">(.*?)</div>',
    "youzi_sell": r'<div class="section-title[^"]*">▼ 游资卖出.*?</div>\s*<div class="stock-row">(.*?)</div>',
}


def extract_all_sections(html: str) -> dict:
    """
    扫描所有单元格，按 section 类型收集每个 section 的完整 HTML（含标题+stock-row）。
    返回 {section_type: [(full_html, title_class, item_count, signature), ...]}
    """
    sections = {k: [] for k in SECTION_PATTERNS}

    # 找所有 week-table 的 tbody tr
    for tbl_match in re.finditer(r'<table class="week-table".*?</table>', html, re.DOTALL):
        tbl_html = tbl_match.group(0)
        tbody = re.search(r'<tbody>(.*?)</tbody>', tbl_html, re.DOTALL)
        if not tbody:
            continue
        tr = re.search(r'<tr>(.*?)</tr>', tbody.group(1), re.DOTALL)
        if not tr:
            continue
        tds = extract_td_contents(tr.group(1))
        for td_attrs, td_inner in tds:
            if "休市" in td_inner or "empty-content" in td_inner:
                continue
            for sec_type, pat in SECTION_PATTERNS.items():
                m = re.search(pat, td_inner, re.DOTALL)
                if m:
                    full_html = m.group(0)
                    title_cls = ""
                    tc = re.search(r'class="section-title([^"]*)"', full_html)
                    if tc:
                        title_cls = tc.group(1).strip()
                    # 统计 item 数
                    items = extract_stock_items(m.group(1))
                    sig = normalize_structure_signature(full_html)
                    sections[sec_type].append({
                        "full_html": full_html,
                        "title_class": title_cls,
                        "item_count": len(items),
                        "signature": sig,
                        "items": items,
                    })
    return sections


def build_template_from_most_common(sections: dict) -> dict:
    """
    对每类 section，取出现最多的结构签名作为模板。
    返回 {section_type: {title_class, item_template_html, section_open, section_close}}
    """
    template = {}
    for sec_type, sec_list in sections.items():
        if not sec_list:
            continue
        # 按签名聚类，选最多的
        sig_counter = Counter(s["signature"] for s in sec_list)
        most_common_sig, _ = sig_counter.most_common(1)[0]
        # 取一个该签名的样本
        sample = next(s for s in sec_list if s["signature"] == most_common_sig)

        # 提取第一个 stock-item 的结构作为 item 模板
        row_match = re.search(r'<div class="stock-row">(.*?)</div>', sample["full_html"], re.DOTALL)
        if not row_match:
            continue
        row_html = row_match.group(1)
        # 取第一个 stock-item
        item_start = row_html.find('<span class="stock-item">')
        if item_start == -1:
            continue
        # 找到它的结束
        depth = 1
        p = item_start + len('<span class="stock-item">')
        item_end = -1
        while depth > 0:
            no = row_html.find('<span', p)
            nc = row_html.find('</span>', p)
            if nc == -1:
                break
            if no != -1 and no < nc:
                depth += 1
                p = no + 5
            else:
                depth -= 1
                if depth == 0:
                    item_end = nc
                p = nc + 7
        if item_end == -1:
            continue
        item_full = row_html[item_start:item_end + 7]

        # 把文本替换为占位符
        item_tpl = item_full
        # icon
        icon_m = re.search(r'(<span class="stock-icon[^"]*"[^>]*>)([^<]*)(</span>)', item_full, re.DOTALL)
        if icon_m:
            item_tpl = item_tpl.replace(icon_m.group(2), "{{ICON}}")
        # name
        name_m = re.search(r'(<span class="stock-name[^"]*"[^>]*>)([^<]*)(</span>)', item_full, re.DOTALL)
        if name_m:
            item_tpl = item_tpl.replace(name_m.group(2), "{{NAME}}")
        # amount
        amt_m = re.search(r'(<span class="stock-amount[^"]*"[^>]*>)([^<]*)(</span>)', item_full, re.DOTALL)
        if amt_m:
            item_tpl = item_tpl.replace(amt_m.group(2), "{{AMOUNT}}")

        # 提取 section 的开头（标题 + stock-row 开头）和结尾
        sec_full = sample["full_html"]
        row_start = sec_full.find('<div class="stock-row">')
        if row_start == -1:
            continue
        section_open = sec_full[:row_start] + '<div class="stock-row">'
        row_end_close = sec_full.rfind('</div>')
        if row_end_close == -1:
            continue
        section_close = '</div>'

        template[sec_type] = {
            "title_class": sample["title_class"],
            "item_template": item_tpl,
            "section_open": section_open,
            "section_close": section_close,
        }

    return template


# ========== 单元格数据提取与重建 ==========

def extract_cell_data(td_inner: str) -> dict:
    """从 td 内容提取结构化数据"""
    data = {
        "day_number": "",
        "day_number_cls": "",
        "has_resonance_tag": False,
        "institution": [],
        "resonance": [],
        "youzi_buy": [],
        "youzi_sell": [],
        "has_empty": False,
        "is_holiday": False,
        "youzi_placeholder": False,
    }

    if 'empty-content' in td_inner:
        data["has_empty"] = True
    if '休市' in td_inner:
        data["is_holiday"] = True

    m = re.search(r'<span class="day-number([^"]*)">(\d+)</span>', td_inner)
    if m:
        data["day_number_cls"] = m.group(1).strip()
        data["day_number"] = m.group(2)

    data["has_resonance_tag"] = "resonance-tag" in td_inner and "★共振" in td_inner

    for sec_type, pat in SECTION_PATTERNS.items():
        m = re.search(pat, td_inner, re.DOTALL)
        if m:
            items = extract_stock_items(m.group(1))
            data[sec_type] = items

    # 旧格式游资占位
    if not data["youzi_buy"] and not data["youzi_sell"]:
        m = re.search(
            r'<div class="section-title[^"]*">▼ 游资席位动向.*?</div>\s*<div class="stock-row">(.*?)</div>',
            td_inner, re.DOTALL,
        )
        if m:
            items = extract_stock_items(m.group(1))
            if any("暂无数据" in it["name"] for it in items):
                data["youzi_placeholder"] = True

    return data


def render_stock_item(item: dict, item_template: str) -> str:
    """用 item 模板渲染单个 stock-item"""
    tpl = item_template
    tpl = tpl.replace("{{ICON}}", item["icon"])
    tpl = tpl.replace("{{NAME}}", item["name"])
    tpl = tpl.replace("{{AMOUNT}}", item["amount"])
    return tpl


def build_cell_html(data: dict, template: dict) -> str:
    """根据数据和模板重建单元格HTML（td内部）"""
    # 选各区域模板
    inst_tpl = template.get("institution")
    res_tpl = template.get("resonance") or inst_tpl
    buy_tpl = template.get("youzi_buy") or inst_tpl
    sell_tpl = template.get("youzi_sell") or buy_tpl

    lines = []
    lines.append('<div class="day-cell">')

    # day-header
    day_cls = data["day_number_cls"]
    if day_cls and not day_cls.startswith(' '):
        day_cls = ' ' + day_cls
    if data["has_resonance_tag"]:
        lines.append(
            f'    <div class="day-header">'
            f'<span class="day-number{day_cls}">{data["day_number"]}</span>'
            f'<span class="amount resonance-tag">★共振</span></div>'
        )
    else:
        lines.append(
            f'    <div class="day-header">'
            f'<span class="day-number{day_cls}">{data["day_number"]}</span></div>'
        )

    if data["is_holiday"]:
        lines.append('    <div class="empty-content"><span class="amount holiday">休市</span></div>')
        lines.append('</div>')
        return "\n".join(lines)

    if data["has_empty"] and not data["institution"] and not data["youzi_buy"] and not data["resonance"]:
        lines.append('    <div class="empty-content">--</div>')
        lines.append('</div>')
        return "\n".join(lines)

    lines.append('    <div class="stock-list">')

    # 机构区
    if data["institution"] and inst_tpl:
        items_html = " ".join(render_stock_item(it, inst_tpl["item_template"]) for it in data["institution"])
        lines.append(f'        <div class="section-title{inst_tpl["title_class"] and " " + inst_tpl["title_class"]}">▲ 机构净买入TOP5</div>')
        lines.append(f'        <div class="stock-row">{items_html}</div>')
    elif data["institution"]:
        # 无模板，原样（不应该发生）
        pass

    # 共振区
    if data["resonance"] and res_tpl:
        tcls = res_tpl["title_class"]
        tcls_str = " " + tcls if tcls else ""
        lines.append(f'        <div class="section-title{tcls_str}">★ 机游共振</div>')
        items_html = " ".join(render_stock_item(it, res_tpl["item_template"]) for it in data["resonance"])
        lines.append(f'        <div class="stock-row">{items_html}</div>')

    # 游资买入区
    if data["youzi_buy"] and buy_tpl:
        tcls = buy_tpl["title_class"]
        tcls_str = " " + tcls if tcls else ""
        lines.append(f'        <div class="section-title{tcls_str}">▲ 游资买入</div>')
        items_html = " ".join(render_stock_item(it, buy_tpl["item_template"]) for it in data["youzi_buy"])
        lines.append(f'        <div class="stock-row">{items_html}</div>')

    # 游资卖出区
    if data["youzi_sell"] and sell_tpl:
        tcls = sell_tpl["title_class"]
        tcls_str = " " + tcls if tcls else ""
        lines.append(f'        <div class="section-title{tcls_str}">▼ 游资卖出</div>')
        items_html = " ".join(render_stock_item(it, sell_tpl["item_template"]) for it in data["youzi_sell"])
        lines.append(f'        <div class="stock-row">{items_html}</div>')

    # 游资占位
    has_youzi_data = bool(data["youzi_buy"] or data["youzi_sell"])
    if not has_youzi_data and (data["institution"] or data["resonance"]):
        lines.append('        <div class="section-title">▼ 游资席位动向</div>')
        lines.append('        <div class="stock-row"><span class="stock-item"><span class="stock-icon down">▼</span><span class="stock-name">暂无数据</span></span></div>')

    lines.append('    </div>')
    lines.append('</div>')
    return "\n".join(lines)


# ========== 主流程 ==========

def normalize_html(html: str) -> str:
    """主处理函数"""
    sections = extract_all_sections(html)
    template = build_template_from_most_common(sections)

    print(f"  📐 模板构建完成，共 {len(template)} 类区域:")
    for k, v in template.items():
        print(f"     - {k}: title_class='{v['title_class']}'")

    def replace_table(match):
        table_html = match.group(0)
        tbody_m = re.search(r'(<tbody>)(.*?)(</tbody>)', table_html, re.DOTALL)
        if not tbody_m:
            return table_html
        tbody_inner = tbody_m.group(2)
        tr_m = re.search(r'(<tr>)(.*?)(</tr>)', tbody_inner, re.DOTALL)
        if not tr_m:
            return table_html
        tr_inner = tr_m.group(2)
        tds = extract_td_contents(tr_inner)
        new_tds = []
        for td_attrs, td_inner in tds:
            if 'day-cell' not in td_inner:
                new_tds.append(f'<td{td_attrs}>{td_inner}</td>')
                continue
            data = extract_cell_data(td_inner)
            new_inner = build_cell_html(data, template)
            new_tds.append(f'<td{td_attrs}>\n{new_inner}\n</td>')
        new_tr_inner = "\n".join(new_tds)
        new_tbody = (tbody_m.group(1) + tr_m.group(1) + new_tr_inner +
                     tr_m.group(3) + tbody_m.group(3))
        return table_html[:tbody_m.start()] + new_tbody + table_html[tbody_m.end():]

    result = re.sub(
        r'<table class="week-table".*?</table>',
        replace_table,
        html,
        flags=re.DOTALL,
    )
    return result


def main():
    html_path, in_place = parse_args()
    if not os.path.exists(html_path):
        print(f"❌ 文件不存在: {html_path}")
        sys.exit(2)

    print(f"📐 格式统一 — {os.path.basename(html_path)}")

    with open(html_path, "r", encoding="utf-8") as f:
        html = f.read()

    try:
        new_html = normalize_html(html)
    except Exception as e:
        print(f"❌ 处理失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    changed = new_html != html
    if not changed:
        print("✅ 格式已经统一，无需修改")
        sys.exit(0)

    if in_place:
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(new_html)
        print(f"✅ 格式统一完成，已写入 {html_path}")
    else:
        tmp_path = html_path + ".normalized.html"
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(new_html)
        print(f"✅ 格式统一完成，输出: {tmp_path}")

    sys.exit(0)


if __name__ == "__main__":
    main()
