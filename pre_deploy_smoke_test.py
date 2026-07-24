#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
防冒烟验证脚本 - 部署前检查HTML文件是否有明显结构问题
用法: python3 pre_deploy_smoke_test.py [文件路径...]
不传参数则默认检查所有核心HTML文件
"""
import os
import re
import sys
import json

# 默认检查的核心文件
DEFAULT_FILES = [
    "北向资金日历.html",
    "index.html",
    "jiyou-resonance.html",
    "northbound-analysis.html",
    "jiyou-signal-analysis.html",
]

# 每个文件必须包含的关键元素（确保页面基本功能正常）
REQUIRED_ELEMENTS = {
    "all": [
        ("<!DOCTYPE", "DOCTYPE声明"),
        ("<html", "html标签"),
        ("<head", "head标签"),
        ("<body", "body标签"),
        ("</html>", "html闭合"),
    ],
    "北向资金日历.html": [
        ("周度汇总", "周度汇总标题"),
        ("月度汇总", "月度汇总标题"),
        ("week-grid", "week-grid容器"),
        ("week-box", "week-box卡片"),
        ("monthly-summary", "月度汇总容器"),
    ],
    "index.html": [
        ("机游共振", "机游共振标题"),
        ("周度汇总", "周度汇总标题"),
        ("月度汇总", "月度汇总标题"),
        ("week-grid", "week-grid容器"),
        ("week-box", "week-box卡片"),
    ],
    "jiyou-resonance.html": [
        ("机游共振", "机游共振标题"),
        ("周度汇总", "周度汇总标题"),
        ("月度汇总", "月度汇总标题"),
        ("week-grid", "week-grid容器"),
    ],
    "northbound-analysis.html": [
        ("北向资金分析", "页面标题"),
        ("行业配置趋势", "行业配置模块"),
        ("连续加仓", "连续加仓模块"),
        ("机构共振", "机构共振模块"),
        ("持仓变化榜", "持仓变化模块"),
    ],
    "jiyou-signal-analysis.html": [
        ("机游信号分析", "页面标题"),
        ("rank-table", "rank-table表格"),
    ],
}

def check_div_balance(content, filepath):
    """检查div标签配对"""
    opens = len(re.findall(r'<div\b', content, re.IGNORECASE))
    closes = len(re.findall(r'</div>', content, re.IGNORECASE))
    return opens == closes, opens, closes

def check_required_elements(content, filepath):
    """检查关键元素是否存在"""
    filename = os.path.basename(filepath)
    checks = REQUIRED_ELEMENTS.get("all", []) + REQUIRED_ELEMENTS.get(filename, [])
    results = []
    for pattern, desc in checks:
        found = bool(re.search(pattern, content, re.IGNORECASE))
        results.append((desc, found))
    return results

def check_file_size(filepath, min_kb=1, max_kb=5000):
    """检查文件大小是否在合理范围"""
    size_kb = os.path.getsize(filepath) / 1024
    return min_kb <= size_kb <= max_kb, size_kb

def check_js_syntax(content, filepath):
    """简单检查JS语法（括号配对、未闭合的字符串等）"""
    # 提取script内容
    scripts = re.findall(r'<script[^>]*>(.*?)</script>', content, re.DOTALL | re.IGNORECASE)
    issues = []
    for i, script in enumerate(scripts):
        if not script.strip():
            continue
        # 检查大括号配对
        opens = script.count('{')
        closes = script.count('}')
        if opens != closes:
            issues.append(f"script#{i}: 大括号不配对 ({opens}{{ vs {closes}}})")
        # 检查圆括号配对
        p_open = script.count('(')
        p_close = script.count(')')
        if p_open != p_close:
            issues.append(f"script#{i}: 圆括号不配对 ({p_open}( vs {p_close}))")
    return issues

def check_week_box_count(content, filepath):
    """检查周度卡片数量（日历页面应为5个）"""
    count = len(re.findall(r'class="week-box"', content))
    if count == 0:
        return None, 0  # 非周度页面
    return count == 5, count

def run_checks(filepath):
    """对单个文件运行所有检查"""
    result = {
        "file": filepath,
        "passed": True,
        "checks": [],
        "errors": [],
    }
    
    if not os.path.exists(filepath):
        result["passed"] = False
        result["errors"].append("文件不存在")
        return result
    
    # 文件大小检查
    ok, size_kb = check_file_size(filepath)
    result["checks"].append(("文件大小", ok, f"{size_kb:.1f} KB"))
    if not ok:
        result["passed"] = False
        result["errors"].append(f"文件大小异常: {size_kb:.1f} KB")
    
    # 读取内容
    try:
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
    except Exception as e:
        result["passed"] = False
        result["errors"].append(f"读取失败: {e}")
        return result
    
    # 空文件检查
    if len(content.strip()) < 100:
        result["passed"] = False
        result["errors"].append("文件内容过空")
        return result
    
    # div配对检查
    ok, opens, closes = check_div_balance(content, filepath)
    result["checks"].append(("div标签配对", ok, f"{opens}开 / {closes}合"))
    if not ok:
        result["passed"] = False
        result["errors"].append(f"div标签不配对: open={opens}, close={closes}, 差={opens-closes}")
    
    # 关键元素检查
    elem_results = check_required_elements(content, filepath)
    for desc, found in elem_results:
        result["checks"].append((f"关键元素: {desc}", found, ""))
        if not found:
            result["passed"] = False
            result["errors"].append(f"缺少关键元素: {desc}")
    
    # JS语法检查
    js_issues = check_js_syntax(content, filepath)
    if js_issues:
        result["checks"].append(("JS语法检查", False, f"{len(js_issues)}个问题"))
        result["passed"] = False
        for issue in js_issues:
            result["errors"].append(f"JS问题: {issue}")
    else:
        result["checks"].append(("JS语法检查", True, ""))
    
    # 周度卡片数量检查
    ok_count, count = check_week_box_count(content, filepath)
    if ok_count is not None:
        result["checks"].append(("week-box数量(应为5)", ok_count, f"{count}个"))
        if not ok_count:
            result["passed"] = False
            result["errors"].append(f"week-box数量异常: {count}个 (应为5)")
    
    return result

def main():
    # 确定要检查的文件
    if len(sys.argv) > 1:
        files = sys.argv[1:]
    else:
        files = DEFAULT_FILES
    
    all_passed = True
    total_checks = 0
    passed_checks = 0
    
    print("=" * 60)
    print("  防冒烟验证 - 部署前HTML检查")
    print("=" * 60)
    print()
    
    for filepath in files:
        result = run_checks(filepath)
        
        status_icon = "✅" if result["passed"] else "❌"
        print(f"{status_icon} {result['file']}")
        
        if not result["passed"]:
            all_passed = False
        
        for name, ok, detail in result["checks"]:
            total_checks += 1
            if ok:
                passed_checks += 1
            icon = "  ✓" if ok else "  ✗"
            detail_str = f" [{detail}]" if detail else ""
            print(f"{icon} {name}{detail_str}")
        
        if result["errors"]:
            print(f"  错误:")
            for err in result["errors"]:
                print(f"    - {err}")
        
        print()
    
    print("-" * 60)
    print(f"  总计: {passed_checks}/{total_checks} 项通过")
    print(f"  结果: {'全部通过 ✅' if all_passed else '存在失败 ❌'}")
    print("=" * 60)
    
    sys.exit(0 if all_passed else 1)

if __name__ == "__main__":
    main()
