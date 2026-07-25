#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
部署前布局一致性校验脚本
对比当前HTML文件与生成脚本中的CSS模板，检查关键布局样式是否一致。
若不一致则报错并退出非零状态码，防止GHA自动生成时覆盖手动调整的样式。

用法: python3 scripts/verify_layout_consistency.py [--root /path/to/repo]
退出码: 0=全部一致, 1=存在不一致
"""

import re
import sys
import os
import argparse
from typing import List, Tuple, Dict


# ==================== 校验规则定义 ====================
# 每条规则: (描述, HTML文件路径, 生成脚本路径, 要校验的CSS选择器列表)
# 对于每个选择器，提取该选择器对应的CSS声明块，比较HTML和脚本中的声明是否一致
#
# 注意：CSS提取使用简单正则，适用于本项目中的内联<style>块。
# 若CSS格式有大幅变化，需要同步更新提取逻辑。

CHECK_RULES = [
    {
        "name": "北向分析页 - rank-table宽度(auto)",
        "html_file": "northbound-analysis.html",
        "script_file": "scripts/northbound_analysis.py",
        "selector": ".rank-table",
        "property": "width",
        "expected_value": "auto",
    },
    {
        "name": "北向分析页 - two-col-grid存在",
        "html_file": "northbound-analysis.html",
        "script_file": "scripts/northbound_analysis.py",
        "selector": ".two-col-grid",
        "property": "display",
        "expected_value": "grid",
    },
    {
        "name": "北向分析页 - two-col-grid两列",
        "html_file": "northbound-analysis.html",
        "script_file": "scripts/northbound_analysis.py",
        "selector": ".two-col-grid",
        "property": "grid-template-columns",
        "expected_value": "1fr 1fr",
    },
    {
        "name": "机游信号分析页 - rank-table宽度(auto)",
        "html_file": "jiyou-signal-analysis.html",
        "script_file": "scripts/jiyou_signal_analysis.py",
        "selector": ".rank-table",
        "property": "width",
        "expected_value": "auto",
    },
    {
        "name": "机游主页面(index) - week-grid布局(grid)",
        "html_file": "index.html",
        "script_file": None,  # GHA增量脚本不改CSS，只检查HTML本身符合预期
        "selector": ".week-grid",
        "property": "display",
        "expected_value": "grid",
    },
    {
        "name": "机游主页面(index) - week-grid 5列",
        "html_file": "index.html",
        "script_file": None,
        "selector": ".week-grid",
        "property": "grid-template-columns",
        "expected_value": "repeat(5, minmax(0, 1fr))",
    },
    {
        "name": "机游主页面(index) - week-stock-name自动换行",
        "html_file": "index.html",
        "script_file": None,
        "selector": ".week-stock-name",
        "property": "overflow-wrap",
        "expected_value": "break-word",
    },
    {
        "name": "机游主页面(index) - 月度汇总两列",
        "html_file": "index.html",
        "script_file": None,
        "selector": ".monthly-summary",
        "property": "grid-template-columns",
        "expected_value": "1fr 1fr",
    },
    {
        "name": "机游主页面(index) - rank-amount右对齐",
        "html_file": "index.html",
        "script_file": None,
        "selector": ".rank-amount",
        "property": "margin-left",
        "expected_value": "auto",
    },
    {
        "name": "机游主页面(index) - youzi-stock类存在",
        "html_file": "index.html",
        "script_file": None,
        "selector": ".rank-name.youzi-stock",
        "property": "font-weight",
        "expected_value": "600",
    },
    {
        "name": "机游主页面(index) - rank-dept类存在",
        "html_file": "index.html",
        "script_file": None,
        "selector": ".rank-dept",
        "property": "color",
        "expected_value": "#8b949e",
    },
    {
        "name": "机游备选页(jiyou-resonance) - week-grid布局(grid)",
        "html_file": "jiyou-resonance.html",
        "script_file": None,
        "selector": ".week-grid",
        "property": "display",
        "expected_value": "grid",
    },
    {
        "name": "机游备选页(jiyou-resonance) - week-grid 5列",
        "html_file": "jiyou-resonance.html",
        "script_file": None,
        "selector": ".week-grid",
        "property": "grid-template-columns",
        "expected_value": "repeat(5, minmax(0, 1fr))",
    },
    {
        "name": "北向日历 - week-grid布局(grid)",
        "html_file": "北向资金日历.html",
        "script_file": None,
        "selector": ".week-grid",
        "property": "display",
        "expected_value": "grid",
    },
    {
        "name": "北向日历 - week-grid 5列",
        "html_file": "北向资金日历.html",
        "script_file": None,
        "selector": ".week-grid",
        "property": "grid-template-columns",
        "expected_value": "repeat(5, minmax(0, 1fr))",
    },
    {
        "name": "北向日历 - week-stock-name自动换行",
        "html_file": "北向资金日历.html",
        "script_file": None,
        "selector": ".week-stock-name",
        "property": "overflow-wrap",
        "expected_value": "break-word",
    },
    {
        "name": "北向日历 - 月度汇总两列",
        "html_file": "北向资金日历.html",
        "script_file": None,
        "selector": ".monthly-summary",
        "property": "grid-template-columns",
        "expected_value": "1fr 1fr",
    },
    {
        "name": "北向日历 - rank-amount右对齐",
        "html_file": "北向资金日历.html",
        "script_file": None,
        "selector": ".rank-amount",
        "property": "margin-left",
        "expected_value": "auto",
    },
]

# 脚本中生成的HTML结构校验（确保class名称一致）
STRUCTURE_CHECKS = [
    {
        "name": "机游GHA脚本 - 月度游资排名使用youzi-stock+rank-dept结构",
        "script_file": "scripts/update_jiyou_resonance_gha.py",
        "must_contain": [
            'rank-name youzi-stock',
            'rank-dept',
        ],
    },
    {
        "name": "北向分析页脚本 - two-col-grid容器包裹连续加仓+机构共振",
        "script_file": "scripts/northbound_analysis.py",
        "must_contain": [
            '<div class="two-col-grid">',
            'id="nb-continuous"',
            'id="nb-inst-resonance"',
        ],
    },
]


def extract_css_block(content: str, selector: str) -> str:
    """从HTML/Python内容中提取指定选择器的CSS声明块。

    支持多行和单行格式的CSS声明块。
    返回值: 声明块字符串 (不含花括号)，若找不到返回空字符串
    """
    # 转义选择器中的特殊字符（但保留类名点号的字面匹配）
    # selector 本身就是 .xxx 形式，直接用 re.escape
    esc_selector = re.escape(selector)
    # 匹配: 选择器 { ... }
    # 支持单行和多行格式
    pattern = esc_selector + r'\s*\{\s*([^}]*?)\s*\}'
    m = re.search(pattern, content)
    if m:
        return m.group(1).strip()
    return ''


def get_css_property(decl_block: str, prop: str) -> str:
    """从CSS声明块中提取指定属性的值。

    支持单行和多行声明。值会去除首尾空白。
    返回值: 属性值，若不存在返回空字符串
    """
    # 匹配 属性: 值;
    pattern = r'(?:^|;)\s*' + re.escape(prop) + r'\s*:\s*([^;]+?)\s*(?=;|$)'
    m = re.search(pattern, decl_block)
    if m:
        return m.group(1).strip()
    return ''


def read_file_safe(path: str) -> str:
    """安全读取文件，失败返回空字符串"""
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            return f.read()
    except Exception as e:
        print(f"  ⚠️  读取文件失败: {path} ({e})")
        return ''


def check_css_rule(rule: Dict, root: str) -> Tuple[bool, str]:
    """校验单条CSS规则。返回 (是否通过, 详情信息)"""
    html_path = os.path.join(root, rule["html_file"])
    html_content = read_file_safe(html_path)
    if not html_content:
        return False, f"HTML文件读取失败: {rule['html_file']}"

    html_block = extract_css_block(html_content, rule["selector"])
    if not html_block:
        return False, f"HTML中未找到选择器 {rule['selector']}"

    html_value = get_css_property(html_block, rule["property"])
    html_value_norm = html_value.replace(' ', '').replace('\n', '')
    expected_norm = rule["expected_value"].replace(' ', '')

    if html_value_norm != expected_norm:
        return False, (
            f"HTML中 {rule['selector']}.{rule['property']} = \"{html_value}\", "
            f"预期 = \"{rule['expected_value']}\""
        )

    # 如果指定了脚本文件，也检查脚本中的CSS模板是否一致
    if rule.get("script_file"):
        script_path = os.path.join(root, rule["script_file"])
        script_content = read_file_safe(script_path)
        if not script_content:
            return False, f"脚本文件读取失败: {rule['script_file']}"

        script_block = extract_css_block(script_content, rule["selector"])
        if not script_block:
            return False, f"脚本 {rule['script_file']} 中未找到选择器 {rule['selector']}"

        script_value = get_css_property(script_block, rule["property"])
        script_value_norm = script_value.replace(' ', '').replace('\n', '')

        if script_value_norm != expected_norm:
            return False, (
                f"脚本中 {rule['selector']}.{rule['property']} = \"{script_value}\", "
                f"预期 = \"{rule['expected_value']}\""
            )

        # 同时校验脚本和HTML一致
        if html_value_norm != script_value_norm:
            return False, (
                f"HTML和脚本不一致: HTML=\"{html_value}\", 脚本=\"{script_value}\""
            )

    return True, f"{rule['selector']}.{rule['property']} = \"{html_value}\" ✓"


def check_structure_rule(rule: Dict, root: str) -> Tuple[bool, str]:
    """校验脚本中是否包含必需的HTML结构字符串。"""
    script_path = os.path.join(root, rule["script_file"])
    content = read_file_safe(script_path)
    if not content:
        return False, f"脚本文件读取失败: {rule['script_file']}"

    missing = []
    for s in rule["must_contain"]:
        if s not in content:
            missing.append(s)

    if missing:
        return False, f"缺少结构: {', '.join(missing)}"

    return True, f"全部结构匹配 ✓"


def main():
    parser = argparse.ArgumentParser(description="部署前布局一致性校验")
    parser.add_argument("--root", default=".", help="仓库根目录路径")
    args = parser.parse_args()

    root = os.path.abspath(args.root)
    print(f"📁 仓库根目录: {root}")
    print(f"{'='*60}")
    print("🔍 布局一致性校验开始")
    print(f"{'='*60}\n")

    errors = []
    passed = 0
    total = len(CHECK_RULES) + len(STRUCTURE_CHECKS)

    # CSS规则校验
    print("📐 CSS 样式规则校验 ({}/{}):".format(0, len(CHECK_RULES)))
    print("-" * 50)
    for i, rule in enumerate(CHECK_RULES, 1):
        ok, detail = check_css_rule(rule, root)
        status = "✅" if ok else "❌"
        print(f"  [{i:2d}/{len(CHECK_RULES)}] {status} {rule['name']}")
        print(f"       {detail}")
        if ok:
            passed += 1
        else:
            errors.append(f"[CSS] {rule['name']}: {detail}")

    print()

    # 结构规则校验
    print("🏗️  HTML 结构规则校验 ({}/{}):".format(0, len(STRUCTURE_CHECKS)))
    print("-" * 50)
    for i, rule in enumerate(STRUCTURE_CHECKS, 1):
        ok, detail = check_structure_rule(rule, root)
        status = "✅" if ok else "❌"
        print(f"  [{i:2d}/{len(STRUCTURE_CHECKS)}] {status} {rule['name']}")
        print(f"       {detail}")
        if ok:
            passed += 1
        else:
            errors.append(f"[结构] {rule['name']}: {detail}")

    print()
    print(f"{'='*60}")
    print(f"📊 结果: {passed}/{total} 通过")
    if errors:
        print(f"❌ {len(errors)} 项不一致:")
        for e in errors:
            print(f"   • {e}")
        print(f"{'='*60}")
        print("\n💡 请将HTML中的手动改动同步到对应生成脚本，防止GHA自动生成时被覆盖。")
        sys.exit(1)
    else:
        print(f"🎉 全部通过，布局一致性校验成功！")
        print(f"{'='*60}")
        sys.exit(0)


if __name__ == "__main__":
    main()
