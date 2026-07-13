#!/usr/bin/env python3
"""
日历HTML结构验证脚本 — 部署前运行，确保标签、日期正确
用法：
  python3 validate_calendar_html.py                   # 检查当前目录所有日历
  python3 validate_calendar_html.py 文件路径...        # 检查指定文件
返回码：0=通过，1=有错误
"""

import re
import sys
import os

errors = []
warnings = []

# ====== 各日历的预期结构 ======
# 每个文件定义：7月区标签 → 预期日期列表
# 注意：6月区跨月表(6/29-6/30+7/1-7/5)的存在会导致7月区起始周不同

CALENDAR_SPECS = {
    "北向资金日历.html": {
        "july_labels": [
            ("第1周 7/1-7/5",    [29, 30, 1, 2, 3, 4, 5]),
            ("第2周 7/6-7/12",   [6, 7, 8, 9, 10, 11, 12]),
            ("第3周 7/13-7/19",  [13, 14, 15, 16, 17, 18, 19]),
            ("第4周 7/20-7/26",  [20, 21, 22, 23, 24, 25, 26]),
            ("第5周 7/27-8/2",   [27, 28, 29, 30, 31, 1, 2]),
        ],
        # 7月区不应出现的顶部标签
        "forbidden_labels": [],
        # 底部汇总应有标签
        "bottom_labels": [
            "第1周 7/1-7/5", "第2周 7/6-7/12", "第3周 7/13-7/19",
            "第4周 7/20-7/26", "第5周 7/27-8/2",
        ],
    },
    "机游共振日历.html": {
        "july_labels": [
            ("第1周 7/1-7/5",    [29, 30, 1, 2, 3, 4, 5]),
            ("第2周 7/6-7/12",   [6, 7, 8, 9, 10, 11, 12]),
            ("第3周 7/13-7/19",  [13, 14, 15, 16, 17, 18, 19]),
            ("第4周 7/20-7/26",  [20, 21, 22, 23, 24, 25, 26]),
            ("第5周 7/27-8/2",   [27, 28, 29, 30, 31, 1, 2]),
        ],
        "forbidden_labels": [],
        "bottom_labels": [
            "第1周 7/1-7/5", "第2周 7/6-7/12", "第3周 7/13-7/19",
            "第4周 7/20-7/26", "第5周 7/27-8/2",
        ],
    },
}

# 6月区已清空，JUNE_LABELS已移除

TOP_LABEL_PATTERN = r'<div style="text-align:left;font-size:12px;color:#8b949e;margin:-15px 0 25px 5px;">%s</div>'


def check_file(filepath):
    """检查单个HTML文件"""
    basename = os.path.basename(filepath)
    spec = CALENDAR_SPECS.get(basename)
    if not spec:
        warnings.append(f"[{basename}] 未知日历类型，跳过针对性检查")
        return

    if not os.path.exists(filepath):
        errors.append(f"[{basename}] 文件不存在")
        return

    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    file_errors = []
    file_warnings = []

    # 0. 6月区已清空，跳过检查

    # 1. 检查7月区标签 + 日期对应
    for label_text, expected_dates in spec["july_labels"]:
        label_pattern = TOP_LABEL_PATTERN % re.escape(label_text)
        match = re.search(label_pattern, content)
        if not match:
            file_errors.append(f"缺少7月区顶部标签: 「{label_text}」")
            continue

        # 找到标签后的第一个week-table
        table = re.search(
            r'<table[^>]*class="week-table"[^>]*>.*?</table>',
            content[match.end():], re.DOTALL
        )
        if not table:
            file_errors.append(f"标签「{label_text}」后未找到表格")
            continue

        day_numbers = re.findall(
            r'<span class="day-number(?: other)?">(\d+)</span>',
            table.group(0)
        )
        actual_dates = [int(d) for d in day_numbers]

        if actual_dates != expected_dates:
            file_errors.append(
                f"标签「{label_text}」日期不匹配\n"
                f"        预期: {expected_dates}\n"
                f"        实际: {actual_dates}"
            )
        else:
            print(f"  ✅ {label_text} → 日期 {actual_dates}")

    # 2. 检查不应出现的顶部标签
    for label_text in spec["forbidden_labels"]:
        pattern = TOP_LABEL_PATTERN % re.escape(label_text)
        matches = re.findall(pattern, content)
        if matches:
            file_errors.append(
                f"存在不应出现的顶部标签: 「{label_text}」(共{len(matches)}处)"
            )

    # 3. 检查底部汇总标签
    for label_text in spec["bottom_labels"]:
        pattern = r'<div class="week-title">' + re.escape(label_text) + r'</div>'
        if not re.search(pattern, content):
            file_errors.append(f"缺少底部汇总标签: 「{label_text}」")

    # 输出
    if file_errors:
        print(f"  ❌ 发现 {len(file_errors)} 个错误:")
        for e in file_errors:
            print(f"    {e}")
        errors.extend([f"[{basename}] {e}" for e in file_errors])
    else:
        print(f"  ✅ 全部通过")

    for w in file_warnings:
        warnings.append(f"[{basename}] {w}")


def main():
    if len(sys.argv) > 1:
        files = sys.argv[1:]
    else:
        # 默认检查当前目录
        files = list(CALENDAR_SPECS.keys())

    print(f"🔍 日历HTML结构验证 — {len(files)} 个文件")
    print("=" * 60)

    for f in files:
        filepath = f
        if not os.path.isabs(filepath):
            if not os.path.exists(filepath):
                filepath = os.path.join(os.path.dirname(__file__) or '.', f)
        basename = os.path.basename(filepath)
        print(f"\n📄 {basename}")
        print("-" * 40)
        check_file(filepath)

    print("\n" + "=" * 60)

    if warnings:
        print(f"⚠️  警告 {len(warnings)} 条:")
        for w in warnings:
            print(f"  {w}")

    if errors:
        print(f"\n❌ 共 {len(errors)} 个错误，部署已中止")
        for e in errors:
            print(f"  ❌ {e}")
        sys.exit(1)
    else:
        print(f"\n✅ 全部验证通过，可以部署！")
        sys.exit(0)


if __name__ == "__main__":
    main()